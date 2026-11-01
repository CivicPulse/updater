# Macon Water Authority importer validation — October 27, 2026

Owner: updater implementation. Scope: local review-only meeting import module;
no deployment, publishing, database connection or remote write.

The official publications page links to
https://macon-water.community.diligentoneplatform.com/Portal/ rather than the
older BoardDocs search result. The portal's JavaScript uses public GET requests
under `/Services/MeetingsService.svc/meetings`. Calendar type options identify
board 18, committees 19/20/23/24/25/26/27 and mixed imported archive 17.
Subsidiary/alliance types 21/22/28 are excluded. The calendar lists unpublished
agenda schedules publicly; the module retains them with a warning and does not
request their document details.

Live checks used the normal CLI and pinned-DNS/TLS HTTP transport, with a separate
state directory `/tmp/civpulse-mwa-validation-20261027`. Source validation passed.
A same-day initial query returned zero records; bounded endpoint comparisons
confirmed identical from/to parameters return an empty array. The importer now
requests through the following day and filters inclusively. A pipeline regression
test reproduces this endpoint behavior.

The corrected October 8, 2026 collection completed as run
`9ff87e0e-0db2-4b73-8467-08447083e53e`: one meeting, one public agenda HTML document,
ten PDF attachments, 3,465,186 document bytes, start `2026-10-08T16:00:00-04:00`,
no collection errors. Entries marked private were excluded; no public standalone
minutes document was available in this sample. Offline replay completed as
`891aa6b7-89ce-469c-8fe5-127a7da26040`, one record and zero additional proposals.
These are local observations, not published Civic Pulse content.

Validation: 13 module tests pass; 226 repository non-browser tests pass. Ruff
lint and format checks pass for the repository. Bandit reports no issues in the
new adapter. The full suite initially reported 225 passing tests and eight
Simbli browser failures (before adding the single-day regression). An isolated
failure confirms Playwright's expected Chromium executable is absent; optional
browser tests remain unverified in this environment. No dependencies changed.

Tests cover collection and offline replay, unchanged/revised data, failed required
downloads retaining prior records, checksum-addressed materialized HTML, private
link exclusion, source scope, schedules without agendas, rescheduling, public
minutes roles, cancellation, malformed input, identity conflicts, download limits,
HTML responses masquerading as PDFs, DST ambiguity and tenant restrictions.
The existing transport suite covers DNS/TLS/redirect boundaries; this module adds
no transport implementation. Browser and hosted database tests do not apply to
this HTTP-only local importer.

Risks and limits: undocumented upstream schema, incomplete upstream history,
strict PDF-only attachments, no full DOCX/packet exports, no external attachments,
no OCR, no BoardDocs archive or member/election import. Raw response JSON may
contain metadata marked private, and remains private local evidence. Meetings
with incomplete dependencies cannot produce proposals. Absence never deletes.

Rollback: remove/disable `sources/macon-water-authority.toml` to stop new
collections. Registration and adapter removal affect only this new source;
retain the state/evidence directory for audit and replay with the original parser.
No migration or production rollback is required. Existing unrelated workspace
changes were preserved; deployment and activation were not performed.

## Full 2026 collection

The user subsequently requested January 1–December 31, 2026. The shared state
lock was held by an active Bibb school-board collection, so the collection used
the separate `.state/captures/macon-water-2026` directory without interrupting it.
Run `89cec4bf-85b5-4ddf-a4f4-1576ebf36240` downloaded all dependencies for 15
in-scope meetings and initially proposed 11; four meetings were withheld because
public `/document/` attachments served official HTML minutes rather than PDFs.

Parser 1.0.1 accepts those HTML attachments only with the observed exact
`iCompass-Open-Document` metadata marker, an Aspose.Words generator, a document
title ending ` - Html` and a nonempty HTML body. Generic/login/challenge HTML
remains rejected. Attachment bytes and checksums are retained without execution,
with the `Attachment` role; prior-meeting minutes are not relabeled as current
meeting minutes. This supersedes the PDF-only attachment limitation above.
A regression test covers collection, saved bytes and replay of this format;
the existing generic HTML rejection test remains a valid negative control.

Offline replay `2ddcf9bd-2bf3-4ac7-8d32-ddcb7ecd09fe` completed with all 15
records, four additional proposals and no errors. The state now contains 15
meeting records and 117 document references; five November schedules have
unpublished agendas and warnings. There are 15 review proposals in total.
This captures currently exposed portal records, including future schedules;
it does not guarantee every 2026 meeting exists in the upstream listing.
Nothing was published. All 14 module tests and 229 repository non-browser tests
pass (nine browser tests deselected); changed Python files pass Ruff.

To inspect the proposals:

```sh
.venv/bin/civpulse-updater --state-dir .state/captures/macon-water-2026 changes list
```

## Reviewed corrections and Civic Pulse reconciliation

Subsequent user review corrected January 8, February 5 and March 5 to their
verified scheduled board start of 4 PM Eastern, preserving the original
proposals as superseded revisions. The January official event notice and the
February/March packet covers distinguish 2 PM committee sessions from the 4 PM
board meeting, which can start later if committees run over. November entry 281
was retained and entry 282 rejected as a probable duplicate. The user approved
the remaining 14 proposals with the schedule and correction warnings acknowledged.

These were manual review decisions, not automatic adapter rules. Version 1.0.1
still parses blank-time imported calendar entries as the supplied midnight
`MeetingDateTime`; it does not automatically recover packet times or suppress
entry 282. Verify these cases before approving future fresh collections.

At explicit user direction, authenticated Civic Pulse content tools reconciled
all 14 approved proposals to already existing public event UUIDs. Existing
calendar times, titles, locations and editorial content were preserved. Reviewed
warnings and 117 official document references were added to meeting notes and
event descriptions. No duplicate events, binary uploads or independent public
archive copies were created. Original files remain hosted by the authority.
The updater's built-in publisher remains disabled; local review statuses remain
approved, with reconciliation receipts stored privately beside the capture.

API readback verified all 14 updates. Public browser checks of the January viewer
on `www.civpulse.org` and `new.civpulse.org` still displayed the earlier description
and omitted the new notes. Public website visibility of the new material was
therefore not verified; a frontend refresh/deployment remains outstanding. This
is distinct from the successfully verified live content-record updates.
