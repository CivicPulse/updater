# Full Georgia SOS Qlik archive validation — November 1, 2026

**Superseded default:** the user subsequently chose numerical summaries.
`capture-qlik` now uses the [compact profile](georgia-qlik-summary-validation-2026-11-01.md).
This document records the earlier archive implementation and validation; it is
not the current download workflow. Existing archive evidence remains preserved.

This additive archive mode addresses the expanded request for every chart, field
and underlying data table on both SOS dashboard pages. It is separate from the
existing small review-table collectors. No production publication or app save
was performed, and unrelated workspace changes were preserved.

## Operation and scope

```sh
uv run --locked civpulse-updater capture-qlik all
uv run --locked civpulse-updater verify-qlik SNAPSHOT_ID
```

`all` means turnout plus the registration page's live and monthly apps. The
archive uses pinned public bootstrap/app/tenant identities and anonymous public
sessions. It discovers actual child objects recursively, rather than hardcoding
the three turnout tables or selected registration dimensions. It retains all
accessible object properties/layouts, master items, variables, bookmarks and
story/slide definitions; field metadata and all field symbols; raw tables with
all original rows/columns and synthetic key/association metadata; and chart/list
pages, including nested map/undo layers, native pivots/stacks and explicit
straight projections of pivot/stack/suppressed calculation branches.

Chart contexts cover the unfiltered state, every published election date or
monthly snapshot date, and the finite literal settings/simple numeric toggle
found in source chart buttons. They do not attempt the infinite cross product
of arbitrary selections and expressions. The underlying associative data and
chart expressions are retained for other analyses. External basemap/extension
software and tenant-private/denied data are outside the public export scope.
Unsupported or inaccessible data remains an explicit outstanding issue.

Opaque internal identifiers and high-cardinality model fields are included in
this local archive. Authentication handshakes, tokens and authorization headers
are excluded. Persistent mutation, reload/save, object creation in the source app
and arbitrary RPC invocation are unavailable. Only anonymous session objects,
selections and chart-variable values are changed. Control values are verified
against variable layouts and restored; closing the unsaved session also discards
session overrides after failure.

Original successful RPC bytes and bootstrap files are SHA-256 addressed. Page
indexes reuse wire response files instead of copying large results. An atomic
SQLite page/counter transaction checkpoints each chunk. Manifests expose exact
page coverage, original/projection definitions, source reload identity, hashes
and issues. Completed contexts/pages are checksum-verified before being skipped.
Reloads have separate snapshot IDs. Source definition drift without a reload
stops resume. No partial archive can be labeled complete, and offline verification
reports byte integrity separately from completeness. No raw data enters CMS
review proposals or publication.

## Live inventories

The bounded `capture-qlik all --inventory-only` invocation completed for all apps
with no outstanding issues: 906 requests and 5,345,796 received bytes.

| Public app | Objects including variables | Fields including hidden/system | Tables | Model rows |
| --- | ---: | ---: | ---: | ---: |
| Turnout | 131 | 62 | 7 | 21,237,032 |
| Live registration | 73 | 39 | 6 | 29,503,966 |
| Monthly registration | 42 | 13 | 1 | 9,332,061 |
| Total | 246 | 114 | 14 | 60,073,059 |

These are model rows, not registered voter totals. Counts include historical,
association and repeated source rows. The inventory is under the isolated local
state `/tmp/ga-qlik-final-inventory/qlik-archive`; its status is explicitly
`inventory-only`. Offline CLI verification of the turnout inventory succeeded
with `integrity: verified` and `complete: false`.

## Live data checks and regressions

A separate bounded live page capture exercised native straight, pivot and stacked
charts, filter lists, nested map geometry, conditional map-layer projections,
field values and small underlying model tables in all three apps. It also paged
the full 51,515-row monthly demographics chart. It completed all 49 selected
items (58,329 mixed chart/field/model/metadata rows) in 154 requests, receiving
32,077,142 bytes. Each app retained only the intentional
`bounded-live-smoke-not-full-archive` issue; no chart/page errors remained.

A separate controls check exercised both values of turnout's Election Day flag
and all three registration map types, including the conditional map layer. All
82 selected items completed; 156 requests received 58,030,178 bytes. These checks
stay labeled partial because they deliberately sample the full inventory.

Observed behaviors now have regression tests:

- Stacked chart `qSize.qcy` counts leaf rows, but native page rectangles count outer
  nodes. For example, 318 registration leaf rows produce 159 county nodes. The
  reader follows the returned outer-node count and continuation, retaining the
  original source size and a separate straight projection.
- Qlik `SetNumValue` changes the session variable value without changing its
  `qDefinition`. Controls are verified with `GetLayout.qNum/qText`, rather than
  falsely rejecting the change by checking its immutable definition.
- Migrated containers contain stale historical `refId` strings. Actual objects
  are enumerated using `GetAllInfos` and recursive `GetChildInfos`; stale property
  references remain preserved as metadata instead of invented accessible objects.

Late-offset reads also succeeded for the last two rows of the largest table in
all three apps: turnout `Snapshot` at offset 14,741,162; live registration
`DVB_tmp` at offset 16,698,849; and monthly `SnapshotVoters` at offset 9,332,059.
These checks establish that the public table API is not limited to the initial
preview rows. Fresh-session monthly inventory fingerprints matched the earlier
inventory, validating same-generation resume against the live source. Offline
verification of all three bounded page captures also passed, while correctly
reporting `complete: false` for the intentional partial scope.

**The full 60-million-row download was not completed during validation.** The
implementation is tested against a complete disposable small app, actual live
inventories and representative live pages. Completion of a large real archive
must be established by its manifest and `verify-qlik`, not inferred from these
checks. The command defaults to 10,000 requests and 10 GB received per invocation;
rerunning resumes within the same source generation. Source pacing remains in
force. A full export will require substantial disk space and runtime.

## Automated checks

- 317 non-browser tests passed; nine existing browser tests were deselected because
  their configured Playwright executables are absent (previously reproduced).
- Seventeen archive tests cover complete capture, nested objects/maps, master items,
  variables, hidden fields, all chart modes, date contexts, controls, raw-table
  pagination, resume/skip behavior, checksum corruption, truncation, inaccessible
  objects, reloads, explicit inventory-only status and CLI dispatch.
- A disposable real TLS/WebSocket test exercises the new transport, DNS/TLS
  pinning, actual wire bytes, both bootstrap configurations, field/table/chart
  capture and offline verification. It asserts credential exclusion from evidence
  and logs. No UI changed, so rendered browser QA is not an acceptance layer.
- Ruff lint/changed-file formatting and Bandit pass. The repository-wide formatter
  still reports the pre-existing `tests/test_simbli_browser.py` formatting issue;
  that unrelated work was not modified.

Primary protocol references used during implementation:
[GetTableData](https://help.qlik.com/en-US/sense-developer/May2026/Subsystems/EngineJSONAPI/Content/service-doc-gettabledata.htm),
[GetTablesAndKeys](https://help.qlik.com/en-US/sense-developer/May2026/Subsystems/EngineJSONAPI/Content/service-doc-gettablesandkeys.htm),
[Qlik engine object API](https://qlik.dev/apis/json-rpc/qix/genericobject/), and
[variable list definition](https://qlik.dev/apis/javascript/sense-client-objects/).
