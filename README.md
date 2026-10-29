# Civic Pulse updater

An independent Python collector for official civic data. Version 0.1 collects
configured civic events, official commission/school-board meetings, and Georgia
legislative records and unofficial Georgia SOS turnout, preserves their evidence, and
creates local review proposals. **It cannot upload to Civic Pulse.** Approving a
proposal records a local decision only; `publish` always exits with code 4.

## Quick start

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then run from
this repository:

```sh
uv sync --locked
uv run --locked civpulse-updater doctor
uv run --locked civpulse-updater sources validate bibb-democrats
uv run --locked civpulse-updater collect bibb-democrats --from 2026-10-25 --to 2026-12-31
uv run --locked civpulse-updater changes list
uv run --locked civpulse-updater changes show CHANGE_ID
```

Python 3.13.15 is pinned in `.python-version`; uv manages the environment and
locked dependencies. The stack uses Typer, Pydantic, Loguru, SQLAlchemy/Alembic,
HTTPX, Beautiful Soup, and iCalendar parsing. Ruff, Bandit, pytest and Trivy
provide quality/security checks.

Dates are inclusive local calendar dates in the source's configured timezone,
with a maximum 367-day window. Discovery follows the site's visible pagination
and fetches the discovered ICS links, including events outside the window to
avoid trusting HTML dates for filtering. The resulting proposals contain events
starting inside the requested window. A complete run means the discovered
listing was processed, **not** that the source exposes its entire historical archive.

## Review and replay

```sh
uv run --locked civpulse-updater changes approve CHANGE_ID --actor YOUR_NAME --reason "Verified source"
uv run --locked civpulse-updater changes reject CHANGE_ID --actor YOUR_NAME --reason "Needs correction"
uv run --locked civpulse-updater replay RUN_ID
uv run --locked civpulse-updater status
```

Proposals with warnings require `--acknowledge-warnings` for approval. A warning
acknowledgement records acceptance of the original payload; it does not resolve
or edit conflicting source facts. The current release has no proposal editor.
Reject questionable proposals and retain their evidence for a later correction.

The official listing and ICS may disagree. The adapter retains ICS times as the
candidate value, the listing's displayed times in warning text, and both original
source files. It does not silently correct either source. No status in ICS means
`unknown`; it is not an inferred confirmation. Cancellation is normalized only
from an explicit ICS status, with title-only cancellation flagged for review.

Identities use ICS UID plus recurrence identity. Rescheduling preserves that key.
Daily/weekly/monthly/yearly recurrence, exclusions, and exceptions are supported;
subdaily/overly large expansions are rejected. All-day end dates remain exclusive
as specified by iCalendar. Floating times use the source timezone; ambiguous DST
times are rejected. Missing required fields and malformed documents produce
partial/failed runs, never deletions.

Replay reads stored bytes without network access using the currently installed
parser, preserving the original observation ordering. Old evidence cannot
supersede a newer observation. Unchanged records create no additional proposals;
changed records supersede any pending/approved predecessor. Rejected unchanged
records are not repeatedly proposed. A reversion observed in a new collection is
a new reviewable change.

## State and configuration

The default `.state/` directory contains `state.sqlite3`, a process lock, and
SHA-256-addressed evidence files. Run inputs, evidence metadata, parser versions,
proposals, and review decisions are durable. JSON goes to stdout; Loguru logs go
to stderr. New state directories use owner-only permissions (`0700`). Use `--json-logs` for structured logs.

Global options precede the command:

```sh
uv run --locked civpulse-updater --state-dir /path/to/state --sources-dir sources status
```

Equivalent environment variables are `CIVPULSE_STATE_DIR`,
`CIVPULSE_SOURCES_DIR`, and `CIVPULSE_JSON_LOGS`. Source TOML manifests contain no
credentials. Only `review-only` is accepted as a mode. Both the source URL and
all redirected/download URLs must use explicitly allowed HTTPS hosts and public
IP addresses. Connections pin validated IPs while preserving TLS hostname
verification. GET retries are bounded; long Retry-After values defer to a later
manual collection. Downloads, redirects, pages and event counts are bounded.

