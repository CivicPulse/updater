import copy
import json
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from civpulse_updater.adapters.simbli import LISTING, Simbli
from civpulse_updater.evidence import EvidenceStore
from civpulse_updater.models import Evidence, SourceConfig, Window, digest
from civpulse_updater.pipeline import collect, replay
from civpulse_updater.state import State

FIXTURES = Path(__file__).parent / "fixtures" / "simbli"


def fixture(name):
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def source():
    return SourceConfig(
        id="bibb-school-board",
        adapter="simbli",
        url=LISTING,
        allowed_hosts=("simbli.eboardsolutions.com",),
        max_resources=100,
    )


@pytest.fixture
def window():
    return Window(start=date(2026, 9, 9), end=date(2026, 9, 9))


class Feed:
    def __init__(self):
        rows = fixture("listing.json")
        self.rows = [r for r in rows if r["Master_MeetingID"] in (139673, 138067)]
        self.rows.append(dict(rows[-1], ML_TypeTitle="School Council"))
        self.meeting = fixture("meeting.json")
        self.tree = fixture("tree.json")
        self.minutes = fixture("minutes.json")
        self.calls = []
        self.fail = False
        self.token = "public-session-one"
        self.pdf = b"%PDF-1.7\npublic exhibit"
        self.attachment = dict(
            SchoolID="4013",
            IsDeleted=False,
            VisibilityMode=0,
            AttachmentID=123,
            EnitityID=138067,
            FileName="Budget.pdf",
            Title="Budget",
            EncrId="file-token",
        )
        self.detail_private = False

    def get(self, url):
        self.calls.append(url)
        path = urlsplit(url).path
        query = parse_qs(urlsplit(url).query)
        if url == LISTING:
            body = (
                "ContentPlaceHolder1_MeetingGrid_newGrid.dataBuffer="
                + json.dumps(self.rows)
                + ";"
            ).encode()
        elif path.endswith("ViewMeeting.aspx"):
            body = (
                f"<title>View Board Meeting | Bibb County School District</title><script>var sToken = '{self.token}'; var enDID = 'district-public'; var enMeetingID = 'meeting-public';</script>"
            ).encode()
        elif "/GetMeeting/" in path:
            body = self.meeting
        elif "/GetItemsTreeDTO/" in path:
            body = self.tree
        elif "/GetSupportingDocuments/" in path:
            body = {"Attachment": []}
        elif "/GetMeetingItemDetailsModel/" in path:
            item = next(
                x for x in self.tree["Items"] if x["ID"] == query["enitemid"][0]
            )
            body = {
                "ItemDetails": {
                    "EncrID": item["ID"],
                    "VisibilityMode": 1 if self.detail_private else 0,
                    "Deleted": False,
                },
                "ItemContents": [
                    {
                        "FieldVisibility": 0,
                        "Content": "Public agenda content",
                        "Attachments": [self.attachment]
                        if item["HasAttachment"]
                        else [],
                    }
                ],
            }
        elif "/GetMeetingMinutes/" in path:
            body = self.minutes
        elif path.endswith("Attachment.aspx") or path.endswith("MinuteDocument.aspx"):
            if self.fail:
                raise ValueError("document unavailable")
            body = self.pdf
        else:
            raise AssertionError("Unrecognized endpoint")
        if not isinstance(body, bytes):
            body = json.dumps(body).encode()
        return body, Evidence(
            sha256=digest(body),
            url=url,
            media_type="application/octet-stream",
            size=len(body),
            fetched_at=datetime.now(UTC),
        )


def test_collect_replay_rotation_revision_and_failure(
    tmp_path, source, window, monkeypatch
):
    state, store = State(tmp_path), EvidenceStore(tmp_path / "evidence")
    feed = Feed()
    result = collect(state, store, source, window, feed)
    assert result["status"] == "complete" and result["records"] == 1
    record = json.loads(state.rows("SELECT payload FROM records")[0]["payload"])
    assert record["starts_at"] == "2026-09-09T11:00:00-04:00"
    assert {"Agenda", "Agenda Item", "Minutes", "Attachment"} == {
        d["role"] for d in record["documents"]
    }
    assert "sct=" not in json.dumps(record) and feed.token not in json.dumps(record)
    feed.token = "rotated-session"
    assert collect(state, store, source, window, feed)["proposed_changes"] == 0
    monkeypatch.setattr(
        "civpulse_updater.transport.Fetcher.get",
        lambda *_: pytest.fail("network in replay"),
    )
    assert replay(state, store, result["run_id"])["proposed_changes"] == 0
    feed.pdf += b"\nrevision"
    assert collect(state, store, source, window, feed)["proposed_changes"] == 1
    before = state.rows("SELECT payload FROM records")[0]["payload"]
    feed.fail = True
    failed = collect(state, store, source, window, feed)
    assert failed["status"] == "failed"
    assert state.rows("SELECT payload FROM records")[0]["payload"] == before
    assert replay(state, store, failed["run_id"])["status"] == "failed"


