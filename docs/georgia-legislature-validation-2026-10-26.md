# Georgia legislature connector validation — October 26, 2026

The local `georgia-legislature` connector pins the official **2025–2026 Regular
Session, ID 1033**, separately from the 2026 Special Session (1034). It creates
review proposals in private updater state. Publication remains disabled; no hosted
schema, service or website was changed.

## Full capture

The full collection for January 1, 2025–December 31, 2026 completed successfully.
All **7,700 discovered references** and 160 supporting calendar responses were
captured. The resulting **10,727 unique records** comprise:

| Record type | Count |
| --- | ---: |
| Bills and resolutions | 5,480 |
| Votes | 1,870 |
| Member profiles | 247 |
| Derived seats | 236 |
| Committees | 74 |
| Calendar publications | 882 |
| Meeting schedules | 1,937 |
| Session | 1 |

Capture `a7295e67-332f-42ad-a1ea-2ce37e9f68c3` finished at
**2026-10-26 21:50:30 UTC**, with no errors or stale records. It resumed saved
downloads from `7e052408-6178-43e2-8c33-4840448fcc94`, following the original
`d8d7aee4-ab45-4503-9ee0-7e44662ee0f7` capture. The original partial capture
exposed the House attendance issue described below; interruption recovery also
preserved completed downloads during this collection.

Offline replay `abaa36f7-bc97-4302-954e-52ea40c85308`, using final adapter 1.0.5,
reproduced all **10,727 records with zero new proposals, zero stale records and
zero errors**. The final independent saved-evidence audit verified 7,813 evidence
hashes, stored payload hashes, and exact identity coverage for every record type:
no missing/extra identities or conflicting normalized records. All 198 schedules
with explicit cancellation prefixes have cancelled status.

The 1,870 vote records include **76 aggregate-only House attendance records** and
**1,794 individual roll calls**. An independent audit matched all **200,160 ballot
entries** to official yea/nay/excused/not-voting totals without discrepancies.
All 242 positive voter identities referenced by ballots resolve to the 247-profile
session roster. Calendar responses contained 1,079 rows that deduplicated to 882
identical publication records. All 236 distinct district identities were retained
despite member turnover. Schedule chunk boundaries were checked against saved
responses, including meetings on each inclusive end date where exposed.

The final local review queue contains **10,727 pending proposals** and 74 superseded
committee proposals from the earlier parser that lacked chamber information.
No proposals were approved or published.

Private evidence, checkpoints and review proposals are in `.state/`; supporting
capture reports are in `.state/captures/georgia-2025-2026/`. Preserve the entire
state directory for recovery and offline replay. These files are excluded from
Git and distribution packages.

## Findings incorporated into code

- The full two-year session requires an explicit `--full-session` window, bounded
  to 731 days and restricted to this adapter. Georgia's larger pagination and
  reference limits accommodate the verified 5,480-result bill search.
- House attendance roll calls intentionally have no individual detail view in the
  official client. Their detail endpoint returned HTTP 500. The connector now
  validates and reuses the original listing evidence for aggregate-only records,
  with an explicit warning. Senate attendance still retrieves individual detail.
- Repeated member ID zero entries named `VACANT` remain separate anonymous
  ballots. Positive member IDs must be unique. The official ballot codes 0–4 are
  preserved; undocumented codes are rejected.
- Committee detail omits its chamber. The connector retains and validates the
  chamber from the corresponding session listing.
- Explicit `CANCELED`/`Cancelled` schedule-title prefixes now produce cancelled
  status with a warning identifying that source basis. Optional null/absent
  meeting metadata is accepted; malformed metadata is rejected. Midnight source
  times remain warned date-only TBD entries, and ambiguous naive DST times fail.
- Complete listing feeds are parsed from their saved discovery bytes, avoiding a
  second download and changes between discovery and parsing.
- `--resume-latest` selects the newest matching usable checkpoint under the state
  lock, validates evidence checksums, and downloads only missing inputs. A newer
  completed capture starts a fresh observation; incomplete discovery also starts
  fresh instead of reviving an older snapshot. Explicit `--resume-run` remains
  available. Recovery is covered by a real subprocess interruption/restart test.

The official API and anonymous handshake were verified against the website's
published Angular client and live public responses. Tokens are held only in
memory and excluded from evidence and fixtures; one HTTP 401 triggers bounded
renewal. The only permitted POST is the official read-only, session-filtered bill
search; its exact request is retained as private evidence. Shared URL restrictions,
TLS/DNS pinning, pacing and request/byte budgets apply to transport operations.

## Automated validation

The current adapter is version **1.0.5**. All **197 tests passed**, including 22
Georgia tests, the existing browser cases, offline replay, pagination/identity
checks, token renewal/scoping, failed-download recovery, cancellation handling,
optional metadata, frozen discovery inputs and concurrent state locking.

Ruff lint and formatting, Bandit, and source-only Trivy secret, HIGH/CRITICAL
vulnerability and misconfiguration scans passed. The Python wheel and source
archive built successfully. Test browsers were supplied through
`PLAYWRIGHT_BROWSERS_PATH=/tmp/civpulse-playwright`. Scanner inputs exclude private
state, Git metadata and the virtual environment.

## Repeat and recover

```sh
uv run --locked civpulse-updater collect georgia-legislature --from 2025-01-01 --to 2026-12-31 --full-session --resume-latest
uv run --locked civpulse-updater replay RUN_ID
uv run --locked civpulse-updater changes list
```

Collection is paced at one request per second and can take hours. Interruption
preserves completed downloads; repeat the same command to recover. Missing/short
bill-search pages, changed totals, duplicate identities, wrong sessions, checksum
failures and unavailable required detail responses prevent a complete result.
Failures retain existing records and never create deletions.

## Coverage limits and rollback

Bills/resolutions, member profiles, committees and derived seats are whole-session
snapshots. Date windows select votes, available meeting schedules and calendar
metadata on listed legislative days. Seat identity does not establish current
occupancy or complete vacancy history. The schedule endpoint is not a guaranteed
historical archive, and interim calendar publications are not asserted complete.
PDF/version/agenda links are retained, but binaries, PDF text, video, transcripts,
geographic boundaries and separate study-committee detail pages are outside this
connector's scope. Source field names and enum codes remain available in structured
data; undocumented meanings are not inferred.

Rollback removes the new source, adapter, transport and additive integration
changes while retaining private state. Existing event hashes remain compatible.
No automatic collection schedule, deployment or public import was enabled.
