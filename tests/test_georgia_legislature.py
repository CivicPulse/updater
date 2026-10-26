import json
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from civpulse_updater.adapters.georgia_legislature import GeorgiaLegislature
from civpulse_updater.cli import app
from civpulse_updater.config import load_source
from civpulse_updater.evidence import EvidenceStore
from civpulse_updater.georgia_transport import API, GeorgiaFetcher
from civpulse_updater.models import (
    Evidence,
    ListingRequest,
    Reference,
    Window,
    canonical,
    digest,
)
from civpulse_updater.pipeline import collect, replay
from civpulse_updater.state import State
from civpulse_updater.transport import Fetcher, FetchError

FIXTURES = Path(__file__).parent / "fixtures" / "georgia"


def fixture(name):
    return (FIXTURES / f"{name}.json").read_bytes()


@pytest.fixture
def config():
    return load_source(Path("sources"), "georgia-legislature")


@pytest.fixture
def window():
    return Window(start=date(2026, 1, 1), end=date(2026, 12, 31))


class GeorgiaFixtures:
    def __init__(self, config=None, capture=None, fail=None):
        self.calls = []
        self.fail = fail

    def get(self, url):
        self.calls.append(url)
        if self.fail and self.fail in url:
            raise FetchError("Fixture unavailable")
        path = url.removeprefix(API)
        if path == "sessions":
            body = fixture("sessions")
        elif path.startswith("members/list/"):
            body = fixture("members")
        elif path.startswith("members/detail/"):
            body = fixture("member")
        elif path.startswith("committees/List/"):
            body = fixture("committees")
        elif path.startswith("committees/details/"):
            body = fixture("committee")
        elif path.startswith("Vote/list/1/"):
            body = fixture("votes")
        elif path.startswith("Vote/list/2/"):
            body = b"[]"
        elif path.startswith("Vote/detail/"):
            body = fixture("vote")
        elif path.startswith("legislation/detail/"):
            body = fixture("bill")
        elif path.startswith("sessions/days/"):
            body = fixture("days")
        elif path.startswith("Document/calendars?"):
            body = fixture("calendars") if "chamber=1" in path else b"[]"
        elif path.startswith("meetings?"):
            # Feed returns an overlapping row; pipeline deduplicates identical events.
            body = fixture("meetings")
        else:
            raise AssertionError(url)
        return body, Evidence(
            url=url,
            sha256=digest(body),
            size=len(body),
            media_type="application/json",
            fetched_at=datetime.now(UTC),
        )

    def get_listing_page(self, request):
        assert request.content == b'{"sessionId":1033}'
        assert request.url.endswith("/200/0")
        body = canonical(
            {
                "results": [{"legislationId": 69281, "session": {"id": 1033}}],
                "resultCount": 1,
            }
        ).encode()
        return body, Evidence(
            url=request.url,
            method="POST",
            sha256=digest(body),
            size=len(body),
            media_type="application/json",
            fetched_at=datetime.now(UTC),
        )


def test_collection_review_and_offline_replay(tmp_path, config, window, monkeypatch):
    state, store = State(tmp_path), EvidenceStore(tmp_path / "evidence")
    result = collect(state, store, config, window, GeorgiaFixtures())
    assert result["status"] == "complete", result
    rows = [
        json.loads(row["payload"]) for row in state.rows("SELECT payload FROM records")
    ]
    assert {r.get("record_type", "meeting") for r in rows} == {
        "session",
        "bill",
        "vote",
        "member",
        "seat",
        "committee",
        "calendar",
        "meeting",
    }
    vote = next(r for r in rows if r.get("record_type") == "vote")
    assert len(vote["data"]["detail"]["votes"]) == 180
    seat = next(r for r in rows if r.get("record_type") == "seat")
    assert seat["data"]["district"]["number"] == 43
    assert seat["warnings"]
    committee = next(r for r in rows if r.get("record_type") == "committee")
    assert committee["data"]["chamber"] == 1
    before = len(state.rows("SELECT * FROM changes"))

    def forbidden(*args, **kwargs):
        raise AssertionError("Replay must remain offline")

    monkeypatch.setattr(GeorgiaFetcher, "exchange", forbidden)
    repeated = replay(state, store, result["run_id"])
    assert repeated["status"] == "complete" and repeated["proposed_changes"] == 0
    assert len(state.rows("SELECT * FROM changes")) == before
    change = next(
        row
        for row in state.rows("SELECT * FROM changes")
        if json.loads(row["payload"]).get("record_type") == "bill"
    )
    assert (
        state.review(change["id"], "approved", "tester", "verified", False)["published"]
        is False
    )