def test_delayed_minutes_never_requests_unpublished_content(tmp_path, source, window):
    state, store = State(tmp_path), EvidenceStore(tmp_path / "evidence")
    feed = Feed()
    row = next(r for r in feed.rows if r["Master_MeetingID"] == 138067)
    row["MinutesStatus"] = "AUTHORISED"
    assert collect(state, store, source, window, feed)["status"] == "complete"
    assert not any("/GetMeetingMinutes/" in u for u in feed.calls)
    record = json.loads(state.rows("SELECT payload FROM records")[0]["payload"])
    assert record["warnings"] and not any(
        d["role"] == "Minutes" for d in record["documents"]
    )
    row["MinutesStatus"] = "PUBLISHED"
    assert collect(state, store, source, window, feed)["proposed_changes"] == 1
    assert any("/GetMeetingMinutes/" in u for u in feed.calls)


def test_schedule_before_agenda_publication(tmp_path, source, window):
    feed = Feed()
    feed.meeting["IsPublished"] = False
    next(r for r in feed.rows if r["Master_MeetingID"] == 138067)["MinutesStatus"] = (
        "UNPUBLISHED"
    )
    state, store = State(tmp_path), EvidenceStore(tmp_path / "evidence")
    result = collect(state, store, source, window, feed)
    assert result["status"] == "complete"
    record = json.loads(state.rows("SELECT payload FROM records")[0]["payload"])
    assert not record.get("documents") and "schedule only" in record["warnings"][0]
    assert not any("/GetItemsTreeDTO/" in u for u in feed.calls)


@pytest.mark.parametrize(
    "failure",
    [
        "challenge",
        "private",
        "wrong_district",
        "wrong_meeting",
        "html_file",
        "limit",
        "malformed",
        "unknown_status",
        "missing_item",
        "wrong_item",
    ],
)
def test_fail_closed(tmp_path, source, window, failure):
    feed = Feed()
    if failure == "challenge":
        original = feed.get

        def challenge(url):
            body, meta = original(url)
            if url == LISTING:
                body = b"<html>Request unsuccessful. Incapsula incident ID</html>"
                meta = meta.model_copy(
                    update={"sha256": digest(body), "size": len(body)}
                )
            return body, meta

        feed.get = challenge
    elif failure == "private":
        feed.detail_private = True
    elif failure == "wrong_district":
        feed.attachment["SchoolID"] = "9999"
    elif failure == "wrong_meeting":
        feed.meeting["ID"] = 999
    elif failure == "html_file":
        feed.pdf = b"<html>login</html>"
    elif failure == "limit":
        source = source.model_copy(update={"max_resources": 1})
    elif failure == "malformed":
        feed.minutes = {"MeetingMinutes": {}, "LstItemMinutes": "invalid"}
    elif failure == "unknown_status":
        feed.rows[1]["MinutesStatus"] = "NEW_UNKNOWN_STATE"
    elif failure == "missing_item":
        feed.tree["Items"][0].pop("AgendaID")
    elif failure == "wrong_item":
        feed.attachment["EnitityID"] = 999
    state, store = State(tmp_path), EvidenceStore(tmp_path / "evidence")
    assert collect(state, store, source, window, feed)["status"] == "failed"
    assert not state.rows("SELECT * FROM records")


def test_full_official_listing_scope_and_coverage(source, window):
    adapter = Simbli(source)
    adapter.start_urls(window)
    body = (
        "ContentPlaceHolder1_MeetingGrid_newGrid.dataBuffer="
        + json.dumps(fixture("listing.json"))
        + ";"
    ).encode()
    refs, _ = adapter.discover(body, LISTING, "a" * 64)
    assert len(refs) == 3 and sum(r.minutes_published for r in refs) == 1
    adapter.start_urls(Window(start=date(2026, 3, 1), end=date(2026, 9, 9)))
    with pytest.raises(ValueError, match="coverage"):
        adapter.discover(body, LISTING, "a" * 64)


def test_corrupt_document_blocks_replay(tmp_path, source, window):
    state, store = State(tmp_path), EvidenceStore(tmp_path / "evidence")
    feed = Feed()
    result = collect(state, store, source, window, feed)
    store.path(digest(feed.pdf)).write_bytes(b"changed")
    assert replay(state, store, result["run_id"])["status"] == "failed"


