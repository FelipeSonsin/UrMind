import { readFileSync } from 'node:fs';
import { expect, test, type Page } from '@playwright/test';
import { stubPublicApi } from './fixtures';

// Sessão Supabase simulada SÓ no navegador de teste: a chave de armazenamento é a
// do projeto configurado no build (frontend/.env.local), e a API é interceptada.
function supabaseStorageKey(): string {
  const env = readFileSync(new URL('../.env.local', import.meta.url), 'utf8');
  const url = /VITE_SUPABASE_URL=(.+)/.exec(env)?.[1]?.trim();
  if (!url) throw new Error('VITE_SUPABASE_URL ausente em frontend/.env.local');
  return `sb-${new URL(url).hostname.split('.')[0]}-auth-token`;
}
async function signedIn(page: Page) {
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

test.beforeEach(async ({ page }) => {
  // Fixtures exclusivamente de teste. Nenhuma ocorrência fictícia entra no produto.
  await stubPublicApi(page, { events: [], detail: null });
});

test('separa o centro público da operação e navega entre os dois', async ({ page }, testInfo) => {
  await page.goto('/');
  await expect(
    page.getByRole('heading', { name: 'O que o UrMind está vendo na cidade' }),
  ).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('overview.png'), fullPage: true });
  await page.getByRole('link', { name: 'Revisão', exact: true }).click();
  // Sem sessão, a revisão pede login em vez de mostrar números.
  await expect(page.getByRole('heading', { name: 'Entrar no UrMind' })).toBeVisible();
  await page.getByRole('link', { name: 'Transparência' }).click();
  await expect(page.getByRole('heading', { name: 'Como o UrMind analisou' })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
});

test('salva foto real localmente, restaura após recarga e mantém edição idempotente', async ({
  page,
}) => {
  await page.goto('/#/capture');
  // Imagem decodificável criada no próprio navegador, exclusivamente para o teste.
  const png = await page.evaluate(() => {
    const canvas = document.createElement('canvas');
    canvas.width = 20;
    canvas.height = 20;
    canvas.getContext('2d')!.fillRect(0, 0, 20, 20);
    return canvas.toDataURL('image/png').split(',')[1];
  });
  await page.getByLabel('Escolher foto').setInputFiles({
    name: 'evidencia.png',
    mimeType: 'image/png',
    buffer: Buffer.from(png, 'base64'),
  });
  await expect(page.getByAltText('Evidência selecionada')).toBeVisible();
  await page.getByRole('button', { name: 'Salvar e enviar' }).click();
  await expect(page.getByText('Localização pendente', { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByRole('heading', { name: 'evidencia.png' })).toBeVisible();
  await page.getByRole('button', { name: 'Continuar edição' }).click();
  await page.getByLabel('Latitude', { exact: true }).fill('0');
  await page.getByLabel('Longitude', { exact: true }).fill('0');
  await page.getByRole('button', { name: 'Salvar e enviar' }).click();
  await expect(page.getByText('0.00000, 0.00000', { exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'evidencia.png' })).toHaveCount(1);
  page.once('dialog', (dialog) => dialog.accept());
  await page.getByRole('button', { name: 'Excluir', exact: true }).click();
  await expect(
    page.getByRole('heading', { name: 'Seu próximo registro começa aqui' }),
  ).toBeVisible();
});

test('rejeita conteúdo inválido com extensão de imagem', async ({ page }) => {
  await page.goto('/#/capture');
  await page.getByLabel('Escolher foto').setInputFiles({
    name: 'falsa.jpg',
    mimeType: 'image/jpeg',
    buffer: Buffer.from('não é imagem'),
  });
  await expect(page.getByRole('alert')).toContainText('Não foi possível ler');
});

test('a revisão exibe dados da API, filtra e mantém zero de confiança', async ({ page }) => {
  await page.route('**/api/v1/events?limit=500', (route) =>
    route.fulfill({
      json: [
        {
          id: '8b573981-61d4-4ee9-9d5f-a0a19af0f4ba',
          event_key: 'fixture-test-only',
          urmind_class: 'URMIND_ROAD_D40',
          status: 'review',
          occurred_at: '2026-09-07T12:00:00Z',
          evidence_mode: 'photo',
          visual_confidence: 0,
          latitude: -23.5,
          longitude: -46.6,
          snapped_latitude: -23.501,
          snapped_longitude: -46.601,
          factors: {},
        },
      ],
    }),
  );
  await page.route('**/api/v1/events/8b573981-61d4-4ee9-9d5f-a0a19af0f4ba', (route) =>
    route.fulfill({
      json: {
        id: '8b573981-61d4-4ee9-9d5f-a0a19af0f4ba',
        event_key: 'fixture-test-only',
        urmind_class: 'URMIND_ROAD_D40',
        status: 'review',
        occurred_at: '2026-09-07T12:00:00Z',
        evidence_mode: 'photo',
        visual_confidence: 0,
        latitude: -23.5,
        longitude: -46.6,
        snapped_latitude: -23.501,
        snapped_longitude: -46.601,
        distance_to_road_m: 3,
        factors: {},
        image_url: null,
        capture: null,
        detections: [],
        risk: null,
        responsibility: null,
        action: null,
        report: null,
        reviews: [],
      },
    }),
  );
  await signedIn(page);
  await page.goto('/#/review');
  await expect(page.getByText('0.0%', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Ver registro' }).click();
  await expect(page.getByText('-23.5, -46.6', { exact: true })).toBeVisible();
  await expect(page.getByText('-23.501, -46.601 (3.0 m)', { exact: true })).toBeVisible();
  await page.getByLabel('Estado', { exact: true }).selectOption('confirmed');
  await expect(
    page.getByRole('heading', { name: 'Nenhuma ocorrência neste recorte' }),
  ).toBeVisible();
});

test('reabre o aplicativo sem rede após instalar o service worker', async ({ page, context }) => {
  await page.goto('/');
  await page.evaluate(async () => {
    await navigator.serviceWorker.ready;
  });
  await page.reload();
  await expect
    .poll(() => page.evaluate(() => Boolean(navigator.serviceWorker.controller)))
    .toBe(true);
  await context.setOffline(true);
  // A emulação de rede do Edge não altera necessariamente navigator.onLine.
  // Confira a falha real de uma requisição não armazenada pelo service worker.
  expect(
    await page.evaluate(async () => {
      try {
        await fetch('/api/offline-probe', { cache: 'no-store' });
        return false;
      } catch {
        return true;
      }
    }),
  ).toBe(true);
  await page.reload();
  await expect(
    page.getByRole('heading', { name: 'O que o UrMind está vendo na cidade' }),
  ).toBeVisible();
  await page.getByRole('link', { name: 'Registrar evidência' }).click();
  await expect(page.getByRole('button', { name: 'Salvar e enviar' })).toBeVisible();
});
