"""Public Bibb school-board records from Simbli (tenant 4013).

Only observed public GET endpoints are used. Never evaluate upstream JavaScript.
"""

import json
import re
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlencode, urlsplit
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from ..models import Document, Envelope, Event, Manifest, Reference, digest

BASE = "https://simbli.eboardsolutions.com"
LISTING = BASE + "/SB_Meetings/SB_MeetingListing.aspx?S=4013"
TYPES = {"Board Meeting", "Committee Meeting", "Work Session"}


class ListingCoverageError(ValueError):
    """The requested window requires unsupported historical pagination."""


class PublicDocumentUnavailable(ValueError):
    """A public document endpoint returned a challenge or login page."""


def obj(value):
    if not isinstance(value, dict):
        raise ValueError("Expected Simbli object")
    return value


def array(value):
    if not isinstance(value, list):
        raise ValueError("Expected Simbli list")
    return value


def string(value):
    if not isinstance(value, str):
        raise ValueError("Expected Simbli text")
    return value


def identifier(value):
    if type(value) is not int or value <= 0:
        raise ValueError("Invalid Simbli ID")
    return value


def plain(value):
    soup = BeautifulSoup(string(value), "html.parser")
    for tag in soup.select("script,style,head"):
        tag.decompose()
    return soup.get_text(" ", strip=True)


def page(content):
    value = content.decode("utf-8-sig")
    if "Request unsuccessful" in value or "<title>Login" in value:
        raise ValueError("Simbli public page unavailable")
    return value


def meeting_url(mid):
    return BASE + "/SB_Meetings/ViewMeeting.aspx?" + urlencode({"S": 4013, "MID": mid})


def walk(items, child_key):
    queue = list(array(items))
    count = 0
    while queue:
        item = obj(queue.pop(0))
        count += 1
        if count > 2000:
            raise ValueError("Simbli item limit exceeded")
        yield item
        queue[0:0] = array(item.get(child_key) or [])


