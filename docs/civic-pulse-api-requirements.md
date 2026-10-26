# Civic Pulse API requirements for reliable ingestion

Date: October 25, 2026. Status: assessment and proposed contract; no API changes or production writes performed.

The existing API offers useful content operations, but the inspected interfaces do not establish the guarantees needed for unattended replayable ingestion. Prioritize an event-only ingestion contract with stable external identity, durable idempotency receipts, scoped authorization, and optimistic concurrency. Add document finalization and meeting child operations in subsequent releases.

## Evidence and limits

Local API source was inspected at `/mnt/storage/projects/www.civpulse.org`, HEAD `99f4adab5f597911457493862b5c872b2a5fd516`. The checkout contains unrelated local changes; this assessment did not modify them. Migration filenames are identifiers, not evidence that a hosted migration has been applied.

The connected Civic Pulse tool catalog was also inspected. It advertises meeting fields and attachment operations absent from the local MCP registrations. Therefore local source and the connected catalog must be treated as different observations. Catalog declarations establish advertised capabilities, not successful authenticated upload behavior. No write/concurrency/failure-injection tests were run on production.

Evidence references (paths relative to the website repository):

| Reference | Evidence |
| --- | --- |
| `apps/mcp/src/index.ts` | HTTP MCP transport; Supabase user-token authentication, claims checks, and user-token forwarding. Deployment/auth revocation behavior still requires integration verification. |
| `apps/mcp/src/toolkit.ts:57` | Create uses an insert with returned row; no source/idempotency parameter in the generic operation. |
| `apps/mcp/src/toolkit.ts:65` | Update preserves omitted fields and selects by ID; no expected revision predicate. Its idempotent annotation is not an operation receipt or concurrency guarantee. |
| `apps/mcp/src/toolkit.ts:9` | Generic errors become `isError` plus text; no stable application error taxonomy. |
| `apps/mcp/src/tools.ts:185` | Local event schema lacks connected catalog's meeting fields. |
| `supabase/migrations/20261022010000_calendars.sql:71` | Events have UUID IDs and timestamps, without an upstream identity constraint in that migration. |
| `supabase/migrations/20261118000000_meeting_records.sql` | Attendance, votes, choices, attachment metadata, visibility checks, and a 26,214,400-byte attachment bound. |
| `apps/web/src/scripts/admin-meetings.ts:218` | Browser uploads to random storage paths, then inserts metadata; attempts cleanup after metadata failure. Storage and database operations are separate. |
| `supabase/migrations/20261113000000_offices.sql` | Offices and actual service terms already exist; term writes require admin authorization. |
| `docs/mcp.md` | Describes OAuth user access, admin content writes, and no rebuild triggered by content edits. |

A targeted search of local MCP code and migrations found no `idempotency`, `external_id`, `ingestion`, `operation_id`, `expected_version`, or `row_version` ingestion contract. This is a bounded source finding, not a claim about every deployed endpoint.

Connected catalog observations:

- `create_event` and `update_event` advertise `is_meeting`, `meeting_notes`, and `meeting_transcript`.
- `get_meeting` advertises notes, transcript, attendance, votes, and attachment metadata.
- `upload_meeting_attachment` accepts base64 bytes, filename and meeting ID, up to 25 MB. Its description explicitly says uploads create new attachments and an uncertain result requires inspection before retry.
- `get_meeting_attachment_url` advertises 60-second signed downloads and requires both meeting and attachment IDs.
- No dedicated attendance/vote or office/term write tools were found in the connected catalog inspected for this task. Existing Data API tables are not equivalent to a stable ingestion API.

## Capability and gap matrix

