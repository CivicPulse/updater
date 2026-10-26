# Bibb Democrats fixtures

Captured October 25, 2026 from the organization's public official website:

- Listing: https://www.bibbdems.com/events
- ICS: https://www.bibbdems.com/events/monthly-meeting-macon-bibb-democrats-5?format=ical

`bibb-listing.html` contains only the first event article, with scripts/styles
removed. It retains the source's displayed 10 AM–noon times. `bibb-event.ics`
contains the source's original downloaded calendar bytes: 16:00–18:00 UTC on
November 21, corresponding to 11 AM–1 PM in America/New_York. This disagreement
is intentional regression evidence, not a corrected fixture.

Other calendar payloads in tests are synthetic, including recurrence exceptions,
cancellations, invalid dates, and rescheduling. No private data or credentials
are included. The fixtures are test evidence, not current event guidance.

`civicclerk-event.json` is a field-reduced public response from
`https://maconbibbcoga.api.civicclerk.com/v1/Events/2395`, retrieved 2026-10-25
America/New_York. It represents the October 6 commission meeting. Unused fields
were removed; retained values are unchanged. The corresponding test substitutes
small synthetic PDF and agenda bytes. Signed attachment URLs are not committed.
Other CivicClerk tests use synthetic records shaped like the observed API.

`simbli/` contains field-reduced public responses retrieved October 25, 2026
(America/New_York) from the Bibb School District's official Simbli tenant 4013:

- `listing.json`: the initial 50 meeting rows embedded in
  `https://simbli.eboardsolutions.com/SB_Meetings/SB_MeetingListing.aspx?S=4013`.
- `meeting.json`, `tree.json`, `minutes.json`: public APIs used by
  `https://simbli.eboardsolutions.com/SB_Meetings/ViewMeeting.aspx?S=4013&MID=138067`
  for the September 9, 2026, 11:00 AM millage rate hearing.

Only fields needed for parser fixtures are retained. No session token or complete
upstream page is committed. Fixture fetchers supply synthetic page parameters,
item-content models and binary attachments. These substitutions are explicit in
the tests; live collection verification is documented separately.