One process owns the state lock at a time. Interrupted runs remain identifiable;
a later run marks abandoned running rows as interrupted. To back up, stop the
collector and copy the entire state directory. Restore it as a unit, then run
`status` and replay a known run to verify checksums. Evidence is not automatically
pruned. Restrict filesystem access to trusted operators: actor names are local
audit labels, not authenticated identities. Logs do not include raw exception
messages, headers or source bodies.

Exit codes: 0 completed command, 1 partial/failed collection, 2 input/configuration
or evidence-integrity error, 3 state lock contention, 4 publishing unavailable.
Review-pending proposals do not make an otherwise complete collection fail.

## Development and plugins

Built-in adapters expose a versioned manifest, HTML discovery and offline parsing;
the core owns fetching, evidence capture and state. New installed adapters can
register `civpulse_updater.sources` entry points and must also be named in the
source manifest's `enabled_plugins`. Duplicate names and unsupported protocol
versions are rejected. Plugins execute trusted Python code and are not sandboxed.
Install them through uv and commit the resulting lockfile.

The current source config and protocol are intentionally small. There is no API
publisher, account authentication setup, scheduler, OCR worker,
or automatic approval policy in this release. Conditional HTTP caching and
adaptive polling from the broader plan are deferred. Add future schema changes
as Alembic revisions; never edit a migration after distributing it.

```sh
uv run --locked ruff format --check .
uv run --locked ruff check .
uv run --locked pytest -q
uv run --locked bandit -r src -c pyproject.toml
trivy fs --scanners vuln --include-dev-deps --severity HIGH,CRITICAL --exit-code 1 --skip-dirs .venv .
trivy fs --scanners misconfig --severity HIGH,CRITICAL --exit-code 1 --skip-dirs .venv .
trivy fs --scanners secret --exit-code 1 --skip-dirs .venv .
uv build
```

CI pins uv 0.12.5 and Trivy 0.76.0 and checks the built container as well. Scanner
failures fail CI. Tests use saved fixtures and a local TLS server; they do not
contact the live website or Civic Pulse. The sanitized official fixture and its
source are documented in [fixture notes](tests/fixtures/README.md).

## Container

```sh
docker build -t civpulse-updater:review .
docker run --rm civpulse-updater:review doctor
docker run --rm -v civpulse-updater-data:/data civpulse-updater:review collect bibb-democrats --from 2026-10-25 --to 2026-12-31
docker run --rm -v civpulse-updater-data:/data civpulse-updater:review changes list
trivy image --scanners vuln --severity HIGH,CRITICAL --exit-code 1 civpulse-updater:review
```

The runtime runs as UID 10001 with a persistent `/data` volume. A bind mount must
be writable by that UID. The default command is a startup diagnostic and exits;
this is a batch collector, not a daemon. The health probe verifies CLI startup,
not data freshness. With Podman, build with `--format docker` to retain that probe.
No scheduler or production deployment is installed by this repository.

- [Implementation plan](docs/implementation-plan.md)
- [Civic Pulse API assessment](docs/civic-pulse-api-requirements.md)

## Macon-Bibb commission meetings

```sh
uv run civpulse-updater sources validate macon-bibb-commission
uv run civpulse-updater collect macon-bibb-commission --from 2026-10-06 --to 2026-10-06
uv run civpulse-updater changes list
uv run civpulse-updater replay RUN_ID
```

The `civicclerk` adapter reads the public Macon-Bibb CivicClerk API, scoped to
category **26 (Board of Commissioners)**, including Pre-Commission meetings.
It also reads category **24 (General)** and selects explicitly named special-called
commission/pre-commission meetings, Committee on Committees, and public hearings
(including FY budget hearings). The county files these legislative records outside
category 26. Unrelated General boards and unnamed events are excluded.
It collects local meeting times, cancellation labels, published agenda/packet
and minutes PDFs, and downloadable attachments nested within agenda items.
Documents retain source IDs, roles, titles, SHA-256 checksums and byte sizes in
the meeting proposal. An attachment containing earlier meeting minutes stays an
attachment to the current meeting; it is not relabeled as that meeting's minutes.

