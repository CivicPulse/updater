# Selectable Firefox and bounded live recovery

The Simbli fallback now accepts `browser_engine = "firefox"` in source TOML or
`--browser firefox` on collect/probe commands. Chromium remains the default.
Collection still requires `--browser-fallback`; selecting an engine alone does
not enable it. Unsupported engines are rejected. There is no automatic engine
rotation or retry after a source deferral.

Both engines share the existing pinned HTTP transport, one-second minimum GET
spacing, persistent cooldowns, request/byte budgets, evidence store, and disabled
publishing. Firefox disables WebRTC, HTTP/3 and prefetching and uses its genuine
default user agent. Browser routes and the unavailable proxy restrict network
access; POSTs and third-party requests remain blocked. CI installs both browsers
and runs the same real-browser fixture against each.

## Live result

The previous saved cooldown expired at 2026-10-26 01:26:59 UTC. After verifying
expiry, exactly one probe was started at 01:27:16 UTC (October 25, 21:27 EDT):

```sh
uv run --extra browser civpulse-updater probe-simbli-attachment \
  19903d79-34fc-4d01-9a71-d6c5e23b1716 --browser firefox
```

**Recovered**, using Firefox through the Playwright broker:

- Target: tenant 4013, meeting 138067, attachment 2059365.
- Three GETs: 6,058-byte challenge HTML, 836,222-byte challenge JavaScript,
  3,544,999-byte PowerPoint attachment.
- Total downloaded: 4,387,279 bytes; finished at 01:27:22 UTC.
- Document checksum verified locally; ZIP directory contains `[Content_Types].xml`
  and `ppt/presentation.xml`. File contents were not executed.
- Zero proposals and uploads; original source run and records remain unchanged.
- No second attachment or browser comparison was attempted.

Probe ID: `d201ebd4-ae82-4934-9454-074fbf505186`.
Document SHA-256:
`fcd21a65e5a69e5d5b091800561e4dc3972973893b067f140ca0999b2159a333`.
Report SHA-256:
`2ee428db40c976aaf675e296e4a2168f8d6f8fc3dbb365a3dd4a2bfb057855d3`.

Both are stored in the private content-addressed evidence store. The probe saves
artifacts only; a later normal collection is needed to create review proposals.
Success establishes recovery for this attachment at this time. Since browser
selection and the bootstrap/header fixes changed together, this does not establish
that Firefox is necessary or that corrected Chromium would fail.

## Validation

The full suite covers Chromium and Firefox script/cookie recovery, blocked POST
and third-party traffic, byte accounting, persistent pacing/cooldowns, probe
immutability and CLI engine validation. Ruff and Bandit passed. Trivy filesystem
scans found no HIGH/CRITICAL findings in dependencies/configuration/secrets; the
downloaded browser binaries were not scanned by that check.
