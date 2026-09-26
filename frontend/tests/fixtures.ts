import { readFileSync } from 'node:fs';
import type { Page, Route } from '@playwright/test';
import taxonomyFixture from './taxonomy.fixture.json' with { type: 'json' };

// Sessão Supabase simulada SÓ no navegador de teste: a chave de armazenamento é a
// do projeto configurado no build (frontend/.env.local), e a API é interceptada.
export function supabaseStorageKey(): string {
  const env = readFileSync(new URL('../.env.local', import.meta.url), 'utf8');
  const url = /VITE_SUPABASE_URL=(.+)/.exec(env)?.[1]?.trim();
  if (!url) throw new Error('VITE_SUPABASE_URL ausente em frontend/.env.local');
  return `sb-${new URL(url).hostname.split('.')[0]}-auth-token`;
}
export async function signedIn(page: Page) {
  const session = {
    access_token: 'token-de-teste',
    token_type: 'bearer',
    expires_in: 3600,
    expires_at: Math.floor(Date.now() / 1000) + 3600,
    refresh_token: 'refresh-de-teste',
    user: {
      id: '0b0e4c7e-1111-4222-8333-944445555666',
      aud: 'authenticated',
      role: 'authenticated',
    },
  };
  await page.addInitScript(
    ([key, value]) => localStorage.setItem(key, value),
    [supabaseStorageKey(), JSON.stringify(session)],
  );
}
/** Logout como o Supabase propaga entre abas. */
export async function signOut(page: Page) {
  await page.evaluate((key) => {
    localStorage.removeItem(key);
    const channel = new BroadcastChannel(key);
    channel.postMessage({ event: 'SIGNED_OUT', session: null });
    channel.close();
  }, supabaseStorageKey());
}

/** Synthetic texture for upload mechanics only; never a scientific or real E2E photo. */
export async function syntheticReportPhoto(page: Page): Promise<string> {
  return page.evaluate(() => {
    const canvas = document.createElement('canvas');
    canvas.width = 640;
    canvas.height = 640;
    const context = canvas.getContext('2d')!;
    const image = context.createImageData(640, 640);
    for (let y = 0; y < 640; y++)
      for (let x = 0; x < 640; x++) {
        const i = (y * 640 + x) * 4;
        image.data[i] = image.data[i + 1] = image.data[i + 2] = (x * 73 + y * 151) % 256;
        image.data[i + 3] = 255;
      }
    context.putImageData(image, 0, 0);
    return canvas.toDataURL('image/png').split(',')[1];
  });
}

/**
 * A mesma foto sintética em JPEG, com um bloco EXIF que só contém GPS (sem precisão,
 * como numa foto de galeria). Exclusivo de teste: prova o caminho EXIF, não um local real.
 */
export async function syntheticJpegWithGps(
  page: Page,
  latitude: number,
  longitude: number,
): Promise<Buffer> {
  const jpeg = Buffer.from(
    await page.evaluate(() => {
      const canvas = document.createElement('canvas');
      canvas.width = 640;
      canvas.height = 640;
      const context = canvas.getContext('2d')!;
      const image = context.createImageData(640, 640);
      for (let y = 0; y < 640; y++)
        for (let x = 0; x < 640; x++) {
          const i = (y * 640 + x) * 4;
          image.data[i] = image.data[i + 1] = image.data[i + 2] = (x * 73 + y * 151) % 256;
          image.data[i + 3] = 255;
        }
      context.putImageData(image, 0, 0);
      return canvas.toDataURL('image/jpeg', 0.95).split(',')[1];
    }),
    'base64',
  );
  // TIFF little-endian: IFD0 → GPS IFD com latitude/longitude em graus, minutos, segundos.
  const tiff = Buffer.alloc(128);
  tiff.write('II', 0, 'ascii');
  tiff.writeUInt16LE(42, 2);
  tiff.writeUInt32LE(8, 4);
  tiff.writeUInt16LE(1, 8);
  tiff.writeUInt16LE(0x8825, 10);
  tiff.writeUInt16LE(4, 12);
  tiff.writeUInt32LE(1, 14);
  tiff.writeUInt32LE(26, 18);
  tiff.writeUInt32LE(0, 22);
  const entries: [number, number, number, number | string][] = [
    [0x0001, 2, 2, latitude < 0 ? 'S' : 'N'],
    [0x0002, 5, 3, 80],
    [0x0003, 2, 2, longitude < 0 ? 'W' : 'E'],
    [0x0004, 5, 3, 104],
  ];
  tiff.writeUInt16LE(entries.length, 26);
  entries.forEach(([tag, type, count, value], index) => {
    const at = 28 + index * 12;
    tiff.writeUInt16LE(tag, at);
    tiff.writeUInt16LE(type, at + 2);
    tiff.writeUInt32LE(count, at + 4);
    if (typeof value === 'string') tiff.write(value, at + 8, 'ascii');
    else tiff.writeUInt32LE(value, at + 8);
  });
  tiff.writeUInt32LE(0, 28 + entries.length * 12);
  for (const [offset, decimal] of [
    [80, Math.abs(latitude)],
    [104, Math.abs(longitude)],
  ]) {
    const degrees = Math.floor(decimal);
    const minutes = Math.floor((decimal - degrees) * 60);
    const seconds = Math.round(((decimal - degrees) * 60 - minutes) * 60 * 10_000);
    [
      [degrees, 1],
      [minutes, 1],
      [seconds, 10_000],
    ].forEach(([numerator, denominator], index) => {
      tiff.writeUInt32LE(numerator, offset + index * 8);
      tiff.writeUInt32LE(denominator, offset + index * 8 + 4);
    });
  }
  const payload = Buffer.concat([Buffer.from('Exif\0\0', 'binary'), tiff]);
  const header = Buffer.alloc(4);
  header.writeUInt16BE(0xffe1, 0);
  header.writeUInt16BE(payload.length + 2, 2);
  return Buffer.concat([jpeg.subarray(0, 2), header, payload, jpeg.subarray(2)]);
}

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