def test_restricted_agenda_subtree_keeps_public_meeting(tmp_path, source, window):
    feed = Feed()
    hidden = copy.deepcopy(feed.tree["Items"][0])
    hidden.update(
        ID="private-root",
        AgendaID=999991,
        Visibility=3,
        Title="PRIVATE CONTENT MUST NOT BE PROPOSED",
    )
    child = copy.deepcopy(feed.tree["Items"][0])
    child.update(ID="private-child", AgendaID=999992)
    hidden["Children"] = [child]
    feed.tree["Items"].append(hidden)
    state, store = State(tmp_path), EvidenceStore(tmp_path / "evidence")
    result = collect(state, store, source, window, feed)
    assert result["status"] == "complete" and result["records"] == 1
    assert not any("private-root" in u or "private-child" in u for u in feed.calls)
    event = json.loads(state.rows("SELECT payload FROM records")[0]["payload"])
    agenda = next(d for d in event["documents"] if d["role"] == "Agenda")
    projected = store.read(agenda["sha256"])
    assert b"PRIVATE CONTENT" not in projected and b"private-child" not in projected
    assert len(json.loads(projected)["Items"]) == len(feed.tree["Items"]) - 1
    assert "public projection" in agenda["title"]
    assert any("Excluded 1" in w for w in event["warnings"])
    assert b"PRIVATE CONTENT" in store.read(digest(json.dumps(feed.tree).encode()))
    assert replay(state, store, result["run_id"])["proposed_changes"] == 0


def test_restricted_minutes_subtree_never_downloads_attachments(
    tmp_path, source, window
):
    feed = Feed()
    hidden = copy.deepcopy(feed.minutes["LstItemMinutes"][0])
    hidden.update(
        Visibility=3, Attachments=[dict(feed.attachment, AttachmentID=999991)]
    )
    feed.minutes["LstItemMinutes"].append(hidden)
    state, store = State(tmp_path), EvidenceStore(tmp_path / "evidence")
    result = collect(state, store, source, window, feed)
    assert result["status"] == "complete"
    assert not any("999991" in url for url in feed.calls)
    event = json.loads(state.rows("SELECT payload FROM records")[0]["payload"])
    minutes = next(d for d in event["documents"] if d["role"] == "Minutes")
    assert (
        len(json.loads(store.read(minutes["sha256"]))["LstItemMinutes"])
        == len(feed.minutes["LstItemMinutes"]) - 1
    )


def test_unknown_outline_visibility_still_fails_closed(tmp_path, source, window):
    feed = Feed()
    feed.tree["Items"][0]["Visibility"] = 99
    state = State(tmp_path)
    assert (
        collect(state, EvidenceStore(tmp_path / "evidence"), source, window, feed)[
            "status"
        ]
        == "failed"
    )
    assert not state.rows("SELECT * FROM records")


def test_archive_pagination_collects_early_year_and_replays(tmp_path, source):
    from civpulse_updater.adapters.simbli import ARCHIVE
    from civpulse_updater.simbli_browser import validate_archive_request

    class ArchiveFeed(Feed):
        def __init__(self):
            super().__init__()
            original = copy.deepcopy(self.rows[1])
            self.historical = dict(
                original, DateTime="01/15/2026 - 11:00 AM", MeetingsCount=51
            )
            self.rows = [
                dict(
                    original,
                    Master_MeetingID=200000 + i,
                    DateTime="03/05/2026 - 09:00 AM",
                    ML_TypeTitle="School Council",
                    MeetingsCount=51,
                )
                for i in range(50)
            ]
            self.meeting["TitleDateTime"] = self.historical["DateTime"]

        def get(self, url):
            body, meta = super().get(url)
            if url == LISTING:
                body += (
                    "var meetingCustGrd = JSON.parse('"
                    + json.dumps(fixture("archive-context.json"))
                    + "'); var constr = 'fixture-public-connection';"
                ).encode()
                meta = meta.model_copy(
                    update={"sha256": digest(body), "size": len(body)}
                )
            return body, meta

        def get_listing_page(self, request):
            assert request.url == ARCHIVE
            validate_archive_request(request.content)
            assert json.loads(request.content)["RecordStart"] == 51
            self.calls.append(ARCHIVE)
            body = json.dumps([self.historical]).encode()
            return body, Evidence(
                method="POST",
                url=ARCHIVE,
                sha256=digest(body),
                size=len(body),
                media_type="application/json",
                fetched_at=datetime.now(UTC),
            )

    feed = ArchiveFeed()
    window = Window(start=date(2026, 1, 1), end=date(2026, 12, 31))
    state, store = State(tmp_path), EvidenceStore(tmp_path / "evidence")
    result = collect(state, store, source, window, feed)
    assert result["status"] == "complete" and result["records"] == 1
    event = json.loads(state.rows("SELECT payload FROM records")[0]["payload"])
    assert event["starts_at"].startswith("2026-01-15")
    assert ARCHIVE in feed.calls
    assert replay(state, store, result["run_id"])["proposed_changes"] == 0


