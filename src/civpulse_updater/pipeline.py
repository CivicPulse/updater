"""Collection and replay persist proposals only; no publisher exists."""

import json

from loguru import logger

from .adapters.simbli import PublicDocumentUnavailable
from .evidence import EvidenceStore
from .models import ListingRequest, Reference, SourceConfig, Window, digest
from .plugins import load_adapter
from .state import State
from .transport import Fetcher, FetchError, ResponseLimitError, validate_url


class ResumeError(ValueError):
    """Operator guidance consisting only of fixed, non-sensitive messages."""


def resume_identity(config: SourceConfig):
    return (
        config.id,
        config.adapter,
        config.url,
        config.allowed_hosts,
        config.timezone,
        config.legislature_session_id,
    )


def latest_resumable_run(state: State, config: SourceConfig, window: Window):
    """Called while holding the state lock; never revive a superseded snapshot."""
    candidates = state.rows(
        "SELECT id,status,config,window FROM runs WHERE source=:source ORDER BY started_at DESC",
        source=config.id,
    )
    for row in candidates:
        original = SourceConfig.model_validate_json(row["config"])
        if (
            resume_identity(original) != resume_identity(config)
            or Window.model_validate_json(row["window"]) != window
        ):
            continue
        # The newest matching observation controls recovery. A completed or
        # non-resumable newer run must not revive an older unfinished snapshot.
        if row["status"] == "complete":
            return None
        checkpoint = state.rows(
            "SELECT errors,inputs != '[]' AS has_inputs FROM runs WHERE id=:id",
            id=row["id"],
        )[0]
        errors = json.loads(checkpoint["errors"])
        if not checkpoint["has_inputs"] or any(
            error.get("stage") == "discover" or error.get("code") == "event_limit"
            for error in errors
        ):
            return None
        return row["id"]
    return None


