"""Official Georgia General Assembly snapshots and meeting schedules."""

import json
import re
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode, urlsplit
from zoneinfo import ZoneInfo

from ..georgia_transport import API, SEARCH_SIZE
from ..models import (
    Envelope,
    Event,
    LegislativeRecord,
    ListingRequest,
    Manifest,
    Reference,
    canonical,
    digest,
)
from .civicclerk import array, identifier, obj, text


class GeorgiaLegislature:
    manifest = Manifest(
        name="georgia-legislature",
        version="1.0.5",
        capabilities=(
            "sessions",
            "bills",
            "votes",
            "members",
            "seats",
            "committees",
            "calendars",
            "meetings",
        ),
    )

    def __init__(self, config):
        self.config = config
        if (
            config.url != API + "sessions"
            or config.allowed_hosts != ("www.legis.ga.gov",)
            or config.legislature_session_id is None
        ):
            raise ValueError(
                "Georgia requires the official API and an explicit session ID"
            )
        self.session = config.legislature_session_id
        self.total = None
        self.bill_ids = set()
        self.window = None

    def search(self, page):
        return ListingRequest(
            url=f"{API}Legislation/Search/{SEARCH_SIZE}/{page}",
            content=canonical({"sessionId": self.session}).encode(),
        )

    def start_urls(self, window):
        self.window = window
        urls = [
            self.config.url,
            API + f"members/list/{self.session}",
            API + f"committees/List/{self.session}",
            API + f"Vote/list/1/{self.session}",
            API + f"Vote/list/2/{self.session}",
            API + f"sessions/days/{self.session}",
            self.search(0),
        ]
        # The official schedule UI queries short date ranges; avoid annual queries.
        start = window.start
        while start <= window.end:
            end = min(start + timedelta(days=27), window.end)
            urls.append(
                API
                + "meetings?"
                + urlencode(
                    {"startDate": start.isoformat(), "endDate": end.isoformat()}
                )
            )
            start = end + timedelta(days=1)
        return urls

    def inside(self, value):
        day = datetime.fromisoformat(value).date()
        return self.window is None or self.window.start <= day <= self.window.end

    def discover(self, content, url, sha):
        data = json.loads(content)
        refs = []

        def add(target, listing=None):
            refs.append(
                Reference(
                    url=target,
                    page_url=url,
                    listing_hash=sha,
                    description=canonical(listing) if listing is not None else None,
                )
            )

        path = urlsplit(url).path.removeprefix("/api/")
        if path.startswith("Legislation/Search/"):
            data = obj(data)
            rows = array(data["results"])
            total = data["resultCount"]
            if type(total) is not int or not 0 <= total <= 10000:
                raise ValueError("Invalid legislation result count")
            if self.total is not None and total != self.total:
                raise ValueError("Legislation result count changed during pagination")
            self.total = total
            page = int(path.rsplit("/", 1)[1])
            if len(rows) != max(0, min(SEARCH_SIZE, total - page * SEARCH_SIZE)):
                raise ValueError("Incomplete legislation search page")
            for row in rows:
                row = obj(row)
                if obj(row["session"])["id"] != self.session:
                    raise ValueError("Search session mismatch")
                bid = identifier(row["legislationId"])
                if bid in self.bill_ids:
                    raise ValueError("Duplicate legislation across search pages")
                self.bill_ids.add(bid)
                add(API + f"legislation/detail/{bid}")
            return refs, [self.search(page + 1)] if (
                page + 1
            ) * SEARCH_SIZE < total else []
        rows = array(data)
        if path == "sessions":
            if sum(obj(row)["id"] == self.session for row in rows) != 1:
                raise ValueError("Configured session missing or duplicated")
            add(url)
        elif path.startswith("members/list/"):
            seen = set()
            for row in rows:
                row = obj(row)
                district = obj(row["district"])
                chamber = district["chamberType"]
                if (
                    type(chamber) is not int
                    or chamber not in (1, 2)
                    or row["sessionId"] != self.session
                ):
                    raise ValueError("Member session or chamber mismatch")
                mid = identifier(row["id"])
                key = (mid, chamber)
                if key in seen:
                    raise ValueError("Duplicate member listing")
                seen.add(key)
                add(
                    API
                    + f"members/detail/{mid}?"
                    + urlencode({"session": self.session, "chamber": chamber}),
                    row,
                )
        elif path.startswith("committees/List/"):
            seen = set()
            for row in rows:
                row = obj(row)
                cid = identifier(row["id"])
                if row["sessionId"] != self.session or cid in seen:
                    raise ValueError("Committee session or identity mismatch")
                seen.add(cid)
                add(API + f"committees/details/{cid}/{self.session}", row)
        elif path.startswith("Vote/list/"):
            seen = set()
            for row in rows:
                row = obj(row)
                vid = identifier(row["id"])
                if vid in seen:
                    raise ValueError("Duplicate vote listing")
                seen.add(vid)
                if self.inside(row["date"]):
                    add(
                        API + f"Vote/detail/{vid}",
                        {"chamber": int(path.split("/")[2]), "vote": row},
                    )
        elif path.startswith("meetings") or path.startswith("sessions/days/"):
            add(url)
        else:
            raise ValueError("Unknown Georgia listing endpoint")
        return refs, []

    def uses_listing_content(self, ref):
        # These feeds already contain their complete records. Preserve the
        # discovery snapshot instead of fetching the same URL a second time.
        if ref.url == ref.page_url and (
            ref.url in (self.config.url, API + f"sessions/days/{self.session}")
            or urlsplit(ref.url).path == "/api/meetings"
        ):
            return True
        if not urlsplit(ref.url).path.startswith("/api/Vote/detail/"):
            return False
        context = obj(json.loads(ref.description))
        vote = obj(context["vote"])
        if type(vote["isRollCall"]) is not bool:
            raise ValueError("Invalid attendance indicator")
        # The official UI's rollCallCheck intentionally omits House attendance
        # detail requests, while Senate attendance supports individual ballots.
        return context["chamber"] == 1 and vote["isRollCall"]

    def calendar_urls(self, content, window):
        urls = []
        seen = set()
        for raw in array(json.loads(content)):
            raw = obj(raw)
            day = datetime.fromisoformat(raw["date"]).date()
            if window.start <= day <= window.end and day not in seen:
                seen.add(day)
                for chamber in (1, 2):
                    urls.append(
                        API
                        + "Document/calendars?"
                        + urlencode(
                            {
                                "sessionId": self.session,
                                "chamber": chamber,
                                "date": day.isoformat(),
                            }
                        )
                    )
        return urls

    def resources_for(self, content, ref, resources):
        if "/sessions/days/" in ref.url:
            return self.calendar_urls(content, self.window)
        return []

    def parse_bundle(self, content, ref, sha, window, resources):
        data = json.loads(content)
        path = urlsplit(ref.url).path.removeprefix("/api/")
        hashes = tuple(
            dict.fromkeys(
                [ref.listing_hash, sha, *(digest(b) for b in resources.values())]
            )
        )
        records = []

        def add(kind, key, title, value, warnings=(), source_url=None):
            records.append(
                LegislativeRecord(
                    source_id=self.config.id,
                    uid=f"ga:{self.session}:{kind}:{key}",
                    record_type=kind,
                    title=text(title),
                    source_url=source_url or ref.url,
                    session_id=self.session,
                    data=value,
                    warnings=warnings,
                )
            )

        if path == "sessions":
            matches = [
                obj(row) for row in array(data) if obj(row)["id"] == self.session
            ]
            if len(matches) != 1:
                raise ValueError("Configured session not found")
            add("session", self.session, matches[0]["description"], matches[0])
        elif path.startswith("legislation/detail/"):
            raw = obj(data)
            bid = identifier(raw["id"])
            if (
                ref.url != API + f"legislation/detail/{bid}"
                or obj(raw["session"])["id"] != self.session
            ):
                raise ValueError("Bill identity or session mismatch")
            for field in (
                "sponsors",
                "versions",
                "committees",
                "statusHistory",
                "votes",
            ):
                if raw[field] is not None:
                    array(raw[field])
            add("bill", bid, raw["title"], raw)
        elif path.startswith("members/detail/"):
            raw = obj(data)
            listing = obj(json.loads(ref.description))
            district = obj(listing["district"])
            chamber = district["chamberType"]
            if (
                raw["id"] != listing["id"]
                or raw["contextSessionId"] != self.session
                or raw["chamber"] != chamber
                or raw["districtNumber"] != district["number"]
            ):
                raise ValueError("Member identity, session or seat mismatch")
            add("member", f"{chamber}:{identifier(raw['id'])}", raw["displayName"], raw)
            # A district's identity survives member replacement. Occupancy is not
            # inferred from the roster, which includes former session members.
            add(
                "seat",
                f"{chamber}:{identifier(district['number'])}",
                f"Georgia {'House' if chamber == 1 else 'Senate'} District {district['number']}",
                {"district": district, "sessionId": self.session},
                (
                    "Seat derived from the official member district; roster does not establish current occupancy or vacancies.",
                ),
                source_url=API + f"members/list/{self.session}",
            )
        elif path.startswith("committees/details/"):
            raw = obj(data)
            listing = obj(json.loads(ref.description))
            if raw["id"] != listing["id"] or raw["sessionId"] != self.session:
                raise ValueError("Committee identity or session mismatch")
            array(raw["members"])
            array(raw["subcommittees"])
            chamber = listing["chamber"]
            if type(chamber) is not int or chamber not in (1, 2):
                raise ValueError("Invalid committee chamber")
            if "chamber" in raw and raw["chamber"] != chamber:
                raise ValueError("Committee chamber mismatch")
            add(
                "committee",
                identifier(raw["id"]),
                raw["name"],
                {**raw, "chamber": chamber},
            )
        elif path.startswith("Vote/detail/"):
            listing = obj(json.loads(ref.description))
            vid = identifier(listing["vote"]["id"])
            if self.uses_listing_content(ref):
                matches = [obj(row) for row in array(data) if obj(row)["id"] == vid]
                if (
                    len(matches) != 1
                    or matches[0] != listing["vote"]
                    or sha != ref.listing_hash
                ):
                    raise ValueError("Attendance listing evidence mismatch")
                add(
                    "vote",
                    vid,
                    matches[0]["name"],
                    {
                        "chamber": 1,
                        "listing": matches[0],
                        "detail": None,
                        "detail_availability": "aggregate-only",
                    },
                    (
                        "Official House attendance roll calls expose aggregate totals; individual ballots are not available in the website detail view.",
                    ),
                    source_url=ref.page_url,
                )
                return [
                    Envelope(
                        event=record,
                        evidence_hashes=hashes,
                        parser_version=self.manifest.version,
                        locator=record.uid,
                    )
                    for record in records
                ]
            raw = obj(data)
            if obj(raw["session"])["id"] != self.session:
                raise ValueError("Vote session mismatch")
            for link in array(raw["legislation"]):
                if obj(link)["voteId"] != vid or link["sessionId"] != self.session:
                    raise ValueError("Vote identity mismatch")
            seen = set()
            for ballot in array(raw["votes"]):
                member = obj(obj(ballot)["member"])
                mid = member["id"]
                # Official roll calls repeat id=0 for vacant seats. Keep these
                # anonymous entries; they cannot be joined to a roster member.
                vacant = type(mid) is int and mid == 0 and member["name"] == "VACANT"
                if not vacant:
                    identifier(mid)
                choice = ballot["memberVoted"]
                if (
                    (not vacant and mid in seen)
                    or type(choice) is not int
                    or choice not in (0, 1, 2, 3, 4)
                ):
                    raise ValueError("Invalid or duplicate member ballot")
                seen.add(mid)
            add(
                "vote",
                vid,
                listing["vote"]["name"],
                {
                    "chamber": listing["chamber"],
                    "listing": listing["vote"],
                    "detail": raw,
                },
            )
        elif path == "meetings":
            for raw in array(data):
                raw = obj(raw)
                start = self.local_time(raw["start"])
                if not window.start <= start.date() <= window.end:
                    continue
                mid = raw["id"]
                if not isinstance(mid, str) or not mid:
                    raise ValueError("Invalid meeting identity")
                chamber = raw["chamber"]
                if type(chamber) is not int or chamber not in (1, 2, 3):
                    raise ValueError("Invalid meeting chamber")
                tbd = start.time().isoformat() == "00:00:00"
                cancelled = bool(
                    re.match(r"^\s*cancell?ed\b", text(raw["subject"]), re.I)
                )
                records.append(
                    Event(
                        source_id=self.config.id,
                        uid=f"ga:meeting:{raw['chamber']}:{mid}",
                        title=text(raw["subject"]),
                        source_url="https://www.legis.ga.gov/schedule",
                        starts_at=start.date() if tbd else start,
                        all_day=tbd,
                        timezone=self.config.timezone,
                        status="cancelled" if cancelled else "unknown",
                        description=(text(raw["body"]) or None)
                        if raw.get("body") is not None
                        else None,
                        location=(text(raw["location"]) or None)
                        if raw.get("location") is not None
                        else None,
                        warnings=(
                            (
                                "Cancellation explicitly indicated by the official schedule title.",
                            )
                            if cancelled
                            else ()
                        )
                        + (
                            (
                                "Official midnight time means time TBD; date-only schedule.",
                            )
                            if tbd
                            else ()
                        )
                        + (
                            ("Agenda link: " + raw["agendaUri"],)
                            if raw.get("agendaUri")
                            else ()
                        ),
                    )
                )
        elif path.startswith("sessions/days/"):
            for url in self.calendar_urls(content, window):
                for raw in array(json.loads(resources[url])):
                    raw = obj(raw)
                    day = datetime.fromisoformat(raw["date"]).date()
                    if not window.start <= day <= window.end:
                        continue
                    # Retain the source document URL; PDF content is not extracted.
                    doc_url = raw["url"]
                    if not isinstance(doc_url, str) or not doc_url.startswith(
                        API + "document/"
                    ):
                        raise ValueError("Calendar outside official document API")
                    key = digest(urlsplit(doc_url).path.encode())
                    add("calendar", key, raw["title"], raw, source_url=doc_url)
        else:
            raise ValueError("Unknown Georgia detail endpoint")
        return [
            Envelope(
                event=record,
                evidence_hashes=hashes,
                parser_version=self.manifest.version,
                locator=record.uid,
            )
            for record in records
        ]

    def local_time(self, value):
        parsed = datetime.fromisoformat(value)
        zone = ZoneInfo(self.config.timezone)
        if parsed.tzinfo is not None:
            return parsed.astimezone(zone)
        local = parsed.replace(tzinfo=zone)
        if (
            local.utcoffset() != parsed.replace(tzinfo=zone, fold=1).utcoffset()
            or local.astimezone(UTC).astimezone(zone).replace(tzinfo=None) != parsed
        ):
            raise ValueError("Ambiguous or nonexistent Georgia local time")
        return local
