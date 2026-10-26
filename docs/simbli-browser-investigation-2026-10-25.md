# Simbli attachment recovery investigation

## Findings established locally

The previous probe's two 6,058-byte responses are Imperva interstitial HTML.
Neither is an attachment. The saved page dynamically creates a script element
and assigns a root-relative URL with two path segments. The original fallback
only discovered static script tags and accepted single-segment script paths.
Replaying the saved HTML in both real Chromium and Firefox confirmed that this
script request was blocked by our own policy. The previous probe never fetched
it. This is a sufficient explanation for the observed failure; it does not prove
that fixing it will satisfy Imperva.

A transport regression test also confirmed duplicate User-Agent headers: the
lowercase browser header and uppercase CivicPulseUpdater fallback were both sent.
HTTP header names are case-insensitive; a Python dict was not appropriate here.

Measured offline with the installed Playwright browsers:

| Browser | Default user agent indicator | navigator.webdriver |
|---|---|---|
| Chromium 156 | HeadlessChrome/156.0.8078.4 | true |
| Firefox 157 | Firefox/157.0, no headless marker | true |

There were no new requests to Simbli during this investigation. Browser binaries
were downloaded from Playwright's distribution service. Local replay fulfilled
HTML from stored evidence and aborted all subordinate network requests.

## Changes made

- Use case-insensitive HTTP headers, preserving exactly one supplied browser UA.
- Discover the literal `scriptElement.src` bootstrap pattern in recognized
  challenge HTML, permitting its exact same-host URL with one or two constrained
  path segments. Unlisted URLs and POST requests remain blocked.
- Reduce minimum Simbli GET spacing from three seconds to one second, retaining
  serialization, response/aggregate byte caps, request caps, and all existing
  server-deferral and browser-attempt cooldowns. Slower configured pacing wins.
- Regression tests reproduced both defects before fixes. The Chromium integration
  test now executes a dynamically inserted script, retains cookies, and recovers
  a synthetic PDF while blocking POSTs and third-party requests.

The production fallback still selects Chromium; Firefox was tested as a local
investigation, not added as an automatic retry or selectable production engine.
The default Chromium UA still contains HeadlessChrome; duplicate-header repair
is not a claim that automation is undetectable.

## Recovery options, in order

1. After the existing cooldown expires, run a single attachment probe with the
   corrected bootstrap policy. Record blocked request method/type/path and safe
   error categories so any next failure can be explained. Do not infer that all
   challenge requests need permission from one blocked request.
2. Add an explicit Firefox option with its genuine default UA. Test cookie,
   network isolation, byte caps and sandbox behavior for Firefox before live use.
   Do not automatically cycle through browsers on a source deferral. Firefox
   avoids the Chromium UA marker but does not hide all automation indicators.
3. Compare Chromium's newer headless channel or headed Chromium. A context UA
   override is supported, but it must match browser version/platform and browser
   client hints; rotating or stale UA strings can introduce inconsistencies.
   WebKit is supported by Playwright but is lower priority because it requires
   its own compatibility and containment tests and no evidence favors it here.
4. If challenge requests now run but recovery still fails, consider an isolated
   native-browser transport. Today HTTPX performs all TLS and HTTP requests, so
   switching rendering engines does not change the source-facing TLS stack.
   Native networking needs equivalent egress controls and streaming byte limits;
   checking download size after completion does not preserve our current limits.
5. Obtain an official attachment feed/export or approved access from the district
   or Simbli. Alternatively, import manually downloaded public files with their
   source URL, checksum and review proposal. These avoid coupling routine updates
   to an undocumented challenge flow. No contact or upload has been performed.

One GET per second and one browser at a time is a reasonable starting policy,
not a verified statement of Simbli's capacity. Keep Retry-After authoritative and
stop on rate-limit responses. Adjust browser-attempt cooldowns only after useful
recovery evidence; retries could not fix the blocked-script bug.

## Documentation consulted

- https://playwright.dev/python/docs/browsers — Chromium, Firefox, WebKit and channels.
- https://playwright.dev/python/docs/api/class-browser — context user_agent option.
- https://playwright.dev/python/docs/network — routing, proxies and service workers.

All collection remains review-only; no publishing, approvals or original run
records were changed.