The source permits 100 MB per response and 500 MB total per run, 100 supporting
resources, 30 meetings and 10 listing pages, paced at one request per second.
Use narrow date windows: real agenda packets can exceed 64 MB. Limits and failed
required downloads withhold the affected meeting's proposal and preserve its
previous record. Other successfully collected meetings may produce proposals in
a partial run. Absence from a listing never deletes a stored meeting.

For an annual capture, use the explicit bounded profile and resume incomplete
bundles from their saved checkpoint:

```sh
uv run --locked civpulse-updater collect macon-bibb-commission --from 2026-01-01 --to 2026-12-31 --systematic
uv run --locked civpulse-updater collect macon-bibb-commission --from 2026-01-01 --to 2026-12-31 --systematic --resume-run RUN_ID
```

This permits 100 meetings, ten listing pages, 5,000 supporting resources,
2,000 requests, 500 MB per response and 10 GB per run, with one-second pacing.
It does not enable browser recovery or publication. A complete capture covers
currently posted records, including future schedules; later revisions and
unpublished minutes require a fresh collection. General-category unnamed events
cannot be attributed to the commission and require separate scope review.

The core supports optional `start_urls(window)`, `resources(content, saved_bytes)`
and `parse_bundle(content, reference, hash, window, saved_bytes)` adapter hooks.
Dependencies are discovered in stages (event, structured agenda, PDFs); the core
owns every GET, evidence checksum, budget and replay. A meeting becomes a saved
replay input only after all its dependencies are collected. Failed attempts retain
raw evidence but require another collection to retry; replay never repairs them
with network requests. No database migration is needed for document arrays in
existing JSON payloads. Existing events without documents retain their hashes.

Only public PDF downloads are supported. External link attachments, unpublished
files, votes, OCR and historic BoardDocs records are outside this adapter's scope.
The upstream API is undocumented and may change. CivicClerk's misleading `Z`
time markers are interpreted as America/New_York wall time; ambiguous DST times
are rejected. Signed storage query strings are excluded from normalized proposals
so token rotation does not generate changes. Raw local evidence retains original
API responses and signed URLs: keep `.state` private and out of Git. Approval is
local only; `publish` remains disabled.

## Bibb County school board

```sh
uv run civpulse-updater sources validate bibb-school-board
uv run civpulse-updater collect bibb-school-board --from 2026-09-09 --to 2026-09-09
uv run civpulse-updater changes list
uv run civpulse-updater replay RUN_ID
```

The `simbli` adapter uses the district's official Simbli tenant, **4013**. It
includes meeting types **Board Meeting**, **Committee Meeting**, **Work
Session**, and **Training Meeting**; individual school councils and other meeting types are excluded.
Schedules use America/New_York. A publicly listed meeting without a published
agenda can produce a schedule-only proposal with a warning.

Agendas are preserved as the official structured JSON tree plus each item's
content model; published minutes are preserved as the official JSON document,
including its HTML text. Explicitly restricted/deleted agenda and minutes
subtrees and restricted content fields are excluded before discovering their detail/attachment URLs. When
exclusions occur, the proposed document is a labelled public projection with a
warning and its own checksum; original source bytes remain private evidence.
Unknown visibility states or contradictory detail visibility still fail closed.
Original PDF, Office, PNG and JPEG attachments are
stored unchanged. Historical attachments with an absent or empty filename use
the source's explicit `FileExtension`, subject to the same format allowlist and
file-signature validation. These files are evidence, not rendered or executed by the
collector. External hyperlinks, videos, linked policy libraries, OCR, extracted
votes and other attachment formats are outside this first version's scope.

Minutes are requested only when the **public listing** says `PUBLISHED`.
`UNPUBLISHED` and `AUTHORISED` remain pending: authorized does not mean publicly
published. The meeting detail API's minutes flag proved inconsistent with the
public listing, so it is not used to authorize retrieval. Later publication or a
changed attachment creates a fresh review proposal. Failed required downloads
withhold the affected meeting, preserving the previous record. If a document
endpoint returns an HTML page, other verified public resources in that bundle
are still checkpointed; the unavailable file is attempted once per run and the
bundle remains incomplete. Source deferrals and challenge failures still stop
network access.