def test_archive_rejects_duplicate_rows(source):
    from civpulse_updater.adapters.simbli import ARCHIVE

    adapter = Simbli(source)
    adapter.start_urls(Window(start=date(2026, 1, 1), end=date(2026, 12, 31)))
    rows = fixture("listing.json")
    body = (
        "ContentPlaceHolder1_MeetingGrid_newGrid.dataBuffer="
        + json.dumps(rows)
        + ";var meetingCustGrd = JSON.parse('"
        + json.dumps(fixture("archive-context.json"))
        + "');var constr = 'fixture-connection';"
    ).encode()
    _, following = adapter.discover(body, LISTING, digest(body))
    assert following
    with pytest.raises(ValueError, match="Duplicate"):
        adapter.discover(json.dumps([rows[-1]]).encode(), ARCHIVE, "a" * 64)


def test_resume_uses_verified_checkpoints_without_repeating_downloads(
    tmp_path, source, window
):
    feed = Feed()
    feed.fail = True
    state, store = State(tmp_path), EvidenceStore(tmp_path / "evidence")
    first = collect(state, store, source, window, feed)
    assert first["status"] == "failed"
    previous = state.rows("SELECT * FROM runs WHERE id=:id", id=first["run_id"])[0]
    checkpoint = json.loads(previous["inputs"])[0]
    assert checkpoint["complete"] is False and checkpoint["resources"]
    used = set(checkpoint["resources"]) | {LISTING, checkpoint["reference"]["url"]}
    second = Feed()
    result = collect(state, store, source, window, second, resume_run=first["run_id"])
    assert result["status"] == "complete" and result["records"] == 1
    assert not used.intersection(second.calls)
    assert all("Attachment.aspx" in u for u in second.calls)
    record = state.rows("SELECT observed_at FROM records")[0]
    assert record["observed_at"] == previous["started_at"]
    assert replay(state, store, result["run_id"])["proposed_changes"] == 0


def test_resume_rejects_corruption_before_network(tmp_path, source, window):
    feed = Feed()
    feed.fail = True
    state, store = State(tmp_path), EvidenceStore(tmp_path / "evidence")
    first = collect(state, store, source, window, feed)
    checkpoint = json.loads(
        state.rows("SELECT inputs FROM runs WHERE id=:id", id=first["run_id"])[0][
            "inputs"
        ]
    )[0]
    store.path(next(iter(checkpoint["resources"].values()))).write_bytes(b"corrupt")
    second = Feed()
    with pytest.raises(ValueError, match="checksum"):
        collect(state, store, source, window, second, resume_run=first["run_id"])
    assert not second.calls


def test_invalid_download_is_evidence_but_not_a_resume_checkpoint(
    tmp_path, source, window
):
    feed = Feed()
    feed.pdf = b"<html>temporary unavailable page</html>"
    state, store = State(tmp_path), EvidenceStore(tmp_path / "evidence")
    first = collect(state, store, source, window, feed)
    assert first["status"] == "failed"
    entry = json.loads(
        state.rows("SELECT inputs FROM runs WHERE id=:id", id=first["run_id"])[0][
            "inputs"
        ]
    )[0]
    assert digest(feed.pdf) not in entry["resources"].values()
    assert store.read(digest(feed.pdf)) == feed.pdf
    assert (
        collect(state, store, source, window, Feed(), resume_run=first["run_id"])[
            "status"
        ]
        == "complete"
    )


def test_resume_cannot_erase_incomplete_discovery(tmp_path, source, window):
    feed = Feed()
    source = source.model_copy(update={"max_events": 1})
    # Two distinct public meeting references exceed the event limit.
    feed.rows.insert(2, dict(feed.rows[1], Master_MeetingID=138068))
    state, store = State(tmp_path), EvidenceStore(tmp_path / "evidence")
    first = collect(state, store, source, window, feed)
    assert any(e["code"] == "event_limit" for e in first["errors"])
    with pytest.raises(ValueError, match="discovery"):
        collect(state, store, source, window, Feed(), resume_run=first["run_id"])