export const urbanAnalysis = {
  schema_version: 'urmind-urban-analysis-v1',
  identification: {
    issue_code: 'URMIND_ROAD_D40',
    display_name: 'Buraco',
    family: 'ROAD_SURFACE',
    model_support_status: 'EXPERIMENTAL_MODEL',
    visual_confidence: 0.61,
    reviewed: false,
  },
  description: 'Indício visual de buraco na superfície da via.',
  diagnosis: 'A avaliação persistida indica necessidade de inspeção no local.',
  potential_consequences: [
    {
      domain: 'road_safety',
      statement: 'Pode comprometer a circulação se confirmado no local.',
      conditional: true,
      source: 'persisted_phase5',
    },
  ],
  possible_causes: [],
  severity: 'high',
  risk_level: 'medium',
  priority_lane: null,
  action: eventDetail.action,
  responsibility: eventDetail.responsibility,
  responsibility_domain: 'ROAD_MAINTENANCE',
  context: [],
  limitations: ['A fotografia não permite medir a profundidade do dano.'],
  provenance: {
    taxonomy_version: 'urmind-issue-taxonomy-v2',
    model_version: 'baseline_early',
    model_stage: 'EXPERIMENTAL_SHADOW',
    dataset_version: null,
    ruleset_version: 'risk-v1',
    assessed_at: null,
    assessment_source: 'persisted_phase5',
    method: 'deterministic_template',
  },
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
const ONE_PIXEL_PNG =
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=';

export async function stubPublicApi(page: Page, stubs: PublicStubs = {}) {
  await page.route('**/api/v1/captures/nearby-reports?*', (route) => route.fulfill({ json: [] }));
  await page.route('**/api/v1/public/privacy-notice', (route) =>
    route.fulfill({
      json: {
        version: 'urmind-capture-privacy-v1',
        text: 'Foto e localização ficam privadas; publicação depende de revisão.',
      },
    }),
  );
  const json = (route: Route, body: unknown) => route.fulfill({ json: body as object });
  // A suíte comum não depende da internet. O runtime continua usando o style
  // OpenFreeMap real; apenas o navegador de teste recebe um style MapLibre mínimo.
  await page.route(
    (url) => url.hostname === 'tiles.openfreemap.org' && url.pathname.startsWith('/styles/'),
    (route) => json(route, { version: 8, sources: {}, layers: [] }),
  );
  // Imagem de satélite (EOX): o teste recebe um ladrilho 1×1 local, sem internet.
  await page.route(
    (url) => url.hostname === 'tiles.maps.eox.at',
    (route) =>
      route.fulfill({ contentType: 'image/png', body: Buffer.from(ONE_PIXEL_PNG, 'base64') }),
  );
  await page.route('**/api/v1/health', (route) =>
    json(route, { status: 'ok', database: 'connected' }),
  );
  await page.route('**/api/v1/public/status', (route) => json(route, stubs.status ?? publicStatus));
  await page.route('**/api/v1/public/scout', (route) => json(route, stubs.scout ?? scoutOffline));
  await page.route('**/api/v1/public/transparency', (route) =>
    json(route, stubs.transparency ?? transparency),
  );
  // Generated from app.schemas.issue_taxonomy; a backend test keeps it in sync.
  await page.route('**/api/v1/public/taxonomy', (route) => json(route, taxonomyFixture));
  await page.route('**/api/v1/public/capture-markers', (route) => json(route, []));
  await page.route('**/api/v1/captures/markers*', (route) => json(route, []));
  await page.route('**/api/v1/public/photo-policy', (route) =>
    json(route, {
      min_side: 640,
      brightness_min: 20,
      brightness_max: 240,
      laplacian_min: 25,
    }),
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

// Câmera e modelo simulados SÓ no navegador de teste. Isto prova interface e
// contratos; não é teste de câmera física nem de detecção real.
export type FakeCameraMode = 'ok' | 'NotAllowedError' | 'NotReadableError' | 'NotFoundError';

export async function fakeCamera(page: Page, mode: FakeCameraMode = 'ok', fps = 15) {
  await page.addInitScript(
    ([failure, rate]) => {
      const state = {
        calls: [] as MediaStreamConstraints[],
        tracks: [] as MediaStreamTrack[],
        stopped: 0,
      };
      (window as unknown as { __camera: typeof state }).__camera = state;
      const devices = [
        { deviceId: 'cam-a', kind: 'videoinput', label: 'Webcam integrada', groupId: 'a' },
        { deviceId: 'cam-b', kind: 'videoinput', label: 'Câmera USB', groupId: 'b' },
      ];
      Object.defineProperty(navigator, 'mediaDevices', {
        configurable: true,
        value: {
          enumerateDevices: async () => devices.map((d) => ({ ...d, toJSON: () => d })),
          getUserMedia: async (constraints: MediaStreamConstraints) => {
            state.calls.push(constraints);
            if (failure !== 'ok') throw new DOMException('simulada', failure);
            const canvas = document.createElement('canvas');
            canvas.width = 1280;
            canvas.height = 720;
            const context = canvas.getContext('2d')!;
            // Textura fixa (passa no porteiro de nitidez), gerada uma vez, e uma faixa que
            // anda a cada quadro: cada quadro é diferente, no ritmo pedido, com custo baixo.
            const texture = document.createElement('canvas');
            texture.width = 1280;
            texture.height = 720;
            const textureContext = texture.getContext('2d')!;
            const image = textureContext.createImageData(1280, 720);
            for (let i = 0; i < image.data.length; i += 4) {
              const v = (i * 2654435761) % 200;
              image.data[i] = image.data[i + 1] = image.data[i + 2] = 30 + v;
              image.data[i + 3] = 255;
            }
            textureContext.putImageData(image, 0, 0);
            let frame = 0;
            const paint = () => {
              context.drawImage(texture, 0, 0);
              context.fillStyle = '#d0d0d0';
              context.fillRect((frame++ * 8) % 1280, 0, 4, 720);
            };
            paint();
            const timer = setInterval(paint, 1000 / rate);
            const stream = canvas.captureStream(rate);
            for (const track of stream.getVideoTracks()) {
              const stop = track.stop.bind(track);
              track.stop = () => {
                state.stopped += 1;
                clearInterval(timer);
                stop();
              };
              state.tracks.push(track);
            }
            return stream;
          },
        },
      });
    },
    [mode, fps] as const,
  );
}

export const camera = (page: Page) =>
  page.evaluate(() => {
    const s = (
      window as unknown as {
        __camera: { calls: MediaStreamConstraints[]; stopped: number; tracks: MediaStreamTrack[] };
      }
    ).__camera;
    return {
      calls: s.calls,
      stopped: s.stopped,
      live: s.tracks.filter((t) => t.readyState === 'live').length,
    };
  });

/** Manifesto de modelo do navegador só para teste de contrato (checksum/tamanho). */
export const liveModelManifest = (sha256: string, size: number) => ({
  schema_version: 1,
  model_id: 'yolox-s-model-v2',
  model_version: 'contrato-de-teste',
  scientific_status: 'EXPERIMENTAL',
  use_authorized: true,
  distribution_authorized: true,
  authorization_ref: 'tests/live-detection.spec.ts',
  onnx: { path: '/models/teste.onnx', sha256, size_bytes: size },
  input: {
    name: 'images',
    size: [640, 640],
    layout: 'NCHW',
    dtype: 'float32',
    color: 'BGR',
    range: '0..255',
    normalization: 'none',
    letterbox: { pad_value: 114, anchor: 'top-left' },
  },
  output: { name: 'output', format: 'yolox_decoded_cxcywh_obj_cls' },
  class_names: ['URMIND_ROAD_D00', 'URMIND_ROAD_D10', 'URMIND_ROAD_D20', 'URMIND_ROAD_D40'],
  postprocess: {
    score_threshold: 0.25,
    nms_threshold: 0.65,
    nms: 'class_agnostic',
    max_detections: 50,
  },
  provenance: {
    registration_manifest_sha256: 'd'.repeat(64),
    closure_manifest_sha256: null,
    contract_sha256: 'c'.repeat(64),
  },
});