The initial public listing contains the latest 50 meetings across all types.
Older pages use the observed **read-only** `GetMeetingListing` POST, with public
anonymous session context from that listing. The collector allows only this exact
endpoint, tenant 4013, 50-row pages, descending date order, and no custom filters
or user identity. Source bytes and request evidence stay private. Pagination
stops only after covering the requested start date or exhausting the archive.
Changed totals, duplicate identities, missing context, unexpected order, and page
limits make coverage incomplete. A future window may have no posted meetings;
this is not proof that no meetings are scheduled.

For an annual capture, opt into the larger **bounded** systematic profile:

```sh
PLAYWRIGHT_BROWSERS_PATH=/tmp/civpulse-playwright uv run --extra browser civpulse-updater collect bibb-school-board --from 2026-01-01 --to 2026-12-31 --systematic --headed
PLAYWRIGHT_BROWSERS_PATH=/tmp/civpulse-playwright uv run --extra browser civpulse-updater collect bibb-school-board --from 2026-01-01 --to 2026-12-31 --systematic --headed --resume-run RUN_ID
```

Systematic capture permits 2,000 total requests, 100 meetings, ten listing pages,
5,000 resource candidates, 500 MB and 180 seconds per response, and 10 GB downloaded per run. It
retains one-second pacing, persisted cooldowns, hourly/browser limits, public
destination/TLS checks, and local review. Ordinary collection retains its 120
request cap and source download/event limits. No public records are uploaded.

Every discovered meeting and every successful resource download is checkpointed.
Incomplete bundles never produce proposals. `--resume-run` verifies saved
checksums before network access, reuses completed downloads, and retrieves only
missing resources. Source identity, allowed hosts, timezone and requested dates
must match. Incomplete discovery/event-limit runs require fresh discovery rather
than resume. A resumed run preserves the original observation time, so it cannot
overwrite a newer observation. It continues the original source snapshot; use a
fresh collection to check for revisions. Expired public session context or invalid
saved source structures may also require a fresh collection. Replay remains
offline and never repairs missing downloads.

This source retrieves civic data with public GET requests and the constrained read-only archive POST through the existing transport, with
no login. Browser recovery is optional (see below); unexpected source structures
fail collection. Limits: 15 meetings, 250 supporting resources,
100 MB per response, 500 MB total downloaded bytes, at least one second between requests.
Public-page session parameters stay in private local evidence; normalized
proposals contain stable public meeting/document URLs. Keep `.state` private.
Review remains local and publishing remains disabled.


## Optional Simbli browser prototype

Install on a host supporting sandboxed Chromium (the existing Alpine Docker image
is not configured for browser recovery):

```sh
uv sync --locked --extra browser
uv run --extra browser playwright install chromium firefox
uv run --extra browser civpulse-updater probe-simbli-attachment RUN_ID --index 0 --browser firefox
uv run --extra browser civpulse-updater collect bibb-school-board --from 2026-09-09 --to 2026-09-09 --browser-fallback
```

`probe-simbli-attachment` selects one attachment whose challenge response was
saved in the specified run's replay inputs. It makes an ordinary GET first and
uses Chromium only if a recognized challenge returns again. It stores responses
and a content-addressed JSON report; it never changes records, proposals,
approvals, or the original run. The output includes `report_sha256`. A subsequent
normal collection is needed to propose recovered documents. Failed downloads
without complete saved replay inputs cannot be selected by this probe.

Browser fallback is disabled by default, restricted to the exact Bibb tenant 4013 listing URL and attachment
routes, and available only for the Simbli adapter. Normal collection still
validates file formats and preserves evidence; replay remains entirely offline.
Publishing remains disabled.

Protections apply to Simbli even without browser recovery:

- One serialized collector per state directory, at least 1 second between requests,
  at most 120 requests per ordinary run (2,000 with `--systematic`), and no automatic HTTP retry loop.
- At most five browser attempts for distinct URLs per run, at least 30 seconds between attempts,
  and at most 45 attempts per rolling hour, persisted before browser launch.
- HTTP 401/403/429/503 or any Retry-After header stops network access for at least
  30 seconds; a longer Retry-After is honored. An unresolved challenge also
  triggers that cooldown. Do not delete policy state to force a retry.
