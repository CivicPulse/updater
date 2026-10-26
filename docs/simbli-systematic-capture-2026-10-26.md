# Bibb school-board 2026 systematic capture

Status: **complete for all 50 currently listed public 2026 meetings**, verified October 26. Requested window: January 1–December 31, 2026,
America/New_York. This is a capture of currently available public material, not a
claim that the calendar year has finished.

## Source and discovery

The district's official Simbli tenant is 4013. The public listing and one
read-only archive page expose **50 2026 board, committee and work-session
meetings**. Individual school councils are excluded. The first two pages contain
100 rows across all types, against 1,015 all-time rows; the second page extends
into 2025 and establishes coverage of the requested start date.

Sources:

- [Official meeting listing](https://simbli.eboardsolutions.com/SB_Meetings/SB_MeetingListing.aspx?S=4013)
- [Official Board Brief archive](https://www.bcsdk12.net/departments/communications/the-board-brief-boe-newsletter)

The official listing script's `NeedData` function uses a POST to
`/Services/api/GetMeetingListing`. The implemented exception accepts only that
exact endpoint, anonymous tenant-4013 context, descending dates and 50-row pages.
It rejects caller-supplied filters, identities, additional fields and write
endpoints. Browser-initiated civic API POSTs remain blocked. Request/response
bytes and public session parameters stay in private local evidence.

## Collector improvements

- Archive pagination verifies counts, ordering and unique identities, and stops
  only after covering the start date or exhausting the archive. Missing or
  inconsistent pages cannot yield a complete backfill.
- Restricted/deleted agenda and minutes subtrees, and restricted content fields
  inside public items, are projected out before following their detail or
  attachment links. Projected documents have separate SHA-256 digests and clear
  warnings. Raw evidence is retained privately. Unknown visibility and
  contradictory item identity/publication flags still fail closed.
- Discovered meetings, page bytes and each valid downloaded resource are
  checkpointed. Resume verifies checksums before network access, preserves the
  original observation ordering and retrieves missing resources. Incomplete
  discovery cannot be relabelled complete by resuming. Invalid responses remain
  evidence but cannot poison a valid-resource checkpoint.
- `--systematic` explicitly permits 2,000 requests, 100 meetings, ten pages,
  5,000 resource candidates, 500 MB and 180 seconds per response, and 10 GB per
  run. Ordinary collection retains its existing request/download limits.
  One-second pacing, server deferrals, persisted cooldowns, five recovery targets
  per run and 45 browser attempts/hour remain enforced.
- Duration checks consume arriving chunks without waiting for a full buffer;
  an outer browser deadline remains authoritative. Numeric size diagnostics
  distinguish response and aggregate limits without disclosing session URLs.
- Legacy Office file signatures are validated correctly. Challenge probes can
  use retained response evidence without treating it as a successful checkpoint.
- Resume input errors provide safe operator guidance. Newly created state
  directories use owner-only permissions. Existing local capture state was also
  restricted to its owner.

A large file driving the download fixes was **Athletics Review Final 2026 2.pptx**,
301,597,639 bytes, attachment 2052724 to the August 20, 2026, 4 p.m. Committee
Meeting (137622). The resumed capture retrieved it successfully without changing
request pacing.

## Run ledger

- `758c603d-3280-40a9-aeed-0d79b7f3c449`: full-window discovery, 15 complete
  meeting bundles and 451 checkpointed resources before the old 30-second
  response duration limit stopped an attachment.
- `e8e6f0c8-4afb-48a4-9134-86a75f13eec9`: resumed the original snapshot with
  the bounded larger response window. An April 16 committee item exposed a
  restricted content field; the public-field projection fix was added with
  regression coverage while the other meetings continued. This pass completed
  49 meetings, withholding only that April committee bundle.
- `1c4c9e23-aa8e-4adc-b93a-8d5bbf1e2b5c`: resumed the missing bundle with the
  field-projection fix; **50 records, one new proposal, zero errors**.
- `cb382ff2-2fb3-418e-b1f2-5fa20247ab95`: final offline replay reproduced all
  **50 records with zero new proposals and zero errors**.

Raw run inputs and browser evidence remain under `.state/`, outside Git.
The directory `.state/captures/bibb-school-board-2026/` contains the inventory,
coverage manifest, normalized records, document manifests, supplementary materials
and verified ZIP export. All 50 listed identities match saved records; none remain
uncaptured. The public document set has **1,631 references / 1,629 unique document contents**:
50 agendas, 929 agenda-item models, 613 attachments and 39 published minutes.
Nine documents are labelled public projections. Eleven meetings have minutes
that are not publicly published. All document digests and sizes were verified.
The store has exactly **50 pending school-board review proposals**.

The export is organized into dated meeting folders with agenda, agenda-item,
minutes and attachment subfolders. It contains 1,681 entries and is
1,864,676,796 bytes. Export CRC verification passed. An additional check of all
4,078 visibility flags in exported JSON found no restricted values; no nonempty
security-token/connection-context values were present.

ZIP SHA-256:
`06cb5e81daaa5622acd9b61e78c2dce6811e6dbfc92030d8a41a0f4ca83c2c6a`

## Supplementary records

All **21 currently published 2026 Board Brief PDFs** were preserved and
checksum-verified, with searchable text copies. These are district summaries,
kept distinct from formal agendas/minutes. The January newsletter supplies
**12 approved regular meeting calendar dates**, including November 19 and
December 17; those dates have no posted meeting bundles in the captured Simbli
listing. Times were left unknown where the calendar provides dates only.

Unpublished minutes require later refresh. Videos, linked external resources,
OCR and separately extracted structured votes remain outside the adapter's
scope. Source agenda/minutes JSON is retained without interpreting votes.

## Validation and rollback

Collector commits: `368551e` (historical discovery, projections, checkpoints),
`53fdbab` (bounded large-document transfers and safe resume guidance), and
`3891caf` (restricted fields inside public agenda items). **164 tests passed**,
including the real browser fixtures. A separate check against the actual April
response confirmed its restricted field is absent from the public projection.
The raw restricted response hash is absent from all proposed document references.

Ruff, Bandit, Trivy HIGH/CRITICAL dependency/configuration scans and the secret
scan passed. Wheel and source-distribution builds passed; both were inspected to
confirm private state was excluded. Tests exercise collection/replay, mixed
public/private trees and fields, restricted attachment non-retrieval, archive
POST policy, checksum rejection, interrupted-download continuation, stale
observation protection, pinned transport and real browser fixtures. Live source
reads independently verified the archive request contract.

No Civic Pulse uploads, approvals, infrastructure changes, deployments or remote
Git writes occurred. Local proposals require review. Rollback means reverting
the collector commits while preserving the entire private state directory;
resume/replay of new-format runs requires a compatible collector. Do not delete
policy/cooldown state or source evidence as a recovery step.
