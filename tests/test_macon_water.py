import copy
import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from civpulse_updater.adapters.macon_water import API, HOST, MaconWater
from civpulse_updater.evidence import EvidenceStore
from civpulse_updater.models import Evidence, SourceConfig, Window, digest
from civpulse_updater.pipeline import collect, replay
from civpulse_updater.state import State

FIXTURES = Path(__file__).parent / "fixtures" / "macon-water"
WINDOW = Window(start=date(2026, 10, 1), end=date(2026, 10, 31))


@pytest.fixture
def config():
    return SourceConfig(
        id="macon-water-authority",
        adapter="macon-water-authority",
        url=API,
        allowed_hosts=(HOST,),
    )


class Feed:
    def __init__(self):
        self.rows = json.loads((FIXTURES / "listing.json").read_bytes())
        self.docs = json.loads((FIXTURES / "documents.json").read_bytes())
        self.pdf = b"%PDF-1.7\nfixture"
        self.calls = []
        self.fail = False

    def get(self, url):
        self.calls.append(url)
        if "?from=" in url:
            data = self.rows
        elif url.endswith("/meetingDocuments"):
            data = self.docs
        else:
            if self.fail:
                raise ValueError("Unavailable attachment")
            data = self.pdf
        body = data if isinstance(data, bytes) else json.dumps(data).encode()
        return body, Evidence(
            url=url,
            sha256=digest(body),
            size=len(body),
            media_type="application/octet-stream",
            fetched_at=datetime.now(UTC),
        )


def test_collect_noop_replay_revision_and_failed_dependency(
    tmp_path, config, monkeypatch
):
    state, store, feed = State(tmp_path), EvidenceStore(tmp_path / "evidence"), Feed()
    first = collect(state, store, config, WINDOW, feed)
    assert first["status"] == "complete" and first["proposed_changes"] == 1
    event = json.loads(state.rows("SELECT payload FROM records")[0]["payload"])
    assert event["starts_at"] == "2026-10-08T16:00:00-04:00"
    assert event["uid"] == "mwa:272"
    assert {d["role"] for d in event["documents"]} == {"Agenda", "Attachment"}
    for doc in event["documents"]:
        assert len(store.read(doc["sha256"])) == doc["size"]
    assert collect(state, store, config, WINDOW, feed)["proposed_changes"] == 0
    monkeypatch.setattr(
        "civpulse_updater.transport.Fetcher.get",
        lambda *_: pytest.fail("Network during replay"),
    )
    assert replay(state, store, first["run_id"])["status"] == "complete"
    feed.pdf += b"revised"
    assert collect(state, store, config, WINDOW, feed)["proposed_changes"] == 1
    before = state.rows("SELECT payload FROM records")[0]["payload"]
    feed.fail = True
    failed = collect(state, store, config, WINDOW, feed)
    assert failed["status"] == "failed" and failed["proposed_changes"] == 0
    assert state.rows("SELECT payload FROM records")[0]["payload"] == before
    assert replay(state, store, failed["run_id"])["status"] == "failed"


def test_private_documents_excluded_before_link_discovery(tmp_path, config):
    feed = Feed()
    private = copy.deepcopy(feed.docs["Documents"][0])
    private.update(
        Id=999, IsPublic=False, Html='<a href="https://evil.example/private">secret</a>'
    )
    feed.docs["Documents"].append(private)
    state, store = State(tmp_path), EvidenceStore(tmp_path / "evidence")
    assert collect(state, store, config, WINDOW, feed)["status"] == "complete"
    event = json.loads(state.rows("SELECT payload FROM records")[0]["payload"])
    assert "secret" not in json.dumps(event)
    assert not any("evil.example" in u for u in feed.calls)


@pytest.mark.parametrize(
    "mutation", ["html", "identity", "visibility", "limit", "duplicate", "dst", "shape"]
)
def test_invalid_input_withholds_proposal(tmp_path, config, mutation):
    feed = Feed()
    if mutation == "html":
        feed.pdf = b"<html>login</html>"
    elif mutation == "identity":
        feed.docs["Documents"][0]["MeetingId"] = 42
    elif mutation == "visibility":
        del feed.docs["Documents"][0]["IsPublic"]
    elif mutation == "limit":
        config = config.model_copy(update={"max_resources": 1})
    elif mutation == "duplicate":
        feed.rows.append(copy.deepcopy(feed.rows[0]))
    elif mutation == "dst":
        feed.rows[0].update(
            MeetingDate="2026-11-01", MeetingDateTime="2026-11-01 01:30"
        )
    elif mutation == "shape":
        feed.rows = {"error": "unavailable"}
    state, store = State(tmp_path), EvidenceStore(tmp_path / "evidence")
    result = collect(state, store, config, WINDOW, feed)
    assert result["status"] == "failed" and result["proposed_changes"] == 0
    assert not state.rows("SELECT * FROM records")