def collect(
    state: State,
    store: EvidenceStore,
    config: SourceConfig,
    window: Window,
    fetcher=None,
    resume_run=None,
):
    if window.full_session and config.adapter != "georgia-legislature":
        raise ValueError("Full-session windows require the Georgia legislature adapter")
    adapter = load_adapter(config)
    validate_url(config.url, config.allowed_hosts)
    previous_inputs = []
    if resume_run:
        previous = state.rows("SELECT * FROM runs WHERE id=:id", id=resume_run)
        if not previous:
            raise ResumeError("Unknown resume run")
        previous = previous[0]
        if any(
            error.get("stage") == "discover" or error.get("code") == "event_limit"
            for error in json.loads(previous["errors"])
        ):
            raise ResumeError(
                "Incomplete discovery cannot be resumed; start a fresh capture"
            )
        original = SourceConfig.model_validate_json(previous["config"])
        if (
            resume_identity(original) != resume_identity(config)
            or Window.model_validate_json(previous["window"]) != window
        ):
            raise ResumeError("Resume source and window must match")
        previous_inputs = json.loads(previous["inputs"])
        if not previous_inputs:
            raise ResumeError(
                "Run has no checkpointed records; start a fresh collection"
            )
        for entry in previous_inputs:
            ref = Reference.model_validate(entry["reference"])
            validate_url(ref.url, config.allowed_hosts)
            validate_url(ref.page_url, config.allowed_hosts)
            store.read(ref.listing_hash)
            if entry.get("sha256"):
                store.read(entry["sha256"])
            for url, sha in entry.get("resources", {}).items():
                validate_url(url, config.allowed_hosts)
                store.read(sha)
    run = state.start_run(
        config.id,
        config.model_dump_json(),
        window.model_dump_json(),
        replay_of=resume_run,
    )
    if fetcher is None:
        if config.adapter == "simbli":
            from .simbli_browser import SimbliFetcher

            def capture(body, evidence):
                if store.put(body) != evidence.sha256:
                    raise ValueError("Browser evidence mismatch")
                state.observe(run, evidence.model_dump(mode="json"))

            fetcher = SimbliFetcher(config, state.root, capture)
        elif config.adapter == "georgia-legislature":
            from .georgia_transport import GeorgiaFetcher

            def capture(body, evidence):
                if store.put(body) != evidence.sha256:
                    raise ValueError("Georgia evidence mismatch")
                state.observe(run, evidence.model_dump(mode="json"))

            fetcher = GeorgiaFetcher(config, capture)
        elif config.adapter == "georgia-sos-turnout":
            from .turnout_transport import GeorgiaTurnoutFetcher

            def capture(body, evidence):
                if store.put(body) != evidence.sha256:
                    raise ValueError("Turnout evidence mismatch")
                state.observe(run, evidence.model_dump(mode="json"))

            fetcher = GeorgiaTurnoutFetcher(config, window, capture)
        else:
            fetcher = Fetcher(config)
    inputs, errors, refs = [], [], {}
    pages = (
        adapter.start_urls(window) if hasattr(adapter, "start_urls") else [config.url]
    )
    if resume_run:
        pages = []
        refs = {
            entry["reference"]["url"]: Reference.model_validate(entry["reference"])
            for entry in previous_inputs
        }
    visited = set()
    resource_count = 0
    while pages:
        request = pages.pop(0)
        url = request.url if isinstance(request, ListingRequest) else request
        key = (
            (url, digest(request.content))
            if isinstance(request, ListingRequest)
            else url
        )
        if key in visited:
            continue
        if len(visited) >= config.max_pages:
            errors.append({"stage": "discover", "code": "page_limit"})
            break
        visited.add(key)
        try:
            validate_url(url, config.allowed_hosts)
            if isinstance(request, ListingRequest):
                body, evidence = fetcher.get_listing_page(request)
            else:
                body, evidence = fetcher.get(url)
            sha = store.put(body)
            if sha != evidence.sha256:
                raise ValueError("Transport evidence mismatch")
            state.observe(run, evidence.model_dump(mode="json"))
            found, following = adapter.discover(body, evidence.url, sha)
            for ref in found:
                validate_url(ref.url, config.allowed_hosts)
                validate_url(ref.page_url, config.allowed_hosts)
                refs.setdefault(ref.url, ref)
            for next_request in following:
                next_url = (
                    next_request.url
                    if isinstance(next_request, ListingRequest)
                    else next_request
                )
                validate_url(next_url, config.allowed_hosts)
                next_key = (
                    (next_url, digest(next_request.content))
                    if isinstance(next_request, ListingRequest)
                    else next_url
                )
                if next_key not in visited and next_request not in pages:
                    pages.append(next_request)
        except (ValueError, OSError, KeyError, TypeError) as error:
            errors.append(
                {
                    "stage": "discover",
                    "code": type(error).__name__,
                    "detail": str(error)
                    if isinstance(error, FetchError)
                    else "Listing could not be processed",
                }
            )
        state.save_inputs(run, inputs, errors)
    if len(refs) > config.max_events:
        errors.append({"stage": "fetch", "code": "event_limit"})
    resumed = {entry["reference"]["url"]: entry for entry in previous_inputs}
    inputs = [
        resumed.get(
            ref.url,
            {
                "reference": ref.model_dump(mode="json"),
                "complete": False,
                "resources": {},
            },
        )
        for ref in list(refs.values())[: config.max_events]
    ]
    state.save_inputs(run, inputs, errors)
    for entry in inputs:
        ref = Reference.model_validate(entry["reference"])
        if entry.get("complete", True) and entry.get("sha256"):
            continue
        stop_network = False
        failed_resources = set()
        try:
            if entry.get("sha256"):
                body = store.read(entry["sha256"])
            elif hasattr(
                adapter, "uses_listing_content"
            ) and adapter.uses_listing_content(ref):
                # Reuse original listing bytes as a replay input, never invent
                # a successful detail request for a listing-only record.
                body = store.read(ref.listing_hash)
                entry["sha256"] = ref.listing_hash
                state.save_inputs(run, inputs, errors)
            else:
                body, evidence = fetcher.get(ref.url)
                sha = store.put(body)
                if sha != evidence.sha256:
                    raise ValueError("Transport evidence mismatch")
                state.observe(run, evidence.model_dump(mode="json"))
                entry["sha256"] = sha
                state.save_inputs(run, inputs, errors)
            if hasattr(adapter, "resources") or hasattr(adapter, "resources_for"):
                resources = {
                    url: store.read(sha)
                    for url, sha in entry.get("resources", {}).items()
                }
                while True:
                    needed = (
                        adapter.resources_for(body, ref, resources)
                        if hasattr(adapter, "resources_for")
                        else adapter.resources(body, resources)
                    )
                    missing = [
                        url
                        for url in needed
                        if url not in resources and url not in failed_resources
                    ]
                    if not missing:
                        break
                    for resource_url in missing:
                        if resource_count >= config.max_resources:
                            raise ValueError("Resource limit exceeded")
                        validate_url(resource_url, config.allowed_hosts)
                        resource_count += 1
                        resource_body, metadata = fetcher.get(resource_url)
                        resource_sha = store.put(resource_body)
                        if resource_sha != metadata.sha256:
                            raise ValueError("Resource evidence mismatch")
                        state.observe(run, metadata.model_dump(mode="json"))
                        if hasattr(adapter, "validate_resource"):
                            try:
                                adapter.validate_resource(
                                    body, ref, resources, resource_url, resource_body
                                )
                            except PublicDocumentUnavailable:
                                # Retain the failed response as evidence, but fetch
                                # the other verified public resources once. The
                                # bundle stays incomplete and cannot be proposed.
                                failed_resources.add(resource_url)
                                errors.append(
                                    {
                                        "stage": "fetch",
                                        "code": "PublicDocumentUnavailable",
                                        "meeting_url": ref.url,
                                        "detail": "Public document returned HTML; bundle remains incomplete",
                                    }
                                )
                                state.save_inputs(run, inputs, errors)
                                continue
                        resources[resource_url] = resource_body
                        entry.setdefault("resources", {})[resource_url] = resource_sha
                        state.save_inputs(run, inputs, errors)
                entry["resources"] = {
                    url: store.put(data) for url, data in resources.items()
                }
            entry["complete"] = not failed_resources
        except (ValueError, OSError, KeyError, TypeError) as error:
            stop_network = isinstance(error, FetchError) and (
                not isinstance(error, ResponseLimitError) or error.aggregate
            )
            errors.append(
                {
                    "stage": "fetch",
                    "code": type(error).__name__,
                    "meeting_url": ref.url if config.adapter == "simbli" else None,
                    "detail": str(error)
                    if isinstance(error, FetchError)
                    else "Evidence could not be stored",
                }
            )
        state.save_inputs(run, inputs, errors)
        if stop_network:
            break
    return parse_run(state, store, adapter, run, inputs, errors, window)


