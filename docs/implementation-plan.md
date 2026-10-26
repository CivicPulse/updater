# Civic Pulse Python updater implementation plan

Date: October 25, 2026. Status: proposed implementation; Python and the user-selected tooling are accepted requirements. No application, API, infrastructure, or publication changes are implemented by this plan.

Implementation update: the local 0.1 release now implements the review-only Python foundation and Bibb Democrats event/ICS collection. See the [README](../README.md) for the implemented commands and explicit limits, and [validation record](validation-2026-10-25.md) for evidence. This does not mark the broader milestones, production deployment, or API publishing as complete.

Build an independent Python service that collects official civic information, preserves evidence, prepares reviewable changes, and publishes through the Civic Pulse API. The updater must continue collecting during Civic Pulse outages and recover interrupted publication without duplicate records. It must not connect directly to Civic Pulse's database.

The companion [API assessment](civic-pulse-api-requirements.md) identifies current capabilities, evidence limits, and the server changes required for unattended uploads.

## Technology choices

| Area | Choice | Implementation requirement |
| --- | --- | --- |
| Language and environment | Python, uv | Initially target Python 3.13, subject to adapter compatibility tests. Pin the exact runtime in `.python-version` and the container. Commit `pyproject.toml` and `uv.lock`; use `uv sync --locked` and `uv run --locked`. |
| CLI | Typer | One `civpulse-updater` command, scriptable exit codes, JSON output on stdout, logs on stderr. No interactive prompts in scheduled execution. |
| Models | Pydantic v2, pydantic-settings | Versioned source configuration, normalized records, change sets, and API envelopes. Strict validation at normalized/API boundaries, unknown fields forbidden. Explicit parsing at source boundaries. |
| Logging | Loguru | JSON in containers and readable local output; bind run, source, entity, and operation IDs. Disable exception-local diagnostics (`diagnose=False`) in production. Redact tokens, signed URLs, and private content. |
| Formatting and lint | Ruff | Shared `pyproject.toml`; format check plus lint in CI. Start with E4/E7/E9/F/I/UP/B rules; document exceptions. |
| Python security analysis | Bandit | Scan production source; review findings, no blanket skips. A targeted exception needs a rationale and owner. |
| Dependency and container security | Trivy | Scan lockfile including development dependencies, configuration, secrets, and release image. Pin scanner version and record database freshness. |
| HTTP | HTTPX | Shared bounded client with TLS verification, timeout budgets, conditional requests, rate limits, and redirect validation. |
| HTML and browser | Beautiful Soup/lxml initially; optional Playwright | Prefer documented API, feed, then HTML. Add browser rendering only for adapters that require it. Evaluate Crawlee/Scrapy if crawl complexity justifies them. |
| State | SQLite initially, behind repository interfaces | One host, persistent local disk, single publishing process; SQLAlchemy and Alembic for updater-owned state. PostgreSQL migration is a later multi-host decision. |
| Evidence | Content-addressed filesystem initially | Atomic file writes on persistent storage, checksum verification, backup; an object-storage backend can follow. |
| Tests | pytest, HTTP test fixtures, local API integration | Fixtures and real process/database tests for critical boundaries; no write tests against production. |

Keep optional extras for browser, CivicClerk clients, and PDF/OCR dependencies. A schedule-only deployment should not require OCR models or browsers. Verify licenses, maintenance, supported Python versions, and fixtures before adopting civic-scraper or libmuni.

