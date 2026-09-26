import { readFileSync } from 'node:fs';
import { expect, test, type Page } from '@playwright/test';
import { EVENT_ID, eventDetail, stubPublicApi } from './fixtures';

const paths = [
  'dashboard',
  'eventos',
  `eventos/${EVENT_ID}`,
  'reviews',
  'ground-truth',
  'mapa',
  'admin',
  'modelos',
  'auditoria',
];
const record = {
  id: EVENT_ID,
  event_key: 'private-fixture',
  urmind_class: 'URMIND_ROAD_D40',
  status: 'review',
  occurred_at: '2026-09-07T12:00:00Z',
  evidence_mode: 'photo',
  factors: {},
};

test('navegação interna dedicada cabe em 320px e usa menu inferior', async ({ page }) => {
  await session(page);
  await page.setViewportSize({ width: 320, height: 740 });
  await page.route('**/api/v1/me', (route) =>
    route.fulfill({ json: { id: EVENT_ID, email: null, can_review: true, can_admin: false } }),
  );
  await page.route('**/api/v1/events?*', (route) => route.fulfill({ json: [] }));
  await page.goto('/#/app/dashboard');
  const nav = page.getByRole('navigation', { name: 'Navegação interna' });
  await expect(nav.getByText('Área interna')).toBeVisible();
  await expect(nav.getByRole('link')).toHaveText(['Painel', 'Fila', 'Mapa', 'Relatos/GT']);
  const box = await nav.boundingBox();
  expect(box!.y + box!.height).toBeLessThanOrEqual(740);
  expect(box!.y).toBeGreaterThan(600);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(320);
  await nav.getByText('Mais', { exact: true }).click();
  await expect(nav.getByRole('link', { name: 'Auditoria' })).toBeVisible();
  await expect(nav.getByRole('link', { name: 'Administração' })).toHaveCount(0);
});

async function session(page: Page, anonymous = false) {
  const env = readFileSync(new URL('../.env.local', import.meta.url), 'utf8');
  const url = /VITE_SUPABASE_URL=(.+)/.exec(env)?.[1]?.trim();
  if (!url) throw new Error('VITE_SUPABASE_URL ausente');
  await page.addInitScript(
    ([key, value]) => localStorage.setItem(key, value),
    [
      `sb-${new URL(url).hostname.split('.')[0]}-auth-token`,
      JSON.stringify({
        access_token: 'private-test-token',
        token_type: 'bearer',
        expires_at: Math.floor(Date.now() / 1000) + 3600,
        refresh_token: 'test-refresh',
        user: {
          id: EVENT_ID,
          aud: 'authenticated',
          role: 'authenticated',
          is_anonymous: anonymous,
          user_metadata: { urmind_role: 'admin' },
        },
      }),
    ],
  );
}

test.beforeEach(async ({ page }) => {
  await stubPublicApi(page);
  await page.route('**/api/v1/ops/integrations', (route) =>
    route.fulfill({
      json: [
        {
          name: 'Nominatim',
          status: 'UNAVAILABLE',
          detail: 'ReadTimeout',
          checked_at: '2026-09-24T19:00:00Z',
          latency_ms: 5000,
          last_success: null,
          last_failure: '2026-09-24T19:00:00Z',
        },
      ],
    }),
  );
  await page.route('**/api/v1/ops/reports?*', (route) =>
    route.fulfill({
      json: {
        today: 3,
        week: 8,
        awaiting_review: 4,
        without_location: 1,
        location_conflicts: 2,
        published: 2,
        day_timezone: 'UTC',
        rejected_by_reason: { blur: 2 },
      },
    }),
  );
  await page.route('**/api/v1/ops/ground-truth', (route) =>
    route.fulfill({
      json: {
        entries: [],
        counts_by_class: {},
        dataset: {},
        rows: [],
        training_authorized: false,
      },
    }),
  );
  await page.route('**/api/v1/ops/ground-truth/summary', (route) =>
    route.fulfill({
      json: {
        reviewed_events: 0,
        eligible_events: 0,
        counts_by_class: {},
        training_authorized: false,
      },
    }),
  );
  await page.route('**/api/v1/ops/metrics', (route) =>
    route.fulfill({
      json: {
        queue: { pending: 7, processing: 1, archived: 3, retried: 0 },
        outcomes: { analysis_completed: 3 },
        latency_ms: { p50_ms: null, p95_ms: null, samples: 0 },
      },
    }),
  );
});

