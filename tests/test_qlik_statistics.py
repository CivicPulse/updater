import json
from datetime import date
from pathlib import Path

import pytest
from test_georgia_registration import Engine as RegistrationEngine
from test_georgia_registration import FixtureFetcher
from test_georgia_registration import snapshot as legacy_snapshot
from test_georgia_turnout import Engine as TurnoutEngine
from typer.testing import CliRunner

from civpulse_updater.adapters.georgia_registration import (
    APPS,
    LIVE_APP,
    MONTHLY_APP,
    SUMMARY_LIVE_TABLES,
    read_registration,
)
from civpulse_updater.adapters.georgia_turnout import APP, TABLES, read_tables
from civpulse_updater.cli import app
from civpulse_updater.config import load_source
from civpulse_updater.evidence import EvidenceStore
from civpulse_updater.models import Evidence, Window, canonical, digest
from civpulse_updater.pipeline import collect, replay
from civpulse_updater.qlik_statistics import collect_statistics
from civpulse_updater.state import State
from civpulse_updater.transport import FetchError

WINDOW = Window(start=date(2026, 11, 1), end=date(2026, 11, 1))


def registration_snapshot(all_history=True):
    engines = [RegistrationEngine(a) for a in APPS]
    for e in engines:
        read_registration(e, e.app_id, WINDOW, compact=True, all_history=all_history)
    return canonical(
        {
            "schema_version": 2,
            "profile": "statistics-summary",
            "all_history": all_history,
            "app_ids": list(APPS),
            "window": WINDOW.model_dump(mode="json"),
            "messages": {e.app_id: e.messages for e in engines},
        }
    ).encode(), engines


def turnout_snapshot():
    engine = TurnoutEngine()
    read_tables(engine, WINDOW, current=True)
    return canonical(
        {
            "schema_version": 2,
            "selection": "current-election",
            "app_id": APP,
            "window": WINDOW.model_dump(mode="json"),
            "messages": engine.messages,
        }
    ).encode()


def install_fetchers(monkeypatch, registration, turnout):
    monkeypatch.setattr(
        "civpulse_updater.registration_transport.GeorgiaRegistrationFetcher",
        lambda *a: FixtureFetcher(registration),
    )

    class TurnoutFetcher:
        def get(self, url):
            from datetime import UTC, datetime

            if turnout is None:
                raise FetchError("Synthetic unavailable turnout source")
            return turnout, Evidence(
                url=url,
                method="RPC",
                transport="qlik",
                sha256=digest(turnout),
                size=len(turnout),
                media_type="application/json",
                fetched_at=datetime.now(UTC),
            )

    monkeypatch.setattr(
        "civpulse_updater.turnout_transport.GeorgiaTurnoutFetcher",
        lambda *a: TurnoutFetcher(),
    )


def test_summary_has_no_raw_maps_charts_or_joint_history():
    _, engines = registration_snapshot()
    requests = [json.loads(m["request"]) for e in engines for m in e.messages]
    assert not {r["method"] for r in requests} & {
        "GetAllInfos",
        "GetTablesAndKeys",
        "GetTableData",
        "GetHyperCubePivotData",
        "GetHyperCubeStackData",
    }
    assert {r["params"][0] for r in requests if r["method"] == "GetObject"} == {
        "dSyCvqD"
    }
    dimensions = []
    for r in requests:
        if r["method"] == "CreateSessionObject" and "qHyperCubeDef" in r["params"][0]:
            definition = r["params"][0]["qHyperCubeDef"]
            dimensions.append(
                [d["qDef"]["qFieldDefs"][0] for d in definition["qDimensions"]]
            )
            if "USER" in definition["qMeasures"][0]["qDef"]["qDef"]:
                assert (
                    "Active" in definition["qMeasures"][0]["qDef"]["qDef"]
                    and "Inactive" in definition["qMeasures"][0]["qDef"]["qDef"]
                )
    assert dimensions and all(
        len(fields) <= 3 and "USER" not in fields and "%USER" not in fields
        for fields in dimensions
    )
    assert all(
        not any("Geometry" in f or "Coordinates" in f for f in fields)
        for fields in dimensions
    )
    rows = read_registration(
        RegistrationEngine(MONTHLY_APP),
        MONTHLY_APP,
        WINDOW,
        compact=True,
        all_history=True,
    )
    assert [d.isoformat() for d, _ in rows] == ["2026-10-01", "2026-11-01"]
    assert all(
        t["table"] == "monthly-status" and t["dimensions"] == ["County", "Voter Status"]
        for _, t in rows
    )
    live = read_registration(
        RegistrationEngine(LIVE_APP), LIVE_APP, WINDOW, compact=True
    )
    assert len(live) == 5
    precinct = next(t for _, t in live if t["table"] == "live-precinct")
    assert precinct["rows"] == [] and precinct["omitted_missing_precinct_rows"] == 2


