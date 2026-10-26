from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from civpulse_updater.models import Evidence, Reference, SourceConfig, Window, digest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def config():
    return SourceConfig(
        id="bibb-democrats",
        url="https://www.bibbdems.com/events",
        allowed_hosts=("www.bibbdems.com",),
    )


@pytest.fixture
def window():
    return Window(start=date(2026, 1, 1), end=date(2026, 12, 31))


@pytest.fixture
def reference():
    return Reference(
        url="https://www.bibbdems.com/event?format=ical",
        page_url="https://www.bibbdems.com/event",
        listing_hash="a" * 64,
    )


def calendar(*events):
    return (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n"
        + "\r\n".join("BEGIN:VEVENT\r\n" + event + "\r\nEND:VEVENT" for event in events)
        + "\r\nEND:VCALENDAR\r\n"
    ).encode()


def event(uid="test", start="20261025T140000Z", extra=""):
    return f"UID:{uid}\r\nDTSTART:{start}\r\nSUMMARY:Meeting\r\n{extra}".rstrip()


class FixtureFetcher:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []

    def get(self, url):
        self.calls.append(url)
        if "format=ical" in url:
            if self.fail:
                raise ValueError("download failed")
            body = (FIXTURES / "bibb-event.ics").read_bytes()
            media_type = "text/calendar"
        else:
            body = (FIXTURES / "bibb-listing.html").read_bytes()
            media_type = "text/html"
        return body, Evidence(
            sha256=digest(body),
            url=url,
            media_type=media_type,
            size=len(body),
            fetched_at=datetime.now(UTC),
        )
