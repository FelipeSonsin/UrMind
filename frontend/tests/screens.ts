import { readFileSync } from 'node:fs';
import type { Page } from '@playwright/test';
import { EVENT_ID, stubPublicApi } from './fixtures';

/*
 * As telas que as varreduras de qualidade percorrem (linguagem simples e acabamento
 * visual), com os dados simulados de cada uma. As rotas vêm de src/app/routes.ts:
 * página nova entra nas varreduras sem editar este arquivo.
 */

/** Endereços declarados em src/app/routes.ts: página nova entra na varredura sozinha. */
const routesSource = readFileSync(new URL('../src/app/routes.ts', import.meta.url), 'utf8');
function declaredRoutes(list: string): string[] {
  const start = routesSource.indexOf(`export const ${list} = [`);
  const end = routesSource.indexOf('] as const;', start);
  return [...routesSource.slice(start, end).matchAll(/href: '(#\/[^']*)'/g)].map(
    (found) => found[1],
  );
}
export const CITIZEN_ROUTES = declaredRoutes('navigation');
export const TEAM_ROUTES = declaredRoutes('privateNavigation');

export const CAPTURE = '11111111-1111-4111-8111-111111111111';
export const NO_LOCATION = '33333333-3333-4333-8333-333333333333';
const markers = [
  {
    id: CAPTURE,
    latitude: -23.556,
    longitude: -46.637,
    report_status: 'processing',
    protocol_code: 'URM-2026-00042',
    location_source: 'gps_device',
    accuracy_m: 8,
    user_description: 'Buraco grande perto da faixa de pedestres',
    created_at: '2026-09-24T12:00:00Z',
    address: { status: 'ok', road: 'Rua da Glória', suburb: 'Liberdade', city: 'São Paulo' },
  },
  {
    id: '22222222-2222-4222-8222-222222222222',
    latitude: -23.557,
    longitude: -46.638,
    report_status: 'published',
    protocol_code: 'URM-2026-00031',
    urmind_class: 'URMIND_ROAD_D40',
    severity: 'high',
    event_public_id: EVENT_ID,
    created_at: '2026-09-24T12:00:00Z',
  },
  {
    id: NO_LOCATION,
    latitude: null,
    longitude: null,
    report_status: 'location_required',
    user_description: 'Calçada quebrada em frente à escola',
    created_at: '2026-09-24T12:00:00Z',
  },
];

const processing = (id: string, status: string, protocol: string | null) => ({
  capture_id: id,
  status,
  protocol_code: protocol,
  requires_manual_location: status === 'location_required',
  event_ids: [],
  event_public_ids: [],
  model_version_id: null,
  model_status: null,
  updated_at: null,
});

export async function citizenData(page: Page) {
  await stubPublicApi(page);
  await page.route('**/api/v1/captures/markers*', (route) => route.fulfill({ json: markers }));
  await page.route(`**/api/v1/captures/${CAPTURE}/processing`, (route) =>
    route.fulfill({ json: processing(CAPTURE, 'needs_review', 'URM-2026-00042') }),
  );
  await page.route(`**/api/v1/captures/${NO_LOCATION}/processing`, (route) =>
    route.fulfill({ json: processing(NO_LOCATION, 'location_required', null) }),
  );
  await page.route('**/api/v1/captures/*/timeline', (route) => route.fulfill({ json: [] }));
  await page.route('**/api/v1/captures/*/image', (route) =>
    route.fulfill({ json: { image_url: '/icon.svg' } }),
  );
}

