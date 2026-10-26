# Simbli browser prototype validation — October 25, 2026

Implemented optional Python Playwright/Chromium recovery for recognized HTML
challenge responses on Bibb tenant attachment routes. All requests pass through
the pinned Python transport and retain existing byte limits and evidence storage.
The browser is opt-in; publishing remains disabled.

## Verification

- Real sandboxed Chromium with synthetic HTTP responses executed a challenge
  script, retained its cookie, and recovered a PDF. POST and third-party traffic
  never reached the transport. Shared byte accounting and evidence were checked.
- Tests cover persistent pacing, attempt caps, cooldowns, Retry-After, request
  budgets, target restrictions, aggregate byte accounting, and probe immutability.
- Ruff and Bandit passed. Trivy filesystem scanning of the uv lockfile (including
  development dependencies), configuration and secrets found no HIGH/CRITICAL
  findings. This does not scan the downloaded Chromium binary or a browser image.
- CI includes a separate optional-browser test job with Chromium installation.
  The existing Alpine container remains the HTTP-only runtime.

## One bounded live probe

Source run: `19903d79-34fc-4d01-9a71-d6c5e23b1716`.
Probe: `3ade4147-a4e0-4422-b82b-3431c7fad014`.
Time: October 25 at 21:11 EDT (October 26 at 01:11 UTC).
Target: attachment AID 2059365, meeting 138067, tenant 4013.

Result: **not recovered**. Browser recovery attempted; only two GETs were made,
12,116 bytes received, zero proposals created. The source cooldown was persisted
and no second live probe was attempted.

Content-addressed report SHA-256:
`2c2a8925e4fc3e6cdceda03c9aed40a678ee5d62141bc8eb82ca4856a33b8393`.
The report and raw responses are in the private local evidence store.

This validates safe failure against the live source, not successful live anti-bot
recovery. The broker deliberately restricts requests and uses Python's TLS client;
challenges depending on other traffic or native browser TLS may remain blocked.
Use a shared state directory across Simbli collectors so pacing and cooldowns
remain coordinated. Do not reset cooldown state or loosen protections to retry.