- At most 12 additional browser requests within a 45-second network budget, with a
  2 MB cap per script/challenge resource. Existing attachment and aggregate byte
  limits include browser traffic. Browser launch has a separate 15-second timeout;
  cleanup/navigation can add elapsed time beyond the network budget.
- Only the target listing/attachment and narrowly allowed same-host challenge resources
  can load. Civic-data POSTs, third-party requests, service workers and WebSockets are blocked.
  No CAPTCHA interaction, stealth patches, or broad site crawling.

Chromium runs with its sandbox enabled in a temporary isolated context. Requests
are fulfilled through the existing pinned-DNS, TLS-verified Python transport;
normal browser networking is directed to an unavailable local proxy. This keeps
budgets enforceable but does **not** reproduce Chromium's native TLS connection,
so some anti-bot systems will remain unresolved. The fallback fails closed.

Use one shared state directory for all collectors targeting Simbli; separate
state directories or hosts do not coordinate these protections. Keep `.state`
private: challenge responses, scripts, session-bearing URLs and probe reports
are evidence. Do not run repeated full collections to test browser recovery;
prefer the one-attachment probe and respect its persisted cooldown.

Browser integration tests use synthetic responses and make no requests to Simbli:

```sh
uv run --extra browser pytest -m browser
```

Choose `--browser chromium` (default) or `--browser firefox` for collection or
attachment probes. Source TOML can also set `browser_engine = "firefox"`.
For collection, browser selection alone does not enable fallback: include
`--browser-fallback`. A probe enables fallback for that one attachment. There is
no automatic switch to another engine after failure, and both engines share the
same persistent cooldown and byte/request budgets. Reports record the selected
engine even when ordinary HTTP retrieves the file without launching a browser.
Firefox keeps its genuine default user agent and disables WebRTC, HTTP/3 and
prefetching; both engines use the same restricted HTTP broker. Browser fixtures
in CI exercise both engines without contacting Simbli.

The same opt-in fallback now recovers the exact Bibb meeting listing URL when it
returns a recognized challenge. Recovered HTML must pass the existing Simbli
listing parser before it can be used; arbitrary pages and other tenants remain
out of scope. A successful browser session's cookies and user agent are reused
in memory for public pages and attachments during that run through the existing
pinned HTTP transport. JSON requests under `/Services/api/` retain the updater
identity and do not inherit browser cookies: the Firefox identity caused API
read timeouts in a bounded live comparison.
Cookies retain host, path and expiry restrictions and are not saved as a reusable
browser profile. Listing and attachments share a five-attempt run budget. Each URL gets at most
one recovery attempt. Different challenged URLs wait for the full 30-second
cooldown, without source traffic, before the next browser launch. A failed
recovery or server deferral stops network access; browsers never run concurrently.

Browser recovery continues servicing page-initiated navigation and delayed scripts
until it captures valid content, encounters an error, or exhausts the existing
45-second budget. It permits one manual reload after ten seconds when needed;
returning another challenge page does not immediately close the browser. The
same 12-request cap and byte limits apply throughout. Valid document bytes are
captured before navigation is aborted, so attachments are never rendered.

A challenge may make at most two verification POSTs to the same constrained,
same-host endpoint whose JavaScript was already fetched successfully in that
recovery. Each body is capped at 64 KiB. These count against the existing
12-request recovery budget and the selected ordinary/systematic run budget and use the same pacing,
deadline, TLS/DNS validation and response byte limits. `/Services/` writes,
unlisted endpoints and third-party POSTs remain blocked. This exception only
submits browser challenge verification, never Civic Pulse records. Request and
response evidence is saved privately with method/direction metadata; old evidence
defaults to GET/response. Never publish the private verification evidence.

If Simbli requests hCaptcha and ordinary verification does not recover the target,
the run stops with `interactive CAPTCHA verification` and persists a cooldown.
The collector does not contact hCaptcha or solve CAPTCHA challenges. Such a run
is incomplete and cannot create a proposal for a meeting with missing documents.

### Manual Firefox verification

For a supervised Simbli run, open a visible Firefox window:

```sh
PLAYWRIGHT_BROWSERS_PATH=/tmp/civpulse-playwright uv run --extra browser civpulse-updater collect bibb-school-board --from 2026-09-02 --to 2026-09-02 --headed
```

