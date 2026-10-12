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
  await page.goto('/admin/#/app/dashboard');
  const nav = page.getByRole('navigation', { name: 'Navegação interna' });
  // No celular o topo fica só com a marca e a situação: o menu já diz que é a equipe.
  await expect(page.locator('.topbar').getByText('Área da equipe')).toHaveCount(0);
  // Texto visível: no celular os rótulos curtos substituem os longos.
  await expect(nav.getByRole('link')).toHaveText(['Painel', 'Fila', 'Mapa'], {
    useInnerText: true,
  });
  const box = await nav.boundingBox();
  expect(box!.y + box!.height).toBeLessThanOrEqual(740);
  expect(box!.y).toBeGreaterThan(600);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(320);
  await nav.getByText('Mais', { exact: true }).click();
  await expect(nav.getByRole('link', { name: 'Auditoria' })).toBeVisible();
  await expect(nav.getByRole('link', { name: 'Administração' })).toHaveCount(0);
});

/** Sessão da área da equipe (/admin/) por padrão; `public` para o site do cidadão. */
async function session(page: Page, anonymous = false, surface: 'public' | 'admin' = 'admin') {
  const env = readFileSync(new URL('../.env.local', import.meta.url), 'utf8');
  const url = /VITE_SUPABASE_URL=(.+)/.exec(env)?.[1]?.trim();
  if (!url) throw new Error('VITE_SUPABASE_URL ausente');
  await page.addInitScript(
    ([key, value]) => localStorage.setItem(key, value),
    [
      `sb-${new URL(url).hostname.split('.')[0]}-${surface === 'admin' ? 'admin-' : ''}auth-token`,
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
  // Uma verificação real (fora do ar) e uma só de configuração, que não foi testada.
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
        {
          name: 'Supabase',
          status: 'CONFIGURED',
          detail: 'offline registry',
          checked_at: '2026-09-24T19:00:00Z',
          latency_ms: null,
          last_success: null,
          last_failure: null,
        },
      ],
    }),
  );
  await page.goto('/admin/#/app/dashboard');
  const panel = page.getByRole('region', { name: 'Serviços externos' });
  // O problema em uma frase, com a consequência para os relatos.
  await expect(panel).toContainText(
    'Busca de endereços está fora do ar: os relatos continuam chegando, sem o endereço aproximado.',
  );
  await panel.getByText('Todos os serviços').click();
  const address = panel.locator('.service-list li').filter({ hasText: 'Busca de endereços' });
  await expect(address).toContainText('Fora do ar');
  await expect(address).toContainText('Último sucesso: nunca registrado');
  // Configuração não é saúde: fica como não verificada, nunca como funcionando.
  const login = panel.locator('.service-list li').filter({ hasText: 'Login e armazenamento' });
  await expect(login).toContainText('Não verificado');
  await expect(login).not.toContainText('Funcionando');
  // Nomes internos e códigos de estado não aparecem.
  for (const code of ['Nominatim', 'Supabase', 'UNAVAILABLE', 'CONFIGURED', 'ReadTimeout'])
    await expect(panel).not.toContainText(code);
});

