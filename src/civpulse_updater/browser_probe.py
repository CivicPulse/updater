"""Retry one previously captured Simbli attachment; never create review changes."""

import json
from datetime import UTC, datetime
from uuid import uuid4

from .adapters.simbli import Simbli
from .models import Reference, SourceConfig
from .simbli_browser import SimbliFetcher, attachment_url, binary_document, challenge
from .transport import FetchError


def probe(
    state, store, run_id, index=0, fetcher_factory=SimbliFetcher, browser_engine=None
):
    rows = state.rows("SELECT * FROM runs WHERE id=:id", id=run_id)
    if not rows:
        raise ValueError("Unknown source run")
    previous = rows[0]
    config = SourceConfig.model_validate_json(previous["config"])
    config = SourceConfig.model_validate(
        config.model_dump()
        | {
            "browser_fallback": True,
            "browser_engine": browser_engine or config.browser_engine,
        }
    )
    adapter = Simbli(config)
    candidates = {}
    for entry in json.loads(previous["inputs"]):
        ref = Reference.model_validate(entry["reference"])
        store.read(ref.listing_hash)
        resources = {
            url: store.read(sha) for url, sha in entry.get("resources", {}).items()
        }
        _, definitions, _ = adapter.plan(store.read(entry["sha256"]), ref, resources)
        for _, (_, role, url, _) in definitions.items():
            if (
                role in ("Attachment", "Minutes Attachment")
                and attachment_url(url)
                and url in resources
                and challenge(resources[url])
            ):
                candidates[url] = True
    targets = sorted(candidates)
    if index < 0 or index >= len(targets):
        raise ValueError("No captured challenged attachment at that index")
    report = {
        "kind": "simbli-attachment-probe",
        "probe_id": str(uuid4()),
        "source_run": run_id,
        "target": targets[index],
        "candidate_count": len(targets),
        "mode": "review-only",
        "browser_engine": config.browser_engine,
        "started_at": datetime.now(UTC).isoformat(),
        "evidence": [],
        "recovered": False,
        "proposals_created": 0,
    }

    def capture(body, evidence):
        if store.put(body) != evidence.sha256:
            raise ValueError("Probe evidence mismatch")
        report["evidence"].append(evidence.model_dump(mode="json"))

    fetcher = fetcher_factory(config, state.root, capture)
    try:
        body, evidence = fetcher.get(targets[index])
        capture(body, evidence)
        if not binary_document(body):
            raise FetchError("Probe did not recover a supported document")
        report.update(
            recovered=True,
            sha256=evidence.sha256,
            size=len(body),
            transport=evidence.transport,
        )
    except (ValueError, OSError) as error:
        report["error"] = (
            str(error) if isinstance(error, FetchError) else "Probe unavailable"
        )
    report.update(
        browser_attempted=fetcher.browser_attempted,
        requests=fetcher.requests,
        downloaded_bytes=fetcher.received_bytes,
        finished_at=datetime.now(UTC).isoformat(),
    )
    # Content-address the complete report as well as each response, without
    # modifying the old run's inputs or any record/approval state.
    report_sha = store.put(json.dumps(report, sort_keys=True).encode())
    return {k: v for k, v in report.items() if k != "evidence"} | {
        "report_sha256": report_sha
    }
