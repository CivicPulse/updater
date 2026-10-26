# Bibb school board Firefox collection attempt

At the user's request, changed the local browser-attempt and default deferral
cooldown from 900 to 300 seconds. Existing Retry-After deferrals remain
unchanged, and longer future Retry-After values remain authoritative. One-second
GET spacing, three browser attempts per hour, per-run limits, byte limits and
review-only behavior remain unchanged. Updated the saved browser reservation
under the state lock so the pending collection used the five-minute cooldown.

After expiry, ran the September 9, 2026 window with Firefox fallback enabled.
Run ID: `cf1bd7f8-f725-4e85-be35-a9b23c3e6336`.

Result: failed during discovery. Simbli returned a recognized challenge for the
meeting listing, before any attachment could be requested. The fallback is
restricted to attachments and therefore did not launch Firefox for the listing.
A new cooldown was saved. No repeated collection was attempted.

Zero records processed and zero new proposals. The existing three pending school
board proposals remain available. No approvals or uploads occurred. Collection
has not recovered the remaining attachments; doing so requires the listing to
become accessible, or a separately implemented and tested listing-browser path.

Cooldown tests: 17 passed (browser tests deselected because the only code change
was the cooldown constant). Ruff lint and format checks passed.
