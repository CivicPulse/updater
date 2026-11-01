"""Small numerical datasets from SOS Qlik, with offline replayable provenance."""

import json
import os
import tempfile
from datetime import UTC, datetime

from .config import load_source
from .models import Reference, Window, canonical
from .pipeline import collect
from .plugins import load_adapter

GROUPS = {
    "all": ("georgia-sos-registration", "georgia-sos-turnout"),
    "registration": ("georgia-sos-registration",),
    "turnout": ("georgia-sos-turnout",),
}


def records_from_evidence(state, store, run_id):
    """Export this exact run, never stale records left over from a broader profile."""
    run = state.rows("SELECT * FROM runs WHERE id=:id", id=run_id)[0]
    from .models import SourceConfig

    config = SourceConfig.model_validate_json(run["config"])
    adapter = load_adapter(config)
    window = Window.model_validate_json(run["window"])
    records = []
    for entry in json.loads(run["inputs"]):
        if not entry.get("complete") or not entry.get("sha256"):
            raise ValueError("Summary evidence is incomplete")
        ref = Reference.model_validate(entry["reference"])
        store.read(ref.listing_hash)
        records.extend(
            {
                "record": envelope.event.model_dump(mode="json"),
                "evidence_hashes": list(envelope.evidence_hashes),
            }
            for envelope in adapter.parse(
                store.read(entry["sha256"]), ref, entry["sha256"], window
            )
        )
    return records


def collect_statistics(
    state,
    store,
    sources_dir,
    group,
    today,
    *,
    max_requests=1500,
    max_total_bytes=100000000,
):
    if group not in GROUPS:
        raise ValueError("Unknown Qlik statistics source")
    results, records = [], []
    window = Window(start=today, end=today)
    for name in GROUPS[group]:
        config = load_source(sources_dir, name)
        config = type(config).model_validate(
            config.model_dump()
            | {
                "max_requests": max_requests,
                "max_total_bytes": max_total_bytes,
                "qlik_all_history": name == "georgia-sos-registration",
                "qlik_current_election": name == "georgia-sos-turnout",
            }
        )
        result = collect(state, store, config, window)
        results.append({"source": name, **result})
        if result["status"] == "complete":
            records.extend(records_from_evidence(state, store, result["run_id"]))
    complete = all(result["status"] == "complete" for result in results)
    report = {
        "mode": "review-only",
        "profile": "statistics-summary",
        "status": "complete" if complete else "partial",
        "sources": results,
    }
    if not complete:
        # Keep the last successful bundle rather than replacing it with partial data.
        return report
    if not records:
        raise ValueError("Summary sources returned no records")
    bundle = {
        "schema_version": 1,
        "profile": "statistics-summary",
        "observed_at": datetime.now(UTC).isoformat(),
        "sources": results,
        "datasets": records,
    }
    content = (canonical(bundle) + "\n").encode()
    target = state.root / f"qlik-statistics-{group}.json"
    fd, temporary = tempfile.mkstemp(dir=state.root)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    report.update(
        output=str(target.resolve()),
        datasets=len(records),
        rows=sum(len(item["record"]["data"]["rows"]) for item in records),
        output_bytes=len(content),
        offline_evidence_verified=True,
    )
    return report
