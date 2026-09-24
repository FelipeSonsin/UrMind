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
  await expect(page.getByText('Relatório interno de teste')).toBeVisible();
  await expect(page.getByText('ANÁLISE EXPERIMENTAL', { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByText('Relatório interno de teste')).toBeVisible();
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
  await page.goto('/#/app/admin');
  await expect(page.getByRole('heading', { name: 'Administração' })).toBeVisible();
  await expect(
    page.getByText('Sua conta tem permissão administrativa.', { exact: false }),
  ).toBeVisible();
  await page.goto('/#/login');
  await expect(page.getByRole('heading', { name: 'Painel interno' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Fila de processamento' })).toBeVisible();
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
