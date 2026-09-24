# UrMind Mobile Brazil GPS and Integrations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make capture location explicit and reliable, confine the operational map to Brazil, simplify public navigation, and verify external integrations without changing scientific data.

**Architecture:** Extend the existing `CapturePage`, `UrbanMap`, `mapConfig`, photo-ingest API, and external-source registry. A versioned IBGE boundary is the authority for admission of new located captures; MapLibre bounds are only a presentation guard. The camera and gallery keep distinct provenance, and historical/scientific data are untouched.

**Tech Stack:** React, TypeScript, MapLibre, FastAPI, PostgreSQL/PostGIS, pytest, Vitest, Playwright.

**Spec:** `docs/superpowers/specs/2026-09-24-mobile-brazil-gps-integrations-design.md`

## Global Constraints

- Never train/evaluate YOLOX or XGBoost, reopen Frozen Test, alter `.env`, or promote a model.
- Never delete or geographically filter scientific datasets, historical lineage, or Ground Truth.
- Never invent coordinates. The original location and snapped road point remain separate.
- Only the Urmind DEV environment may receive external writes.
- Maintain existing ownership, RLS, upload validation, quotas, and publication gates.
- No new map, upload endpoint, queue, or external provider is justified without a failed existing path.

## Review Focus

- Gallery photo with valid EXIF taken elsewhere: do not silently substitute current device GPS.
- Gallery photo with erroneous EXIF: explicit manual correction must retain the original EXIF as provenance.
- Coast/border/island position with poor accuracy: do not label an uncertain point definitively outside Brazil.
- Existing historical or scientific records outside Brazil: retain them while excluding them from the new operational map.
- Overpass 504 or Nominatim lease contention: Capture must still succeed and the health label must identify the actual failure.

---

### Task 1: Brazil map presentation

**Files:**
- Modify: `frontend/src/mapConfig.ts`, `frontend/src/mapConfig.test.ts`, `frontend/src/components/UrbanMap.tsx`
- Test: `frontend/tests/public.spec.ts`

**Interfaces:**
- Produces: `BRAZIL_MAP_BOUNDS` as `[west,south,east,north]`; `isInBrazilMapBounds(lat, lon)` for UI presentation only.

- [ ] Add a failing Vitest assertion that the Brazil operational bounds include São Paulo and exclude Lisbon, and a Playwright assertion that the map begins over Brazil.
- [ ] Run `npm test -- --run src/mapConfig.test.ts` and focused Playwright; confirm the new assertions fail for the current world-centered map.
- [ ] Export one MapLibre bounding constant and apply `maxBounds` and `renderWorldCopies: false` to the existing map. Filter operational marker framing by the same presentation bounds, without changing the source database.
- [ ] Run focused tests and the complete frontend test suite; commit the map change only after green.

### Task 2: Capture provenance and explicit gallery location

**Files:**
- Modify: `frontend/src/pages/CapturePage.tsx`, `backend/app/services/photo_ingest.py`, `backend/app/api/v1/core.py`
- Test: `frontend/tests/public.spec.ts`, `backend/tests/test_photo_ingest.py`

**Interfaces:**
- Consumes: existing `CaptureDraft`, `LocationSource`, and `quality` provenance JSON.
- Produces: explicit confirmation of current GPS for a gallery image and explicit manual override of EXIF while retaining EXIF coordinates in `quality`.

- [ ] Add failing tests for camera auto-GPS feedback, gallery current-position confirmation, and manual correction of a valid EXIF coordinate.
- [ ] Run focused pytest and Playwright and confirm failure is specifically the missing confirmation/override behavior.
- [ ] Reuse `locate()` after an explicit gallery confirmation; pass an explicit manual-override flag through the existing upload route into `ingest_photo`. Keep the EXIF and submitted point in `quality` and maintain `location_source` honestly.
- [ ] Run focused tests, full backend/frontend regression, then commit.

### Task 3: Authoritative Brazil admission

**Files:**
- Extend: `backend/app/services/photo_ingest.py`, `backend/app/api/v1/core.py`, existing PostGIS repository/migration pattern
- Create only if no existing boundary responsibility is found: a versioned territory importer and the next Alembic revision
- Test: `backend/tests/test_photo_ingest.py`, `backend/tests/test_migrations.py`, new narrow PostGIS integration test

**Interfaces:**
- Consumes: resolved original coordinate and device accuracy.
- Produces: admission decision `inside`, `outside`, or `uncertain` with IBGE source/version; outside is rejected before Storage, uncertain remains reviewable.

- [ ] Verify exact current Alembic head and existing geospatial tables; register the official IBGE country geometry with checksum and source URL, never using a rectangle as admission authority.
- [ ] Add failing tests with Brazil interior, neighboring country, coast/island, low accuracy, and location absent; prove existing historical/scientific data are untouched.
- [ ] Implement a PostGIS `ST_Covers`/distance check before Storage upload; preserve current `location_required` for no coordinate and return a corrective 422 only for confirmed outside points.
- [ ] Run focused tests, migration upgrade/downgrade checks in DEV if a migration exists, and backend regression; commit only after green.

### Task 4: Public navigation and truthful copy

**Files:**
- Modify: `frontend/src/App.tsx`, `frontend/src/pages/CapturePage.tsx`
- Test: `frontend/tests/public.spec.ts`, `frontend/tests/private-routes.spec.ts`, `frontend/tests/responsive.spec.ts`

**Interfaces:**
- Produces: four public primary destinations: Início, Registrar, Meus relatos, Mapa. Existing technical URLs and internal role gates remain.

- [ ] Add failing Playwright tests for four primary public links, About in secondary navigation, 320 px fit, and unchanged private authorization.
- [ ] Run those tests and observe the current cluttered navigation fail.
- [ ] Remove technical destinations only from primary navigation. Correct any copy suggesting the archived detector currently performs analysis.
- [ ] Run Playwright and Vitest suites; commit after green.

### Task 5: Integration diagnosis and pre-training readiness

**Files:**
- Extend only as evidence requires: `backend/app/services/external_sources/checks.py`, `backend/tests/test_external_sources_live_checks.py`, `docs/EXTERNAL_INTEGRATIONS.md`, `docs/PROJECT_STATE.md`

**Interfaces:**
- Produces: observed health versus configuration, with 504/lease contention distinguished from missing credentials; separately reports YOLOX/XGBoost data gates.

- [ ] Add failing tests only for reproduced misclassification or failure propagation found by `live-check` and current worker tests.
- [ ] Fix the existing provider/health implementation, preserving degradable context behavior and rate limits; do not add a new key without proof.
- [ ] Run live-check and provider tests, record actual status, then run Ruff, mypy, TypeScript, Prettier, build, Playwright, backend full suite, DEV-safe integration, and `git diff --check`.
- [ ] Update project-state docs with exact test evidence, open blockers, and scientific readiness. Do not mark training ready if reviews, holdout, or Ground Truth are missing.

## Execution choice

The user approved the design and instructed continuation. Execute natively on the existing feature branch; do not delegate or create another worktree. Stop before any risky external mutation not already authorized by the approved design.
