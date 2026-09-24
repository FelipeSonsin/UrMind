# Photo → map diagnosis — 2026-09-24

## Etapa 0 — inspected before implementation

Source: current code and read-only official Supabase connector, Urmind DEV (`impm…ggy`). No model inference, training, Auth change or scientific evaluation was run.

1. `frontend/src/pages/CapturePage.tsx`: camera/file inputs, EXIF preview (`exifr`), browser Geolocation (accuracy/time/heading/speed), confirmed MapLibre location already exist. `CaptureDraft.note` is already persisted in IndexedDB. It is not yet the requested 500-character `user_description`. `App.tsx:451` rejects sending a draft without coordinates.
2. `backend/app/api/v1/core.py:157` provides authenticated multipart POST `/api/v1/captures/photo`. Image content/size validation, owner-scoped checksum deduplication, quotas, server-generated Storage path and compensation exist. `photo_ingest.py:87` already parses original EXIF with Pillow; `exif.py` rejects invalid/out-of-range/(0,0) GPS and retains EXIF time as an unverified claim. Device/manual coordinate currently wins over EXIF. The API rejects missing location at `core.py:245` BEFORE Storage. The note is currently private JSON `quality.client_note_unverified` (up to 2000 characters), not a Capture column.
3. `worker.py:171` resolves the authorized model. No model raises `ModelNotAvailableError`; handler around line 255 persists `model_not_available` and archives the job. Event only originates from successful detection via `CoreService.consolidate_capture` around worker line 222. Therefore no model means no Event, correctly, but also no map point today.
4. Public map consumes `/api/v1/public/events`, which enforces explicit review/publication and sanitized-image policy. Private map consumes authenticated Events. `UrbanMap` only receives Event-shaped markers. There is no Capture report layer. Realtime subscribes to Events/RiskAssessments, and the App enables it for internal reviewers only. No Capture notification is delivered.
5. Answer: a valid-GPS photo with no authorized model currently produces NO point in author, reviewer or public maps. Capture persists and status is recoverable, but both map feeds require an Event. A locationless photo is rejected outright. Public publication must remain a separate gate, not be weakened to solve this.

## DEV schema evidence

Read-only connector confirmed PostgreSQL 17.6.1, active Urmind DEV, Alembic `0021_history_snapshot_retention`. Capture has nullable geography point, accuracy/heading/speed, private Storage path, JSON quality, no description column. RLS is enabled with **no Capture client policies**. `supabase_realtime` contains only `events`, `risk_assessments`. These are observations, not claims from prior status documents.

## Reuse decision / implementation boundaries

Extend canonical Capture schema/repository/upload, existing EXIF parser, Worker, draft note, processing route, UrbanMap and existing Realtime subscription. Do not create a second upload, queue, map or Event for an unanalyzed report. One marker identity is the Capture ID; Event analysis enriches it. Missing-location completion must only fill an absent original point, never overwrite an existing one. Public generic report layer must be opt-in, with no photo, description, owner or Storage path.

## Verification

Diagnosis inspection complete. Implementation, migration and new behavior tests pending; prior baseline is not new verification evidence. Latest earlier offline baseline: 1230 passed / 20 skipped.

## Implementation and verification — completed work in this round

`EXTENDED_EXISTING_COMPONENT`: Capture schema/ORM/repository, upload, Pillow EXIF ingestion, existing Queue trigger/Worker, IndexedDB `note` draft (sent as `user_description`), processing route, UrbanMap, public map and existing Realtime subscriber. No parallel upload/queue/map backend was introduced. New files are this required report and the necessary Alembic revision only.