| Capability | Observed support | Requirement |
| --- | --- | --- |
| Basic event/calendar/person/organization CRUD | Present in local MCP and connected catalog | Retain; add an ingestion-specific transactional boundary. |
| Partial update semantics | Omitted fields preserved locally | Specify omitted/null/empty semantics in versioned schemas; preserve them in Python serialization. |
| External record identity | Not found in inspected contract | Unique `(source_id, entity_type, external_id)` binding, exact lookup, controlled adoption of preexisting records. |
| Retry-safe creation | Not established | Durable idempotency key and payload hash with stored result. |
| Editorial conflict protection | Local generic update filters only by ID | Required expected revision for updates and explicit field ownership/override policy. |
| Meeting details | Tables and read API exist; connected catalog has basic meeting fields | Atomic meeting child operations before automated votes/attendance. |
| File uploads | Connected catalog advertises base64 create; local UI has separate storage/metadata writes | Upload sessions, byte/hash verification, repeatable finalization and receipts. |
| Machine authorization | Existing API is user/admin oriented | Dedicated non-human principal scoped to source, organization/calendar and operations. No broad admin/service-role credential in updater. |
| Provenance | Some entities have source URLs | Structured source identity, evidence hashes/locators, parser version and change audit. |
| Recovery reads | Ordinary search/get tools | Exact operation and external-key lookups plus stable paginated source inventory. |
| Publication visibility | API edits do not trigger a site rebuild per docs | Distinguish API commit/read-back from static-site build visibility. |

## Proposed transport and authorization

Expose a versioned JSON HTTPS ingestion API backed by transactional database functions. Route names below are proposals, not existing endpoints. MCP wrappers can call the same functions for manual use, but the Python worker should not need an LLM or interactive agent runtime to upload.

Authenticate a dedicated updater principal with revocable, source-scoped credentials or short-lived tokens issued through a supported machine identity flow. The API owner must select and test that flow; do not assume the current OAuth server supports client-credentials grants. A human OAuth refresh token is not the recommended permanent service identity. The updater never receives the Supabase service-role key. If a server component uses privileged credentials internally, it must enforce source/object authorization on every operation before reaching privileged storage/database code.

Authorization includes permitted source namespaces, destination organizations/calendars, entity kinds, maximum publication visibility, and create/update/upload permissions. No account promotion, arbitrary SQL, or unrelated content deletion. Source IDs are registered server-side and cannot be claimed by an untrusted payload. Rotation/revocation must stop new writes without losing the ability of an authorized replacement principal to reconcile earlier operations.

## Proposed minimum endpoint contract

| Endpoint | Purpose |
| --- | --- |
| `GET /v1/ingestion/capabilities` | Contract version, entity support, limits, auth identity/scope, attachment mechanisms, receipt retention and error semantics. |
| `GET /v1/ingestion/records?source_id=...&entity_type=...&external_id=...` | Exact authorized identity lookup, including hidden records within the principal's scope. |
| `GET /v1/ingestion/inventory?source_id=...&cursor=...` | Deterministic snapshot/cursor pagination over source bindings; tombstones included. |
| `POST /v1/ingestion/changes` | Apply one bounded entity aggregate, with idempotency, revision preconditions and evidence. MVP supports one event, not arbitrary cross-entity batches. |
| `GET /v1/ingestion/operations/{operation_id}` | Authoritative durable operation state and receipt; accessible to authorized successor credentials. |
| `POST /v1/ingestion/uploads` | Later: reserve upload session bound to parent, source document key/version, expected bytes/hash and operation ID. |
| `POST /v1/ingestion/uploads/{id}/finalize` | Later: validate uploaded bytes and atomically publish attachment metadata and receipt. |

Define JSON Schema/OpenAPI and checked-in fixtures before implementing the client. Pydantic validates the same wire contract. Reject unsupported versions; never silently discard unknown fields.

Each change includes a client-generated operation UUID persisted before transmission, source/entity/external key, schema version, exact payload hash, source revision if known, expected destination revision (null for create-only), desired field changes, and evidence references. Use the operation UUID as the `Idempotency-Key`; require agreement if also included in the body. Return destination ID, new revision, payload hash, source binding and applied/no-op status.