test('painel apresenta saúde observada sem transformar configuração em sucesso', async ({
  page,
}) => {
  await session(page);
  await page.route('**/api/v1/me', (route) =>
    route.fulfill({ json: { id: EVENT_ID, email: null, can_review: true, can_admin: false } }),
  );
  await page.route('**/api/v1/events?*', (route) => route.fulfill({ json: [] }));
  await page.goto('/#/app/dashboard');
  const panel = page.getByRole('region', { name: 'Integrações' });
  await expect(panel.getByText('Nominatim: UNAVAILABLE')).toBeVisible();
  await expect(panel.getByText(/Último sucesso: não observado/)).toBeVisible();
});

test('relato sem modelo recebe revisão humana e publicação sanitizada', async ({ page }) => {
  await session(page);
  await page.route('**/api/v1/me', (route) =>
    route.fulfill({ json: { id: EVENT_ID, email: null, can_review: true, can_admin: true } }),
  );
  const captureId = '2b120c24-7ff1-4f58-bda8-c2f82a94fc05';
  const publicId = 'a732cb867fd24d188f0f234afde8a664';
  const reviewId = '5a120c24-7ff1-4f58-bda8-c2f82a94fc05';
  let reviewed = false;
  let published = false;
  await page.route('**/api/v1/captures/markers*', (route) =>
    route.fulfill({
      json: [
        {
          id: captureId,
          public_id: publicId,
          protocol_code: 'URM-7K3Q9XYZ',
          latitude: -23.55,
          longitude: -46.63,
          report_status: reviewed ? 'human_confirmed' : 'model_not_available',
          user_description: 'Relato de teste',
        },
      ],
    }),
  );
  await page.route(`**/api/v1/captures/${captureId}/image`, (route) =>
    route.fulfill({ json: { image_url: '/synthetic-private.jpg' } }),
  );
  await page.route(`**/api/v1/captures/${captureId}/review`, (route) =>
    route.fulfill({
      json: {
        id: captureId,
        protocol_code: 'URM-7K3Q9XYZ',
        user_description: 'Relato de teste',
        location: { latitude: -23.55, longitude: -46.63, accuracy_m: null },
        location_source: 'manual',
        location_conflict: false,
        photo_gate: null,
        human_review: null,
        events: reviewed
          ? [{ id: EVENT_ID, public_id: publicId, status: 'confirmed', origin: 'human_review' }]
          : [],
        reviews: reviewed
          ? [
              {
                id: reviewId,
                decision: 'correct',
                corrected_class: 'URMIND_ROAD_D40',
                notes: null,
                created_at: '2026-09-24T12:00:00Z',
              },
            ]
          : [],
      },
    }),
  );
  await page.route(`**/api/v1/captures/${captureId}/reviews`, (route) => {
    expect(route.request().postDataJSON()).toMatchObject({
      decision: 'correct',
      corrected_class: 'URMIND_ROAD_D40',
      adjudicate: true,
    });
    reviewed = true;
    return route.fulfill({
      json: {
        review_id: reviewId,
        event_id: EVENT_ID,
        status: 'confirmed',
        ground_truth_status: 'adjudicated',
      },
    });
  });
  await page.route(`**/api/v1/events/${EVENT_ID}/publication`, (route) => {
    expect(route.request().postDataJSON()).toMatchObject({
      publish: true,
      review_id: reviewId,
      visible_content_reviewed: true,
    });
    published = true;
    return route.fulfill({ json: { event_id: EVENT_ID, publication_status: 'published' } });
  });
  await page.route('**/api/v1/captures/export?*', (route) => {
    expect(route.request().headers().authorization).toBe('Bearer private-test-token');
    expect(new URL(route.request().url()).searchParams.get('format')).toBe('csv');
    return route.fulfill({
      contentType: 'text/csv',
      body: 'latitude,longitude,issue_code,status,date\r\n-23.55,-46.63,,model_not_available,2026-09-24T12:00:00Z\r\n',
    });
  });
  await page.goto('/#/app/mapa');
  const csvDownload = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Exportar CSV', exact: true }).click();
  expect((await csvDownload).suggestedFilename()).toBe('urmind-pontos-filtrados.csv');
  await page.getByText(/Lista acessível de pontos/).click();
  await page.getByRole('button', { name: /Selecionar ponto: Análise indisponível/ }).click();
  const panel = page.getByRole('region', { name: 'Revisão do relato' });
  await panel.getByLabel('Classe humana').selectOption('URMIND_ROAD_D40');
  await expect(panel.getByText('Sugestão, não encaminhamento')).toBeVisible();
  await expect(panel.getByRole('link', { name: 'canal oficial SP156' })).toHaveAttribute(
    'href',
    'https://sp156.prefeitura.sp.gov.br/portal',
  );
  await panel.getByLabel('Adjudicar como admin').check();
  await panel.getByRole('button', { name: 'Confirmar rótulo humano' }).click();
  await expect(panel.getByText(/correct URMIND_ROAD_D40/)).toBeVisible();
  await panel.getByLabel(/Atesto que o conteúdo visual/).check();
  await panel.getByRole('button', { name: 'Publicar relato', exact: true }).click();
  await expect.poll(() => published).toBe(true);
  await page.route('**/api/v1/public/events?*', (route) =>
    route.fulfill({ json: [{ ...eventDetail, id: publicId, status: 'confirmed' }] }),
  );
  await page.route(`**/api/v1/public/events/${publicId}`, (route) =>
    route.fulfill({
      json: {
        ...eventDetail,
        id: publicId,
        status: 'confirmed',
        model_stage: null,
        image: {
          available: true,
          privacy_redacted: true,
          reason: null,
          url: '/synthetic-sanitized.jpg',
        },
      },
    }),
  );
  await page.goto(`/#/mapa?ponto=${publicId}`);
  await expect(page.getByAltText('Foto publicada e sanitizada da ocorrência')).toBeVisible();
});

