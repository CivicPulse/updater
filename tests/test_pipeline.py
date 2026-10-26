import json

import pytest
from conftest import FixtureFetcher

from civpulse_updater.evidence import EvidenceStore
from civpulse_updater.pipeline import collect, replay
from civpulse_updater.state import State


def test_collect_noop_replay_and_review(tmp_path, config, window):
    state = State(tmp_path)
    store = EvidenceStore(tmp_path / "evidence")
    first = collect(state, store, config, window, FixtureFetcher())
    assert first["proposed_changes"] == 1
    second = collect(state, store, config, window, FixtureFetcher())
    assert second["proposed_changes"] == 0
    # Reopen state to prove persisted restart behavior.
    state.engine.dispose()
    state = State(tmp_path)
    repeated = replay(state, store, first["run_id"])
    assert repeated["proposed_changes"] == 0 and repeated["status"] == "complete"
    change = state.rows("SELECT * FROM changes")[0]
    with pytest.raises(ValueError, match="acknowledgement"):
        state.review(change["id"], "approved", "tester", "checked", False)
    assert (
        state.review(change["id"], "approved", "tester", "checked both sources", True)[
            "published"
        ]
        is False
    )
    with pytest.raises(ValueError):
        state.review(change["id"], "approved", "tester", "again", True)
    assert len(state.rows("SELECT * FROM decisions")) == 1


def test_failed_fetch_does_not_remove_records(tmp_path, config, window):
    state, store = State(tmp_path), EvidenceStore(tmp_path / "evidence")
    collect(state, store, config, window, FixtureFetcher())
    result = collect(state, store, config, window, FixtureFetcher(fail=True))
    assert result["status"] == "failed"
    assert len(state.rows("SELECT * FROM records")) == 1
    assert replay(state, store, result["run_id"])["status"] == "failed"


def test_evidence_tampering_is_detected(tmp_path, config, window):
    state, store = State(tmp_path), EvidenceStore(tmp_path / "evidence")
    result = collect(state, store, config, window, FixtureFetcher())
    inputs = json.loads(
        state.rows("SELECT inputs FROM runs WHERE id=:id", id=result["run_id"])[0][
            "inputs"
        ]
    )
    store.path(inputs[0]["sha256"]).write_bytes(b"tampered")
    assert replay(state, store, result["run_id"])["status"] == "failed"
    assert len(state.rows("SELECT * FROM changes")) == 1


def test_interrupted_run_stays_incomplete_on_replay(tmp_path, config, window):
    state, store = State(tmp_path), EvidenceStore(tmp_path / "evidence")
    old = state.start_run(config.id, config.model_dump_json(), window.model_dump_json())
    result = replay(state, store, old)
    assert result["status"] == "failed"
    assert (
        state.rows("SELECT status FROM runs WHERE id=:id", id=old)[0]["status"]
        == "interrupted"
    )


def test_traversal_rejected(tmp_path):
    store = EvidenceStore(tmp_path)
    with pytest.raises(ValueError):
        store.read("../../secret")


def test_changed_event_supersedes_approval_and_reversion_is_new_change(
    tmp_path, config, window
):
    from conftest import calendar, event

    from civpulse_updater.adapters.bibb_democrats import BibbDemocrats
    from civpulse_updater.models import Reference

    state = State(tmp_path)
    ref = Reference(url=config.url, page_url=config.url, listing_hash="a" * 64)
    adapter = BibbDemocrats(config)
    original = adapter.parse(calendar(event()), ref, "b" * 64, window)
    moved = adapter.parse(
        calendar(event(start="20261026T140000Z")), ref, "c" * 64, window
    )

    def save(envelopes):
        run = state.start_run(
            config.id, config.model_dump_json(), window.model_dump_json()
        )
        return state.finish(run, envelopes, [])

    assert save(original)["proposed_changes"] == 1
    first = state.rows("SELECT * FROM changes")[0]
    state.review(first["id"], "approved", "tester", "checked", False)
    assert save(moved)["proposed_changes"] == 1
    assert (
        state.rows("SELECT status FROM changes WHERE id=:id", id=first["id"])[0][
            "status"
        ]
        == "superseded"
    )
    assert save(original)["proposed_changes"] == 1
    assert len(state.rows("SELECT * FROM records")) == 1
    assert len(state.rows("SELECT * FROM changes")) == 3


def test_offline_replay_never_fetches(tmp_path, config, window, monkeypatch):
    state, store = State(tmp_path), EvidenceStore(tmp_path / "evidence")
    original = collect(state, store, config, window, FixtureFetcher())

    def forbidden(*args):
        raise AssertionError("Replay must not access network")

    monkeypatch.setattr("civpulse_updater.transport.Fetcher.get", forbidden)
    assert replay(state, store, original["run_id"])["status"] == "complete"


def test_transaction_rollback(tmp_path, config, window):
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    state, store = State(tmp_path), EvidenceStore(tmp_path / "evidence")
    with state.engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TRIGGER reject_record BEFORE INSERT ON records BEGIN SELECT RAISE(ABORT, 'injected failure'); END"
            )
        )
    with pytest.raises(IntegrityError):
        collect(state, store, config, window, FixtureFetcher())
    assert state.rows("SELECT * FROM changes") == []
    assert state.rows("SELECT * FROM records") == []


def test_old_replay_cannot_replace_newer_observation(tmp_path, config, window):
    from civpulse_updater.models import digest

    state, store = State(tmp_path), EvidenceStore(tmp_path / "evidence")
    first = collect(state, store, config, window, FixtureFetcher())

    class ChangedFetcher(FixtureFetcher):
        def get(self, url):
            body, evidence = super().get(url)
            if "format=ical" in url:
                body = body.replace(
                    b"DTSTART:20261121T160000Z", b"DTSTART:20261121T170000Z"
                )
                evidence = evidence.model_copy(
                    update={"sha256": digest(body), "size": len(body)}
                )
            return body, evidence

    collect(state, store, config, window, ChangedFetcher())
    before = state.rows("SELECT payload FROM records")[0]["payload"]
    replayed = replay(state, store, first["run_id"])
    assert replayed["proposed_changes"] == 0
    assert state.rows("SELECT payload FROM records")[0]["payload"] == before