Define the payload digest over the canonical UTF-8 request object with the `payload_hash` field omitted, then insert that digest into the transmitted envelope. The server recomputes it using the same versioned canonicalization rules. Store the complete transmitted bytes locally; a retry changes neither the operation ID nor the envelope. Authentication headers and signed transfer URLs are outside this digest.

## Identity, idempotency and concurrency semantics

1. Maintain external mappings independently of human-readable titles and times. Multiple source mappings may refer to one canonical entity only after explicit matching. Enforce source-key uniqueness in the database.
2. Before enabling creates, reconcile existing calendars/events/people with source keys. Ambiguous matches require review. A title/time match alone is not a permanent identity rule.
3. Atomically commit the source mapping, domain change, evidence metadata and operation receipt in one database transaction. Enforce a unique operation namespace/key and serialize simultaneous submissions.
4. Same key plus same canonical payload returns the original receipt with no new mutation. Same key with different payload returns `409 idempotency_conflict`. Idempotency lookup precedes revision evaluation, so a successful retry returns its receipt rather than failing against its own updated revision.
5. Create-only with an already bound external key returns `409 record_exists`; client reads and proposes an explicit update. If the API offers upsert later, it still needs required update preconditions.
6. Updates require an opaque monotonically changing entity revision. Every writer, including admin UI/MCP, must advance that revision in the database; otherwise stale-editor protection is incomplete. Stale preconditions return `412 revision_conflict` without mutation. A parent revision covers meeting child changes for aggregate updates.
7. Source ordering is separate from destination revision. A late-arriving older source version cannot overwrite a newer accepted observation; when no reliable upstream order exists, serialize proposals per entity and review conflicting observations.
8. Preserve idempotency receipts for the lifetime of the source integration initially. If retention later becomes bounded, retain operation tombstones and document a recovery policy; an expired key must not silently recreate an operation.
9. A missing lookup while an earlier request may still be in flight is not proof of failure. Resubmission is allowed only with the same durable key/payload under the verified server contract. Never generate a replacement operation ID just because a request timed out.
10. No distributed exactly-once promise: aim for at-least-once delivery with a single committed effect for each idempotency key, plus source-key uniqueness across different keys.

Field ownership is explicit: source-managed fields may update under revision checks; editorial overrides produce a review conflict. Omitted means unchanged; null clears only declared nullable fields; empty text/arrays are values only where allowed. Meeting child lists use explicit keyed changes in MVP, not implicit replacement. Absence from a scrape never means deletion.

Cancellation must have a supported state, not an invented date or deleted event. No cancellation field was observed in the inspected event schema. Add it before automatic cancellation publication, or leave those proposals blocked for review. Unknown vote outcomes remain raw source values plus an unresolved normalization state; do not force them into the current passed/failed/tabled/withdrawn set.

## Attachment protocol