/** Sessão na área da equipe, com as rotas internas simuladas. */
export async function teamData(page: Page, access = { can_review: true, can_admin: true }) {
  const env = readFileSync(new URL('../.env.local', import.meta.url), 'utf8');
  const url = /VITE_SUPABASE_URL=(.+)/.exec(env)![1].trim();
  await page.addInitScript(
    ([key, value]) => localStorage.setItem(key, value),
    [
      `sb-${new URL(url).hostname.split('.')[0]}-admin-auth-token`,
      JSON.stringify({
        access_token: 'private-test-token',
        token_type: 'bearer',
        expires_at: Math.floor(Date.now() / 1000) + 3600,
        refresh_token: 'test-refresh',
        user: { id: EVENT_ID, aud: 'authenticated', role: 'authenticated', is_anonymous: false },
      }),
    ],
  );
  await citizenData(page);
  const json = (body: unknown) => ({ json: body as object });
  await page.route('**/api/v1/me', (route) =>
    route.fulfill(json({ id: EVENT_ID, email: null, ...access })),
  );
  await page.route('**/api/v1/ops/integrations', (route) =>
    route.fulfill(
      json([
        {
          name: 'Nominatim',
          status: 'UNAVAILABLE',
          detail: 'ReadTimeout',
          checked_at: '2026-09-24T19:00:00Z',
          latency_ms: 5000,
          last_success: null,
          last_failure: '2026-09-24T19:00:00Z',
        },
      ]),
    ),
  );
  await page.route('**/api/v1/ops/reports?*', (route) =>
    route.fulfill(
      json({
        today: 3,
        week: 8,
        awaiting_review: 4,
        without_location: 1,
        location_conflicts: 2,
        published: 2,
        day_timezone: 'UTC',
        rejected_by_reason: { blur: 2, non_urban_scene: 1 },
      }),
    ),
  );
  await page.route('**/api/v1/ops/metrics', (route) =>
    route.fulfill(
      json({
        queue: { pending: 7, processing: 1, archived: 3, retried: 0 },
        outcomes: { analysis_completed: 3, not_processed: 2 },
        latency_ms: { p50_ms: null, p95_ms: null, samples: 0 },
      }),
    ),
  );
  await page.route('**/api/v1/ops/audit?*', (route) =>
    route.fulfill(
      json([
        {
          id: EVENT_ID,
          operation: 'photo_gate_configuration',
          entity_type: 'operational_configuration',
          entity_id: EVENT_ID,
          created_at: '2026-09-24T12:00:00Z',
          event_hash: 'a'.repeat(64),
        },
      ]),
    ),
  );
  await page.route('**/api/v1/ops/photo-gate', (route) =>
    route.fulfill(
      json({
        public_capture_markers_enabled: false,
        min_side: 640,
        brightness_min: 20,
        brightness_max: 240,
        laplacian_min: 25,
        phash_distance: 6,
        old_photo_days: 30,
        scene_accept_margin: 0.02,
        scene_reject_margin: -0.02,
        dominant_face_ratio: 0.15,
        nearby_radius_m: 25,
      }),
    ),
  );
  const record = {
    id: EVENT_ID,
    event_key: `human-review-${CAPTURE}`,
    urmind_class: 'URMIND_ROAD_D40',
    status: 'review',
    occurred_at: '2026-09-07T12:00:00Z',
    evidence_mode: 'photo',
    factors: {},
  };
  await page.route('**/api/v1/events?*', (route) => route.fulfill(json([record])));
  await page.route(`**/api/v1/events/${EVENT_ID}`, (route) =>
    route.fulfill(
      json({
        ...record,
        model_status: null,
        image_url: null,
        capture: null,
        detections: [],
        risk: null,
        responsibility: null,
        action: null,
        report: null,
        reviews: [],
        context: [],
      }),
    ),
  );
  await page.route(`**/api/v1/captures/${CAPTURE}/review`, (route) =>
    route.fulfill(
      json({
        id: CAPTURE,
        protocol_code: 'URM-2026-00042',
        user_description: 'Buraco grande perto da faixa de pedestres',
        location: { latitude: -23.556, longitude: -46.637, accuracy_m: 8 },
        location_source: 'gps_device',
        location_conflict: true,
        photo_gate: { status: 'NEEDS_REVIEW', reasons: ['blur'] },
        human_review: null,
        events: [],
        reviews: [
          {
            id: '5a120c24-7ff1-4f58-bda8-c2f82a94fc05',
            decision: 'correct',
            corrected_class: 'URMIND_ROAD_D40',
            notes: null,
            created_at: '2026-09-24T12:00:00Z',
          },
        ],
      }),
    ),
  );
}

/** Todas as telas do site do cidadão: as rotas declaradas e as de endereço variável. */
export const CITIZEN_SCREENS = [
  ...CITIZEN_ROUTES.map((href) => `/${href}`),
  `/#/events/${EVENT_ID}`,
  `/#/resultado/${EVENT_ID}`,
  `/#/processando/${CAPTURE}`,
  `/#/processando/${NO_LOCATION}`,
  '/#/relato/URM-2026-00042',
  '/#/camera-robo/celular',
];

/** Todas as telas da área da equipe, inclusive detalhe, revisão e endereço inexistente. */
export const TEAM_SCREENS = [
  ...TEAM_ROUTES,
  `#/app/eventos/${EVENT_ID}`,
  `#/app/relato/${CAPTURE}`,
  '#/app/pagina-que-nao-existe',
].map((href) => `/admin/${href}`);

/**
 * Abre uma tela com carga completa. Só trocar o `#/…` reaproveita o documento e, durante
 * a transição de página, a tela anterior ainda está visível: a varredura leria a errada.
 */
export async function openScreen(page: Page, path: string) {
  // Um arquivo estático do próprio site: mesma origem (o localStorage da sessão vale)
  // e nenhuma tela do app montada.
  await page.goto('/icon.svg');
  await page.goto(path);
}