class Simbli:
    manifest = Manifest(
        name="simbli",
        version="1.0.0",
        capabilities=("meetings", "agendas", "minutes", "documents"),
    )

    def __init__(self, config):
        self.config = config
        self.window = None
        if config.url != LISTING or config.allowed_hosts != (
            "simbli.eboardsolutions.com",
        ):
            raise ValueError("Simbli adapter is restricted to the verified Bibb tenant")

    def local_time(self, value):
        naive = datetime.strptime(string(value), "%m/%d/%Y - %I:%M %p")
        zone = ZoneInfo(self.config.timezone)
        local = naive.replace(tzinfo=zone)
        if (
            local.utcoffset() != naive.replace(tzinfo=zone, fold=1).utcoffset()
            or local.astimezone(UTC).astimezone(zone).replace(tzinfo=None) != naive
        ):
            raise ValueError("Ambiguous or nonexistent Simbli meeting time")
        return local

    def start_urls(self, window):
        self.window = window
        return [LISTING]

    def discover(self, content, url, sha):
        if url != LISTING:
            raise ValueError("Unexpected Simbli listing URL")
        html = page(content)
        marker = "ContentPlaceHolder1_MeetingGrid_newGrid.dataBuffer="
        if marker not in html:
            raise ValueError("Simbli listing structure missing")
        rows, _ = json.JSONDecoder().raw_decode(html.split(marker, 1)[1].lstrip())
        rows = array(rows)
        refs, dates = [], []
        for raw in rows:
            row = obj(raw)
            if row["MM_DistrictID"] != 4013:
                raise ValueError("Wrong Simbli district")
            start = self.local_time(row["DateTime"])
            dates.append(start.date())
            if row["ML_TypeTitle"] not in TYPES:
                continue
            if (
                row["ACCESS"] != "P"
                or row["ML_IsPublic"] is not True
                or row["MM_isTemplate"] is not False
            ):
                continue
            if row["MinutesStatus"] not in ("PUBLISHED", "UNPUBLISHED", "AUTHORISED"):
                raise ValueError("Unknown minutes publication state")
            if self.window and not self.window.start <= start.date() <= self.window.end:
                continue
            refs.append(
                Reference(
                    url=meeting_url(identifier(row["Master_MeetingID"])),
                    page_url=url,
                    listing_hash=sha,
                    displayed_start=row["DateTime"],
                    source_title=string(row["MM_MeetingTitle"]),
                    meeting_type=row["ML_TypeTitle"],
                    minutes_published=row["MinutesStatus"] == "PUBLISHED",
                )
            )
        # The public initial page is descending and limited to 50 rows. Older
        # pagination requires POST/session state; don't claim a complete backfill.
        if rows:
            total = rows[0]["MeetingsCount"]
            if type(total) is not int or total < len(rows):
                raise ValueError("Invalid listing count")
            if dates != sorted(dates, reverse=True):
                raise ValueError("Unexpected listing order")
            if total > len(rows) and self.window and self.window.start <= min(dates):
                raise ListingCoverageError(
                    "Requested window exceeds public initial listing coverage"
                )
        elif "No Meetings Found." not in html:
            raise ValueError("Unverified empty listing")
        return refs, []

    def context(self, content, ref):
        query = parse_qs(urlsplit(ref.url).query)
        mid = int(query["MID"][0])
        if (
            ref.url != meeting_url(identifier(mid))
            or ref.meeting_type not in TYPES
            or ref.minutes_published is None
        ):
            raise ValueError("Invalid meeting provenance")
        html = page(content)
        params = {}
        for key, variable in (
            ("sct", "sToken"),
            ("endid", "enDID"),
            ("enmid", "enMeetingID"),
        ):
            matches = re.findall(r"\bvar\s+" + variable + r"\s*=\s*'([^'\r\n]+)'", html)
            if not matches or len(set(matches)) != 1:
                raise ValueError("Public Simbli API parameters unavailable")
            params[key] = matches[0]
        return mid, params

    def api(self, name, params, **extra):
        return BASE + "/Services/api/" + name + "/?" + urlencode(params | extra)

    def plan(self, content, ref, saved):
        mid, params = self.context(content, ref)
        metadata_url = self.api(
            "GetMeeting", {k: v for k, v in params.items() if k != "sct"}
        )
        required = [metadata_url]
        documents = {}

        def data(url):
            return obj(json.loads(saved[url]))

        def add(key, title, role, url, extension="json"):
            candidate = (plain(title), role, url, extension)
            if key in documents and documents[key] != candidate:
                raise ValueError("Conflicting attachment identity")
            documents[key] = candidate
            if url not in required:
                required.append(url)

        def attachment(a, role="Attachment", minutes_kind=None):
            a = obj(a)
            if (
                str(a["SchoolID"]) != "4013"
                or a.get("IsDeleted") is not False
                or a.get("VisibilityMode") != 0
            ):
                raise ValueError("Attachment not verified public for Bibb")
            aid = identifier(a["AttachmentID"])
            if minutes_kind:
                url = (
                    BASE
                    + f"/SB_Meetings/View{minutes_kind}MinuteDocument.aspx?"
                    + urlencode({"S": 4013, "aid": string(a["EncrId"])})
                )
            else:
                if a["EnitityID"] != mid:
                    raise ValueError("Attachment meeting identity mismatch")
                url = (
                    BASE
                    + "/Meetings/Attachment.aspx?"
                    + urlencode({"S": 4013, "AID": aid, "MID": mid})
                )
            ext = string(a["FileName"]).rsplit(".", 1)[-1].lower()
            if ext not in {
                "pdf",
                "docx",
                "xlsx",
                "pptx",
                "doc",
                "xls",
                "ppt",
                "png",
                "jpg",
                "jpeg",
            }:
                raise ValueError("Unsupported attachment format")
            add(
                f"{minutes_kind or 'agenda'}-attachment:{aid}",
                a["Title"],
                role,
                url,
                ext,
            )

        if metadata_url not in saved:
            return required, documents, None
        metadata = data(metadata_url)
        if (
            metadata["ID"] != mid
            or type(metadata["IsPublished"]) is not bool
            or metadata["IsTemplate"] is not False
        ):
            raise ValueError("Meeting publication identity invalid")
        if metadata["IsPublished"] is False:
            if ref.minutes_published:
                raise ValueError("Published minutes conflict with unpublished agenda")
            return required, documents, metadata
        tree_url = self.api("MeetingView/GetItemsTreeDTO", params, enuid="", v="")
        add(f"agenda:{mid}", "Agenda structure", "Agenda", tree_url)
        support_url = self.api(
            "GetSupportingDocuments", params, enentityid=params["enmid"], enitemid=""
        )
        required.append(support_url)
        if support_url in saved:
            for a in array(data(support_url)["Attachment"]):
                attachment(a)
        if tree_url in saved:
            for item in walk(data(tree_url)["Items"], "Children"):
                if item["Visibility"] != 0 or item["Deleted"] is not False:
                    raise ValueError("Non-public agenda item")
                iid = identifier(item["AgendaID"])
                detail_url = self.api(
                    "MeetingView/GetMeetingItemDetailsModel",
                    params,
                    enitemid=string(item["ID"]),
                    enuid="",
                    view="",
                    stab="1",
                )
                add(f"agenda-item:{iid}", item["Title"], "Agenda Item", detail_url)
                if detail_url in saved:
                    detail = data(detail_url)
                    identity = obj(detail["ItemDetails"])
                    if (
                        identity["EncrID"] != item["ID"]
                        or identity["VisibilityMode"] != 0
                        or identity["Deleted"] is not False
                    ):
                        raise ValueError("Agenda item identity or visibility mismatch")
                    for field in array(detail["ItemContents"]):
                        if obj(field)["FieldVisibility"] != 0:
                            raise ValueError("Non-public agenda content")
                        for a in array(field.get("Attachments") or []):
                            attachment(a)
        if ref.minutes_published:
            minutes_url = self.api(
                "GetMeetingMinutes",
                params,
                enuid="",
                enajs="",
                searchText="",
                matchType="",
            )
            add(f"minutes:{mid}", "Published meeting minutes", "Minutes", minutes_url)
            if minutes_url in saved:
                minutes = data(minutes_url)
                meeting_minutes = obj(minutes["MeetingMinutes"])
                for a in array(meeting_minutes.get("Attachments") or []):
                    attachment(a, "Minutes Attachment", "Meeting")
                for item in walk(minutes["LstItemMinutes"], "ChildMinutesLst"):
                    if item["Visibility"] != 0:
                        raise ValueError("Non-public minutes")
                    for a in array(item.get("Attachments") or []):
                        attachment(a, "Minutes Attachment", "Agenda")
        return list(dict.fromkeys(required)), documents, metadata

    def resources_for(self, content, ref, resources):
        return self.plan(content, ref, resources)[0]

    def parse_bundle(self, content, ref, sha, window, resources):
        required, definitions, metadata = self.plan(content, ref, resources)
        if any(url not in resources for url in required) or metadata is None:
            raise ValueError("Incomplete Simbli evidence")
        start = self.local_time(metadata["TitleDateTime"])
        if not window.start <= start.date() <= window.end:
            return []
        documents = []
        for key, (title, role, url, extension) in sorted(definitions.items()):
            body = resources[url]
            if body.lstrip().lower().startswith((b"<!doctype html", b"<html")):
                raise PublicDocumentUnavailable("Public document returned an HTML page")
            if extension == "json":
                obj(json.loads(body))
            elif extension == "pdf":
                if not body.startswith(b"%PDF-"):
                    raise ValueError("Expected PDF attachment")
            elif extension in ("docx", "xlsx", "pptx"):
                if not body.startswith(b"PK\x03\x04"):
                    raise ValueError("Expected Office attachment")
            elif extension in ("doc", "xls", "ppt"):
                if not body.startswith(bytes.fromhex("d0cf11e0a1b11e1")):
                    raise ValueError("Expected Office binary attachment")
            elif extension in ("png", "jpg", "jpeg"):
                if not body.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff")):
                    raise ValueError("Expected image attachment")
            else:
                raise ValueError("Unsupported attachment format")
            # Session tokens stay only in raw local evidence, never proposals.
            public_url = (
                ref.url + ("&T=1" if role == "Minutes" else "")
                if extension == "json"
                else url
            )
            documents.append(
                Document(
                    id=key,
                    title=title,
                    role=role,
                    source_url=public_url,
                    sha256=digest(body),
                    size=len(body),
                )
            )
        warnings = []
        if metadata["IsPublished"] is False:
            warnings.append("Agenda is not yet published; schedule only.")
        if not ref.minutes_published:
            warnings.append(
                "Minutes are not published in the official meeting listing."
            )
        if (
            ref.displayed_start != metadata["TitleDateTime"]
            or ref.source_title != metadata["Title"]
        ):
            warnings.append(
                "Meeting details changed since the listing was collected; verify schedule."
            )
        title = plain(metadata["Title"])
        mid, _ = self.context(content, ref)
        event = Event(
            source_id=self.config.id,
            uid=f"simbli:4013:meeting:{mid}",
            title=title,
            source_url=ref.url,
            starts_at=start,
            timezone=self.config.timezone,
            all_day=False,
            status="cancelled"
            if re.search(r"\bcancell?ed\b", title, re.I)
            else "scheduled",
            location=", ".join(
                plain(metadata[k])
                for k in ("Address1", "Address2", "Address3")
                if metadata.get(k)
            )
            or None,
            documents=tuple(documents),
            warnings=tuple(warnings),
        )
        return [
            Envelope(
                event=event,
                evidence_hashes=tuple(
                    dict.fromkeys(
                        [
                            ref.listing_hash,
                            sha,
                            *(digest(b) for b in resources.values()),
                        ]
                    )
                ),
                parser_version=self.manifest.version,
                locator=f"S=4013&MID={mid}",
            )
        ]
