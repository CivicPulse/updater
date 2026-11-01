# Georgia SOS registration collector validation — November 1, 2026

Scope: local review-only collection from both Qlik registration apps linked by
https://sos.ga.gov/election-data-hub. Existing turnout collection remains a separate
source. No website/API publication, credential provisioning or deployment occurred.
Unrelated pre-existing workspace edits were preserved.

## Implemented collection

`georgia-sos-registration` opens the anonymous public registration app
`b4b55ec0-57b4-4c2b-bbcf-602397bc6aa6` and monthly table app
`2977350b-191a-4615-a761-664579f15891`. All configured aggregate pages are fetched;
no individual voter IDs are returned. USER appears only in the fixed server-side
`Count(DISTINCT USER)` expression. Live projections cover county/status,
county/status/demographics/municipal precinct, and county/status/congressional,
House and Senate districts. The monthly source retains published measure
expressions and their July 2025 exception. Its pivot is read through a straight
session-local projection, preserving original and projected definitions.

A date window filters monthly snapshot labels and the live app's reload date in
America/New_York. It does not reconstruct past live data. Monthly history longer
than 367 days requires multiple existing CLI date windows. Map geometry and
individual voter records are outside this aggregate collector's scope.

Tokens never enter logs or stored evidence. Requests and responses, bootstrap
files, app IDs/reload timestamps, source definitions and checksums are retained.
Failures, short pages, wrong/additional selections, changed source measures and
reloads reject the combined dataset without proposing a partial registration
snapshot. The source is additive and can be disabled by not invoking it; existing
sources and historical records remain available. No state migration is needed.

## Live acceptance check

Command, with isolated local state under `/tmp/ga-registration-live-check-3`:

```sh
uv run --locked civpulse-updater --state-dir /tmp/ga-registration-live-check-3 collect georgia-sos-registration --from 2026-10-01 --to 2026-11-01
```

Run `726c4e26-f71b-4fdf-a8b9-8bd669fe1a15` completed with nine records, nine local
review proposals, and no errors:

| Snapshot | Table | Rows |
| --- | --- | ---: |
| November 1 | Live county/status | 794 |
| November 1 | Live demographics/municipal precinct | 85,904 |
| November 1 | Live congressional district | 1,420 |
| November 1 | Live House district | 2,366 |
| November 1 | Live Senate district | 1,609 |
| October 1 | Monthly demographics | 51,472 |
| October 1 | Monthly status | 318 |
| October 15 | Monthly demographics | 51,515 |
| October 15 | Monthly status | 318 |

The live app reload was `2026-11-01T05:18:14.395Z`; the monthly app reload was
`2026-10-17T23:56:56.114Z`. Dates are reported source labels/reload dates, not
assumed equivalent observation times. Saved evidence totaled 171,604,731 bytes,
including the combined transcript and individual RPC frames.

Bibb's live county aggregation returned 109,524 Active and 8,674 Inactive records.
Other statuses were retained separately. Monthly demographic subtotals exactly
matched all 318 county/status groups for both October snapshots (zero differences).

Offline replay of the live run succeeded twice, including after adding the
published-measure fingerprint checks: nine records, zero new proposals, no errors.
Live state is temporary validation output, not a production import.

## Regression and automated validation

Two observed live edge cases have regression coverage: Qlik can omit `qText`
without setting `qIsNull`; an absent-text group and an explicit-null group can
share the same displayed dimensions but have different Qlik element numbers.
The collector retains element metadata so it neither collapses nor rejects these
valid distinct groups. The source's active/inactive table is a pivot, rather than
a straight table; tests use projected official object definitions to exercise this.

- 300 non-browser tests passed; nine browser tests deselected.
- Full suite before the final additional measure regression: 298 passed, nine
  failed because configured Playwright browser executables were absent. A focused
  reproduction confirmed missing Chromium headless shell; these existing browser
  tests do not exercise the registration collector.
- Registration tests include local real TLS/WebSocket sessions for both apps,
  the CLI/review/replay path, corrections, multi-date pagination, null handling,
  source/measure drift, wrong selections, interrupted transcripts, reloads, HTTP
  failures and credential exclusion. No rendered UI is changed, so browser UI
  tests are not a registration acceptance layer.
- Repository Ruff lint passed. All changed Python files pass Ruff formatting;
  repository-wide formatting reports one pre-existing issue in
  `tests/test_simbli_browser.py`.
- Bandit over all `src` reported no issues. Source manifest validation passed.

These are developer checks and a bounded read-only live acceptance run, not an
independent security review or a production deployment.
