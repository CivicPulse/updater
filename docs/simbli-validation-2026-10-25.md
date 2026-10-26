# Bibb school-board adapter validation

Implemented locally October 25, 2026, America/New_York. Review-only; no Civic
Pulse writes, local approvals, scheduler changes or deployment.

## Verified source and scope

The [district's Board Brief page](https://www.bcsdk12.net/departments/communications/the-board-brief-boe-newsletter)
links to [Simbli tenant 4013](https://simbli.eboardsolutions.com/Index.aspx?S=4013).
The adapter reads the [public meeting listing](https://simbli.eboardsolutions.com/SB_Meetings/SB_MeetingListing.aspx?S=4013)
and the same public GET APIs used by its meeting pages. Browser inspection was
used for research only; no browser library was added to the updater. Public
JavaScript was inspected as source, never evaluated by the adapter.

Included types: Board Meeting, Committee Meeting and Work Session. Individual
school councils and other types are excluded. The initial listing has 50 rows
across all types. A window at or before the oldest row is rejected if more rows
exist; older POST/session-based pagination is not implemented. The captured
listing spanned March 5, 2026 through April 20, 2027 and reported 1,015 records.
This date range is an observation, not a permanent configuration limit.

Agendas are saved as structured JSON: an agenda tree and item-content models.
Minutes are saved as the public JSON document with HTML text. Attachments are
saved as original binary bytes; PDF, modern/legacy Office, PNG and JPEG formats
are supported. External hyperlinks, videos, OCR, linked policy libraries and
normalized votes are not collected by this version.

The public listing marks minutes `PUBLISHED`, `UNPUBLISHED` or `AUTHORISED`.
Only `PUBLISHED` permits minutes retrieval. An initial live attempt failed closed
on `AUTHORISED`; the implementation now treats it as pending, matching the public
listing's link behavior. A regression test covers this state and subsequent
publication. The detail API's `IsMinutesPublished` flag was false even for a
meeting with a public published-minutes link, so the listing controls retrieval.

## Live collection and replay

Window: September 9, 2026. Successful run:
`e8dc3b8d-fe45-4a48-8c84-8ca82ce4b916` — three records, three pending proposals,
no errors.

| Meeting | Eastern start | Agenda JSON files | Minutes JSON | Binary attachments |
| --- | --- | ---: | ---: | ---: |
| Millage Rate Public Hearing #2 | 11:00 AM | 6 | 1 | 1 |
| Millage Rate Public Hearing #3 | 6:00 PM | 6 | 0 | 1 |
| Called Board Meeting — Final Adoption of Millage Rate | 6:30 PM | 8 | 0 | 2 |

Twenty-five documents totaling 7,416,849 bytes. The two meetings without published
minutes carry review warnings. Evidence and proposals remain under gitignored
`.state`; original API pages and request metadata contain public-session values
and must remain private. Normalized proposals omit those session parameters.

Repeat collection `19903d79-34fc-4d01-9a71-d6c5e23b1716` was partial: one unchanged
meeting, zero proposals, and two meetings withheld because attachment endpoints
returned HTML anti-bot pages instead of Office files. Existing records were
preserved. This demonstrates intermittent source availability, not a fully
reliable unattended download path. The adapter does not bypass challenges or
mistake their HTML for documents; retry collection later.

Offline replay `54d9e700-d0e8-4e57-a0a4-aeda4e4a68be` of the complete run returned
three records and zero proposals, with one stale observation skipped after the
repeat live collection. It used saved evidence only. A concurrent replay attempt
was correctly rejected by the existing process lock before this successful run.

## Validation and remaining limits

- 78 tests passed, including all existing adapters and real TLS transport tests.
- New tests exercise public scope, initial-listing coverage, publication delay,
  schedule-only meetings, session rotation, content revisions, replay/tampering,
  wrong district/meeting identity, private item visibility, malformed responses,
  byte/resource failure behavior, HTML challenges, Office attachment preservation,
  unsupported formats and DST validation.
- Ruff format/lint passed. Bandit reported no issues. Trivy reported no
  high/critical locked dependency vulnerabilities or repository secret findings.
- Wheel and sdist built. No dependencies or database migrations were added.
- Container image was not rebuilt or deployed for this adapter change.

The generic pipeline gained a provenance-aware `resources_for(content, reference,
saved_resources)` hook so publication metadata from the saved listing can govern
dependent reads. Existing adapters retain their original resource hook.

Rollback: remove the `bibb-school-board` source configuration or revert this
adapter and registration, retaining state/evidence. There is no remote content
to undo. Broad backfills and uninterrupted automated access remain future work.
