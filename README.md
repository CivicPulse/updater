# Civic Pulse updater

An independent Python collector for official civic data. Version 0.1 collects
configured civic events and official commission/school-board meeting records, preserves their evidence, and
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
publisher, authentication setup, scheduler, OCR worker, roster importer,
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
includes meeting types **Board Meeting**, **Committee Meeting**, and **Work
Session**; individual school councils and other meeting types are excluded.
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
stored unchanged. These files are evidence, not rendered or executed by the
collector. External hyperlinks, videos, linked policy libraries, OCR, extracted
votes and other attachment formats are outside this first version's scope.

Minutes are requested only when the **public listing** says `PUBLISHED`.
`UNPUBLISHED` and `AUTHORISED` remain pending: authorized does not mean publicly
published. The meeting detail API's minutes flag proved inconsistent with the
public listing, so it is not used to authorize retrieval. Later publication or a
changed attachment creates a fresh review proposal. Failed required downloads
withhold the affected meeting, preserving the previous record.

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