def test_resuming_old_snapshot_cannot_supersede_newer_observation(
    tmp_path, source, window
):
    state, store = State(tmp_path), EvidenceStore(tmp_path / "evidence")
    old = Feed()
    old.fail = True
    first = collect(state, store, source, window, old)
    latest = Feed()
    latest.pdf += b" newer revision"
    assert collect(state, store, source, window, latest)["status"] == "complete"
    before = state.rows("SELECT * FROM records")[0]
    result = collect(state, store, source, window, Feed(), resume_run=first["run_id"])
    assert result["status"] == "complete" and result["skipped_stale"] == 1
    assert result["proposed_changes"] == 0
    assert state.rows("SELECT * FROM records")[0] == before


def test_minutes_attachment_download(tmp_path, source, window):
    feed = Feed()
    feed.minutes["MeetingMinutes"]["Attachments"] = [copy.deepcopy(feed.attachment)]
    state, store = State(tmp_path), EvidenceStore(tmp_path / "evidence")
    assert collect(state, store, source, window, feed)["status"] == "complete"
    assert any("ViewMeetingMinuteDocument.aspx?" in u for u in feed.calls)
    record = json.loads(state.rows("SELECT payload FROM records")[0]["payload"])
    assert any(d["role"] == "Minutes Attachment" for d in record["documents"])


def test_office_attachment_is_preserved_without_execution(tmp_path, source, window):
    feed = Feed()
    feed.attachment["FileName"] = "Presentation.pptx"
    feed.pdf = b"PK\x03\x04synthetic inert office archive"
    state, store = State(tmp_path), EvidenceStore(tmp_path / "evidence")
    assert collect(state, store, source, window, feed)["status"] == "complete"
    assert store.read(digest(feed.pdf)) == feed.pdf


@pytest.mark.parametrize("extension", ["doc", "xls", "ppt"])
def test_legacy_office_signature_is_validated(tmp_path, source, window, extension):
    feed = Feed()
    feed.attachment["FileName"] = "document." + extension
    feed.pdf = bytes.fromhex("d0cf11e0a1b11ae1") + b"inert legacy fixture"
    state, store = State(tmp_path), EvidenceStore(tmp_path / "evidence")
    assert collect(state, store, source, window, feed)["status"] == "complete"
    feed.pdf = b"not an Office document"
    assert collect(state, store, source, window, feed)["status"] == "failed"


def test_unknown_attachment_type_stops_before_download(tmp_path, source, window):
    feed = Feed()
    feed.attachment["FileName"] = "program.exe"
    result = collect(
        State(tmp_path), EvidenceStore(tmp_path / "evidence"), source, window, feed
    )
    assert result["status"] == "failed"
    assert not any("/Meetings/Attachment.aspx" in u for u in feed.calls)


def test_timezone_and_tenant_scope(source):
    adapter = Simbli(source)
    assert (
        adapter.local_time("01/15/2026 - 06:30 PM").utcoffset().total_seconds()
        == -18000
    )
    for timestamp in ("11/01/2026 - 01:30 AM", "03/08/2026 - 02:30 AM"):
        with pytest.raises(ValueError):
            adapter.local_time(timestamp)
    with pytest.raises(ValueError):
        Simbli(source.model_copy(update={"url": LISTING.replace("4013", "9999")}))


def test_attachment_probe_preserves_records_and_run(tmp_path, source, window):
    from civpulse_updater.browser_probe import probe

    state, store = State(tmp_path), EvidenceStore(tmp_path / "evidence")
    feed = Feed()
    feed.pdf = b"<!doctype html><html>Pardon Our Interruption</html>"
    result = collect(state, store, source, window, feed)
    assert result["status"] == "failed"
    before = {
        table: state.rows(f"SELECT * FROM {table}")
        for table in ("runs", "records", "changes")
    }

    class Recovery:
        def __init__(self, config, root, capture):
            assert config.browser_fallback
            self.browser_attempted = True
            self.requests = 1
            self.received_bytes = len(b"%PDF-1.7 recovered")

        def get(self, url):
            body = b"%PDF-1.7 recovered"
            return body, Evidence(
                sha256=digest(body),
                url=url,
                media_type="application/pdf",
                size=len(body),
                fetched_at=datetime.now(UTC),
                transport="playwright",
            )

    report = probe(state, store, result["run_id"], fetcher_factory=Recovery)
    assert report["recovered"] and report["proposals_created"] == 0
    assert store.read(report["sha256"]) == b"%PDF-1.7 recovered"
    assert (
        json.loads(store.read(report["report_sha256"]))["evidence"][0]["transport"]
        == "playwright"
    )
    assert before == {table: state.rows(f"SELECT * FROM {table}") for table in before}
