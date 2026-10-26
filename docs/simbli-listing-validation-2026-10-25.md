# Simbli listing fallback and collection validation

Added opt-in browser fallback for the exact public tenant-4013 listing URL.
Recovered listings must pass the existing Simbli adapter's discovery parser.
Other listing URLs, tenants and arbitrary pages remain excluded. Firefox and
Chromium use the existing broker, byte budgets, one-second GET spacing,
five-minute cooldown, three-attempt hourly cap and one-browser-attempt run cap.

Successful browser cookies/user agent are retained only in memory for that run's
subsequent pinned HTTP requests, respecting host/path/expiry. No browser profile
is saved and no additional browser attempt is silently started. The recovered
listing is stored as original response bytes, allowing existing offline replay.

## Checks

103 tests passed, including real Chromium/Firefox listing and attachment recovery,
blocked POST/third-party traffic, session reuse, cookie scope and expiry, invalid
listing rejection and existing collection/replay tests. Ruff, Bandit and Trivy
filesystem HIGH/CRITICAL checks passed. Browser binaries were not part of the
Trivy filesystem scan.

## Live collection

After confirming the persisted cooldown had expired, ran the September 9, 2026
Bibb school board window with `--browser-fallback --browser firefox`.

Run: `33f93e0a-5162-49e6-b66d-eeb00fbcc154`.

Firefox successfully recovered the listing (163,617 bytes). Its checksum was
verified from stored evidence, and offline discovery confirmed three relevant
meetings. Listing SHA-256:
`449d26363d3cea39ae0bc939fbf7884d07b53412aec60ff8b5c51502ed380c85`.

The run subsequently failed with three `Source connection failed` errors during
meeting retrieval. The transport deliberately does not expose raw connection
exception details; the underlying cause has not been established. No automatic
retry was added and no second collection was run.

Final outcome: zero completed records, zero new proposals. The existing three
pending proposals remain intact. Remaining attachment recovery is not complete.
No approvals, publishing or uploads occurred.
