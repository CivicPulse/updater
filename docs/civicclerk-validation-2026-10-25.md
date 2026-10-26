# Macon-Bibb CivicClerk adapter validation

Implemented locally on October 25, 2026 (America/New_York). Review-only;
no Civic Pulse API requests, approvals, deployment, or scheduler changes.

## Source verification

- Public categories endpoint: https://maconbibbcoga.api.civicclerk.com/v1/EventCategories
  identifies category 26 as Board of Commissioners. Some Pre-Commission meetings
  are assigned to category 26 by the county; this adapter includes them.
- Public event: https://maconbibbcoga.api.civicclerk.com/v1/Events/2395
- Structured agenda: https://maconbibbcoga.api.civicclerk.com/v1/Meetings/2068
- Portal: https://maconbibbcoga.portal.civicclerk.com/event/2395
- The published commission packet's cover states October 6, 2026, 6:00 PM.
  This corroborates interpreting `eventDate=2026-10-06T18:00:00Z` as local wall
  time, not UTC. Text extraction was used only for this manual verification;
  the adapter stores original PDFs without OCR or text extraction.
- Endpoint research also consulted https://github.com/chapinb/libmuni . No library
  dependency or transport was adopted; existing pinned-DNS GET transport is used.

## Live local evidence

Window: October 6, 2026 only. Successful collection run:
`bce27cf1-d06c-4ad1-9c41-888ae0deea4c`.

| Meeting | Time (Eastern) | PDFs | Total PDF bytes |
| --- | --- | ---: | ---: |
| Pre-Commission | 4:30 PM | 27 | 156,650,625 |
| Board of Commissioners | 6:00 PM | 16 | 127,642,601 |

Two pending review proposals; no collection errors. Documents include published
packets and agenda-item attachments. Minutes support is tested with fixtures;
these live events did not supply a separate published meeting-minutes file.
Earlier meeting minutes attached for approval remain attachments, not minutes
of the October 6 meeting.

Offline replay `5e025d9a-1462-4963-aa1b-87941a7261ae` completed with two records
and zero new proposals. Raw evidence and state remain under gitignored `.state`.
The commission packet checksum is
`7e4a9a192907e9159a7c9e817bb82401cda1b8f4def26317e3a91c42e5487041`.

Initial 10 MB and 50 MB attempts correctly failed closed without creating
proposals. The observed commission packet is 64,069,578 bytes and pre-commission
packet 76,198,955 bytes. Final source limits are 100 MB per response and 500 MB
aggregate downloaded bytes per run; retry bytes count against the aggregate.

## Checks and limits

59 tests passed, including the existing Bibb Democrats suite. Coverage includes
pipeline/state integration, offline replay, file revisions, signed URL rotation,
failed download preservation, public scope, source identity, nested attachments,
pagination bounds, malformed payloads, PDF validation, DST ambiguity, evidence
tampering and aggregate download budget. Existing real TLS transport tests pass.
Ruff formatting/lint passed; Bandit found no issues; Trivy found no high/critical
locked dependency vulnerabilities or repository secrets. Wheel and sdist built.
The existing container was not rebuilt for this adapter change.

No browser UI or database schema was changed. No production acceptance is
claimed. Upstream API compatibility, oversized packets, unpublished documents,
external-link attachments, votes and historic BoardDocs remain limitations.
Rollback: remove/disable the new source configuration and retain local state and
all evidence; existing Bibb Democrats collection remains available. There is no
remote data to undo.