The standard-library [package entry-point API](https://docs.python.org/3/library/importlib.metadata.html) supports installed adapter discovery. [uv locking](https://docs.astral.sh/uv/concepts/projects/sync/), [Pydantic models](https://docs.pydantic.dev/latest/concepts/models/), [Typer](https://typer.tiangolo.com/tutorial/), [Loguru](https://loguru.readthedocs.io/en/stable/api/logger.html), and [Ruff](https://docs.astral.sh/ruff/) are the implementation references. [SQLAlchemy](https://docs.sqlalchemy.org/en/20/orm/quickstart.html) is for the updater's own state, not the Civic Pulse database.

## Module boundaries

```text
src/civpulse_updater/
  cli/                  Typer command groups
  config/               settings and source manifests
  models/               versioned Pydantic contracts
  plugins/              discovery, capability checks, adapter protocol
  adapters/             civicclerk, simbli, ics, html_roster
  transport/            HTTP, browser, rate limiting, URL policy
  evidence/             immutable responses, files, extraction references
  extraction/           HTML, PDF, OCR, calendar parsing
  pipeline/             collection, normalization, matching, diffs, review
  state/                repositories, migrations, checkpoints, outbox
  publishers/           Civic Pulse API client and reconciliation
  observability/        Loguru configuration and health summaries
tests/
  fixtures/             representative public source captures
  unit/
  integration/
  contract/
  e2e/
sources/                non-secret source manifests
```

Adapters cannot publish. The publisher cannot scrape. Domain models contain neither database rows nor SDK-specific response objects. Destination organization/calendar IDs live in configuration and mapping state, rather than platform adapter code.

Start with built-in adapters; expose the same interface through the entry-point group `civpulse_updater.sources`. Load only explicitly enabled, installed, locked distributions. Reject incompatible protocol versions and duplicate adapter names. Plugins are trusted executable code, not a sandbox; arbitrary package installation from source pages is prohibited.

## Adapter contract

An adapter provides `manifest`, `discover`, `fetch`, and `parse` operations. Discovery returns pages of source references plus a checkpoint candidate. Fetch uses the shared transport and produces immutable evidence. Parsing consumes saved evidence and produces records without network access. This makes extraction replayable after parser changes.

Each manifest declares protocol version, adapter version, configuration model, allowed hosts, supported entity types, whether stable external IDs exist, and available capabilities such as attachments or structured votes. A source configuration supplies tenant/site, board filters, timezone, polling windows, destination mappings, and publication policy.

Each batch declares `complete`, `partial`, or `failed` for a precise query/date window. Unsupported capability is separate from an empty result. A partial run never advances a completeness watermark or authorizes removals. Commit discovery progress only after evidence and associated processing work are durable; publication has its own independent watermark.

## Data and evidence contracts

| Model | Required semantics |
| --- | --- |
| SourceConfig | Stable source ID; adapter/version compatibility; organization/board scope; official entry URL; timezone; permitted hosts; schedule and review policy. Secrets are references, not manifest values. |
| SourceRef | Source ID, entity kind, opaque external ID, canonical source URL, optional upstream revision. No title/date-derived identity when a native ID exists. |
| Evidence | SHA-256, media type, size, retrieval time, final URL and redirect history, safe response metadata, source publication time if known, storage reference. Never persist credentials in captured headers. |
| RecordEnvelope | Schema version, source reference, evidence references, parser version, observation time, source revision when available, and typed record. |
| Event/Meeting | Source identity, title, local timezone and normalized timestamps or all-day dates, location, explicit cancellation state, agenda/minutes status. Distinguish scheduled, rescheduled, cancelled, unknown. |
| AgendaItem | Stable source key, parent item, ordering, title, description, linked document references and motions. |
| Document | Stable source document identity plus immutable content versions; role such as agenda, packet, minutes, supporting material; original filename and source URL. |
| Person/Role/Term | Separate person identity, organization membership, office, and actual service dates with precision. No inferred term boundaries from page disappearance. |
| Motion/Vote/Attendance | Exact source outcome and normalized supported outcome; individual choices only when evidenced. Unknown is not absent, abstaining, or no. |
| ChangeSet | Immutable proposed field changes, expected destination revisions, evidence, ownership policy, payload hash, review state and decision. |
| PublicationReceipt | Operation ID, payload hash, destination IDs/revisions, final status, timestamp; website visibility status recorded separately. |

For calendar occurrences use source UID plus recurrence identity, preserving the identity when a start time moves. Account for recurrence exceptions, all-day semantics, explicit cancellations, DST, and absent end times. Never silently truncate source descriptions to fit destination constraints: retain evidence and produce an explicit publication validation issue or separately reviewed summary.

Store precise JSON pointers, PDF page references, or text spans for claims extracted from evidence. AI/OCR output is derived evidence with parser/model provenance; it does not become an official source. Votes, new identity matches, and officeholder changes start with human review. Never infer individual votes from a motion's aggregate outcome.

Use deterministic typed serialization for payload hashing: schema version included, UTF-8, sorted object keys, stable collection ordering where order is nonsemantic, fixed timestamp representation, and explicit missing/null semantics. Store the exact bytes submitted; retries reuse them. Avoid hashes over mutable logs or retrieval timestamps when deciding whether civic content changed.

## State and recovery

Persist sources, runs, checkpoints, evidence, normalized revisions, entity mappings, change sets, review decisions, outbox operations, upload sessions, and receipts. Unique constraints enforce source/entity identities locally. A content revision and retrieval observation are separate, so checking an unchanged page does not create a new civic revision.

Collection progresses through `discovered → fetched → parsed → validated → proposed`, with failed and quarantined branches. Publication progresses through `review_pending → approved → queued → sending → committed → verified`. A timeout after transmission changes the operation to `unknown`; a conflict changes it to `conflict`, never automatic overwrite.

Create approved immutable payloads and outbox entries in one local transaction. On restart, reconcile `sending` and `unknown` operations using the server's operation lookup before further writes for that entity. A single local publisher prevents overlapping local work; only server idempotency and revision checks protect remote effects across crashes or multiple clients.

Use bounded exponential backoff with jitter and `Retry-After` for transient reads. Writes may retry only under the server contract in the companion document. Authentication failures pause publishing; collection continues. Validation failures quarantine the record. An unrecognized capability/schema version disables the affected publisher operation.

Retain unresolved operations across restore and rollback. Restore the database and evidence manifest consistently, verify checksums, then reconcile remote receipts before resuming. A missing local receipt never proves a remote write failed.

## CLI and operations

Proposed commands, not implemented yet:

```text
civpulse-updater doctor
civpulse-updater sources list|validate
civpulse-updater collect SOURCE --from DATE --to DATE
civpulse-updater replay RUN --parser-version VERSION
civpulse-updater changes list|show|approve|reject
civpulse-updater publish --changeset ID
civpulse-updater reconcile --operation ID
civpulse-updater run-due
civpulse-updater status --json
```

`collect` and `replay` never publish. `publish` requires an approved payload matching its stored hash and expected revisions; a dry run never makes remote writes. Automation uses explicit source policies, not a generic skip-review switch. Review decisions record actor and reason; MVP assumes trusted local operators with filesystem access. Cryptographic/multi-user review authority is a later requirement if that assumption changes.

Suggested exit codes: 0 success/no changes, 1 execution failure, 2 invalid input/configuration, 3 unresolved conflict/unknown remote outcome, 4 authentication/capability failure. A successfully collected run awaiting review remains a successful collection and exposes its review status in output.

Initially invoke `run-due` using a systemd timer on one host or an equivalent container scheduler with persistent volumes. Persist due times and acquire a single-instance lock. Allow bounded missed-run catch-up. Initial configurable defaults: events every six hours, hourly within 48 hours of a meeting, daily for 90 days after meetings, rosters daily, and broader historical reconciliation weekly. Adjust to source limits and observed publication patterns; these are proposed policies, not discovered upstream guarantees.

Track last fetch/parse/publication success separately, stale-source age, count anomalies, queue age, bytes, retries, validation failures, and review backlog. Alert on meaningful failures/staleness; unchanged runs should be quiet. Loguru records do not replace durable audit receipts.

## Security and quality gates

All Python dependency/environment work uses uv, including test and lint tools. Trivy is a separately pinned CLI/container, not a Python package. Representative CI commands after scaffolding:

```sh
uv sync --locked --all-extras --group dev
uv run --locked ruff format --check .
uv run --locked ruff check .
uv run --locked pytest
uv run --locked bandit -r src -c pyproject.toml
trivy fs --scanners vuln --include-dev-deps --severity HIGH,CRITICAL --exit-code 1 .
trivy fs --scanners misconfig --severity HIGH,CRITICAL --exit-code 1 .
trivy fs --scanners secret --exit-code 1 .
trivy image --scanners vuln --severity HIGH,CRITICAL --exit-code 1 IMAGE_DIGEST
```

Install Bandit's TOML extra in the locked dev group. Run development dependency scanning as well as release-image scanning. Trivy [supports uv.lock](https://trivy.dev/docs/latest/coverage/language/python/) but excludes development dependencies by default; its uv lockfile coverage does not include license analysis. [Bandit](https://bandit.readthedocs.io/en/latest/) covers Python code patterns, not dependency vulnerabilities. Scanner execution errors fail the gate; no findings is meaningful only after successful execution. Keep secret scan reports access-controlled and sanitized in shared logs. Do not perform external credential verification. Time-limited exceptions require an owner, finding ID, justification, and expiry; lower-severity findings remain tracked.

Run network access through host and scheme allowlists, including every redirect and resolved destination; block loopback, private/link-local ranges and metadata endpoints, and enforce actual connection destinations against DNS rebinding. Source sites may legitimately use several document CDNs; onboarding approves those explicitly. Bound downloads, decompression, page counts, browser time, and OCR resources. Sanitize filenames, HTML, and terminal output. Keep fetched documents and execution workers isolated; no source content becomes shell commands or plugin configuration.

Publishers alone receive Civic Pulse credentials; fetch/browser processes should not inherit them. Use non-root containers, writable state directories only, and verified TLS. Store credentials in a secret provider, not SQLite, fixtures, or manifests. Supabase's [announced TLS cipher retirement](https://supabase.com/changelog/cbc-tls-ciphers-deprecated) is a reason to test the pinned runtime's actual HTTPS connection, not disable verification.

## Delivery sequence

Owners below are workstream responsibilities, not assignments to other chats or agents. Each release retains the prior image/configuration, uses additive state changes, and records the exact deployed revision and checks. API implementation belongs in its repository through its owner.

| Milestone | Owner and scope | Dependencies and acceptance | Rollback and risks |
| --- | --- | --- | --- |
| 1. Package and offline pipeline | Updater: uv package, requested tools, CLI, models, plugin registry, state/evidence store, fixture adapter | Fresh locked install; fixture collection and replay; invalid config rejection; no publication credentials or remote writes. Deploy disabled worker and verify health/version. | Revert image/config; preserve state. Test state migration compatibility and log redaction. |
| 2. First official collection | Updater: Bibb Democrats event/ICS discovery and proposed changes | Event identity, cancellation/DST fixtures, same-input no-op, crash restart; bounded live read comparison. Production collection remains review-only. | Disable source/revert adapter; retain evidence. Main risk is missed events or duplicate occurrence identity. |
| 3. Safe event upload API | API owner: scoped identity, source binding, external IDs, revisions, durable operation receipts, event-only atomic ingest | Companion contract and disposable concurrency/timeout tests; additive disabled deployment, then bounded authorized production smoke. | Disable ingestion routes/principal, keep existing content and receipts. No schema reversal. |
| 4. First publication | Updater: HTTP client, review/outbox, reconciliation and read-back | Milestone 3; lost-response retry creates exactly one event; editor conflict preserved; upload outage does not block collection. Verify actual destination records and relevant website visibility. | Pause publisher, reconcile pending operations, retain previous image. Never automatically delete records as rollback. |
| 5. Meeting documents | API + updater: upload sessions/finalization; CivicClerk adapter and revisioned attachments | Verify Macon-Bibb categories and client compatibility; interrupted binary transfer, corrupt hash, duplicate finalize and private-file checks. | Disable attachment publishing; preserve committed versions and unresolved sessions. |
| 6. School board coverage | Updater: Simbli discovery, schedules, agenda/minutes files | Validate access method on tenant S=4013; captured fixtures; source-structure failure and delayed-minutes tests. | Disable source or revert parser; replay evidence without publishing automatically. |
| 7. People and decisions | API + updater: memberships/terms, structured agenda items, attendance and votes | Stable identity mapping; reviewed terms/dates; atomic children; absent-attendee trigger interactions; unknown values remain unresolved. | Pause affected capabilities; explicit compensating corrections only with fresh revisions. |
| 8. Selective automation | Updater: per-source automatic policy after shadow comparisons | Agreed freshness and accuracy targets; zero unexplained duplicates; all changes have evidence; successful restore drill. Measure reviewed correctness, not parser confidence alone. | Switch policy to review-only; collection continues. |

The smallest next implementation is milestone 1 plus a narrow event/ICS adapter from milestone 2. Do not wait for vote extraction or a distributed queue to deliver useful reviewable collections.

## Required verification

Unit tests cover model validation, deterministic diffs, date precision and adapters against saved fixtures. Integration tests exercise SQLite transactions, real HTTP failure boundaries, source pagination, truncated downloads, atomic evidence writes, and process restart. Contract tests exercise the actual disposable Civic Pulse API and database, including denied principals, simultaneous duplicate requests, editorial conflicts, post-commit response loss, and upload finalization. End-to-end tests run collection through review and publication, then verify API results and visibility. For milestone 1, an API/browser E2E layer is inapplicable because no publication path exists; add it when that path ships.

Source regressions get recorded fixtures. Security regressions include redirect-to-private-host rejection, filename traversal, secret redaction, cross-source publication denial, and private attachment access. Live official-source checks are bounded diagnostics; deterministic CI should not depend on external site uptime.

## Source onboarding

1. Verify the official entry point and record authority separately for schedules, people, and decisions.
2. Select an existing adapter or implement the protocol with fixture tests.
3. Approve document hosts and identify pagination, stable IDs, revisions, timezone, and available capabilities.
4. Bind destination organization/calendar and explicitly match existing records before enabling creates.
5. Run a bounded backfill, compare counts and sampled records, and review proposed changes.
6. Enable publication only for validated capabilities; retain review for ambiguous identity, votes, and terms.

Initial sources: [Bibb Democrats events](https://www.bibbdems.com/events) and [staff](https://www.bibbdems.com/staff), [Macon-Bibb CivicClerk](https://maconbibbcoga.portal.civicclerk.com/), [county roster](https://www.maconbibb.us/clerk-of-commission/), and [Bibb schools Simbli](https://simbli.eboardsolutions.com/Index.aspx?S=4013). Evaluate [civic-scraper](https://civic-scraper.readthedocs.io/en/latest/usage.html) and [libmuni](https://github.com/chapinb/libmuni) behind the adapter contract; neither is assumed proven against these tenants.