- Optional private `captures.user_description`, maximum 500 characters; strips control/format characters and surrounding whitespace. HTML remains literal text and React escapes rendering. The old `note` multipart alias is retained for compatibility, but new descriptions are stored in the column, not duplicated into JSON.
- Priority is device GPS → server EXIF → confirmed manual → no point. Original EXIF and submitted coordinates remain private claims; discrepancy over `LOCATION_CONFLICT_DISTANCE_M` (default 500 metres) sets `quality.location_conflict`. Device time/accuracy/heading/speed remain claims, EXIF time is not trusted chronology.
- Missing location no longer rejects the validated image. Capture/Storage persist and status is `location_required`; PATCH `/api/v1/captures/{id}/location` can only fill an absent original location for its owner. It cannot overwrite a point. Mobile queue insertion waits for a point, then uses the **same** existing inference queue once; non-mobile trigger behavior is unchanged. Worker also handles legacy locationless mobile jobs without attempting inference.
- GET `/api/v1/captures/markers` returns owner reports, or all mobile reports for a verified reviewer/admin. Capture UUID remains marker identity; associated Event/latest assessment enriches it. Without analysis no class/severity/priority is emitted. Original Capture point is not replaced by snapped Event point.
- Existing signed-image service is exposed through owner/reviewer-only `/captures/{id}/image`. Private marker/status/image responses and authenticated frontend requests use `no-store`. The public response never contains original image, Storage path, owner or description.
- Public generic layer `/api/v1/public/capture-markers`: **OFF by default**, controlled only by `PUBLIC_CAPTURE_MARKERS_ENABLED`. Allowlist: marker ID, latitude, longitude, generic report status. Existing publication/review/sanitized-image gate remains intact. No configuration/credential file was changed.
- Existing Realtime subscription now consumes Capture INSERT/UPDATE, not DELETE payloads, then reloads the authorized HTTP projection. RLS uses `(select auth.uid())`, owner index, and verified non-anonymous reviewer/admin claims. No client write policy was granted. Reference: [Supabase Postgres Changes](https://supabase.com/docs/guides/realtime/postgres-changes).
- UI: description counter/draft, missing-location confirmation after upload, report legend, private photo/description, immediate HTTP marker and subsequent Realtime refresh. No inferred class is fabricated. Preserved the private-map heading; fixed reproducible taxonomy limitation-text overflow exposed by the full mobile regression (layout only, no taxonomy content change).

## Migration / DEV

One new Alembic revision: `0022_capture_report_markers`, parent `0021_history_snapshot_retention`, unique head confirmed locally and in DEV. Applied with the official Supabase connector, including an in-round adjustment to the insert trigger so mobile location completion cannot create a duplicate pending job. Both DDL applications are represented by the final revision. Downgrade refuses to discard populated descriptions; a live downgrade was **not executed**.

DEV integration created only generated synthetic JPEG fixtures, called the real upload handler, uploaded/downloaded real Storage bytes, queried real PostGIS marker rows, exercised missing-location completion and the existing queue, checked owner/other-user/reviewer/anonymous-with-reviewer-claim RLS, then deleted exactly its Capture/queue/object fixtures. No Detection/Event was inserted to demonstrate this path. Auth identity was an explicit in-process fixture, not a real login; this does **not** prove JWT + WebSocket + physical-phone E2E.

## Fresh test evidence

| Check / command | Level | Result |
|---|---|---|
| Backend `.venv/Scripts/python.exe -m pytest -q -ra --tb=short` | unit/offline integration | **1239 passed, 21 skipped**, 2 dependency deprecation warnings; 71.90 s |
| DEV `tests/test_db_integration.py -k 'photo_report_marker or rls_and_realtime_publication or migrations_ficam or postgis_disponivel or capture_trigger_queue or realtime_rls_reviewer'` | real Storage/Postgres/PostGIS/queue/RLS | **6 passed, 14 deselected**; fixtures cleaned |
| `npm test -- --run` | frontend unit | **45 passed** |
| `npx playwright test --workers=2` | browser with API fixtures, desktop/mobile/320px | **100 passed, 2 skipped** |
| Ruff `check .`; mypy `app` | static | passed; 72 modules typed |
| Prettier `--check .`; TypeScript; `npm run build` | static/build | passed; existing >500 kB chunk warning retained |
| `alembic heads`, `alembic history -r 0021_history_snapshot_retention:head` | inspection | unique 0022 head |
| `git -c core.safecrlf=false diff --check` | structural | passed |
| Manifest/registry producers, orphans | inspection | no new registered script/artifact producer, model or scientific consumer; canonical consumers reused |
| Protected artifact SHA-256 | inspection | pretrained source and both Frozen Test record hashes unchanged from preceding cleanup |
| Realtime wire delivery/denial across real sessions | real pipeline | **unverified**; RLS/publication and frontend wiring verified separately |
| Physical phone camera/GPS/HTTPS | real pipeline | **unverified** |

Baseline delta: +9 offline tests (2 location/description ingestion, 1 report allowlist, 2 additional upload-location cases, 1 manual-location authorization, 1 description 422, 2 public-flag cases); +1 DEV opt-in test makes offline skips 21 instead of 20. All old tests remain. The 21 skips are the 20 opt-in DEV tests plus `tests/live/test_external_sources_live.py` (external opt-in); six DEV tests ran separately as listed. Fourteen other DEV tests were deliberately deselected, including Auth-user creation and unrelated scientific/event fixtures. Two Playwright real-photo E2Es remain opt-in and were not run with a restored model.

Intermediate failures were retained as diagnostic evidence: old EXIF/manual and missing-location expectations were updated to the explicit new contract; Worker test doubles gained real Capture source/point fields without weakening class guards; private-map heading and mobile text wrapping were corrected. A full-run MLflow **temporary-fixture** code-state hash check caught concurrent code edits; final full run was repeated with a stable working tree and passed. No scientific tracking implementation was changed. No OneDrive access-denied workaround was needed.

## Final gate

Implementation status: **PARTIAL (verification gaps)**. `urmind-verification-gate`: **BLOCKED**, not complete.

| Priority | Open check | Minimum action |
|---|---|---|
| P1 | Real Capture Realtime delivery and cross-user suppression not observed over WebSocket | Use existing authorized sessions A/B/reviewer in DEV; send A's report and verify only A/reviewer receive it without reload. No Auth setting change is needed. |
| P1 | Physical mobile camera/GPS and trusted HTTPS not tested | Execute the steps below on a real phone. |
| P2 | Downgrade runtime path unverified | Exercise 0022 upgrade/downgrade in an isolated transactional/schema fixture, including refusal with descriptions, before using rollback operationally. |

No claim of model availability, model restoration, AI confirmation or scientific training readiness is made. Public generic flag remains false. `.env`, credentials, Auth configuration, raw datasets, labels/images, taxonomy data, pretrained source and Frozen Test records were not edited. Scout-specific code was not changed or removed. No commit was created; pre-existing worktree changes remain preserved.

Skills actually applied: `no-duplicate-files`, `karpathy-guidelines`, `superpowers:systematic-debugging`, `urmind-verification-gate`, `supabase:supabase`, `supabase:supabase-postgres-best-practices`. Plugin/connector used: official Supabase, DEV only. Direct review verified privacy allowlists, source scoping, no original-point overwrite, no invented Event, and no model restoration.

## Manual phone check (PowerShell; three terminals)

1. Backend: `cd backend`; `.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000`.
2. Worker: `cd backend`; `.\.venv\Scripts\python.exe -m app.worker`. With no authorized model it must report `model_not_available`, not restore a model.
3. Frontend: `cd frontend`; `$env:VITE_DEV_HTTPS='1'`; `npm run dev -- --host 0.0.0.0 --port 5173`. This sets only a process-level development flag, not an `.env` file. Use the same LAN, allow the frontend port in the firewall as appropriate, and open `https://<PC-LAN-IP>:5173/#/registrar` on the phone. The development certificate must be trusted by the device for a reliable secure context; no public deploy/HTTPS certificate was provisioned here. API uses the existing frontend proxy.
4. Take/select a non-scientific photo; enter a description; permit GPS or select/confirm location. Send. Expect `#/processando/<capture_id>`, a private report marker without AI class, and eventually the honest no-model status. Refresh must preserve the Capture reference.
5. Send a photo without GPS/EXIF and without confirming a point: image remains stored; `location_required`, no marker. Select and confirm a point on the processing map. Expect exactly one report marker and one queued mobile job.
6. With an existing reviewer session open `#/app/mapa`: verify incoming report and private signed photo. With a different visitor session verify no access to the Capture. Logged-out public map must show no generic Capture layer while the flag is off. No public photo/description appears without existing review/publication policy.
