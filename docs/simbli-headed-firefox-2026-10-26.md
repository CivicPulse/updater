# Supervised Firefox recovery — October 26, 2026

The `collect --headed` option enables a visible, isolated Firefox fallback for
manual CAPTCHA verification. It implies browser fallback and Firefox; conflicting
Chromium selection and other source adapters fail validation. Public Bibb meeting
detail pages now support recovery in addition to listings and document downloads.
Their recovered HTML must contain the public API context before collection resumes.

The user explicitly requested removing challenge blockers outside rate limits.
In headed mode, ordinary public HTTPS GET/verification POST traffic and redirects
therefore use the same pinned broker without automatic-mode script/host filters,
12-request sublimit, two-POST sublimit, or 45-second timeout. Interaction is bounded
to ten minutes; no automated reload interrupts a human challenge. A user may reload
the page after verification if Simbli does not navigate itself.

Unchanged protections: one request/second, 30-second cooldown, 45 browser
attempts/hour, five recovery targets/run, 120 requests/run, server deferrals,
source download and aggregate byte limits, DNS/public-address checks, TLS
verification, evidence capture, process lock, and review-only proposals. Manual
POST bodies use the source per-response byte limit. Automatic mode retains its
64 KiB verification-body cap. Service workers, WebSockets, non-HTTPS and private
network destinations remain disabled. Simbli civic API POSTs remain disabled.
`civpulse.org` and all its subdomains are explicitly blocked by the manual broker.
No Civic Pulse upload or service connection is part of this workflow.

Successful cookies and Firefox's native user agent are reused only in memory
within the current collection. Local raw verification evidence may contain tokens
and must remain private. The browser profile is not persisted.

## Validation and live findings

- Headed Firefox opened on the local display with all network traffic disabled
  during the initial display smoke check.
- The broker integration fixture verifies visible-mode launch selection, a
  synthetic verification submission larger than 64 KiB, saved request/response
  evidence, cookie handoff, and automatic document recovery. CI renders this same
  broker flow headlessly; it does not solve or contact a real CAPTCHA.
- Scope tests cover explicit Civic Pulse blocks, private DNS rejection, civic API
  mutation rejection, headed option validation, retained total request budgets,
  and public meeting detail validation.
- First live run `860bcf75-193f-4ae6-97f5-f069d619c560` found a meeting-detail
  challenge outside the previous listing/attachment-only recovery scope. Added
  the strictly scoped public Bibb meeting detail route.
- Run `9e44a3a0-de9a-473b-922e-113d32dc23ff` displayed the CAPTCHA but stopped at
  the old 64 KiB verification-body cap before the user could finish. Removed that
  automatic-mode sublimit in headed mode and added the regression fixture above.
- A further supervised attempt was started after the saved cooldown. Its final
  outcome is recorded below when the interaction finishes.

This feature does not establish that a sixth meeting has been collected; complete
meeting bundles and published minutes must still pass the existing adapter and
review-proposal pipeline.

## Completed result

Supervised run `4a76ca53-ec14-41f4-8a1c-8d1cb5f1309a` completed successfully:
Firefox recovered the meeting page after 48 broker requests, and the collection
produced two complete September 2 meeting records with one new pending proposal
and no errors. The existing Called Board Meeting remained unchanged.

The sixth complete meeting is **September 2, 2026, 5:30 p.m. — Millage Rate Public
Hearing #1 (138066)**. Its bundle contains one agenda, six agenda items, one
attachment, and published minutes. Minutes evidence SHA-256:
`e889523866282f6503f5e92deff2dc7f6216e7d496131f19173449ca4e116488`.
Pending proposal: `0cfb53ab-dbba-49f4-a4a2-5f11d318afa4`.

The local store now contains six complete Bibb school-board meeting records.
Four have published minutes captured; the other two had no published minutes in
the official listing. Offline replay
`357de68a-c861-44f3-bd74-c16798755fe0` reproduced both September 2 records with zero
new proposals and no errors. No observations in the successful run contacted
Civic Pulse; all proposals remain local and review-only.

Validation: 131 tests passed, including browser fixtures; Ruff formatting/lint,
Bandit, and Trivy HIGH/CRITICAL vulnerability/misconfiguration/secret checks passed.
