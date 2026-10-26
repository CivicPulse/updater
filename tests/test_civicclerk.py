import json
from datetime import UTC, date, datetime

import pytest

from civpulse_updater.adapters.civicclerk import API, CivicClerk
from civpulse_updater.evidence import EvidenceStore
from civpulse_updater.models import Evidence, SourceConfig, Window, digest
from civpulse_updater.pipeline import collect, replay
from civpulse_updater.state import State

BLOB = "https://civicclerk.blob.core.windows.net/stream/MACONBIBBCOGA/test.pdf"


@pytest.fixture
def cc_config():
    return SourceConfig(
        id="commission",
        adapter="civicclerk",
        url=API + "/Events",
        allowed_hosts=(
            "maconbibbcoga.api.civicclerk.com",
            "civicclerk.blob.core.windows.net",
        ),
    )


@pytest.fixture
def cc_window():
    return Window(start=date(2026, 10, 1), end=date(2026, 10, 31))


class Feed:
    def __init__(self):
        self.event = dict(
            id=1,
            eventName="Commission Meeting",
            eventDate="2026-10-06T18:00:00Z",
            categoryId=26,
            isPublished="Published",
            isDeleted=False,
            agendaId=7,
            publishedFiles=[
                dict(fileId=10, name="Agenda", type="Agenda"),
                dict(fileId=11, name="Minutes", type="Minutes"),
            ],
        )
        self.agenda = dict(
            id=7,
            items=[
                dict(
                    childItems=[
                        dict(
                            attachmentsList=[
                                dict(
                                    id=10,
                                    fileName="Exhibit",
                                    isLink=False,
                                    pdfVersionFullPath=BLOB + "?sig=first",
                                )
                            ]
                        )
                    ]
                )
            ],
        )
        self.fail = False
        self.pdf = b"%PDF-1.7\nfixture"
        self.calls = []

    def get(self, url):
        self.calls.append(url)
        if "/Events?" in url:
            data = {"value": [self.event, dict(self.event, id=2, categoryId=35)]}
        elif "/Events/1" in url:
            data = self.event
        elif "/Meetings/7" in url:
            data = self.agenda
        else:
            if self.fail:
                raise ValueError("download failed")
            data = self.pdf
        body = data if isinstance(data, bytes) else json.dumps(data).encode()
        return body, Evidence(
            sha256=digest(body),
            url=url,
            media_type="application/pdf"
            if isinstance(data, bytes)
            else "application/json",
            size=len(body),
            fetched_at=datetime.now(UTC),
        )


def test_complete_noop_rotated_signature_and_offline_replay(
    tmp_path, cc_config, cc_window, monkeypatch
):
    state, store = State(tmp_path), EvidenceStore(tmp_path / "evidence")
    feed = Feed()
    first = collect(state, store, cc_config, cc_window, feed)
    assert first["status"] == "complete" and first["proposed_changes"] == 1
    event = json.loads(state.rows("SELECT payload FROM records")[0]["payload"])
    assert event["starts_at"] == "2026-10-06T18:00:00-04:00"
    assert {d["role"] for d in event["documents"]} == {
        "Agenda",
        "Minutes",
        "Attachment",
    }
    assert len({d["id"] for d in event["documents"]}) == 3
    assert "sig=" not in json.dumps(event)
    feed.agenda["items"][0]["childItems"][0]["attachmentsList"][0][
        "pdfVersionFullPath"
    ] = BLOB + "?sig=rotated"
    assert collect(state, store, cc_config, cc_window, feed)["proposed_changes"] == 0
    monkeypatch.setattr(
        "civpulse_updater.transport.Fetcher.get",
        lambda *_: pytest.fail("network in replay"),
    )
    assert replay(state, store, first["run_id"])["status"] == "complete"
    feed.pdf = b"%PDF-1.7\nrevised"
    assert collect(state, store, cc_config, cc_window, feed)["proposed_changes"] == 1
    before = state.rows("SELECT payload FROM records")[0]["payload"]
    feed.fail = True
    failed = collect(state, store, cc_config, cc_window, feed)
    assert failed["status"] == "failed"
    assert state.rows("SELECT payload FROM records")[0]["payload"] == before
    assert replay(state, store, failed["run_id"])["status"] == "failed"


