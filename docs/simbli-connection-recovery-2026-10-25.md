# Simbli connection fixes and completed collection

## Investigation and changes

A bounded two-request comparison used the same public GetMeeting URL. The
updater identity returned HTTP 200 JSON (3,176 bytes); the Firefox user agent
without cookies produced an HTTPX ReadTimeout. Applying the browser identity to
all later API calls was therefore a reproducible failure mode. JSON API calls
under `/Services/api/` now retain the updater identity and do not inherit browser
cookies. Public pages and attachments continue using the recovered session.
Connection failures now expose a safe category such as `read_timeout`, never raw
exception text containing source parameters.

Corrected cookie domain matching to accept the legitimate parent domain
`eboardsolutions.com`, while still sending cookies only to the exact allowed
Simbli host and respecting path/expiry. This was a separate scope bug; it alone
did not resolve all attachment challenges.

The listing and several distinct attachments each needed browser recovery. The
old one-attempt-per-run restriction prevented a complete collection. Recovery
now permits at most five distinct targets per run, never retries a failed target,
and runs browsers serially. Local waits honor the cooldown without source traffic.
Failed recovery or server deferral still stops subsequent network access.

At the user's explicit direction, the final local cooldown is **30 seconds** and
the hourly browser-attempt cap is **45**. Longer Retry-After values remain
honored. One-second GET spacing, 120 requests per run, 12 additional GETs per
browser recovery, browser network deadlines, and existing 100 MB response / 500 MB
collection byte caps remain in place. Evidence and review-only mode are unchanged.

## Final live result

Window: September 9, 2026.
Command: `collect bibb-school-board --from 2026-09-09 --to 2026-09-09 --browser-fallback --browser firefox`.
Run: `52b27227-9a26-4297-8b29-31a40283a313`.
Status: **complete**, three records, zero errors.

Preserved and checksum-verified documents:

- Three agenda structures and seventeen agenda-item documents.
- Four attachments, including the previously challenged files.
- One published minutes document (11,704 bytes, five item-minute entries).

| Meeting | Attachments | Minutes |
|---|---:|---|
| Millage Rate Public Hearing #2 | 1 | Published and captured |
| Millage Rate Public Hearing #3 | 1 | Not published in official listing |
| Called Board Meeting - Final Adoption of Millage Rate | 2 | Not published in official listing |

Minutes SHA-256:
`0ed2f1950fc44b5b2d5c2b79db69ec8923fc81e9060ccfea34e9a39a19a1d384`.
The adapter also collects meeting-level and agenda-item minutes attachments when
present. No minutes attachments were identified for this window. AUTHORIZED /
AUTHORISED and unpublished statuses are not treated as public publication.

Data matched the existing records: zero duplicate proposals created. All three
existing school board proposals remain pending. No approval or upload occurred.

Offline replay `87a7a2a6-7243-447d-a551-b41ed4ff5a2e` also completed with three
records, zero errors and zero new proposals.

## Validation

Regression coverage includes API identity separation, safe timeout diagnostics,
parent-domain cookie handling, per-run recovery caps, distinct-target cooldown
waiting, server deferral preservation, and published-minutes attachments. Browser
fixtures exercise Chromium and Firefox with no live Simbli traffic.

Final full suite: **108 passed**. Ruff lint/format and Bandit passed. Trivy
filesystem checks earlier in this task found no HIGH/CRITICAL findings; downloaded
browser binaries are outside that filesystem scan's coverage.
