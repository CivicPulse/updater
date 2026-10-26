"""Collection and replay persist proposals only; no publisher exists."""

import json

from loguru import logger

from .evidence import EvidenceStore
from .models import Reference, SourceConfig, Window
from .plugins import load_adapter
from .state import State
from .transport import Fetcher, FetchError, validate_url


def collect(
    state: State,
    store: EvidenceStore,
    config: SourceConfig,
    window: Window,
    fetcher=None,
):
    adapter = load_adapter(config)
    validate_url(config.url, config.allowed_hosts)
    run = state.start_run(config.id, config.model_dump_json(), window.model_dump_json())
    if fetcher is None:
        if config.adapter == "simbli":
            from .simbli_browser import SimbliFetcher

            def capture(body, evidence):
                if store.put(body) != evidence.sha256:
                    raise ValueError("Browser evidence mismatch")
                state.observe(run, evidence.model_dump(mode="json"))

            fetcher = SimbliFetcher(config, state.root, capture)
        else:
            fetcher = Fetcher(config)
    inputs, errors, refs = [], [], {}
    pages = (
        adapter.start_urls(window) if hasattr(adapter, "start_urls") else [config.url]
    )
    visited = set()
    resource_count = 0
    while pages:
        url = pages.pop(0)
        if url in visited:
            continue
        if len(visited) >= config.max_pages:
            errors.append({"stage": "discover", "code": "page_limit"})
            break
        visited.add(url)
        try:
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
            for next_url in following:
                validate_url(next_url, config.allowed_hosts)
                if next_url not in visited and next_url not in pages:
                    pages.append(next_url)
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
    for ref in list(refs.values())[: config.max_events]:
        try:
            body, evidence = fetcher.get(ref.url)
            sha = store.put(body)
            if sha != evidence.sha256:
                raise ValueError("Transport evidence mismatch")
            state.observe(run, evidence.model_dump(mode="json"))
            entry = {"reference": ref.model_dump(mode="json"), "sha256": sha}
            if hasattr(adapter, "resources") or hasattr(adapter, "resources_for"):
                resources = {}
                while True:
                    needed = (
                        adapter.resources_for(body, ref, resources)
                        if hasattr(adapter, "resources_for")
                        else adapter.resources(body, resources)
                    )
                    missing = [url for url in needed if url not in resources]
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
                        resources[resource_url] = resource_body
                entry["resources"] = {
                    url: store.put(data) for url, data in resources.items()
                }
            inputs.append(entry)
        except (ValueError, OSError, KeyError, TypeError) as error:
            errors.append(
                {
                    "stage": "fetch",
                    "code": type(error).__name__,
                    "detail": str(error)
                    if isinstance(error, FetchError)
                    else "Evidence could not be stored",
                }
            )
        state.save_inputs(run, inputs, errors)
    return parse_run(state, store, adapter, run, inputs, errors, window)


def parse_run(state, store, adapter, run, inputs, collection_errors, window):
    errors = list(collection_errors)
    events, conflicts = {}, set()
    for entry in inputs:
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