@pytest.mark.parametrize(
    "mutation", ["host", "limit", "html", "identity", "malformed", "private"]
)
def test_failure_quarantines_meeting(tmp_path, cc_config, cc_window, mutation):
    feed = Feed()
    if mutation == "host":
        feed.agenda["items"][0]["childItems"][0]["attachmentsList"][0][
            "pdfVersionFullPath"
        ] = "https://evil.example/file.pdf"
    elif mutation == "limit":
        cc_config = cc_config.model_copy(update={"max_resources": 1})
    elif mutation == "html":
        feed.pdf = b"<html>login</html>"
    elif mutation == "identity":
        feed.agenda["id"] = 99
    elif mutation == "malformed":
        feed.agenda["items"] = "bad"
    elif mutation == "private":
        feed.event["isPublished"] = "Draft"
    state, store = State(tmp_path), EvidenceStore(tmp_path / "evidence")
    result = collect(state, store, cc_config, cc_window, feed)
    assert result["proposed_changes"] == 0
    assert not state.rows("SELECT * FROM records")
    assert not any("evil.example" in u for u in feed.calls)


def test_schedule_without_documents_and_cancelled(tmp_path, cc_config, cc_window):
    feed = Feed()
    feed.event.update(agendaId=0, publishedFiles=[], eventName="Commission - CANCELLED")
    state, store = State(tmp_path), EvidenceStore(tmp_path / "evidence")
    assert collect(state, store, cc_config, cc_window, feed)["status"] == "complete"
    event = json.loads(state.rows("SELECT payload FROM records")[0]["payload"])
    assert event["status"] == "cancelled" and event.get("documents", []) == []


def test_pagination_and_empty_schema(cc_config, cc_window):
    adapter = CivicClerk(cc_config)
    url = adapter.start_urls(cc_window)[0]
    assert "categoryId" in url and "2026-10-31" in url
    assert adapter.discover(b'{"value":[]}', url, "a" * 64) == ([], [])
    next_url = API + "/Events?$skip=15"
    assert adapter.discover(
        json.dumps({"value": [], "@odata.nextLink": next_url}).encode(), url, "a" * 64
    )[1] == [next_url]
    with pytest.raises(ValueError):
        adapter.discover(b'{"value":[],"@odata.nextLink":2}', url, "a" * 64)
    with pytest.raises(KeyError):
        adapter.discover(b"{}", url, "a" * 64)


@pytest.mark.parametrize(
    "value", ["2026-11-01T01:30:00Z", "2026-03-08T02:30:00Z", "invalid"]
)
def test_ambiguous_dates_rejected(cc_config, value):
    with pytest.raises(ValueError):
        CivicClerk(cc_config).start({"eventDate": value})


def test_tampered_attachment_fails_replay(tmp_path, cc_config, cc_window):
    state, store = State(tmp_path), EvidenceStore(tmp_path / "evidence")
    feed = Feed()
    first = collect(state, store, cc_config, cc_window, feed)
    store.path(digest(feed.pdf)).write_bytes(b"tampered")
    assert replay(state, store, first["run_id"])["status"] == "failed"


def test_official_event_fixture(cc_config, cc_window):
    from pathlib import Path

    from civpulse_updater.models import Reference

    body = Path("tests/fixtures/civicclerk-event.json").read_bytes()
    raw = json.loads(body)
    adapter = CivicClerk(cc_config)
    agenda_url = f"{API}/Meetings/{raw['agendaId']}"
    resources = {agenda_url: json.dumps({"id": raw["agendaId"], "items": []}).encode()}
    for url in adapter.resources(body, resources):
        resources[url] = b"%PDF-1.7\nfixture substitute"
    ref = Reference(
        url=f"{API}/Events/{raw['id']}", page_url=API + "/Events", listing_hash="a" * 64
    )
    record = adapter.parse_bundle(body, ref, digest(body), cc_window, resources)[
        0
    ].event
    assert record.title == "Board of Commissioners Meeting"
    assert record.starts_at.hour == 18
    assert record.documents[0].role == "Agenda Packet"


def test_page_limit_marks_partial(tmp_path, cc_config, cc_window):
    class Paged(Feed):
        def get(self, url):
            body, meta = super().get(url)
            if "/Events?" in url:
                raw = json.loads(body)
                raw["@odata.nextLink"] = API + "/Events?$skip=15"
                body = json.dumps(raw).encode()
                meta = meta.model_copy(
                    update={"sha256": digest(body), "size": len(body)}
                )
            return body, meta

    config = cc_config.model_copy(update={"max_pages": 1})
    result = collect(
        State(tmp_path),
        EvidenceStore(tmp_path / "evidence"),
        config,
        cc_window,
        Paged(),
    )
    assert result["status"] == "partial" and result["records"] == 1
    assert any(e["code"] == "page_limit" for e in result["errors"])