for (const anonymous of [false, true]) {
  test(`protege todas as rotas internas para ${anonymous ? 'visitante anônimo' : 'visitante sem sessão'}`, async ({
    page,
  }) => {
    if (anonymous) await session(page, true);
    let reads = 0;
    await page.route('**/api/v1/events**', (route) => {
      reads++;
      return route.fulfill({ json: [] });
    });
    for (const path of paths) {
      await page.goto(`/#/app/${path}`);
      await expect(page.getByRole('heading', { name: 'Entrar no UrMind' })).toBeVisible();
    }
    expect(reads).toBe(0);
  });
}

test('metadados editáveis não dão acesso de revisor', async ({ page }) => {
  await session(page);
  await page.route('**/api/v1/me', (route) =>
    route.fulfill({ json: { id: EVENT_ID, email: null, can_review: false, can_admin: false } }),
  );
  let reads = 0;
  await page.route('**/api/v1/events**', (route) => {
    reads++;
    return route.fulfill({ json: [] });
  });
  for (const path of paths) {
    await page.goto(`/#/app/${path}`);
    await expect(page.getByRole('heading', { name: 'Acesso restrito' })).toBeVisible();
  }
  expect(reads).toBe(0);
});

test('revisor navega para detalhe interno e conserva a rota ao recarregar', async ({ page }) => {
  await session(page);
  await page.route('**/api/v1/me', (route) =>
    route.fulfill({ json: { id: EVENT_ID, email: null, can_review: true, can_admin: false } }),
  );
  await page.route('**/api/v1/events?*', (route) => route.fulfill({ json: [record] }));
  await page.route(`**/api/v1/events/${EVENT_ID}`, (route) =>
    route.fulfill({
      json: {
        ...record,
        model_status: 'EXPERIMENTAL_SHADOW',
        image_url: null,
        capture: null,
        detections: [],
        risk: null,
        responsibility: null,
        action: null,
        report: 'Relatório interno de teste',
        reviews: [],
        context: [],
      },
    }),
  );
  await page.goto('/#/app/eventos');
  await expect(page.getByText('private-fixture')).toBeVisible();
  await page.getByRole('link', { name: 'Ver registro' }).click();
  await expect(page).toHaveURL(new RegExp(`/app/eventos/${EVENT_ID}$`));
  // Sem revisão humana ainda: relatório, severidade e prioridade ficam ocultos ao revisor.
  await expect(page.getByText('Oculto até a decisão humana').first()).toBeVisible();
  await expect(page.getByText('Relatório interno de teste')).toHaveCount(0);
  await expect(page.getByText('ANÁLISE EXPERIMENTAL', { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByText('Oculto até a decisão humana').first()).toBeVisible();
  for (const [path, heading] of [
    ['dashboard', 'Painel interno'],
    ['reviews', 'Ocorrências urbanas'],
    ['mapa', 'Gêmeo digital 2D'],
    ['ground-truth', 'Ground truth'],
    ['admin', 'Acesso restrito'],
  ]) {
    await page.goto(`/#/app/${path}`);
    await expect(page.getByRole('heading', { name: heading, exact: true })).toBeVisible();
  }
  await expect(
    page
      .getByRole('navigation', { name: 'Navegação interna' })
      .getByRole('link', { name: 'Administração' }),
  ).toHaveCount(0);
});

test('resultado do proprietário desaparece ao encerrar sessão em outra aba', async ({ page }) => {
  await session(page, true);
  await page.route(`**/api/v1/public/events/${EVENT_ID}`, (route) =>
    route.request().headers().authorization
      ? route.fulfill({ json: { ...eventDetail, road_name: 'Somente proprietário', road: null } })
      : route.fulfill({ status: 404, json: { detail: 'Ocorrência não encontrada' } }),
  );
  await page.goto(`/#/resultado/${EVENT_ID}`);
  await expect(page.getByText('Somente proprietário', { exact: true })).toBeVisible();
  await page.evaluate(() => {
    const key = Object.keys(localStorage).find((item) => item.endsWith('-auth-token'))!;
    localStorage.removeItem(key);
    const channel = new BroadcastChannel(key);
    channel.postMessage({ event: 'SIGNED_OUT', session: null });
    channel.close();
  });
  await expect(page.getByText('Somente proprietário', { exact: true })).toHaveCount(0);
  await expect(page.getByRole('alert')).toContainText('Ocorrência não encontrada');
});

test('admin verificado acessa administração; login autenticado abre painel', async ({ page }) => {
  await session(page);
  await page.route('**/api/v1/me', (route) =>
    route.fulfill({ json: { id: EVENT_ID, email: null, can_review: true, can_admin: true } }),
  );
  await page.route('**/api/v1/events?*', (route) => route.fulfill({ json: [] }));
  let policy = {
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
  };
  await page.route('**/api/v1/ops/photo-gate', (route) => {
    if (route.request().method() === 'PUT') policy = route.request().postDataJSON();
    return route.fulfill({ json: policy });
  });
  await page.goto('/#/app/admin');
  await expect(page.getByRole('heading', { name: 'Administração' })).toBeVisible();
  await page.getByLabel('Menor lado da imagem (px)').fill('800');
  await page.getByRole('button', { name: 'Salvar configuração' }).click();
  await expect(page.getByText('Configuração salva com auditoria.')).toBeVisible();
  await page.reload();
  await expect(page.getByLabel('Menor lado da imagem (px)')).toHaveValue('800');
  await page.goto('/#/login');
  await expect(page.getByRole('heading', { name: 'Painel interno' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Fila de processamento' })).toBeVisible();
  await expect(page.getByRole('region', { name: 'Classes de problemas urbanos' })).toBeVisible();
  await expect(page.getByText('Não disponível', { exact: true })).toHaveCount(2);
});

test('falha de métricas não vira contagem zero', async ({ page }) => {
  await session(page);
  await page.route('**/api/v1/me', (route) =>
    route.fulfill({ json: { id: EVENT_ID, email: null, can_review: true, can_admin: false } }),
  );
  await page.route('**/api/v1/events?*', (route) => route.fulfill({ json: [] }));
  await page.route('**/api/v1/ops/metrics', (route) =>
    route.fulfill({ status: 503, json: { detail: 'indisponível' } }),
  );
  await page.goto('/#/app/dashboard');
  await expect(page.getByRole('alert')).toContainText('Métricas indisponíveis');
  await expect(page.getByText('Pendentes', { exact: true })).toHaveCount(0);
});

test('modelos e auditoria interna carregam dados e filtros sem identidades', async ({ page }) => {
  await session(page);
  await page.route('**/api/v1/me', (route) =>
    route.fulfill({ json: { id: EVENT_ID, email: null, can_review: true, can_admin: false } }),
  );
  await page.route('**/api/v1/events?*', (route) => route.fulfill({ json: [] }));
  await page.route('**/api/v1/ops/models?*', (route) =>
    route.fulfill({
      json: [
        {
          id: EVENT_ID,
          name: 'Registro histórico',
          version: '1',
          kind: 'visual',
          status: 'ARCHIVED',
          created_at: '2026-09-24T12:00:00Z',
        },
      ],
    }),
  );
  await page.route('**/api/v1/ops/audit?*', (route) =>
    route.fulfill({
      json: [
        {
          id: EVENT_ID,
          operation: 'photo_gate_configuration',
          entity_type: 'operational_configuration',
          entity_id: EVENT_ID,
          created_at: '2026-09-24T12:00:00Z',
          event_hash: 'a'.repeat(64),
        },
      ],
    }),
  );
  await page.goto('/#/app/modelos');
  await expect(page.getByRole('heading', { name: 'Modelos registrados' })).toBeVisible();
  await expect(page.getByText('ARCHIVED · visual')).toBeVisible();
  await page.goto('/#/app/auditoria');
  await expect(page.getByRole('heading', { name: 'Auditoria operacional' })).toBeVisible();
  await page.getByLabel('Operação', { exact: true }).fill('photo_gate_configuration');
  await expect(page.getByText('photo_gate_configuration', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Anterior' })).toBeDisabled();
});

test('ground truth vazio não habilita exportação científica', async ({ page }) => {
  await session(page);
  await page.route('**/api/v1/me', (route) =>
    route.fulfill({ json: { id: EVENT_ID, email: null, can_review: true, can_admin: false } }),
  );
  await page.route('**/api/v1/events?*', (route) => route.fulfill({ json: [] }));
  await page.goto('/#/app/ground-truth');
  await expect(page.getByText('Nenhuma revisão de ocorrência disponível.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Exportar rótulos elegíveis (0)' })).toBeDisabled();
  await page.goto('/#/app');
  await expect(page.getByRole('heading', { name: 'Painel interno' })).toBeVisible();
  await expect(page.getByText('Dia civil em UTC; semana = últimos sete dias.')).toBeVisible();
  await expect(page.getByText('blur: 2')).toBeVisible();
});

test('ground truth agrega todas as páginas e exporta em lotes autenticados', async ({ page }) => {
  await session(page);
  await page.route('**/api/v1/me', (route) =>
    route.fulfill({ json: { id: EVENT_ID, email: null, can_review: true, can_admin: false } }),
  );
  await page.route('**/api/v1/ops/ground-truth/summary', (route) =>
    route.fulfill({
      json: {
        reviewed_events: 1200,
        eligible_events: 1190,
        counts_by_class: { D40: 1200 },
        training_authorized: false,
      },
    }),
  );
  await page.route('**/api/v1/ops/ground-truth/export', (route) => {
    expect(route.request().headers().authorization).toMatch(/^Bearer /);
    return route.fulfill({
      body: '{"schema":"urmind-ground-truth-export-v1","training_authorized":false}\n',
      contentType: 'application/x-ndjson',
      headers: { 'Content-Disposition': 'attachment; filename="urmind-ground-truth.ndjson"' },
    });
  });
  await page.goto('/#/app/ground-truth');
  await expect(page.getByText('1200 Events revisados')).toBeVisible();
  await expect(page.getByText('D40: 1200')).toBeVisible();
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Exportar rótulos elegíveis (1190)' }).click();
  expect((await download).suggestedFilename()).toBe('urmind-ground-truth.ndjson');
});