test('taxonomia ampliada permite buscar classes por nomes simples e sinônimos', async ({
  page,
}) => {
  await session(page);
  await page.route('**/api/v1/me', (route) =>
    route.fulfill({ json: { id: EVENT_ID, email: null, can_review: true, can_admin: false } }),
  );
  await page.route('**/api/v1/events?*', (route) => route.fulfill({ json: [] }));
  // O catálogo fica no fim da Fila de revisão, recolhido numa linha.
  await page.goto('/admin/#/app/reviews');
  const panel = page.getByRole('region', { name: 'Tipos de problema' });
  await panel.getByText(/Consultar os tipos de problema/).click();
  const search = panel.getByLabel('Buscar tipo de problema');
  await search.fill('FIACAO CAOTICA');
  await expect(panel.locator('li')).toHaveCount(1);
  await expect(panel.locator('li')).toContainText('Fios desorganizados');
  await search.fill('rua_sem_asfalto');
  await expect(panel.locator('li')).toHaveCount(1);
  await expect(panel.locator('li')).toContainText('Rua sem asfalto');
  await search.fill('linhas insuficientes');
  await expect(panel.locator('li')).toContainText('Poucas linhas de ônibus');
  await expect(panel.locator('li')).toContainText('Depende de dados do serviço de ônibus');
  await expect(panel).not.toContainText('YOLO');
  await search.fill('problema inexistente');
  await expect(panel).toContainText('Nenhum tipo encontrado para esta busca.');
  await expect(panel.locator('li')).toHaveCount(0);
  await search.clear();
  await expect(panel.locator('li')).toHaveCount(46);
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
  await page.goto('/admin/#/app/mapa');
  const csvDownload = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Baixar planilha', exact: true }).click();
  expect((await csvDownload).suggestedFilename()).toBe('urmind-pontos-filtrados.csv');
  // A lista do próprio mapa (que segue os filtros dele) abre o relato no mapa.
  await page.getByText(/pontos? em lista/).click();
  await page.getByRole('button', { name: /Selecionar ponto: Aguardando revisão/ }).click();
  const panel = page.getByRole('region', { name: 'Revisão do relato' });
  for (const code of [
    'URMIND_MISSING_CURB_RAMP',
    'URMIND_NONFUNCTIONING_TRAFFIC_LIGHT',
    'URMIND_INSUFFICIENT_BUS_SERVICE',
  ]) {
    await panel.getByLabel('Tipo de problema').selectOption(code);
    await expect(panel.getByRole('status').first()).toContainText(
      'Não dá para confirmar só pela foto',
    );
    // O botão continua ativo e explica, junto dele, por que não confirma.
    await panel.getByRole('button', { name: 'Confirmar relato' }).click();
    await expect(panel.getByRole('alert')).toContainText('Não dá para confirmar só pela foto');
    expect(reviewed).toBe(false);
  }
  await panel.getByLabel('Tipo de problema').selectOption('URMIND_ROAD_D40');
  await expect(panel.getByText('Quem costuma resolver')).toBeVisible();
  await expect(panel.getByRole('link', { name: 'SP156' })).toHaveAttribute(
    'href',
    'https://sp156.prefeitura.sp.gov.br/portal',
  );
  await panel.getByLabel(/Decidir como administrador/).check();
  await panel.getByRole('button', { name: 'Confirmar relato' }).click();
  await expect(panel.getByText(/confirmou como Buraco/)).toBeVisible();
  await panel.getByLabel(/Confirmo que a foto pode ser publicada/).check();
  await panel.getByRole('button', { name: 'Publicar foto', exact: true }).click();
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
  await expect(
    page.getByAltText('Foto publicada da ocorrência, revisada para privacidade'),
  ).toBeVisible();
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
      await page.goto(`/admin/#/app/${path}`);
      await expect(page.getByRole('heading', { name: 'Entrar na área da equipe' })).toBeVisible();
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
    await page.goto(`/admin/#/app/${path}`);
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
  await page.goto('/admin/#/app/eventos');
  await expect(page.getByRole('cell', { name: 'Buraco' })).toBeVisible();
  // O código interno da ocorrência não aparece na lista.
  await expect(page.locator('main')).not.toContainText('private-fixture');
  await page.getByRole('link', { name: 'Ver ocorrência' }).click();
  await expect(page).toHaveURL(new RegExp(`/app/eventos/${EVENT_ID}$`));
  // Sem revisão humana ainda: relatório, gravidade e prioridade ficam ocultos ao revisor.
  await expect(page.getByRole('heading', { name: 'Decisão da equipe' })).toBeVisible();
  await expect(page.getByText('Gravidade', { exact: true })).toHaveCount(0);
  await expect(page.getByText('Relatório interno de teste')).toHaveCount(0);
  await expect(page.getByText('ANÁLISE EXPERIMENTAL', { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Decisão da equipe' })).toBeVisible();
  await expect(page.getByText('Gravidade', { exact: true })).toHaveCount(0);
  for (const [path, heading] of [
    ['dashboard', 'Painel'],
    ['reviews', 'Fila de revisão'],
    ['mapa', 'Mapa interno'],
    ['admin', 'Acesso restrito'],
  ]) {
    await page.goto(`/admin/#/app/${path}`);
    await expect(page.getByRole('heading', { name: heading, exact: true })).toBeVisible();
  }
  await expect(
    page
      .getByRole('navigation', { name: 'Navegação interna' })
      .getByRole('link', { name: 'Administração' }),
  ).toHaveCount(0);
});

test('resultado do proprietário desaparece ao encerrar sessão em outra aba', async ({ page }) => {
  await session(page, true, 'public');
  await page.route(`**/api/v1/public/events/${EVENT_ID}`, (route) =>
    route.request().headers().authorization
      ? route.fulfill({ json: { ...eventDetail, road_name: 'Somente proprietário', road: null } })
      : route.fulfill({ status: 404, json: { detail: 'Ocorrência não encontrada' } }),
  );
  await page.goto(`/#/resultado/${EVENT_ID}`);
  await expect(page.getByText(/Somente proprietário/)).toBeVisible();
  await page.evaluate(() => {
    const key = Object.keys(localStorage).find((item) => item.endsWith('-auth-token'))!;
    localStorage.removeItem(key);
    const channel = new BroadcastChannel(key);
    channel.postMessage({ event: 'SIGNED_OUT', session: null });
    channel.close();
  });
  await expect(page.getByText(/Somente proprietário/)).toHaveCount(0);
  // Sem a sessão do dono, a ocorrência (não publicada) aparece como não encontrada.
  await expect(page.getByRole('heading', { name: 'Ocorrência não encontrada' })).toBeVisible();
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
  await page.goto('/admin/#/app/admin');
  await expect(page.getByRole('heading', { name: 'Administração' })).toBeVisible();
  await page.getByLabel('Tamanho mínimo da foto').fill('800');
  await page.getByRole('button', { name: 'Salvar configuração' }).click();
  await expect(page.getByText('Configuração salva e registrada na auditoria.')).toBeVisible();
  await page.reload();
  await expect(page.getByLabel('Tamanho mínimo da foto')).toHaveValue('800');
  await page.goto('/admin/#/login');
  await expect(page.getByRole('heading', { name: 'Painel', exact: true })).toBeVisible();
  await expect(page.getByRole('region', { name: 'Próximos a revisar' })).toBeVisible();
  // A fila da análise automática fica nos detalhes técnicos, recolhidos.
  await page.getByText('Detalhes técnicos').click();
  await expect(page.getByRole('heading', { name: 'Fila da análise automática' })).toBeVisible();
  // Sem medições, uma linha só, sem "Não disponível" repetido.
  await expect(page.getByText('ainda sem medições')).toBeVisible();
  await expect(page.getByText('Não disponível', { exact: true })).toHaveCount(0);
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
  await page.goto('/admin/#/app/dashboard');
  await page.getByText('Detalhes técnicos').click();
  await expect(page.getByRole('alert')).toContainText('Não foi possível carregar os números');
  await expect(page.getByText('Pendentes', { exact: true })).toHaveCount(0);
});

test('auditoria interna carrega filtros sem identidades', async ({ page }) => {
  await session(page);
  await page.route('**/api/v1/me', (route) =>
    route.fulfill({ json: { id: EVENT_ID, email: null, can_review: true, can_admin: false } }),
  );
  await page.route('**/api/v1/events?*', (route) => route.fulfill({ json: [] }));
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
  await page.goto('/admin/#/app/auditoria');
  await expect(page.getByRole('heading', { name: 'Auditoria', exact: true })).toBeVisible();
  await page
    .getByLabel('Tipo de registro', { exact: true })
    .selectOption('photo_gate_configuration');
  // A operação aparece em palavras.
  const entry = page
    .getByRole('listitem')
    .filter({ hasText: 'Regras da checagem de fotos alteradas' });
  await expect(entry).toBeVisible();
  // Nem no texto nem nas dicas: o código interno não aparece na tela.
  await expect(page.locator('[title*="photo_gate"]')).toHaveCount(0);
  await expect(page.locator('main')).not.toContainText('photo_gate_configuration');
  // Uma página só: sem "Anterior" e "Próxima" desligados.
  await expect(page.getByRole('button', { name: 'Anterior' })).toHaveCount(0);
});