def test_failed_detail_keeps_previous_records(tmp_path, config, window):
    state, store = State(tmp_path), EvidenceStore(tmp_path / "evidence")
    collect(state, store, config, window, GeorgiaFixtures())
    before = state.rows("SELECT * FROM records")
    result = collect(state, store, config, window, GeorgiaFixtures(fail="Vote/detail/"))
    assert result["status"] == "partial"
    assert len(state.rows("SELECT * FROM records")) == len(before)
    assert result["proposed_changes"] == 0
    assert replay(state, store, result["run_id"])["status"] == "partial"


def test_search_pagination_and_incomplete_counts(config, window):
    adapter = GeorgiaLegislature(config)
    adapter.start_urls(window)
    rows = [{"legislationId": n + 1, "session": {"id": 1033}} for n in range(200)]
    first = canonical({"results": rows, "resultCount": 201}).encode()
    refs, following = adapter.discover(first, adapter.search(0).url, "a" * 64)
    assert len(refs) == 200 and following == [adapter.search(1)]
    second = canonical(
        {
            "results": [{"legislationId": 201, "session": {"id": 1033}}],
            "resultCount": 201,
        }
    ).encode()
    assert adapter.discover(second, following[0].url, "b" * 64)[1] == []
    with pytest.raises(ValueError, match="Duplicate"):
        adapter.discover(first, adapter.search(0).url, "a" * 64)
    with pytest.raises(ValueError, match="Incomplete"):
        GeorgiaLegislature(config).discover(
            b'{"results":[],"resultCount":201}', adapter.search(0).url, "a" * 64
        )
    with pytest.raises(ValueError, match="changed"):
        adapter.discover(
            b'{"results":[],"resultCount":202}', adapter.search(1).url, "a" * 64
        )


def test_identity_mismatch_and_duplicate_ballots(config, window):
    adapter = GeorgiaLegislature(config)
    adapter.start_urls(window)
    ref = adapter.discover(fixture("votes"), API + "Vote/list/1/1033", "a" * 64)[0][0]
    data = json.loads(fixture("vote"))
    data["legislation"][0]["voteId"] += 1
    with pytest.raises(ValueError, match="identity"):
        adapter.parse_bundle(canonical(data).encode(), ref, "b" * 64, window, {})
    data = json.loads(fixture("vote"))
    data["votes"].append(data["votes"][0])
    with pytest.raises(ValueError, match="ballot"):
        adapter.parse_bundle(canonical(data).encode(), ref, "b" * 64, window, {})
    data = json.loads(fixture("bill"))
    data["session"]["id"] += 1
    ref = Reference(
        url=API + "legislation/detail/69281", page_url=config.url, listing_hash="a" * 64
    )
    with pytest.raises(ValueError, match="session"):
        adapter.parse_bundle(canonical(data).encode(), ref, "b" * 64, window, {})


def test_seat_identity_shared_by_successive_members(config, window):
    adapter = GeorgiaLegislature(config)
    ref = adapter.discover(fixture("members"), API + "members/list/1033", "a" * 64)[0][
        0
    ]
    first = adapter.parse_bundle(fixture("member"), ref, "b" * 64, window, {})[1].event
    listing = json.loads(ref.description)
    listing["id"] = 9999
    data = json.loads(fixture("member"))
    data["id"] = 9999
    second = adapter.parse_bundle(
        canonical(data).encode(),
        ref.model_copy(update={"description": canonical(listing)}),
        "c" * 64,
        window,
        {},
    )[1].event
    assert first == second


