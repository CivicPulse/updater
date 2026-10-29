# Georgia SOS download investigation — October 28, 2026

## Turnout HTTP 403

The public dashboard request fails before Qlik authentication or aggregate parsing.
The source manifest still matches the iframe published on the official SOS
unofficial-turnout page. The iframe itself displays a CloudFront 403 in a normal
browser on this machine. Direct HTTPS requests with both the updater's pinned
transport and ordinary HTTPX also return 403 for all three bootstrap resources:

- `https://d11khjvhu86vzz.cloudfront.net/DH.ELECTION2024/index.html`
- `https://d11khjvhu86vzz.cloudfront.net/DH.ELECTION2024/js/vars.js`
- `https://d11khjvhu86vzz.cloudfront.net/shared-config.js`

The response identifies CloudFront and says “Request blocked.” This establishes
an upstream/CDN rejection from this machine, not a Civic Pulse permission error,
bad Qlik token, parser error, or failure caused solely by DNS pinning. It does not
establish the CDN rule responsible or a worldwide outage. No proxy rotation,
TLS-verification change, CAPTCHA bypass, or credential change was attempted.

Reproduction before the diagnostic change:
`aa8028e6-f03a-4250-b15a-634ce9c2345b` returned the generic
`Source returned HTTP 403` at discovery.

Reproduction after the change:
`f8ddcab4-c153-4d2c-abb7-6a58403b9d96` reports
`Turnout dashboard HTML: Source returned HTTP 403`. Both attempts produced zero
records and proposals and performed no publication. The last successful published
snapshot remains the October 27 source observation.

The collector now names each fixed bootstrap stage and distinguishes a non-2xx
anonymous-authentication response by its HTTP status. It still excludes response
bodies and tokens from errors/evidence, does not retry 403, and withholds partial
captures. This improves diagnosis; it does not resolve the upstream denial.

## Voter-history CAPTCHA

The official voter-history page loads and offers the exact 2026 / November 3
general-and-special-election selection. Selecting those options enables Submit.
The page embeds invisible enterprise reCAPTCHA. Prior scheduled audits stopped
before submission because completing the CAPTCHA needs action-time confirmation
under the browser tool's policy. They did not establish a rejected CAPTCHA,
expired authentication, broken download link, or source outage.

During this investigation the form was prepared with that exact election and
confirmation was requested before Submit. The outcome remains untested until that
confirmation is received. Do not report this condition as a failed CAPTCHA or
assume the mere presence of a reCAPTCHA iframe proves the download is broken.
Unattended scheduling cannot guarantee completion of a step requiring interactive
confirmation. Preserve the last successful archive and all private unmatched
records while awaiting a new download.

### Confirmed download test after user approval

The user approved submission and CAPTCHA completion for this test. At about
12:20 p.m. EDT, the normal Chrome profile successfully submitted the exact election
selection and returned a fresh, short-lived signed ZIP link. No interactive image
puzzle was displayed. The ZIP downloaded successfully (15,295,303 bytes), passed
ZIP/CSV reading, and contained 2,128,302 statewide history records. Bibb contained
26,140 unique November 3 records: 25,277 matched to the February roll across 31
precincts and 863 unmatched, retained separately. No public import was performed
as part of this download test.

The in-app browser encountered a separate Cloudflare human-verification screen
before reaching the form; its checkbox did not advance to the form. Normal Chrome
reached the form and completed its ordinary download flow. This supports using
Chrome for the approved interactive download, not bypassing the protections.
It does not establish that future scheduled CAPTCHA checks can run unattended or
that this approval covers future action-time confirmation requirements.

Private test files and audit:
`.state/analysis/bibb-2026-11-03-precinct-turnout/runs/20261028T162047Z-download-debug/`.
Archive SHA-256:
`5ac85d517150dd93d76b43db1a0dfa0f3c02b20a5e24fb204b36e4d4b434a519`.
Signed URLs and CAPTCHA tokens were not persisted in the audit.

The browser tool's supported `downloadMedia` operation successfully saved the
official generated ZIP link. A subsequent ordinary click used for a screenshot
instead navigated to `ERR_BLOCKED_BY_CLIENT`. That navigation error did not undo
the already downloaded, checksum-validated archive. Prefer the supported file-link
download operation over navigating the tab to this attachment; do not describe
the client navigation error as an SOS HTTP 403 or CAPTCHA rejection.

## Validation and follow-up

Four new HTTP-boundary regression cases failed before the change and passed after
it: 403 at dashboard HTML, app configuration, tenant configuration, and anonymous
authentication. They assert no Qlik connection, no records/proposals, no retries
of the denied resource, and no private response/token content in stored evidence
or returned errors. The existing real local TLS/WebSocket capture and offline
replay test remains the valid-path control. The combined turnout/transport suite
passed 51 tests. No hosted schema, frontend or infrastructure change was made.

Recovery requires the officially published dashboard path to become accessible
again, or a verified replacement published by SOS. Do not silently substitute a
different dashboard, weaken validation, or treat unavailable data as zero. For
history, test the prepared official download after confirmation; retain signed
download URLs privately and never reuse expired links.
