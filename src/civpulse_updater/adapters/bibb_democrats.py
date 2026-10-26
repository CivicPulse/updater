from datetime import UTC, date, datetime, time
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import recurring_ical_events
from bs4 import BeautifulSoup
from icalendar import Calendar

from ..models import Envelope, Event, Manifest, Reference, SourceConfig, Window


class BibbDemocrats:
    manifest = Manifest(name="bibb-democrats", version="0.1.0")

    def __init__(self, config: SourceConfig):
        self.config = config
        self.zone = ZoneInfo(config.timezone)

    def discover(self, content: bytes, url: str, sha: str):
        soup = BeautifulSoup(content, "html.parser")
        articles = soup.select("article.eventlist-event")
        if not articles:
            raise ValueError(
                "Event listing structure missing or empty; review required"
            )
        refs = []
        for article in articles:
            ics = article.select_one("a.eventlist-meta-export-ical[href]")
            title = article.select_one("a.eventlist-title-link[href]")
            if ics is None or title is None:
                raise ValueError("Event missing an ICS or detail link")
            description = article.select_one(".eventlist-description")
            if description:
                for tag in description.select("script, style"):
                    tag.decompose()

            def displayed(selector, article=article):
                tag = article.select_one(selector)
                if tag is None:
                    return None
                return f"{tag.get('datetime', '')} {' '.join(tag.get_text().split())}"

            refs.append(
                Reference(
                    url=urljoin(url, str(ics["href"])),
                    page_url=urljoin(url, str(title["href"])),
                    listing_hash=sha,
                    displayed_start=displayed(".event-time-localized-start"),
                    displayed_end=displayed(".event-time-localized-end"),
                    description=description.get_text(" ", strip=True)
                    if description
                    else None,
                )
            )
        pages = list(
            dict.fromkeys(
                urljoin(url, str(a["href"]))
                for a in soup.select(
                    ".item-pagination a[href], a[rel~=next][href], a[rel~=prev][href]"
                )
            )
        )
        return refs, pages

    def _temporal(self, value):
        if isinstance(value, datetime):
            if value.tzinfo is None:
                # Floating times use configured source timezone. Reject DST ambiguity.
                first = value.replace(tzinfo=self.zone, fold=0)
                second = value.replace(tzinfo=self.zone, fold=1)
                if first.utcoffset() != second.utcoffset():
                    raise ValueError("Ambiguous or nonexistent floating local time")
                value = first
            return value.astimezone(UTC)
        if isinstance(value, date):
            return value
        raise ValueError("Invalid calendar date")

    def parse(self, content: bytes, ref: Reference, sha: str, window: Window):
        calendar = Calendar.from_ical(content)
        if calendar.name != "VCALENDAR" or calendar.errors:
            raise ValueError("Invalid iCalendar document")
        raw = calendar.walk("VEVENT")
        if not raw or len(raw) > self.config.max_events:
            raise ValueError("Missing events or event limit exceeded")
        recurring = set()
        for component in raw:
            if (
                component.errors
                or not component.get("UID")
                or not component.get("DTSTART")
            ):
                raise ValueError("Invalid event or missing UID/DTSTART")
            rule = component.get("RRULE")
            if rule:
                # Reject high-frequency or complex rules before expansion.
                if str(rule.get("FREQ", [""])[0]) not in (
                    "DAILY",
                    "WEEKLY",
                    "MONTHLY",
                    "YEARLY",
                ):
                    raise ValueError("Unsupported high-frequency recurrence")
                if any(key in rule for key in ("BYSECOND", "BYMINUTE", "BYHOUR")):
                    raise ValueError("Subdaily recurrence expansion unsupported")
            if rule or component.get("RDATE") or component.get("RECURRENCE-ID"):
                recurring.add(str(component["UID"]))
        lower = datetime.combine(window.start, time.min, self.zone)
        components = [item for item in raw if str(item["UID"]) not in recurring]
        if recurring:
            series = Calendar()
            # Keep timezone definitions for recurrence evaluation.
            for item in calendar.subcomponents:
                if item.name == "VTIMEZONE" or str(item.get("UID", "")) in recurring:
                    series.add_component(item)
            query = recurring_ical_events.of(series)
            for item in query.after(lower):
                start = self._temporal(item["DTSTART"].dt)
                local_date = (
                    start.astimezone(self.zone).date()
                    if isinstance(start, datetime)
                    else start
                )
                if local_date > window.end:
                    break
                components.append(item)
                if len(components) > 1000:
                    raise ValueError("Recurrence expansion limit exceeded")
            # Libraries may omit cancelled exceptions: retain explicit tombstones.
            components.extend(
                item for item in raw if str(item.get("STATUS", "")) == "CANCELLED"
            )
        result = {}
        for item in components:
            start = self._temporal(item["DTSTART"].dt)
            local_date = (
                start.astimezone(self.zone).date()
                if isinstance(start, datetime)
                else start
            )
            if not window.start <= local_date <= window.end:
                continue
            end = self._temporal(item["DTEND"].dt) if item.get("DTEND") else None
            if end is None and item.get("DURATION"):
                end = start + item["DURATION"].dt
            rid = item.get("RECURRENCE-ID")
            recurrence_id = self._temporal(rid.dt).isoformat() if rid else None
            warnings = []
            for label, displayed, value in (
                ("start", ref.displayed_start, start),
                ("end", ref.displayed_end, end),
            ):
                if displayed and isinstance(value, datetime):
                    try:
                        shown = datetime.strptime(
                            displayed, "%Y-%m-%d %I:%M %p"
                        ).replace(tzinfo=self.zone)
                        if shown != value:
                            warnings.append(
                                f"HTML/ICS {label} mismatch: HTML {shown.isoformat()}, ICS {value.isoformat()}"
                            )
                    except ValueError:
                        warnings.append(f"HTML {label} time could not be compared")
            if item.get("SUMMARY") is None:
                raise ValueError("Event title missing")
            title = str(item["SUMMARY"])
            status = {
                "CONFIRMED": "scheduled",
                "TENTATIVE": "tentative",
                "CANCELLED": "cancelled",
            }.get(str(item.get("STATUS", "")), "unknown")
            if "cancel" in title.casefold() and status != "cancelled":
                warnings.append(
                    "Title mentions cancellation without ICS CANCELLED status"
                )
            event = Event(
                source_id=self.config.id,
                uid=str(item["UID"]),
                recurrence_id=recurrence_id,
                title=title,
                source_url=ref.page_url,
                starts_at=start,
                ends_at=end,
                timezone=self.config.timezone,
                all_day=not isinstance(start, datetime),
                status=status,
                description=str(item.get("DESCRIPTION", "")) or ref.description,
                location=str(item.get("LOCATION", "")) or None,
                warnings=tuple(warnings),
            )
            envelope = Envelope(
                event=event,
                evidence_hashes=(ref.listing_hash, sha),
                parser_version=self.manifest.version,
                locator=f"VEVENT UID={event.uid}",
            )
            previous = result.get(event.external_id)
            if previous and previous.event != event and event.status != "cancelled":
                raise ValueError("Conflicting duplicate UID/recurrence identity")
            result[event.external_id] = envelope
        return list(result.values())