def test_dates_tbd_and_dst(config):
    adapter = GeorgiaLegislature(config)
    window = Window(start=date(2026, 10, 27), end=date(2026, 10, 27))
    raw = json.loads(fixture("meetings"))
    raw[0]["start"] = "2026-10-27T00:00:00-04:00"
    ref = Reference(
        url=API + "meetings?startDate=2026-10-27",
        page_url=config.url,
        listing_hash="a" * 64,
    )
    event = adapter.parse_bundle(canonical(raw).encode(), ref, "b" * 64, window, {})[
        0
    ].event
    assert event.all_day and event.starts_at == window.start and event.warnings
    with pytest.raises(ValueError, match="Ambiguous"):
        adapter.local_time("2026-11-01T01:30:00")
    with pytest.raises(ValueError, match="nonexistent"):
        adapter.local_time("2026-03-08T02:30:00")
    assert (
        adapter.parse_bundle(
            fixture("meetings"),
            ref,
            "b" * 64,
            Window(start=date(2026, 10, 28), end=date(2026, 10, 28)),
            {},
        )
        == []
    )


def test_transport_token_scope_and_search_body(config, monkeypatch):
    calls = []
    captures = []

    def exchange(self, url, **kwargs):
        calls.append((url, kwargs))
        body = b'"anonymous-token"' if "authentication/token?" in url else b"[]"
        evidence = Evidence(
            url=url,
            sha256=digest(body),
            size=len(body),
            media_type="application/json",
            fetched_at=datetime.now(UTC),
        )
        return body, evidence, 200, httpx.Headers()

    monkeypatch.setattr(Fetcher, "exchange", exchange)
    fetcher = GeorgiaFetcher(config, lambda *args: captures.append(args))
    fetcher.get(API + "sessions")
    assert len(calls) == 2
    assert calls[1][1]["headers"]["Authorization"] == "Bearer anonymous-token"
    assert captures == []  # Authentication tokens must never become stored evidence.
    with pytest.raises(FetchError, match="outside"):
        fetcher.get("https://example.com/api/sessions")
    request = GeorgiaLegislature(config).search(0)
    fetcher.get_listing_page(request)
    assert captures[0][1].direction == "request"
    before = len(calls)
    for bad in [
        ListingRequest(url=API + "members/delete/1", content=request.content),
        request.model_copy(update={"content": b'{"sessionId":1}'}),
        request.model_copy(update={"url": API + "Legislation/Search/200/999"}),
    ]:
        with pytest.raises(FetchError):
            fetcher.get_listing_page(bad)
    assert len(calls) == before
    with pytest.raises(FetchError):
        fetcher.validate_request(API + "members/delete/1", "POST", b"{}")


