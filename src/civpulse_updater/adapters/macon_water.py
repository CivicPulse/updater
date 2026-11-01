"""Macon Water Authority's public Diligent Community meeting records."""

import json
import re
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode, urljoin, urlsplit
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from ..models import Document, Envelope, Event, Manifest, Reference, digest

HOST = "macon-water.community.diligentoneplatform.com"
BASE = f"https://{HOST}"
API = BASE + "/Services/MeetingsService.svc/meetings"
TYPES = {18, 19, 20, 23, 24, 25, 26, 27}


def identifier(value):
    if type(value) is not int or value <= 0:
        raise ValueError("Invalid meeting/document identifier")
    return value


def text(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Missing source text")
    return BeautifulSoup(value, "html.parser").get_text(" ", strip=True)


class MaconWater:
    manifest = Manifest(
        name="macon-water-authority",
        version="1.0.1",
        capabilities=("meetings", "documents"),
    )

    def __init__(self, config):
        if config.url != API or config.allowed_hosts != (HOST,):
            raise ValueError(
                "Only the verified Macon Water Authority tenant is supported"
            )
        self.config = config
        self.zone = ZoneInfo(config.timezone)
        self.window = None

    def start_urls(self, window):
        self.window = window
        return [
            API
            + "?"
            + urlencode({"from": window.start, "to": window.end + timedelta(days=1)})
        ]

    def start(self, raw):
        naive = datetime.strptime(raw["MeetingDateTime"], "%Y-%m-%d %H:%M")
        if naive.date().isoformat() != raw["MeetingDate"]:
            raise ValueError("Conflicting meeting dates")
        local = naive.replace(tzinfo=self.zone)
        if (
            local.utcoffset() != naive.replace(tzinfo=self.zone, fold=1).utcoffset()
            or local.astimezone(UTC).astimezone(self.zone).replace(tzinfo=None) != naive
        ):
            raise ValueError("Ambiguous or nonexistent local meeting time")
        return local

    def rows(self, content):
        rows = json.loads(content)
        if not isinstance(rows, list):
            raise ValueError("Expected public meeting array")
        seen = set()
        for raw in rows:
            if not isinstance(raw, dict):
                raise ValueError("Invalid meeting")
            mid = identifier(raw["Id"])
            if mid in seen:
                raise ValueError("Duplicate meeting identity")
            seen.add(mid)
            if (
                type(raw["ExternalCalendar"]) is not bool
                or type(raw["Published"]) is not bool
            ):
                raise ValueError("Missing calendar publication fields")
            if type(raw["MeetingTypeId"]) is not int:
                raise ValueError("Invalid meeting type")
            name = text(raw["Name"])
            scoped = raw["MeetingTypeId"] in TYPES or (
                raw["MeetingTypeId"] == 17
                and name.startswith("Macon Water Authority -")
            )
            if not raw["ExternalCalendar"] and scoped:
                self.start(raw)
                yield raw

    def discover(self, content, url, sha):
        refs = []
        for raw in self.rows(content):
            if (
                self.window
                and not self.window.start <= self.start(raw).date() <= self.window.end
            ):
                continue
            mid = raw["Id"]
            refs.append(
                Reference(
                    url=f"{API}/{mid}/meetingDocuments",
                    page_url=f"{BASE}/Portal/MeetingInformation.aspx?Id={mid}",
                    listing_hash=sha,
                )
            )
        return refs, []

    def uses_listing_content(self, ref):
        return True

    def meeting(self, content, ref):
        for raw in self.rows(content):
            if ref.url == f"{API}/{raw['Id']}/meetingDocuments":
                if (
                    ref.page_url
                    != f"{BASE}/Portal/MeetingInformation.aspx?Id={raw['Id']}"
                ):
                    raise ValueError("Meeting page identity mismatch")
                return raw
        raise ValueError("Meeting missing from saved listing")

    def documents(self, raw, resources):
        url = f"{API}/{raw['Id']}/meetingDocuments"
        payload = json.loads(resources[url])
        if not isinstance(payload, dict) or not isinstance(
            payload.get("Documents"), list
        ):
            raise ValueError("Invalid meeting documents")
        docs, attachments, seen = [], {}, set()
        for doc in payload["Documents"]:
            if not isinstance(doc, dict) or type(doc.get("IsPublic")) is not bool:
                raise ValueError("Missing document visibility")
            # Never inspect private HTML or discover links from it.
            if not doc["IsPublic"]:
                continue
            did = identifier(doc["Id"])
            if doc["MeetingId"] != raw["Id"] or did in seen:
                raise ValueError("Conflicting document identity")
            seen.add(did)
            # 1 = agenda, 2 = minutes (portal Type field, not DocumentType).
            if type(doc.get("Type")) is not int or doc["Type"] not in (1, 2):
                raise ValueError("Unsupported public document role")
            html = doc.get("Html")
            if not isinstance(html, str) or not html.strip():
                raise ValueError("Public document HTML missing")
            role = "Agenda" if doc["Type"] == 1 else "Minutes"
            body = html.encode("utf-8")
            docs.append((f"document:{did}", text(doc["Name"]), role, url, body))
            soup = BeautifulSoup(html, "html.parser")
            for link in soup.select("a[href]"):
                href = str(link["href"])
                target = urljoin(BASE, href)
                parts = urlsplit(target)
                if parts.hostname != HOST or not parts.path.lower().startswith(
                    "/document/"
                ):
                    continue
                if parts.scheme != "https" or parts.port not in (None, 443):
                    raise ValueError("Invalid public attachment URL")
                if not re.fullmatch(
                    r"/document/(?:[0-9]+|[0-9a-fA-F-]{36})", parts.path
                ):
                    raise ValueError("Unexpected attachment route")
                if parts.query or parts.fragment or parts.username or parts.password:
                    raise ValueError("Unexpected attachment URL parameters")
                attachments[target] = text(link.get_text(" ", strip=True))
        return docs, attachments

    def resources_for(self, content, ref, resources):
        raw = self.meeting(content, ref)
        if not raw["Published"]:
            return []
        if ref.url not in resources:
            return [ref.url]
        _, attachments = self.documents(raw, resources)
        return list(attachments)

    def materialized_documents(self, content, ref, resources):
        raw = self.meeting(content, ref)
        if not raw["Published"]:
            return []
        docs, _ = self.documents(raw, resources)
        return [doc[4] for doc in docs]

    def parse_bundle(self, content, ref, sha, window, resources):
        raw = self.meeting(content, ref)
        start = self.start(raw)
        if not window.start <= start.date() <= window.end:
            return []
        documents, warnings = [], []
        if raw["Published"]:
            docs, attachments = self.documents(raw, resources)
            for key, title, role, url, body in docs:
                documents.append(
                    Document(
                        id=key,
                        title=title,
                        role=role,
                        source_url=url,
                        sha256=digest(body),
                        size=len(body),
                    )
                )
            for url, title in sorted(attachments.items()):
                body = resources[url]
                if not body.startswith(b"%PDF-"):
                    soup = BeautifulSoup(body, "html.parser")
                    marker = soup.select_one('meta[name="keywords"]')
                    generator = soup.select_one('meta[name="generator"]')
                    if not (
                        marker is not None
                        and marker.get("content") == "iCompass-Open-Document"
                        and generator is not None
                        and str(generator.get("content", "")).startswith(
                            "Aspose.Words for .NET "
                        )
                        and soup.title is not None
                        and soup.title.get_text(strip=True).endswith(" - Html")
                        and soup.body is not None
                        and soup.body.get_text(strip=True)
                    ):
                        raise ValueError(
                            "Expected PDF or official portal HTML document"
                        )
                documents.append(
                    Document(
                        id="attachment:" + url.rsplit("/", 1)[1],
                        title=title,
                        role="Attachment",
                        source_url=url,
                        sha256=digest(body),
                        size=len(body),
                    )
                )
            if not any(d.role == "Agenda" for d in documents):
                warnings.append("No public agenda returned by the portal")
        else:
            warnings.append("Agenda unpublished; public calendar schedule only")
        name = text(raw["Name"])
        cancelled = bool(re.search(r"\bcancell?ed\b", name, re.I))
        if cancelled:
            warnings.append("Cancellation based on explicit source title")
        event = Event(
            source_id=self.config.id,
            uid=f"mwa:{raw['Id']}",
            title=name,
            source_url=ref.page_url,
            starts_at=start,
            timezone=self.config.timezone,
            all_day=False,
            status="cancelled" if cancelled else "unknown",
            location=text(raw["MeetingLocation"])
            if raw.get("MeetingLocation")
            else None,
            documents=tuple(documents),
            warnings=tuple(warnings),
        )
        return [
            Envelope(
                event=event,
                evidence_hashes=tuple(
                    dict.fromkeys(
                        [ref.listing_hash, sha]
                        + [digest(b) for b in resources.values()]
                    )
                ),
                parser_version=self.manifest.version,
                locator=f"Meeting Id={raw['Id']}",
            )
        ]
