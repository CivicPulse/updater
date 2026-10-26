# Macon-Bibb commission 2026 systematic capture

Completed October 26, 2026, America/New_York. Requested window: January 1–December 31, 2026. **All 58 currently listed public legislative records are captured locally**, with zero missing selected identities. This describes currently posted material, not the completed calendar year.

## Coverage and source evidence

The official all-category API listing contains 144 public 2026 rows, preserved across ten pages. Category 26 supplies 49 Board of Commissioners and Pre-Commission records. Category 24 (General) supplies nine additional legislative records: Committee on Committees (2365), special-called commission meetings (2450, 2578), and six public hearings (2454, 2497, 2522, 2551, 2552, 2553). Unrelated boards are excluded. No 2026 Committee of the Whole rows were exposed by this listing; no meetings are inferred from recurring schedules.

Sources:

- [Official agenda and minutes portal](https://maconbibbcoga.portal.civicclerk.com/)
- [Public event categories](https://maconbibbcoga.api.civicclerk.com/v1/EventCategories)
- [County meeting broadcasts](https://www.maconbibb.us/commissioners/meeting-broadcasts/)
- [Official commission video playlist](https://www.youtube.com/playlist?list=PLBW7To4TmDBbk8QRdkv6YpywifbDVTEfv)
- [County FY27 budget materials](https://www.maconbibb.us/proposedfy27budget/)

There are **nine cancelled records and ten future records**. Three distinct source identities (2138, 2582, 2594) list the same December 1 Pre-Commission title and 4 p.m. time; all are preserved and flagged. The unnamed April 15 “One Time Event” (2453) has no description or agenda identifying the body. Its metadata is retained separately and it is not counted among the 58 legislative records.

## Preserved material

- **615 PDF references / 358 unique PDF contents**: 39 published agenda packets and 576 agenda-item attachments.
- **47 structured agenda responses**, including eight empty cancelled-meeting shells; publication flags distinguish those from published agendas.
- **886 agenda items, 336 source-reported motions, and 2,741 individual votes**, exported as JSON and CSV. Vote totals reconcile to motions, with no duplicate person-votes within a motion. No attendance is inferred from absence in a vote list.
- **Six supplementary FY27 budget PDFs**, including the presentation, budget book, legal advertisements, tax digest and millage presentation, preserved unchanged with checksums.
- **16 official county video references**. The public playlist's first 100 entries reach earlier years; checking the channel's initial streams/videos pages found an additional August 4 recording absent from the playlist. These are linked metadata, not archived video bytes or captions. This is not an exhaustive guarantee for unlisted, unavailable or otherwise unindexed recordings.

CivicClerk advertises no media on these event records, so its media flag alone is insufficient for video research. The county supplies a separate official broadcast playlist. No standalone meeting-minutes PDFs were exposed in publishedFiles. Minutes of earlier meetings attached to later packets remain attachments to the meeting that published them. Public-API minutes/vote fields are source reports and do not establish formal minutes approval.

## Capture and replay ledger

| Run | Result |
| --- | --- |
| `5ee8a948-ad3b-4f5c-a061-04247e1a5536` | 49 main-list records; 47 new proposals; zero errors |
| `14737a28-0e3e-4860-af54-56101791af59` | 9 General-category records; 9 new proposals; zero errors |
| `b5dc92f7-9096-49d9-b3ac-bba8e517dfef` | Offline replay: 49 records, zero new proposals/errors |
| `f779d170-ff78-4d01-a33b-93c5465871c4` | Offline replay: 9 records, zero new proposals/errors |

Two main-list records already existed from the earlier October 6 validation. Exactly **58 commission proposals are pending local review**. No approval or Civic Pulse upload occurred.

The initial capture used a private bounded source profile: 100 meetings, ten listing pages, 5,000 resources, 2,000 requests, 500 MB per response, 10 GB per run and 180-second response deadlines, with one-second pacing. The original `--systematic` option accepted only Simbli; it now also supports the official CivicClerk adapter. The supplement used the same source identity and bounds, with General-only discovery to avoid downloading all 49 main-list bundles again. Saved references retain the actual queries, bytes and provenance. Ordinary CLI replay verifies both runs.

## Export and validation

Private local capture directory: `.state/captures/macon-bibb-commission-2026/`.

The ZIP `macon-bibb-commission-2026.zip` contains dated meeting folders, original PDFs, selected public structured agenda fields, normalized meeting JSON, inventories, document manifests, motion/vote tables, official video references and budget supplements. It has **740 entries**, totaling **6,544,559,766 bytes**. Every archived file was SHA-256 verified and ZIP CRC verification passed. Original API responses, signed storage URLs and YouTube tracking/context data remain outside the export in private evidence. A separate scan of all 105 meeting/agenda JSON files found no signed-URL or internal-metadata markers.

ZIP SHA-256:

`aef883b806174e90685e787c73f7fd493f54f672170ce2830481587a9297c1b6`

Collector regression reproduced against saved official data: the previous implementation selected 49 records; the fixed scope selects 58. All **165 tests passed**, including the browser fixtures. The tightened General-publication controls were then rechecked with all 16 CivicClerk tests. Ruff formatting/lint, Bandit, wheel and source-distribution builds passed. No browser UI, database schema, infrastructure or production deployment changed.

Future records and later-published or revised documents need a fresh capture. Videos/captions, OCR and generated transcripts remain outside this archive. Rollback means reverting the collector code changes while preserving all private state, source evidence and export files; there is no remote publication to undo.
