"""Macon-Bibb's public CivicClerk API; immutable evidence, no remote writes."""

import json
import re
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from ..models import Document, Envelope, Event, Manifest, Reference, digest
from ..transport import validate_url

API = "https://maconbibbcoga.api.civicclerk.com/v1"
PORTAL = "https://maconbibbcoga.portal.civicclerk.com"


def obj(data):
    if not isinstance(data, dict):
        raise ValueError("Expected object")
    return data


def array(data):
    if not isinstance(data, list):
        raise ValueError("Expected list")
    return data


def identifier(value):
    if type(value) is not int or value <= 0:
        raise ValueError("Invalid source identifier")
    return value


def text(value):
    if not isinstance(value, str):
        raise ValueError("Expected text")
    return BeautifulSoup(value, "html.parser").get_text(" ", strip=True)


class CivicClerk:
    manifest = Manifest(
        name="civicclerk", version="1.1.0", capabilities=("meetings", "documents")
    )

    def __init__(self, config):
        self.config = config
        if config.url != API + "/Events":
            raise ValueError(
                "This adapter supports the verified Macon-Bibb tenant only"
            )
        self.window = None

    def start_urls(self, window):
        self.window = window
        query = urlencode(
            {
                "$filter": f"(categoryId eq 26 or categoryId eq 24) and eventDate ge {window.start}T00:00:00Z and eventDate le {window.end}T23:59:59Z",
                "$orderby": "eventDate asc",
            }
        )
        return [self.config.url + "?" + query]

    def start(self, raw):
        value = obj(raw)["eventDate"]
        if not isinstance(value, str) or not value.endswith("Z"):
            raise ValueError("Unexpected CivicClerk timestamp")
        # CivicClerk publishes local wall-clock readings with a misleading Z.
        naive = datetime.fromisoformat(value[:-1])
        if naive.tzinfo is not None:
            raise ValueError("Unexpected offset in wall-clock timestamp")
        zone = ZoneInfo(self.config.timezone)
        local = naive.replace(tzinfo=zone)
        if (
            local.utcoffset() != naive.replace(tzinfo=zone, fold=1).utcoffset()
            or local.astimezone(UTC).astimezone(zone).replace(tzinfo=None) != naive
        ):
            raise ValueError("Ambiguous or nonexistent local time")
        return local

    def public(self, raw):
        obj(raw)
        if (
            type(raw.get("categoryId")) is not int
            or not isinstance(raw.get("isPublished"), str)
            or type(raw.get("isDeleted")) is not bool
        ):
            raise ValueError("Missing public-event scope fields")
        # The county also files legislative hearings and special meetings under
        # General. Never include unrelated boards solely because they use it.
        legislative_general = raw["categoryId"] == 24 and bool(
            re.fullmatch(
                r"(?:Special Called (?:Pre-)?Commission Meeting|"
                r"Committee on Committees|Public Hearing(?: for FY\d{4} Budget)?)"
                r"(?:\s*-\s*CANCELLED)?",
                text(raw.get("eventName") or ""),
                re.I,
            )
        )
        return (
            (raw.get("categoryId") == 26 or legislative_general)
            and raw.get("isPublished") == "Published"
            and raw.get("isDeleted") is False
        )

    def discover(self, content, url, sha):
        payload = obj(json.loads(content))
        refs = []
        for raw in array(payload["value"]):
            if not self.public(raw):
                continue
            if (
                self.window
                and not self.window.start <= self.start(raw).date() <= self.window.end
            ):
                continue
            refs.append(
                Reference(
                    url=f"{API}/Events/{identifier(raw['id'])}",
                    page_url=url,
                    listing_hash=sha,
                )
            )
        following = payload.get("@odata.nextLink")
        if following is not None:
            if not isinstance(following, str):
                raise ValueError("Invalid pagination")
            following = urljoin(url, following)
            if urlsplit(following).path != "/v1/Events":
                raise ValueError("Invalid pagination endpoint")
        return refs, [following] if following else []

    def documents(self, raw, resources):
        docs = {}

        def add(key, title, role, url):
            validate_url(url, self.config.allowed_hosts)
            candidate = (title, role, url)
            if key in docs and docs[key] != candidate:
                raise ValueError("Conflicting document identity")
            docs[key] = candidate

        for file in array(raw["publishedFiles"]):
            obj(file)
            fid = identifier(file["fileId"])
            add(
                f"file:{fid}",
                text(file["name"]),
                text(file["type"]),
                f"{API}/Meetings/GetMeetingFileStream(fileId={fid},plainText=false)",
            )
        aid = raw.get("agendaId", 0)
        if aid:
            agenda_url = f"{API}/Meetings/{identifier(aid)}"
            if agenda_url not in resources:
                return docs, agenda_url
            agenda = obj(json.loads(resources[agenda_url]))
            if agenda.get("id") != aid:
                raise ValueError("Agenda identity mismatch")
            queue = list(array(agenda["items"]))
            count = 0
            while queue:
                item = obj(queue.pop())
                count += 1
                if count > 5000:
                    raise ValueError("Agenda item limit exceeded")
                queue.extend(array(item.get("childItems", [])))
                for attachment in array(item.get("attachmentsList", [])):
                    a = obj(attachment)
                    if a.get("isLink") is True:
                        continue  # External links are not downloadable evidence.
                    url = a.get("pdfVersionFullPath")
                    if not url:
                        continue  # No public download was supplied.
                    if not isinstance(url, str):
                        raise ValueError("Invalid attachment URL")
                    parts = urlsplit(url)
                    if (
                        parts.hostname != "civicclerk.blob.core.windows.net"
                        or not parts.path.startswith("/stream/MACONBIBBCOGA/")
                    ):
                        raise ValueError("Attachment outside tenant storage")
                    add(
                        f"attachment:{identifier(a['id'])}",
                        text(a["fileName"]),
                        "Attachment",
                        url,
                    )
        return docs, None

    def resources(self, content, resources):
        raw = obj(json.loads(content))
        if not self.public(raw):
            raise ValueError("Event is outside public commission scope")
        docs, agenda = self.documents(raw, resources)
        return [agenda] if agenda else [data[2] for data in docs.values()]

    def parse_bundle(self, content, ref, sha, window, resources):
        raw = obj(json.loads(content))
        if not self.public(raw):
            raise ValueError("Event is outside public commission scope")
        eid = identifier(raw["id"])
        if ref.url != f"{API}/Events/{eid}":
            raise ValueError("Event identity mismatch")
        start = self.start(raw)
        if not window.start <= start.date() <= window.end:
            return []
        docs, missing_agenda = self.documents(raw, resources)
        if missing_agenda:
            raise ValueError("Missing agenda evidence")
        documents = []
        for key, (title, role, url) in sorted(docs.items()):
            data = resources[url]
            if not data.startswith(b"%PDF-"):
                raise ValueError("Expected public PDF document")
            parts = urlsplit(url)
            # Signed storage credentials expire and must not affect record identity.
            public_url = (
                urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
                if parts.hostname == "civicclerk.blob.core.windows.net"
                else url
            )
            documents.append(
                Document(
                    id=key,
                    title=title,
                    role=role,
                    source_url=public_url,
                    sha256=digest(data),
                    size=len(data),
                )
            )
        title = text(raw["eventName"])
        location = obj(raw.get("eventLocation") or {})
        duration = timedelta(
            hours=raw.get("durationHrs") or 0, minutes=raw.get("durationMin") or 0
        )
        event = Event(
            source_id=self.config.id,
            uid=f"civicclerk:maconbibbcoga:event:{eid}",
            title=title,
            source_url=f"{PORTAL}/event/{eid}",
            starts_at=start,
            ends_at=start + duration if duration else None,
            timezone=self.config.timezone,
            all_day=False,
            status="cancelled"
            if re.search(r"\bcancell?ed\b", title, re.I)
            else "scheduled",
            description=text(raw.get("eventDescription") or "") or None,
            location=", ".join(
                text(location[k])
                for k in ("address1", "address2", "city", "state", "zipCode")
                if location.get(k)
            )
            or None,
            documents=tuple(documents),
        )
        return [
            Envelope(
                event=event,
                evidence_hashes=tuple(
                    dict.fromkeys(
                        [
                            ref.listing_hash,
                            sha,
                            *(digest(data) for data in resources.values()),
                        ]
                    )
                ),
                parser_version=self.manifest.version,
                locator=f"Events/{eid}",
            )
        ]
