from datetime import date

import pytest
from conftest import FIXTURES, calendar, event

from civpulse_updater.adapters.bibb_democrats import BibbDemocrats
from civpulse_updater.models import Window


def test_real_listing_and_time_conflict(config, window):
    adapter = BibbDemocrats(config)
    refs, pages = adapter.discover(
        (FIXTURES / "bibb-listing.html").read_bytes(), config.url, "a" * 64
    )
    assert not pages
    assert len(refs) == 1
    records = adapter.parse(
        (FIXTURES / "bibb-event.ics").read_bytes(), refs[0], "b" * 64, window
    )
    record = records[0].event
    assert record.uid == "69580e12eee84455982fd4bd@squarespace.com"
    assert record.starts_at.isoformat() == "2026-11-21T16:00:00+00:00"
    assert len(record.warnings) == 2
    assert "HTML/ICS start mismatch" in record.warnings[0]
    assert "Light refreshments" in record.description


def test_rescheduling_preserves_identity(config, reference, window):
    adapter = BibbDemocrats(config)
    first = adapter.parse(calendar(event()), reference, "b" * 64, window)[0].event
    later = adapter.parse(
        calendar(event(start="20261026T140000Z")), reference, "c" * 64, window
    )[0].event
    assert first.external_id == later.external_id
    assert first.starts_at != later.starts_at


def test_all_day_exclusive_end_and_cancel(config, reference, window):
    data = calendar(
        "UID:all-day\r\nDTSTART;VALUE=DATE:20261025\r\nDTEND;VALUE=DATE:20261027\r\nSUMMARY:Cancelled picnic\r\nSTATUS:CANCELLED"
    )
    item = BibbDemocrats(config).parse(data, reference, "b" * 64, window)[0].event
    assert item.all_day and item.ends_at == date(2026, 10, 27)
    assert item.status == "cancelled"


def test_recurrence_dst_exclusion_and_reschedule(config, reference, window):
    data = calendar(
        "UID:series\r\nDTSTART;TZID=America/New_York:20261025T100000\r\nSUMMARY:Series\r\nRRULE:FREQ=WEEKLY;COUNT=4\r\nEXDATE;TZID=America/New_York:20261108T100000",
        "UID:series\r\nRECURRENCE-ID;TZID=America/New_York:20261101T100000\r\nDTSTART;TZID=America/New_York:20261102T100000\r\nSUMMARY:Moved",
    )
    items = BibbDemocrats(config).parse(data, reference, "b" * 64, window)
    assert len(items) == 3
    moved = next(item.event for item in items if item.event.title == "Moved")
    assert moved.recurrence_id == "2026-11-01T15:00:00+00:00"
    assert moved.starts_at.isoformat() == "2026-11-02T15:00:00+00:00"


def test_cancelled_recurrence_retained(config, reference, window):
    data = calendar(
        "UID:s\r\nDTSTART:20261025T140000Z\r\nSUMMARY:Series\r\nRRULE:FREQ=DAILY;COUNT=2",
        "UID:s\r\nRECURRENCE-ID:20261026T140000Z\r\nDTSTART:20261026T140000Z\r\nSUMMARY:Cancelled\r\nSTATUS:CANCELLED",
    )
    items = BibbDemocrats(config).parse(data, reference, "b" * 64, window)
    assert len(items) == 2
    assert sum(item.event.status == "cancelled" for item in items) == 1


@pytest.mark.parametrize(
    "data",
    [
        b"garbage",
        calendar(event().replace("UID:test\r\n", "")),
        calendar(event(extra="RRULE:FREQ=SECONDLY")),
        calendar("UID:ambiguous\r\nDTSTART:20261101T013000\r\nSUMMARY:Ambiguous"),
    ],
)
def test_invalid_calendars_quarantine(config, reference, window, data):
    with pytest.raises(ValueError):
        BibbDemocrats(config).parse(data, reference, "b" * 64, window)


def test_empty_listing_is_failure(config):
    with pytest.raises(ValueError):
        BibbDemocrats(config).discover(
            b"<html>maintenance</html>", config.url, "a" * 64
        )


def test_window_bounds():
    with pytest.raises(ValueError):
        Window(start=date(2020, 1, 1), end=date(2026, 1, 1))