def test_cli_source_and_collection(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "civpulse_updater.georgia_transport.GeorgiaFetcher", GeorgiaFixtures
    )
    runner = CliRunner()
    args = ["--state-dir", str(tmp_path)]
    result = runner.invoke(app, args + ["sources", "validate", "georgia-legislature"])
    assert result.exit_code == 0, result.output
    result = runner.invoke(
        app,
        args
        + [
            "collect",
            "georgia-legislature",
            "--from",
            "2025-01-01",
            "--to",
            "2026-12-31",
            "--full-session",
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["status"] == "complete"


def test_token_renewal_is_bounded(config, monkeypatch):
    calls = []
    statuses = iter([401, 200, 401, 401])

    def exchange(self, url, **kwargs):
        calls.append(url)
        auth = "authentication/token?" in url
        body = b'"anonymous-token"' if auth else b"[]"
        evidence = Evidence(
            url=url,
            sha256=digest(body),
            size=len(body),
            media_type="application/json",
            fetched_at=datetime.now(UTC),
        )
        return body, evidence, 200 if auth else next(statuses), httpx.Headers()

    monkeypatch.setattr(Fetcher, "exchange", exchange)
    fetcher = GeorgiaFetcher(config, lambda *args: None)
    assert fetcher.get(API + "sessions")[0] == b"[]"
    assert (
        len(calls) == 4
    )  # Initial token, rejected read, renewed token, successful read.
    with pytest.raises(FetchError, match="401"):
        fetcher.get(API + "sessions")
    assert len(calls) == 7  # Never renew repeatedly for the same rejected request.


def test_senate_member_and_roll_call(config, window):
    adapter = GeorgiaLegislature(config)
    adapter.start_urls(window)
    for listing, detail, path, expected in [
        ("senate-members", "senate-member", "members/list/1033", {"member", "seat"}),
        ("senate-votes", "senate-vote", "Vote/list/2/1033", {"vote"}),
    ]:
        ref = adapter.discover(fixture(listing), API + path, "a" * 64)[0][0]
        records = adapter.parse_bundle(fixture(detail), ref, "b" * 64, window, {})
        assert {r.event.record_type for r in records} == expected


def test_full_session_window_and_source_scope(tmp_path, config):
    from civpulse_updater.models import SourceConfig

    session = Window(start=date(2025, 1, 1), end=date(2026, 12, 31), full_session=True)
    adapter = GeorgiaLegislature(config)
    assert len(adapter.start_urls(session)) == 34
    assert Window.model_validate_json(session.model_dump_json()) == session
    with pytest.raises(ValueError):
        Window(start=session.start, end=session.end)
    with pytest.raises(ValueError):
        Window(start=date(2024, 1, 1), end=session.end, full_session=True)
    other = SourceConfig(
        id="other", url="https://example.com", allowed_hosts=("example.com",)
    )
    state, store = State(tmp_path), EvidenceStore(tmp_path / "evidence")
    with pytest.raises(ValueError, match="Georgia"):
        collect(state, store, other, session, GeorgiaFixtures())
    assert state.rows("SELECT * FROM runs") == []


def test_house_attendance_uses_listing_and_resumes_old_checkpoint(
    tmp_path, config, window, monkeypatch
):
    class AttendanceFixtures(GeorgiaFixtures):
        def get(self, url):
            if url.endswith("/Vote/detail/26533"):
                raise FetchError("Source returned HTTP 500")
            body, evidence = super().get(url)
            if "/Vote/list/1/" in url:
                body = canonical(
                    json.loads(body) + json.loads(fixture("house-attendance"))
                ).encode()
                evidence = evidence.model_copy(
                    update={"sha256": digest(body), "size": len(body)}
                )
            return body, evidence

    fresh_state = State(tmp_path / "fresh")
    fresh_store = EvidenceStore(tmp_path / "fresh" / "evidence")
    fresh = collect(fresh_state, fresh_store, config, window, AttendanceFixtures())
    assert fresh["status"] == "complete", fresh
    state, store = State(tmp_path), EvidenceStore(tmp_path / "evidence")
    # Reproduce the old collector's request to an endpoint the official UI skips.
    original = GeorgiaLegislature.uses_listing_content
    monkeypatch.setattr(GeorgiaLegislature, "uses_listing_content", lambda *args: False)
    first = collect(state, store, config, window, AttendanceFixtures())
    assert first["status"] == "partial"
    monkeypatch.setattr(GeorgiaLegislature, "uses_listing_content", original)
    fetcher = AttendanceFixtures()
    resumed = collect(state, store, config, window, fetcher, resume_run=first["run_id"])
    assert resumed["status"] == "complete", resumed
    assert not any("/Vote/detail/" in url for url in fetcher.calls)
    rows = [json.loads(r["payload"]) for r in state.rows("SELECT payload FROM records")]
    attendance = next(r for r in rows if r["uid"].endswith(":vote:26533"))
    assert attendance["data"]["listing"]["yea"] == 152
    assert attendance["data"]["detail"] is None
    assert attendance["data"]["detail_availability"] == "aggregate-only"
    assert attendance["warnings"]
    assert replay(state, store, resumed["run_id"])["status"] == "complete"


def test_official_unknown_vote_code_is_preserved(config, window):
    adapter = GeorgiaLegislature(config)
    adapter.start_urls(window)
    ref = adapter.discover(fixture("votes"), API + "Vote/list/1/1033", "a" * 64)[0][0]
    data = json.loads(fixture("vote"))
    # The official client defines Yea=0, Nay=1, Excused=2, NotVoting=3, Unknown=4.
    data["votes"][0]["memberVoted"] = 4
    records = adapter.parse_bundle(canonical(data).encode(), ref, "b" * 64, window, {})
    assert records[0].event.data["detail"]["votes"][0]["memberVoted"] == 4
    data["votes"][0]["memberVoted"] = 5
    with pytest.raises(ValueError, match="ballot"):
        adapter.parse_bundle(canonical(data).encode(), ref, "b" * 64, window, {})


@pytest.mark.parametrize(
    "title,cancelled",
    [
        ("CANCELED - JOINT APPROPRIATIONS", True),
        ("Cancelled- Rules Committee", True),
        ("Discussion of canceled contracts", False),
    ],
)
def test_official_cancellation_prefix_sets_status(config, title, cancelled):
    adapter = GeorgiaLegislature(config)
    window = Window(start=date(2025, 1, 1), end=date(2026, 12, 31), full_session=True)
    raw = json.loads(fixture("cancelled-meeting"))
    raw[0]["subject"] = title
    ref = Reference(
        url=API + "meetings?startDate=2025-01-01",
        page_url=config.url,
        listing_hash="a" * 64,
    )
    record = adapter.parse_bundle(canonical(raw).encode(), ref, "b" * 64, window, {})[
        0
    ].event
    assert record.status == ("cancelled" if cancelled else "unknown")
    assert (
        any("Cancellation explicitly" in warning for warning in record.warnings)
        == cancelled
    )


def test_cli_resume_latest_recovers_then_starts_fresh(tmp_path, monkeypatch):
    created = []

    def fetcher(config, capture):
        value = GeorgiaFixtures(fail="Vote/detail/" if not created else None)
        created.append(value)
        return value

    monkeypatch.setattr("civpulse_updater.georgia_transport.GeorgiaFetcher", fetcher)
    runner = CliRunner()
    args = [
        "--state-dir",
        str(tmp_path),
        "collect",
        "georgia-legislature",
        "--from",
        "2026-01-01",
        "--to",
        "2026-12-31",
    ]
    first = runner.invoke(app, args)
    assert first.exit_code == 1, first.output
    first_id = json.loads(first.stdout)["run_id"]
    resumed = runner.invoke(app, args + ["--resume-latest"])
    assert resumed.exit_code == 0, resumed.output
    resumed_id = json.loads(resumed.stdout)["run_id"]
    state = State(tmp_path)
    assert (
        state.rows("SELECT replay_of FROM runs WHERE id=:id", id=resumed_id)[0][
            "replay_of"
        ]
        == first_id
    )
    assert not any(
        "members/detail/" in url or "committees/details/" in url
        for url in created[1].calls
    )
    assert API + "sessions" not in created[1].calls
    fresh = runner.invoke(app, args + ["--resume-latest"])
    assert fresh.exit_code == 0, fresh.output
    fresh_id = json.loads(fresh.stdout)["run_id"]
    assert (
        state.rows("SELECT replay_of FROM runs WHERE id=:id", id=fresh_id)[0][
            "replay_of"
        ]
        is None
    )
    assert API + "sessions" in created[2].calls


def test_resume_latest_scopes_and_newer_failed_discovery(tmp_path, config, window):
    from civpulse_updater.pipeline import latest_resumable_run

    state, store = State(tmp_path), EvidenceStore(tmp_path / "evidence")
    first = collect(state, store, config, window, GeorgiaFixtures(fail="Vote/detail/"))
    assert latest_resumable_run(state, config, window) == first["run_id"]
    other = config.model_copy(update={"legislature_session_id": 1034})
    other_id = state.start_run(
        other.id, other.model_dump_json(), window.model_dump_json()
    )
    state.finish(other_id, [], [])
    assert latest_resumable_run(state, config, window) == first["run_id"]
    assert latest_resumable_run(state, other, window) is None
    assert (
        latest_resumable_run(
            state, config, Window(start=date(2025, 1, 1), end=date(2025, 12, 31))
        )
        is None
    )
    inputs = json.loads(
        state.rows("SELECT inputs FROM runs WHERE id=:id", id=first["run_id"])[0][
            "inputs"
        ]
    )
    interrupted = state.start_run(
        config.id, config.model_dump_json(), window.model_dump_json(), first["run_id"]
    )
    state.save_inputs(interrupted, inputs, [])
    assert latest_resumable_run(state, config, window) == interrupted
    recovered = collect(
        state, store, config, window, GeorgiaFixtures(), resume_run=interrupted
    )
    assert recovered["status"] == "complete"
    assert (
        state.rows("SELECT status FROM runs WHERE id=:id", id=interrupted)[0]["status"]
        == "interrupted"
    )
    # A newer incomplete discovery must start fresh, rather than revive any
    # older snapshot whose already-discovered identities may now be stale.
    failed = state.start_run(
        config.id, config.model_dump_json(), window.model_dump_json()
    )
    error = [{"stage": "discover", "code": "page_limit"}]
    state.save_inputs(failed, inputs, error)
    state.finish(failed, [], error)
    assert latest_resumable_run(state, config, window) is None


def test_resume_latest_keeps_integrity_checks_and_rejects_conflicting_flags(
    tmp_path, monkeypatch
):
    created = []

    def fetcher(config, capture):
        created.append(True)
        return GeorgiaFixtures(fail="Vote/detail/")

    monkeypatch.setattr("civpulse_updater.georgia_transport.GeorgiaFetcher", fetcher)
    runner = CliRunner()
    args = [
        "--state-dir",
        str(tmp_path),
        "collect",
        "georgia-legislature",
        "--from",
        "2026-01-01",
        "--to",
        "2026-12-31",
    ]
    first = runner.invoke(app, args)
    assert first.exit_code == 1
    first_id = json.loads(first.stdout)["run_id"]
    conflict = runner.invoke(app, args + ["--resume-latest", "--resume-run", first_id])
    assert conflict.exit_code == 2 and "Choose either" in conflict.output
    state = State(tmp_path)
    inputs = json.loads(
        state.rows("SELECT inputs FROM runs WHERE id=:id", id=first_id)[0]["inputs"]
    )
    EvidenceStore(tmp_path / "evidence").path(
        inputs[0]["reference"]["listing_hash"]
    ).write_bytes(b"tampered")
    corrupt = runner.invoke(app, args + ["--resume-latest"])
    assert corrupt.exit_code == 2, corrupt.output
    assert len(created) == 1  # Integrity failure must not cause fresh network access.
    assert len(state.rows("SELECT id FROM runs")) == 1


def test_optional_meeting_metadata_and_required_chamber(config, window):
    adapter = GeorgiaLegislature(config)
    raw = json.loads(fixture("meetings"))
    raw[0]["body"] = None
    raw[0].pop("location")
    ref = Reference(
        url=API + "meetings?startDate=2026-10-26",
        page_url=config.url,
        listing_hash="a" * 64,
    )
    record = adapter.parse_bundle(canonical(raw).encode(), ref, "b" * 64, window, {})[
        0
    ].event
    assert record.description is None and record.location is None
    raw[0]["body"] = []
    with pytest.raises(ValueError, match="text"):
        adapter.parse_bundle(canonical(raw).encode(), ref, "b" * 64, window, {})
    raw[0]["body"] = None
    raw[0]["chamber"] = "1"
    with pytest.raises(ValueError, match="chamber"):
        adapter.parse_bundle(canonical(raw).encode(), ref, "b" * 64, window, {})


def test_terminated_cli_process_resumes_without_repeating_downloads(tmp_path):
    import subprocess
    import sys
    import time

    repo = Path(__file__).resolve().parents[1]
    script = tmp_path / "worker.py"
    state_dir, marker = tmp_path / "state", tmp_path / "paused"
    script.write_text(f"""
import sys,time
from pathlib import Path
sys.path.insert(0, {str(repo / "tests")!r})
from test_georgia_legislature import GeorgiaFixtures
from civpulse_updater.cli import app
import civpulse_updater.georgia_transport as transport
mode,state_dir,marker,calls=sys.argv[1:]
class WorkerFetcher(GeorgiaFixtures):
    def get(self,url):
        with Path(calls).open("a") as handle:
            handle.write(url+"\\n")
        if mode=="pause" and "/Vote/detail/" in url:
            Path(marker).write_text("checkpoint saved")
            time.sleep(60)
        return super().get(url)
transport.GeorgiaFetcher=WorkerFetcher
args=["--state-dir",state_dir,"collect","georgia-legislature","--from","2026-01-01","--to","2026-12-31"]
if mode!="pause":args.append("--resume-latest")
app(args=args)
""")

    def command(mode):
        return [
            sys.executable,
            str(script),
            mode,
            str(state_dir),
            str(marker),
            str(tmp_path / f"{mode}-calls.txt"),
        ]

    worker = subprocess.Popen(
        command("pause"),
        cwd=repo,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 10
        while (
            not marker.exists()
            and worker.poll() is None
            and time.monotonic() < deadline
        ):
            time.sleep(0.02)
        assert marker.exists(), (
            f"Worker did not reach its checkpoint; exit={worker.poll()}"
        )
        competing = subprocess.run(
            command("competing"), cwd=repo, capture_output=True, text=True, timeout=15
        )
        assert competing.returncode == 3, competing.stdout + competing.stderr
        assert not (tmp_path / "competing-calls.txt").exists()
        worker.terminate()
        worker.communicate(timeout=5)
        recovered = subprocess.run(
            command("recover"), cwd=repo, capture_output=True, text=True, timeout=15
        )
        assert recovered.returncode == 0, recovered.stdout + recovered.stderr
        assert json.loads(recovered.stdout)["status"] == "complete"
        calls = (tmp_path / "recover-calls.txt").read_text().splitlines()
        assert API + "sessions" not in calls
        assert not any(
            "members/detail/" in url or "committees/details/" in url for url in calls
        )
        state = State(state_dir)
        rows = state.rows("SELECT id,status,replay_of FROM runs ORDER BY started_at")
        assert len(rows) == 2
        assert rows[0]["status"] == "interrupted"
        assert rows[1]["status"] == "complete" and rows[1]["replay_of"] == rows[0]["id"]
    finally:
        if worker.poll() is None:
            worker.kill()
        worker.communicate(timeout=5)


def test_complete_listing_records_use_frozen_discovery_bytes(tmp_path, config, window):
    class SingleReadFetcher(GeorgiaFixtures):
        def __init__(self):
            super().__init__()
            self.seen = set()

        def get(self, url):
            if url in self.seen:
                raise FetchError(
                    "Listing changed or became unavailable on its second read"
                )
            self.seen.add(url)
            return super().get(url)

    state, store = State(tmp_path), EvidenceStore(tmp_path / "evidence")
    fetcher = SingleReadFetcher()
    result = collect(state, store, config, window, fetcher)
    assert result["status"] == "complete", result
    inputs = json.loads(
        state.rows("SELECT inputs FROM runs WHERE id=:id", id=result["run_id"])[0][
            "inputs"
        ]
    )
    self_listed = [
        entry
        for entry in inputs
        if entry["reference"]["url"] == entry["reference"]["page_url"]
    ]
    assert self_listed
    assert all(
        entry["sha256"] == entry["reference"]["listing_hash"] for entry in self_listed
    )
    assert replay(state, store, result["run_id"])["status"] == "complete"