def test_scope_window_unpublished_and_rescheduling(tmp_path, config):
    feed = Feed()
    original = feed.rows[0]
    feed.rows += [
        dict(original, Id=300, MeetingTypeId=28, Name="Macon Soils"),
        dict(original, Id=301, MeetingTypeId=17, Name="Macon Soils Inc. - Oct 08"),
        dict(original, Id=302, ExternalCalendar=True),
        dict(
            original,
            Id=303,
            MeetingDate="2026-09-01",
            MeetingDateTime="2026-09-01 16:00",
        ),
    ]
    original["Published"] = False
    state, store = State(tmp_path), EvidenceStore(tmp_path / "evidence")
    result = collect(state, store, config, WINDOW, feed)
    assert result["status"] == "complete" and result["records"] == 1
    assert len(feed.calls) == 1
    event = json.loads(state.rows("SELECT payload FROM records")[0]["payload"])
    assert event["warnings"] and not event.get("documents")
    original.update(MeetingDate="2026-10-15", MeetingDateTime="2026-10-15 16:00")
    assert collect(state, store, config, WINDOW, feed)["proposed_changes"] == 1
    assert len(state.rows("SELECT * FROM records")) == 1


def test_imported_mwa_committees_minutes_and_cancelled(tmp_path, config):
    feed = Feed()
    feed.rows[0].update(MeetingTypeId=17, Name="Macon Water Authority - CANCELLED")
    doc = copy.deepcopy(feed.docs["Documents"][0])
    doc.update(Id=9999, Type=2, Html="<html><body>Approved minutes</body></html>")
    feed.docs["Documents"].append(doc)
    state, store = State(tmp_path), EvidenceStore(tmp_path / "evidence")
    assert collect(state, store, config, WINDOW, feed)["status"] == "complete"
    event = json.loads(state.rows("SELECT payload FROM records")[0]["payload"])
    assert event["status"] == "cancelled" and event["warnings"]
    assert "Minutes" in {d["role"] for d in event["documents"]}
    feed.rows[0].update(MeetingTypeId=19, Name="Engineering Committee")
    adapter = MaconWater(config)
    refs, _ = adapter.discover(json.dumps(feed.rows).encode(), API, "a" * 64)
    assert len(refs) == 1


def test_wrong_tenant(config):
    with pytest.raises(ValueError):
        MaconWater(config.model_copy(update={"url": "https://example.org"}))


def test_single_day_request_spans_next_midnight(config):
    # The real endpoint returns [] for equal from/to values, omitting Oct 8.
    # Exercise the pipeline with the endpoint's observed empty-range behavior.
    class DateFeed(Feed):
        def get(self, url):
            from urllib.parse import parse_qs, urlsplit

            if "?from=" in url:
                query = parse_qs(urlsplit(url).query)
                if query["from"] == query["to"]:
                    self.rows = []
            return super().get(url)

    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        state, store, feed = State(root), EvidenceStore(root / "evidence"), DateFeed()
        window = Window(start=date(2026, 10, 8), end=date(2026, 10, 8))
        result = collect(state, store, config, window, feed)
        assert result["records"] == 1 and result["status"] == "complete"
        assert "to=2026-10-09" in feed.calls[0]


def test_official_html_attachment_collects_and_replays(tmp_path, config):
    # Public /document/ links can serve HTML minutes rather than a PDF.
    feed = Feed()
    feed.pdf = b"""<!DOCTYPE html><html><head>
    <meta name="keywords" content="iCompass-Open-Document">
    <meta name="generator" content="Aspose.Words for .NET 24.10.0">
    <title>Macon Water Authority - Minutes - Html</title>
    </head><body><h1>Approved minutes</h1></body></html>"""
    state, store = State(tmp_path), EvidenceStore(tmp_path / "evidence")
    result = collect(state, store, config, WINDOW, feed)
    assert result["status"] == "complete" and result["records"] == 1
    event = json.loads(state.rows("SELECT payload FROM records")[0]["payload"])
    attachments = [d for d in event["documents"] if d["role"] == "Attachment"]
    assert attachments and all(d["sha256"] == digest(feed.pdf) for d in attachments)
    assert store.read(attachments[0]["sha256"]) == feed.pdf
    assert replay(state, store, result["run_id"])["proposed_changes"] == 0