def parse_run(state, store, adapter, run, inputs, collection_errors, window):
    errors = list(collection_errors)
    events, conflicts = {}, set()
    for entry in inputs:
        if entry.get("complete") is False:
            errors.append(
                {
                    "stage": "parse",
                    "code": "IncompleteEvidence",
                    "meeting_url": entry["reference"]["url"],
                }
            )
            continue
        try:
            ref = Reference.model_validate(entry["reference"])
            store.read(ref.listing_hash)  # Validate both evidence dependencies.
            if hasattr(adapter, "parse_bundle"):
                resources = {
                    url: store.read(sha)
                    for url, sha in entry.get("resources", {}).items()
                }
                envelopes = adapter.parse_bundle(
                    store.read(entry["sha256"]), ref, entry["sha256"], window, resources
                )
                if hasattr(adapter, "materialized_documents"):
                    for body in adapter.materialized_documents(
                        store.read(entry["sha256"]), ref, resources
                    ):
                        store.put(body)
            else:
                envelopes = adapter.parse(
                    store.read(entry["sha256"]), ref, entry["sha256"], window
                )
            for envelope in envelopes:
                key = envelope.event.external_id
                previous = events.get(key)
                if previous and previous.event != envelope.event:
                    conflicts.add(key)
                    errors.append({"stage": "parse", "code": "conflicting_identity"})
                else:
                    events[key] = envelope
        except (ValueError, KeyError, TypeError, OSError) as error:
            errors.append(
                {
                    "stage": "parse",
                    "code": type(error).__name__,
                    "sha256": entry["sha256"],
                }
            )
    for key in conflicts:
        events.pop(key, None)
    result = state.finish(run, list(events.values()), errors)
    logger.bind(run_id=run).info(
        "Collection finished: {} records, {} proposals, status {}",
        result["records"],
        result["proposed_changes"],
        result["status"],
    )
    return result


def replay(state: State, store: EvidenceStore, run_id: str):
    rows = state.rows("SELECT * FROM runs WHERE id=:id", id=run_id)
    if not rows:
        raise ValueError("Unknown run")
    previous = rows[0]
    config = SourceConfig.model_validate_json(previous["config"])
    window = Window.model_validate_json(previous["window"])
    inputs = json.loads(previous["inputs"])
    errors = [
        item for item in json.loads(previous["errors"]) if item["stage"] != "parse"
    ]
    if previous["status"] in ("running", "interrupted"):
        errors.append({"stage": "fetch", "code": "incomplete_original_run"})
    adapter = load_adapter(config)
    run = state.start_run(
        config.id, config.model_dump_json(), window.model_dump_json(), run_id
    )
    state.save_inputs(run, inputs, errors)
    return parse_run(state, store, adapter, run, inputs, errors, window)
