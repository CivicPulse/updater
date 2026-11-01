# Georgia Qlik statistics summaries — November 1, 2026

The requested smaller profile is now the default for `capture-qlik`. The full
archive download remains stopped. Existing archive files/checkpoints were
preserved; the normal command no longer resumes that exporter.

```sh
uv run --locked civpulse-updater capture-qlik all
```

## Retained and excluded data

Retained: latest active/inactive county registration totals; separate current
race, gender and age summaries; populated municipal-precinct aggregates; county/
status registration history at every source snapshot date; and the current
election's county early-voting, absentee and ballot-style turnout totals.

Excluded: raw voter/association tables, raw historical model tables, exhaustive
field-value dumps, map geometry, chart/map inventories, native pivot/stack chart
exports and repeated visual variants. Qlik computes numerical aggregates before
transfer. Individual USER identifiers are counted inside Qlik, never returned as
data dimensions. Only the known published statistical table definitions needed
to verify source formulas are read; visual assets are not downloaded.

The original date-window `collect` commands remain usable, but new registration
captures use the compact profile. Schema-1 aggregate snapshots still replay with
their original reader. New summary snapshots record their scope explicitly.
Registration-history/current-election flags also participate in resume identity,
preventing checkpoints from crossing different collection scopes.

The bundle is rebuilt by parsing the exact successful runs' saved evidence, not
by exporting every record ever saved in the state database. Legacy broad tables
therefore cannot leak into the new output. Failure of either requested source
retains the last successful bundle. Source timestamps, formulas, omissions and
warnings accompany the statistics; no publication capability was added.

## History query and regression fixes

The monthly app enforces a single selected AsOfDate, even after ClearAll. The
final history reader uses a session-local cube grouped by AsOfDate, County and
Voter Status with `SUM({1} VoterCount)`. That standard Qlik set expression ignores
the widget's selected date while preserving the public user's data access. No
source properties or persistent selection rules are changed. Pagination captures
all grouped rows, and required-date coverage is checked before reporting success.
The reader fails closed if it accidentally receives only the default selected date.

The source measure's July 15, 2025 exception is retained: if that date is exposed,
its counts come from July 1, 2025. Synthetic tests cover both full-history and
windowed correction behavior. July 15, 2025 is absent from the current 56-date
source list, so no correction was applied in this live capture. Date-window
collections filter the summarized output after the small grouped history query;
they still never fetch historical model rows.

Initial live attempts with the prior date-by-date reader failed during a data
request, and an early bulk query inherited the mandatory UI date filter. Neither
was reported complete or exported as a successful bundle. The final grouped
reader and single-date-filter regression tests address these paths.

## Live acceptance results

Isolated state:
`.state/qlik-summary-test-2026-11-01/`.

Final collection completed both sources without errors:

- Registration run: `1831dc78-a8be-4f5c-9e91-e01063c2ce71` — 61 datasets.
- Turnout run: `e119fc02-232f-45d1-931b-9bcd4f2fb26f` — three datasets for
  November 3, 2026.
- Combined output: **64 datasets, 26,984 numerical rows, 5,375,487 bytes**.
- Historical registration: **56 dates, 17,808 county/status rows**, spanning
  March 1, 2024 through October 15, 2026.
- Successful-run evidence: **81 RPC requests**, 15,366,751 response bytes,
  including bootstrap and protocol/provenance overhead.

Current registration row counts: county/status 318; race 1,885; gender 907;
age 5,426; populated municipal precinct 4. The municipal field is exceptionally
sparse: these four source groups are not complete county election-precinct data.
Missing precinct groups are omitted with a recorded omission count and warning;
county totals are the usable statewide baseline. Demographic tables overlap and
must not be added together.

Request auditing confirmed there were no GetAllInfos, GetTablesAndKeys,
GetTableData, native pivot or stacked-chart data calls. Only the known monthly
statistical table, election filter and three county turnout tables were opened;
no chart/map objects, geometry files or visual asset URLs were fetched.

Both final runs replayed offline successfully with **zero new proposals**.
`capture-result.json`, `capture.log`, `validation.json`, immutable evidence and
`qlik-statistics-all.json` are retained in the isolated state directory.

## Automated validation

Tests cover mandatory single-date selection, complete historical date coverage,
server-side grouped queries, published correction logic, forbidden raw/visual
requests, current-election selection, scope-separated resume identity, exclusion
of stale legacy tables, combined-source failure preserving the last bundle, CLI
routing, legacy replay and real TLS/WebSocket collection. Browser UI tests do not
apply to this data-only change. Existing browser tests still require unavailable
Playwright executables; their files were not changed for this work.

This is local collection/replay validation, not a production publication.