def test_cli_summary_bundle_and_offline_replay_exclude_stale_legacy_tables(
    tmp_path, monkeypatch
):
    body, _ = registration_snapshot()
    install_fetchers(monkeypatch, body, turnout_snapshot())
    # Older, wider records remain for audit, but must not leak into this export.
    state = State(tmp_path)
    store = EvidenceStore(tmp_path / "evidence")
    config = load_source(Path("sources"), "georgia-sos-registration")
    collect(state, store, config, WINDOW, FixtureFetcher(legacy_snapshot(WINDOW)))
    # The orchestrator accepts an explicit date; CLI date handling is tested below.
    result = collect_statistics(state, store, Path("sources"), "all", WINDOW.start)
    assert result["status"] == "complete" and result["datasets"] == 10, result
    bundle = json.loads(Path(result["output"]).read_text())
    keys = {v["record"]["data"]["table"] for v in bundle["datasets"]}
    assert keys == set(SUMMARY_LIVE_TABLES) | {"monthly-status"} | set(TABLES)
    assert (
        "monthly-demographics" not in keys and "live-demographics-precinct" not in keys
    )
    historical = [
        v["record"]["as_of_date"]
        for v in bundle["datasets"]
        if v["record"]["data"]["table"] == "monthly-status"
    ]
    assert historical == ["2026-10-01", "2026-11-01"]
    turnout = [v for v in bundle["datasets"] if v["record"]["record_type"] == "turnout"]
    assert {v["record"]["election_date"] for v in turnout} == {"2026-11-03"}
    for source in result["sources"]:
        assert replay(state, store, source["run_id"])["proposed_changes"] == 0
    first = Path(result["output"]).read_bytes()
    install_fetchers(monkeypatch, body, None)
    failed = collect_statistics(state, store, Path("sources"), "all", WINDOW.start)
    assert failed["status"] == "partial" and "output" not in failed
    assert Path(result["output"]).read_bytes() == first


