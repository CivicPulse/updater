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

`simbli/archive-context.json` is synthetic anonymous grid context shaped like the
public October 26 listing. Its token and connection values are placeholders.
Pagination tests generate a second historical page and verify the pinned,
read-only POST contract, coverage, duplicate detection and offline replay. No
real session context is stored in the fixture.

## Georgia General Assembly

`georgia/` contains public official JSON sampled October 26, 2026 from
`https://www.legis.ga.gov/api/`. List fixtures retain one row (the configured
session for `sessions.json`); detail responses retain their source fields.
Whitespace was reformatted. No anonymous token response, bearer header or request
key is included. The fixtures are reference samples, not a complete session.

| Fixture | Official endpoint relative to `/api/` |
| --- | --- |
| sessions | `sessions` (session 1033) |
| members | `members/list/1033` |
| member | `members/detail/5025?session=1033&chamber=1` |
| committees | `committees/List/1033` |
| committee | `committees/details/87/1033` |
| bill | `legislation/detail/69281` |
| votes | `Vote/list/1/1033` |
| vote | `Vote/detail/26581` |
| days | `sessions/days/1033` |
| calendars | `Document/calendars?sessionId=1033&chamber=1&date=2026-04-02` |
| meetings | `meetings?startDate=Mon%20Oct%2026%202026&endDate=Mon%20Nov%2002%202026` |

Search pagination responses, alternate identities and failed downloads in tests
are synthetic. The end-to-end fixture deliberately repeats one meeting across
schedule chunks to exercise deduplication. Real House roll-call data contains
four distinct anonymous vacant seats with the same zero member ID; the parser
preserves those rows while rejecting duplicate nonzero member identities.

The `senate-members`/`senate-member` pair contains the first Senate member from
the same session listing and its chamber-aware detail request. The
`senate-votes`/`senate-vote` pair contains the first Senate roll call from
`Vote/list/2/1033` and its corresponding `Vote/detail/ID`. These were also sampled
October 26 with the connector's pinned transport; no tokens are included.

`georgia/house-attendance.json` is the public listing row for House vote 26533,
April 2, 2026. Its detail endpoint returned HTTP 500 during the full session
capture. The official website's `rollCallCheck` deliberately skips House
attendance detail requests and displays listing totals; the regression test
reproduces the failed request, then resumes with original listing bytes and an
explicit aggregate-only warning. Senate attendance detail remains supported.
The official client also explicitly defines ballot code 4 as `Unknown`; tests
preserve that code while rejecting unrecognized values.

`georgia/cancelled-meeting.json` retains the public January 21, 2025 Joint
Appropriations schedule row from the full capture. Its explicit `CANCELED` title
prefix exercises cancelled status and source-based review warnings; synthetic
variants exercise optional metadata and reject malformed metadata.

### Macon Water Authority

`macon-water/listing.json` is the official Diligent Community public meeting
listing for October 2026, retrieved October 27, 2026 from
`https://macon-water.community.diligentoneplatform.com/Services/MeetingsService.svc/meetings?from=2026-10-01&to=2026-10-31`.
`macon-water/documents.json` is meeting 272's `meetingDocuments` response, with
all entries not explicitly public removed. The public agenda HTML and attachment
links are retained. PDF bytes in offline tests are synthetic; private-document
and failure cases are synthetic mutations. Tests never access the live portal.