The existing base64 tool is suitable for bounded manual uploads, but filename/size inspection cannot conclusively reconcile duplicates. Retrying a large encoded payload also adds overhead. Prefer API-issued scoped binary upload sessions, with resumable transfers when supported. Supabase [recommends resumable uploads above 6 MB](https://supabase.com/docs/guides/storage/uploads/standard-uploads); the inspected Civic Pulse metadata limit remains 26,214,400 bytes unless changed explicitly.

Reserve a session keyed by source document identity, content revision/hash, parent meeting and operation ID. The API assigns an immutable object path and returns bounded upload authorization. The client cannot choose another parent's path. Signed upload/download URLs are credentials and never become provenance URLs.

After transfer, finalize only if the server verifies actual byte length, SHA-256, allowed media type, parent authorization and current visibility policy. A claimed client hash alone is insufficient. In one database transaction create the attachment version, bind it to the meeting/source, and commit the receipt. Repeated finalization returns the original result.

Database and object storage are not one transaction. Use states `reserved → uploaded → committed`, with expired/rejected branches. Uncommitted objects must not be publicly retrievable. Reconciliation checks session state and object properties after uncertainty. Garbage collection acts only on definitively abandoned sessions after a grace period; unresolved sessions are retained.

Do not globally deduplicate across visibility scopes in a way that exposes private-file existence. Same source document plus same hash is a no-op; changed content at the same URL is a new immutable version with a supersession link. Keep source URL, file role, publication date if known and approval status separate. Documents over the limit remain in the updater evidence store with an explicit blocked-publication status, not silently dropped or truncated.

## Meetings and service history

For meeting ingestion, apply related attendance, motions and choices as one bounded transaction with stable external child keys. Existing triggers require a present attendee for a vote choice and clear choices when a person becomes absent/excused. The API must validate the complete proposal and make any such removal explicit; a partial scrape must not trigger cascading loss of previously published votes.

Structured agenda items, document roles/versions and evidence locators are additions to the inspected model. They can follow schedules/documents without blocking the event-only MVP. Office/term support should preserve existing date precision and separate actual service from candidacy. Do not create exact start dates or selection methods merely because required destination fields are missing in a source; quarantine the proposed term until evidenced.

## Errors, retries and visibility

Use a stable error envelope with code, safe message, operation ID, field errors, and retry guidance. Proposed HTTP mapping: 400 malformed contract; 401 invalid authentication; 403 unauthorized source/object; 409 identity/key conflict; 412 stale revision; 413 oversized payload; 422 domain validation; 429 rate limit with `Retry-After`; 5xx transient service failure. Proxy failures can occur after a commit, so status alone does not prove no mutation. Reconcile any ambiguous write using its operation ID.

A verified receipt establishes API commit. Authorized read-back verifies the stored aggregate and file hash. Website status must separately report `not_required`, `pending`, `visible`, `failed`, or `unknown`, since static pages may need rebuilds while some views read live data. Initially allow `unknown` plus explicit manual verification; a build-generation receipt can be added later. Do not report full website publication based only on database success.

## Release gates and acceptance tests

| Priority | Required release | Evidence before enablement |
| --- | --- | --- |
| P0 | Deployed contract inventory | Compare advertised catalog/OpenAPI, deployed revision and schema; resolve local/catalog mismatch. Validate read-only capability and exact lookup behavior. |
| P0 | Event ingestion | Dedicated scoped auth; external binding; receipts; create/update revisions; explicit cancellation handling; structured errors; read-back. |
| P1 | Document ingestion | Session/finalize APIs, checksums, visibility isolation, duplicate/no-response recovery and size limits. |
| P1 | Meeting children and people | Stable keys, atomic aggregates, revision advancement across all writers, protected editorial fields and service precision. |
| P2 | Operational expansion | Bulk independent operations, publication-generation tracking and multi-worker scaling after the narrow path is proven. |

Run these against a disposable real API/database/storage environment:

- Two concurrent identical creates produce one event and the same receipt; two different keys for one external identity cannot duplicate it.
- Same key/different payload is rejected with no mutation.
- Drop the response after commit, restart the updater, and reconcile to the original ID without another create.
- A manual edit between review and upload produces a revision conflict; an old source observation cannot roll data backward.
- An invalid vote causes the entire meeting transaction to roll back; missing/partial attendance cannot erase existing choices.
- Interrupt uploads before and after object creation and before and after finalization; retry produces one committed attachment version.
- Corrupt bytes, wrong hashes, cross-meeting session reuse, hidden record discovery and expired credentials fail at the real boundary, with valid controls.
- Rotate/revoke credentials and restore local state; successor authorization reconciles unresolved receipts without granting unrelated access.
- Confirm mixed public/private calendars, meetings and attachments remain correctly gated.
- Verify API commit independently from website visibility and report pending builds accurately.

Production validation is a bounded authorized smoke check of the exact release, not failure injection. Disable new routes or the updater principal to roll back behavior; retain committed data, mappings and operation receipts. Any correction to published civic facts is a new evidence-backed, revision-checked operation.
