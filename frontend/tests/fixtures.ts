import type { Page, Route } from '@playwright/test';

// Fixtures existem SOMENTE aqui, nos testes. Nenhum dado fictício entra no produto:
// o app real só mostra o que a API pública devolve. Os formatos abaixo espelham
// backend/app/schemas/public.py.

export const EVENT_ID = '8b573981-61d4-4ee9-9d5f-a0a19af0f4ba';
export const SECOND_ID = 'a1c2e3d4-5f60-4a71-8b92-0c1d2e3f4a5b';

export const publicStatus = {
  api: { name: 'api', status: 'ok', detail: null },
  database: { name: 'database', status: 'ok', detail: null },
  detector: { name: 'detector', status: 'degraded', detail: 'modelo em estágio inicial' },
  scout: { name: 'scout', status: 'unavailable', detail: 'nenhum dispositivo registrado' },
  last_event_at: '2026-09-17T12:00:00+00:00',
  events_total: 2,
  road_segments_total: 832,
  pilot_area: 'Liberdade / FECAP',
  checked_at: '2026-09-17T12:05:00+00:00',
};

export const scoutOffline = {
  status: 'no_device',
  device_code: null,
  last_seen: null,
  camera: {
    mode: 'unavailable',
    reason: 'nenhuma câmera conectada a este ambiente',
    stream_url: null,
    frame_url: null,
    latency_ms: null,
  },
  telemetry: {},
  mission: null,
};

export const scoutSnapshots = {
  status: 'live',
  device_code: 'SCOUT-PILOTO-01',
  last_seen: '2026-09-17T12:04:30+00:00',
  camera: {
    mode: 'live_snapshots',
    reason: null,
    stream_url: null,
    frame_url: '/api/v1/public/scout/frame',
    latency_ms: 180,
  },
  telemetry: { battery_pct: 74 },
  mission: 'Liberdade / FECAP',
};

export const publicEvents = [
  {
    id: EVENT_ID,
    occurred_at: '2026-09-17T12:00:00+00:00',
    urmind_class: 'URMIND_ROAD_D40',
    status: 'review',
    evidence_mode: 'photo',
    visual_confidence: 0.61,
    severity: 'high',
    priority_score: 0.42,
    latitude: -23.5573,
    longitude: -46.6395,
    snapped_latitude: -23.5574,
    snapped_longitude: -46.6396,
    road_name: 'Rua da Glória',
  },
  {
    id: SECOND_ID,
    occurred_at: '2026-09-17T11:30:00+00:00',
    urmind_class: 'URMIND_ROAD_D00',
    status: 'triage_required',
    evidence_mode: 'photo',
    visual_confidence: 0,
    severity: null,
    priority_score: null,
    latitude: -23.5568,
    longitude: -46.6381,
    snapped_latitude: null,
    snapped_longitude: null,
    road_name: null,
  },
];

export const eventDetail = {
  ...publicEvents[0],
  distance_to_road_m: 3.2,
  location_accuracy_m: 12,
  road: {
    name: 'Rua da Glória',
    highway: 'residential',
    distance_m: 3.2,
    jurisdiction: 'municipal',
  },
  detections: [
    {
      urmind_class: 'URMIND_ROAD_D40',
      confidence: 0.61,
      bbox: { x: 0.31, y: 0.44, width: 0.22, height: 0.18 },
    },
  ],
  image: {
    available: false,
    privacy_redacted: false,
    reason:
      'imagem não publicada: o frame ainda não passou por sanitização (rostos, placas e dados pessoais), então só o dado estruturado é público',
    url: null,
  },
  risk: {
    severity: 'high',
    priority_score: 0.42,
    uncertainty: 0.31,
    uncertainty_band: 'média',
    coverage: 0.65,
    ruleset_version: 'risk-v1',
    thresholds_are_calibrated: false,
    explanation: {
      increased: [
        {
          factor: 'severity',
          label: 'severidade da classe',
          value: 0.61,
          weight: 0.4,
          delta: 0.32,
        },
      ],
      decreased: [
        {
          factor: 'confidence',
          label: 'confiança da detecção',
          value: 0.15,
          weight: 0.25,
          delta: -0.14,
        },
      ],
      unavailable: [
        {
          factor: 'environment',
          label: 'condição ambiental (chuva)',
          reason: 'context_unavailable',
        },
      ],
      baseline: 0.29,
    },
    limitations: ['limiares não calibrados com revisão de campo'],
    assessed_at: '2026-09-17T12:01:00+00:00',
  },
  action: { code: 'PATCH_POTHOLE', label: 'Tapar buraco', version: 'actions-v1' },
  responsibility: {
    status: 'assigned',
    responsible: 'Prefeitura — zeladoria viária',
    source: 'jurisdição municipal do trecho',
    version: 'rules-v1',
    note: null,
  },
  context: [
    {
      source: 'nominatim_reverse',
      label: 'Endereço aproximado (Nominatim/OpenStreetMap)',
      status: 'ok',
      fetched_at: '2026-09-17T12:00:30+00:00',
      attribution: '© OpenStreetMap contributors',
      summary: { road: 'Rua da Glória', city: 'São Paulo' },
    },
    {
      source: 'open_meteo_rain',
      label: 'Chuva nas 24 h anteriores (Open-Meteo)',
      status: 'context_unavailable',
      fetched_at: null,
      attribution: null,
      summary: {},
    },
  ],
  prediction: {
    available: false,
    reason: 'ainda não há histórico validado suficiente para estimar a evolução deste problema',
    task: null,
    horizon_days: null,
    value: null,
    uncertainty: null,
    model_version: null,
    generated_at: null,
  },
  trace: [
    {
      step: 'capture',
      status: 'done',
      title: 'Captura',
      source: 'foto enviada por operador autenticado',
      detail: {},
      at: '2026-09-17T12:00:00+00:00',
    },
    {
      step: 'detection',
      status: 'done',
      title: 'Detecção',
      source: 'baseline_early',
      detail: { confiança: '0.61' },
      at: '2026-09-17T12:00:20+00:00',
    },
    {
      step: 'risk',
      status: 'done',
      title: 'Risco',
      source: 'risk-v1',
      detail: {},
      at: '2026-09-17T12:01:00+00:00',
    },
    {
      step: 'action',
      status: 'unavailable',
      title: 'Execução',
      source: null,
      detail: {},
      at: null,
    },
  ],
  model_version: 'baseline_early',
  model_stage: 'Staging',
  dataset_version: 'rdd2022-v1',
  reviewed: false,
};

