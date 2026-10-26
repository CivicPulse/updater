# Remaining Simbli challenge investigation — blocked by interactive verification

## Confirmed collector defects fixed

The old browser lifecycle stopped after an initial navigation, three-second wait,
and one reload. It could close before delayed JavaScript or a second challenge
stage completed. Recovery now services page-initiated navigation until valid
content, a failure, or the existing 45-second budget. One manual reload is allowed
after ten seconds; the existing 12-request and response byte caps remain unchanged.
Captured documents are not rendered. Browser identity is read before navigation
so aborted navigation cannot invalidate successful recovery during cleanup.

Offline replay of saved challenge HTML and JavaScript established that Simbli's
new verification flow requires POSTs. It uses the already fetched script path
with `d=simbli.eboardsolutions.com`, first on a two-segment path and then on a
one-segment path. Previously those POSTs were blocked.

Added a constrained exception: only a same-host script endpoint fetched successfully
in the current recovery can receive verification POSTs. Query scope is the fetched
query, empty, or exactly the observed domain parameter. Maximum two POSTs/recovery,
64 KiB/body. Requests share the existing pacing, deadline, 12-request recovery and
120-request run budgets. Civic API writes, unlisted paths, wrong hosts and arbitrary
query parameters remain rejected. Request and response bytes are content-addressed
as private evidence with additive method/direction metadata. Old evidence defaults
to GET/response. Verification exchanges do not upload Civic Pulse records.

## Live results

Firefox successfully submitted verification and received JSON containing a token,
renewal interval and cookie domain. The subsequent page was a different Imperva
security check. Offline replay identified hCaptcha. A bounded Chromium comparison
reached the same type of security check; automatic browser rotation remains absent.

Final Firefox retry:
`609f8b11-b748-4300-83a6-c630186bd15e`.

Outcome: **failed**, zero completed records, zero new proposals. Explicit error:
`Simbli requires interactive CAPTCHA verification; defer collection`.
The browser made eight additional requests, within its existing cap. The source
still required interactive verification after the supported verification exchanges.
No hCaptcha request was sent and no CAPTCHA solving was attempted. Further live
retries were stopped and the cooldown was retained.

One earlier collector process exited with signal 15. Its saved run
`e8069ea6-0316-4ef6-b191-70172195e27f` was marked interrupted by the next run under
the existing state-lock recovery behavior. Its evidence was retained; it is not
counted as successful.

## Remaining work

The six-complete-meeting objective is **not achieved**. Five complete school board
meetings and five pending proposals remain available from earlier collections.
September 2 Public Hearing #1 is still incomplete. Publicly published minutes
already collected for the completed meetings remain intact. No approvals,
publishing or uploads occurred.

Completion now requires a source-access change (for example, provider-approved
collector access) or obtaining official source files through a human-accessible
workflow and importing them with provenance. A normal user's solved browser
challenge cannot be assumed to transfer to this isolated broker. No provider has
been contacted and no automatic follow-up has been scheduled.

## Validation

Full suite: 113 passed, including delayed-script recovery, verification POSTs,
CAPTCHA detection, cookie handling and existing adapters. Four additional transport
boundary tests then passed, rejecting writes before DNS/network access. Ruff and
Bandit passed; Trivy filesystem scanning found no HIGH/CRITICAL findings during
this task (downloaded browser binaries are outside that check).

Numerical limits retained: 30-second local cooldown, 45 browser attempts/hour,
five distinct recovery targets/run, one request/second, 12 additional browser
requests/recovery, 45-second browser budget, and existing response/aggregate byte
limits. Longer server-requested delays remain authoritative. Review-only mode is
unchanged.