def test_cli_uses_summary_orchestrator_and_never_archive(tmp_path, monkeypatch):
    def summaries(state, store, sources_dir, group, today, **kwargs):
        assert group == "all" and kwargs["max_requests"] == 1500
        return {"status": "complete", "profile": "statistics-summary", "sources": []}

    monkeypatch.setattr(
        "civpulse_updater.qlik_statistics.collect_statistics", summaries
    )
    monkeypatch.setattr(
        "civpulse_updater.qlik_archive.run_archive",
        lambda *a, **kw: pytest.fail("Full archive called"),
    )
    result = CliRunner().invoke(
        app, ["--state-dir", str(tmp_path), "capture-qlik", "all"]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["profile"] == "statistics-summary"


@pytest.mark.parametrize("today", [date(2026, 11, 1), date(2027, 1, 1)])
def test_current_election_selection_uses_next_or_latest(today):
    result = read_tables(TurnoutEngine(), Window(start=today, end=today), current=True)
    assert {day for day, _, _ in result} == {date(2026, 11, 3)}


def test_profile_flags_are_scoped_to_their_sources():
    cfg = load_source(Path("sources"), "georgia-sos-turnout")
    with pytest.raises(ValueError, match="registration adapter"):
        type(cfg).model_validate(cfg.model_dump() | {"qlik_all_history": True})


def test_all_history_uses_one_grouped_query_not_per_date_downloads():
    _, engines = registration_snapshot()
    monthly = engines[1]
    calls = [json.loads(m["request"]) for m in monthly.messages]
    assert [r["params"] for r in calls if r["method"] == "GetObject"] == [["dSyCvqD"]]
    data_calls = [r for r in calls if r["method"] == "GetHyperCubeData"]
    assert len(data_calls) == 1
    assert data_calls[0]["params"][1][0]["qWidth"] == 4
    assert not any(r["method"] == "SelectListObjectValues" for r in calls)


def test_published_july_2025_correction_is_preserved_in_summary():
    engine = RegistrationEngine(MONTHLY_APP)
    engine.date_rows = [
        [dict(qText="07/01/2025", qElemNumber=1, qState="O")],
        [dict(qText="07/15/2025", qElemNumber=2, qState="O")],
    ]
    results = read_registration(
        engine, MONTHLY_APP, WINDOW, compact=True, all_history=True
    )
    first, corrected = [table for _, table in results]
    assert corrected["source_snapshot_date"] == "2025-07-01"
    assert corrected["source_date_correction_applied"] is True
    assert corrected["rows"] == first["rows"]
    assert [r["measures"]["Voter Count"]["value"] for r in corrected["rows"]] == [
        100,
        101,
    ]


def test_windowed_corrected_history_queries_the_required_baseline_date():
    engine = RegistrationEngine(MONTHLY_APP)
    engine.date_rows = [
        [dict(qText="07/01/2025", qElemNumber=1, qState="O")],
        [dict(qText="07/15/2025", qElemNumber=2, qState="O")],
    ]
    day = date(2025, 7, 15)
    results = read_registration(
        engine, MONTHLY_APP, Window(start=day, end=day), compact=True
    )
    assert len(results) == 1 and results[0][0] == day
    assert results[0][1]["source_snapshot_date"] == "2025-07-01"
    assert len(results[0][1]["rows"]) == 2


def test_history_measure_drift_is_rejected_before_data_fetch():
    engine = RegistrationEngine(MONTHLY_APP)
    engine.fault = "measure"
    with pytest.raises(ValueError, match="measure changed"):
        read_registration(engine, MONTHLY_APP, WINDOW, compact=True, all_history=True)
    assert not any(
        json.loads(m["request"])["method"] == "GetHyperCubeData"
        for m in engine.messages
    )


def test_mandatory_single_date_filter_does_not_truncate_history():
    class MandatoryDate(RegistrationEngine):
        def respond(self, handle, method, params):
            if method == "ClearAll":
                self.selected = 2
                return {}
            response = super().respond(handle, method, params)
            if method == "GetLayout" and "qSelectionObject" in response.get(
                "qLayout", {}
            ):
                for selected in response["qLayout"]["qSelectionObject"]["qSelections"]:
                    selected["qOneAndOnlyOne"] = True
            return response

    engine = MandatoryDate(MONTHLY_APP)
    result = read_registration(
        engine, MONTHLY_APP, WINDOW, compact=True, all_history=True
    )
    assert [day.isoformat() for day, _ in result] == ["2026-10-01", "2026-11-01"]
    assert all(len(table["rows"]) == 2 for _, table in result)
    requests = [json.loads(m["request"]) for m in engine.messages]
    definition = next(
        r["params"][0]["qHyperCubeDef"]
        for r in requests
        if r["method"] == "CreateSessionObject" and "qHyperCubeDef" in r["params"][0]
    )
    assert definition["qMeasures"][0]["qDef"]["qDef"] == "SUM({1} VoterCount)"


def test_partial_history_cannot_be_reported_as_complete():
    class LatestDateOnly(RegistrationEngine):
        def respond(self, handle, method, params):
            response = super().respond(handle, method, params)
            if method == "GetHyperCubeData":
                for page in response["qDataPages"]:
                    for row in page["qMatrix"]:
                        row[0]["qText"] = "11/01/2026"
                        row[0]["qElemNumber"] = 2
            return response

    with pytest.raises(ValueError, match="missing required snapshot dates"):
        read_registration(
            LatestDateOnly(MONTHLY_APP),
            MONTHLY_APP,
            WINDOW,
            compact=True,
            all_history=True,
        )


def test_resume_identity_keeps_summary_scopes_separate():
    from civpulse_updater.pipeline import resume_identity

    registration = load_source(Path("sources"), "georgia-sos-registration")
    assert resume_identity(registration) != resume_identity(
        registration.model_copy(update={"qlik_all_history": True})
    )
    turnout = load_source(Path("sources"), "georgia-sos-turnout")
    assert resume_identity(turnout) != resume_identity(
        turnout.model_copy(update={"qlik_current_election": True})
    )