export const transparency = {
  model_name: 'urmind-yolox-s',
  model_version: 'baseline_early',
  stage: 'Staging',
  stage_note: 'modelo inicial: métricas ainda em evolução',
  classes: ['URMIND_ROAD_D00', 'URMIND_ROAD_D10', 'URMIND_ROAD_D20', 'URMIND_ROAD_D40'],
  input_size: [640, 640],
  score_threshold: 0.25,
  metrics: {
    map50: 0.12,
    map50_95: 0.05,
    precision: null,
    recall: null,
    f1: null,
    per_class: {},
    not_computed: ['precision', 'recall', 'f1'],
    samples: 120,
  },
  latency: {
    mean_ms: 310,
    p50_ms: 295,
    p95_ms: 420,
    fps_approx: 3.2,
    execution_provider: 'CPUExecutionProvider',
    hardware: 'CPU local',
  },
  dataset_name: 'RDD2022',
  dataset_version: 'rdd2022-v1',
  dataset_license: 'CC BY-SA 4.0',
  dataset_source: 'https://github.com/sekilab/RoadDamageDetector',
  context_sources: [
    { source: 'OpenStreetMap / Overpass', use: 'malha viária e equipamentos próximos' },
    { source: 'Nominatim', use: 'endereço aproximado (contexto, não é a coordenada)' },
  ],
  rules: {
    severidade_e_prioridade: 'regras versionadas, sem LLM',
    ação: 'catálogo versionado de ações; sempre sugestão',
  },
  limitations: ['limiares ainda não calibrados com revisão de campo'],
};

interface PublicStubs {
  status?: unknown;
  scout?: unknown;
  events?: unknown;
  detail?: unknown;
  transparency?: unknown;
  eventsError?: number;
}

/** Intercepta a API pública. Cada rota devolve exatamente o contrato do backend. */
export async function stubPublicApi(page: Page, stubs: PublicStubs = {}) {
  const json = (route: Route, body: unknown) => route.fulfill({ json: body as object });
  await page.route('**/api/v1/health', (route) =>
    json(route, { status: 'ok', database: 'connected' }),
  );
  await page.route('**/api/v1/public/status', (route) => json(route, stubs.status ?? publicStatus));
  await page.route('**/api/v1/public/scout', (route) => json(route, stubs.scout ?? scoutOffline));
  await page.route('**/api/v1/public/transparency', (route) =>
    json(route, stubs.transparency ?? transparency),
  );
  // Predicado em vez de glob: a lista e o detalhe diferem só pela barra e pela query.
  await page.route(
    (url) => url.pathname === '/api/v1/public/events',
    (route) =>
      stubs.eventsError
        ? route.fulfill({ status: stubs.eventsError, json: { detail: 'serviço indisponível' } })
        : json(route, stubs.events ?? publicEvents),
  );
  await page.route(
    (url) => url.pathname.startsWith('/api/v1/public/events/'),
    (route) => json(route, stubs.detail ?? eventDetail),
  );
}
