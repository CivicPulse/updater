# Georgia SOS turnout pipeline validation — October 26, 2026

Owner/scope: updater development, local review-only import of three public
aggregate turnout tables per election. No deployment, hosted migration, publishing,
scheduler or Civic Pulse record write was performed. This is a locally validated
increment; production deployment/acceptance is not claimed.

## Source discovery

The supplied SOS page links to
`https://d11khjvhu86vzz.cloudfront.net/DH.ELECTION2024/index.html`.
Its published `js/vars.js` pins app
`137c0a43-928b-427a-a267-eb55aa7fa8a9`; `../shared-config.js` identifies
`sos-ga-gov.us.qlikcloudgov.com`. The dashboard's Go Interactive code obtains an
anonymous token via `GET /qlik-oauth-create-user`. That endpoint returned **201**,
so the transport now permits reading a 201 response body only when explicitly
requested by this authentication call. Ordinary GET collection still requires 200.
No token response or bearer header is retained.

The official Qlik protocol reference is
[QIX JSON-RPC](https://www.qlik.dev/apis/json-rpc/). The connector reads existing
published aggregate table objects and applies session-local election selection.
A temporary CurrentSelections object verifies no additional fields are selected;
it disappears with session closure. No permanent source data or app object is
modified. Unexpected dimension definitions stop capture before reading table data.

## Bounded live validation

State was isolated under `/tmp/civpulse-ga-turnout-validation`, separate from the
operator's `.state`. The final live capture selected November 3, 2026 only:

```sh
civpulse-updater --state-dir /tmp/civpulse-ga-turnout-validation collect georgia-sos-turnout --from 2026-11-03 --to 2026-11-03
civpulse-updater --state-dir /tmp/civpulse-ga-turnout-validation replay 81ba1ffe-c505-4b37-a95b-474932aa79dc
```

Capture `81ba1ffe-c505-4b37-a95b-474932aa79dc` completed with three table records,
zero errors and zero additional proposals after an earlier successful capture of
the same tables. Replay `4a0c785c-eed2-4234-979f-765fb5815a07` also completed with
three records, zero errors and zero additional proposals. The final snapshot hash:
`7866c9bfebfb11e375b8ddd6ad088c7476684b313739b1659158b9ca13cfe275`.

| Captured source table | Rows | Observed aggregate |
| --- | ---: | ---: |
| Early in-person by county | 159 | 1,859,755 accepted ballots |
| Absentee by county | 159 | 90,904 accepted ballots |
| Turnout by county/ballot style | 318 | 1,950,659 turnout |

These are observation-time figures, not current facts or certified results. The
first two tables displayed `*Data as of : 10/26/2026 6:34:58 PM`; the turnout-detail
table supplied no footnote, which remains empty. Its reload timestamp is retained
in evidence rather than substituted for the absent source as-of label. The
observed early plus absentee totals equal the turnout-detail sum for this sample;
no such reconciliation is assumed for other elections or future source formulas.

Earlier failed validation attempts exposed the HTTP 201 body requirement; no
proposals were created by those failures. Complete transcripts replay without
network access; incomplete captures require a new collection.

## Acceptance and recovery

Offline tests cover the CLI/local review workflow, correction/idempotency,
no-network replay, actual local TLS/WebSocket transport, election selection,
pagination and failure boundaries. Browser testing is inapplicable to this
headless collector. There is no new SQL schema or hosted authorization surface;
records use the existing JSON state/review tables. Ruff, Bandit, the non-browser
suite, dependency/misconfiguration/secret scans and wheel/sdist build are the
local validation gates; exact executed outcomes are reported in the task.

Executed outcomes: **213 non-browser tests passed**, eight browser tests excluded;
Ruff lint/format, Bandit, Trivy HIGH/CRITICAL dependency and misconfiguration
checks, and wheel/sdist build passed. The source secret scan passed with `.venv`,
`.state` and `.git` excluded. An earlier whole-workspace secret scan, which included
the existing 21 GB private state directory, reported findings. Those raw detections
are not published here; no claim that the private state is secret-free is made.
No container/image build or production validation was performed.

Regression risks are upstream app/object/column changes, unexpected selection
behavior and source formula changes. The connector stops on known schema changes,
extra filters, short pages, mid-capture reloads and invalid values. Source formulas
and display values remain available for review. Public zero suppression prevents
an assertion that missing counties are zero or that every source election is listed.

Dependencies: Python 3.13, the locked `websockets` 15 client, existing pinned HTTP
transport, evidence store and local proposal state. No credentials/configuration
are needed beyond the built-in public source manifest.

Rollback: stop collecting this source and revert the connector/manifest changes
to the previous known-good repository revision; preserve the entire state folder.
No migration or remote data reversal is required. Existing non-turnout adapters
remain independently usable. Stored turnout captures require this parser version
(or a compatible later version) for replay; preserve that environment and raw
evidence before reverting. A future deployment must validate its exact artifact
and configuration separately before claiming production acceptance.