`--headed` enables Firefox fallback automatically. Solve any CAPTCHA yourself in
that window; collection resumes when Simbli returns the requested listing or
document. If the verified page does not redirect, reload the page manually.
Closing the window cancels that recovery. A working desktop display is required.
After manual verification, the recognized empty official attachment form ends
the browser wait and preserves session cookies. Its HTML remains failed document
evidence; it cannot become a valid file or complete meeting bundle. Unknown HTML
and challenge responses do not receive this treatment.

No CAPTCHA solver, answer generation, stealth patches, or persistent browser
profile is used. Successful session cookies stay in memory for this run only.

This opt-in mode removes automatic-mode script/host filters, the 12-request and
two-verification-POST sublimits, and the 45-second timeout. It permits ordinary
public HTTPS verification resources and redirects, with a ten-minute interaction
window. All requests still pass through the pinned, TLS-verified broker: one
request per second, 30-second cooldown, 45 browser attempts/hour, five distinct
recovery targets/run, and the selected ordinary/systematic request cap. Server deferrals still stop the
run. Source response/aggregate download limits apply to all verification assets;
Manual verification POST bodies use the source per-response byte cap;
unattended verification retains its 64 KiB cap. Private network destinations, non-HTTPS,
WebSockets, service workers, and Simbli civic API writes remain disabled.
Connections to `civpulse.org` and its subdomains are explicitly blocked. This mode
does not upload anything and does not change review-only publication behavior.

Verification requests and responses are captured in private local evidence, which
can contain session tokens. Do not share or commit `.state`. Unattended/headless
runs retain their original stricter behavior. If the retained run or download
budget is exhausted, recovery stops; manual mode does not reset any counters.

## Georgia General Assembly

```sh
uv run --locked civpulse-updater sources validate georgia-legislature
uv run --locked civpulse-updater collect georgia-legislature --from 2025-01-01 --to 2026-12-31 --full-session --resume-latest
uv run --locked civpulse-updater collect georgia-legislature --from 2025-01-01 --to 2026-12-31 --full-session --resume-run RUN_ID
uv run --locked civpulse-updater changes list
uv run --locked civpulse-updater replay RUN_ID
```

The `georgia-legislature` connector reads the official website's public API at
`https://www.legis.ga.gov/api/`. Its manifest pins session **1033, 2025–2026 Regular
Session**. Change `legislature_session_id` to collect a different official session;
the session listing validates that ID. The connector uses the website's anonymous
token handshake, with the token held only in memory. No account is required.

Coverage includes:

- Session metadata and bills/resolutions, with sponsors, versions/document links,
  committee referrals, status histories and linked votes.
- House and Senate vote details, official totals, member ballot codes and bill
  relationships. Repeated `VACANT` entries with member ID zero remain anonymous;
  they are never joined to a legislator or assigned a district. House attendance
  roll calls use official listing totals only, matching the website's deliberate
  omission of individual detail; their records carry an explicit warning.
- Member profiles, contact information, party codes, service history and committee
  assignments; committees, officers, memberships and subcommittees.
- Seats derived from the official roster's district records. These carry a review
  warning: session rosters include former members and do not establish current
  occupancy, a complete vacancy history, or unrepresented districts.
- Meeting schedules and calendar/publication metadata for both chambers on the
  official session's listed legislative days. Meeting times use America/New_York;
  source midnight means time TBD and produces a warned date-only schedule.
  Explicit Canceled/Cancelled title prefixes normalize to cancelled status with
  a warning identifying that source basis.

Bills, members, committees and derived seats are **whole-session snapshots**, even
for a one-day collection. Votes and meetings use the inclusive requested dates.
Calendar metadata is collected for listed legislative days inside that window;
this does not assert coverage of interim calendar publications. The schedule
endpoint exposes currently available meetings, not a guaranteed historical archive.
PDF agenda links, bill versions and calendar document URLs are retained, but this
connector does not download their binaries, extract PDF text, or collect video,
transcripts, geographic boundaries or separate study-committee detail pages.
Upstream field names and enum codes are preserved in each legislative record's
`data`; no undocumented party, vote or document-type codes are guessed.

