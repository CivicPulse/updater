# Review only foundation validation

Validated locally on October 25, 2026, America/New_York. These are implementation
and local runtime results, not a production deployment or independent security
review. The GitHub Actions workflow is provided but has not been run remotely.

## Implemented scope

Python 3.13.15 package, uv lockfile, Typer CLI, Pydantic models, Loguru logs,
explicit adapter loading, GET-only source transport, immutable evidence files,
SQLite/Alembic state, proposal review, and offline replay. Bibb Democrats HTML
and ICS collection is enabled through its source manifest. There is no Civic
Pulse API client or scheduled task; `publish` is disabled.

## Checks

| Check | Result |
| --- | --- |
| pytest | 43 passed |
| Ruff lint and format checks | Passed |
| Bandit production source scan | No findings; no disabled checks or skipped source lines |
| Trivy 0.76.0 uv.lock, including dev dependencies | No high/critical vulnerabilities |
| Trivy Dockerfile configuration | No high/critical findings |
| Trivy repository secret scan | No findings; local environment and collected evidence excluded |
| uv source distribution and wheel build | Passed; packaged migrations checked |
| Container build | Passed; Alpine runtime, locked production dependencies, non-root UID 10001 |
| Final container Trivy vulnerability scan | No high/critical vulnerabilities in OS/Python results |
| Final container CLI and database initialization | Passed with networking disabled |

The container test disabled Podman's periodic health timer because the test
runtime has no user systemd service. The CLI startup and database migrations ran
successfully. No claim is made about a deployed health-monitoring service.

Test coverage includes real TLS hostname/SNI verification against a local server,
unsafe URL/DNS/redirect rejection, response bounds and retries, real process
locking, database transaction rollback, restart/replay, checksum corruption,
source pagination and limits, plugin allowlisting, rescheduling, recurrence/DST,
all-day dates, cancellations, rejected malformed calendars, review acknowledgement,
and disabled publication. Civic Pulse API/browser integration tests do not apply
to this release because it has no publication path.

## Live source result

The bounded live collection for October 25 through December 31, 2026 saved two
events and two proposals in the repository's gitignored `.state/` directory.
Run ID: `acd06f86-fab4-440d-a119-49ba409824cc`.

Offline replay `91296646-a984-4049-ac6c-9559c1d10562` processed both records with
zero new proposals. Both proposals remain pending; neither was approved or
uploaded. Use `uv run --locked civpulse-updater changes list` to inspect them.

Both source events contain a one-hour HTML/ICS discrepancy:

- November 21: HTML shows 10 AM–noon; ICS represents 11 AM–1 PM Eastern.
- December 17: HTML shows 6–8 PM; ICS represents 7–9 PM Eastern.

These are source conflicts preserved for review, not assertions about the actual
meeting times. Full listing and ICS evidence is retained locally. The adapter's
candidate timestamps come from ICS and require warning acknowledgement before
local approval.

## Issues found and corrected during validation

- An initial filelock dependency failed the vulnerability gate. The requirement
  now excludes the vulnerable major version; the lockfile selects 4.2.4.
- The initial Debian runtime inherited high-severity OS and packaging findings.
  The final container uses a patched Alpine base and excludes build tooling from
  the runtime. The final image scan passed without suppressing vulnerability IDs.
- Replaying old evidence initially could propose replacing a newer observation.
  A failing regression test reproduced this; observation ordering now prevents it,
  with an additive state migration and a passing regression test.
- The official listing measured 2,006,558 bytes, slightly above the initial
  generic two-million-byte bound. A failing transport/configuration regression
  test reproduced this. The Bibb source now has an explicit three-million-byte
  bound; the general bounded-download policy remains enforced. The failed live
  attempt remains in local history, followed by the successful run above.

## Artifact references and recovery

- uv.lock SHA-256: `8982b2944365ef6ef3e0b6f92275923efd65c4ebb465b20ba79255e7927e213a`.
- Wheel SHA-256: `eb3f54bb9d0808f63efd08b1c243cc7139387d610d0ff9426dcd800ec9082381`.
- Final local image ID: `ebec3a02f7e865ff29bce83abbf8d233669acdcdd219bd80df25857b3d88f4d8`.

The image was built and tested locally, not pushed or deployed. Python dependencies
are locked; base image tags and Alpine package updates can change on a later
build, so scan each resulting image and record its digest rather than treating
this result as permanent.

To disable this release, stop invoking the collector; there is no installed
scheduler to remove. Preserve `.state/` before switching versions. State migrations
are additive and downgrade is intentionally unavailable; restore a compatible
backup for a previous release. No Civic Pulse content needs rollback because no
remote publication occurred.