Non-event proposals have an explicit `record_type` (`session`, `bill`, `vote`,
`member`, `seat`, `committee`, `calendar`), `session_id`, stable source identity,
and structured `data`. They use the same local changes/review workflow and
immutable evidence store as events. Replay is offline. Existing event hashes and
publication behavior are unchanged; approval is local and `publish` remains disabled.
Feeds that contain complete records are parsed from their saved discovery bytes,
so parsing uses the same observation without a second download of the feed.

`--resume-latest` makes this same command safe to rerun after interruption: while
holding the state lock, it selects the newest usable checkpoint matching the
source, session and dates, verifies saved checksums, and downloads only missing
inputs. After a completed capture, the command starts fresh to observe revisions.
A newer run with incomplete discovery also starts fresh; it never revives an older
snapshot. Corrupted evidence stops collection before network access. Use
`--resume-run RUN_ID` to choose an explicit checkpoint instead; the two options
are mutually exclusive.

`--full-session` permits a window of up to 731 days for this adapter only.
Ordinary collection retains its 367-day limit. The source permits 100 listing
pages, 10,000 detail references, 1,000 supporting
resources, 10,000 requests, 20 MB per response and 1 GB total, paced at one request
per second. Even a narrow date window can fetch thousands of bills and take hours.
These are ceilings, not coverage guarantees. Search uses the official read-only
session-filtered POST with 200 rows per page; its exact request is retained as
private evidence. Missing/short pages, changed totals, duplicate bill identities,
wrong sessions and unavailable detail responses make a run partial or failed.
Successfully captured records remain reviewable; missing records are never deleted.
`--resume-run RUN_ID` reuses checkpointed detail downloads when discovery completed;
changed session IDs or incomplete discovery require a fresh capture.

The website's API and anonymous handshake are undocumented and may change. Source
validation checks configuration, not network availability or completeness. Live
endpoint samples and offline fixture validation are described in
[Georgia connector validation](docs/georgia-legislature-validation-2026-10-26.md).

## Georgia SOS turnout

```sh
uv run --locked civpulse-updater sources validate georgia-sos-turnout
uv run --locked civpulse-updater collect georgia-sos-turnout --from 2026-11-03 --to 2026-11-03
uv run --locked civpulse-updater changes list
uv run --locked civpulse-updater replay RUN_ID
```

The `georgia-sos-turnout` adapter starts the Georgia Secretary of State voter and
election import pipeline with **aggregate, unofficial turnout** from the public
[Election Data Hub](https://sos.ga.gov/page/election-data-hub-unofficial-turnout).
The source manifest points directly to the dashboard linked by that page. The
page itself currently rejects the updater's direct HTTP request.

**Dates select election dates**, inclusively, rather than ballot-casting dates or
snapshot dates. For example, the command above captures the currently reported
November 3 election aggregates, including early voting observed before Election
Day. It does not reconstruct a past daily snapshot. Only election dates currently
exposed by the dashboard are available; an empty selection window is not evidence
that no election occurred.

Each selected election produces three `record_type: "turnout"` table snapshots:

- Early in-person ballots accepted, by county.
- Absentee activity by county: requested, issued, returned, accepted, rejected,
  spoiled/canceled, outstanding, and the source acceptance ratio.
- Turnout by county and the source's grouped ballot style.

Each snapshot contains sorted dimension rows, numeric values and original display
text, the public table ID, official measure/dimension definitions, and the table's
as-of footnote when supplied. Ratios remain ratios (for example, `1` displayed as
`100.00%`); null/NaN measures remain null. Missing counties/ballot styles are never
filled with zero: source tables can suppress zero/missing rows. The table's
source expressions determine which ballot categories and dates it includes;
these three tables must not be added together. No candidate vote totals or
certified election outcomes are inferred from participation counts.

Stable identities combine election date and table key. A later cumulative
correction, including a lower count, replaces the prior table through the normal
local review workflow. Repeating unchanged data or replaying a run produces no
extra proposals. All turnout records explicitly carry `unofficial: true` and
review warnings; approval requires acknowledging those warnings. Publication
remains disabled, and no Civic Pulse database connection is made.

The collector uses the dashboard's anonymous **Go Interactive** token endpoint
and Qlik's TLS WebSocket API. The token remains in memory. Only three pinned public
aggregate objects are read; selections and a temporary selection-metadata object
exist only in that session. No persistent Qlik objects, measures, or data are
written. The app ID, hosts and dimension definitions are pinned; unexpected
schemas require inspection and a connector update.

The original bootstrap files and RPC requests/responses are content-addressed
private evidence. A complete snapshot embeds its ordered RPC transcript for
network-free replay. Transport metadata distinguishes Qlik `RPC` frames from
HTTP requests. Authentication responses and bearer headers are excluded from
saved evidence and WebSocket wire logging. DNS addresses are validated and pinned
while TLS still verifies the official hostname; WebSocket redirects/proxies are
not followed. Every RPC is paced and counted, and frames, aggregate bytes,
notifications, response time, election count and table rows are bounded.

The manifest permits 1,500 total HTTP/RPC requests, 20 MB per response/snapshot,
100 MB total network response bytes, and at least one second between requests.
There are at most 100 listed elections, 2,000 rows per table and 1,000 cells per
page. Broad date windows take longer and may exhaust these ceilings; prefer one
election per run. Each capture uses one complete transcript: failed selections,
short pages, changed layouts, extra dashboard filters, source reloads or download
failures withhold that capture's proposals and preserve previous records. Failed
incomplete captures require a fresh collection; successful captures support
normal offline replay.

This first increment excludes registration statistics, individual voter/history
files, demographic charts, certified results, scheduling and publication. Add
those as separate source contracts rather than treating turnout as voter records.
Live and offline validation are recorded in
[Georgia turnout validation](docs/georgia-sos-turnout-validation-2026-10-26.md).

## Macon Water Authority

```sh
uv run --locked civpulse-updater sources validate macon-water-authority
uv run --locked civpulse-updater collect macon-water-authority --from 2026-10-08 --to 2026-10-08
uv run --locked civpulse-updater changes list
uv run --locked civpulse-updater replay RUN_ID
```

The `macon-water-authority` adapter reads the public Diligent Community portal
linked by the [authority's publications page](https://maconwater.org/public-information/publications/).
It includes the authority board, Engineering, Finance, Outdoor, Pension,
Pension Trustee, Personnel and Policy meeting types. The mixed imported archive
(type 17) is included only for titles starting `Macon Water Authority -`.
Macon Soils, Macon Water Alliance and external calendars are excluded.

Dates are inclusive America/New_York dates. Requests extend through the next day
and returned records are filtered locally: the portal returns no records for
identical `from` and `to` parameters. Stable meeting IDs survive rescheduling;
ambiguous/nonexistent local timestamps and conflicting identities fail parsing.
Calendar entries without a published agenda become schedule-only proposals with
a warning. Status remains unknown unless the title explicitly says cancelled,
which produces a cancellation warning. Regular meeting times are never generated
from the authority's general monthly schedule.

Only document entries explicitly marked `IsPublic: true` are processed. Private
document HTML and links are ignored before attachment discovery. Public agenda
and minutes HTML is materialized byte-for-byte as UTF-8, with its own checksum,
size and role. The original JSON response is also preserved as private evidence.
The importer downloads same-tenant `/document/` links from that public HTML;
PDFs and HTML documents bearing the portal's iCompass/Aspose document markers
are accepted. Other HTML responses fail validation;
earlier minutes linked within an agenda retain the `Attachment` role. A failed,
missing or unsupported attachment withholds that meeting's proposal. Approval remains
local and publishing is disabled.

This module does not fetch DOCX/full agenda-packet exports, external link
attachments, images, videos, BoardDocs archives, board member records or election
records. It does not extract PDF text or perform OCR. Returned document JSON can
contain private metadata: keep the evidence directory private and out of Git.
The portal's service is undocumented and may change. A complete run covers the
records returned by its date-filtered listing, not a guaranteed historical
archive. Missing records never delete prior observations. No browser fallback or
systematic capture profile is enabled. Default limits are 100 meetings, 500
supporting resources, 100 MB per response, 500 MB total and one-second pacing.
See [validation notes](docs/macon-water-validation-2026-10-27.md).
