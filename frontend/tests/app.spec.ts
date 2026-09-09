import { expect, test } from '@playwright/test';

test.beforeEach(async ({ page }) => {
  // Fixtures exclusivamente de teste. Nenhuma ocorrência fictícia entra no produto.
  await page.route('**/api/v1/health', (route) =>
    route.fulfill({ json: { status: 'ok', database: 'not_configured' } }),
  );
});
test('abre sem integrações, navega e não inventa contagens', async ({ page }, testInfo) => {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Um olhar atento à cidade.' })).toBeVisible();
  await expect(page.getByText('Banco não configurado', { exact: true })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('overview.png'), fullPage: true });
  await page.getByRole('link', { name: 'Ocorrências', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Aguardando dados do backend' })).toBeVisible();
  await page.getByRole('link', { name: 'Análises e previsões' }).click();
  await expect(page.getByRole('heading', { name: 'Histórico antes de projeções' })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
});
test('salva foto real localmente, restaura após recarga e mantém edição idempotente', async ({
  page,
}) => {
  await page.goto('/#capture');
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
  await page.getByRole('button', { name: 'Salvar rascunho local' }).click();
  await expect(page.getByText('Localização pendente', { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByRole('heading', { name: 'evidencia.png' })).toBeVisible();
  await page.getByRole('button', { name: 'Continuar edição' }).click();
  await page.getByLabel('Latitude', { exact: true }).fill('0');
  await page.getByLabel('Longitude', { exact: true }).fill('0');
  await page.getByRole('button', { name: 'Salvar rascunho local' }).click();
  await expect(page.getByText('0.00000, 0.00000', { exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'evidencia.png' })).toHaveCount(1);
  page.once('dialog', (dialog) => dialog.accept());
  await page.getByRole('button', { name: 'Excluir', exact: true }).click();
  await expect(
    page.getByRole('heading', { name: 'Seu próximo registro começa aqui' }),
  ).toBeVisible();
});
test('rejeita conteúdo inválido com extensão de imagem', async ({ page }) => {
  await page.goto('/#capture');
  await page.getByLabel('Escolher foto').setInputFiles({
    name: 'falsa.jpg',
    mimeType: 'image/jpeg',
    buffer: Buffer.from('não é imagem'),
  });
  await expect(page.getByRole('alert')).toContainText('Não foi possível ler');
});

test('exibe dados da API, filtra e mantém zero de confiança e coordenadas originais', async ({
  page,
}) => {
  await page.route('**/api/v1/health', (route) =>
    route.fulfill({ json: { status: 'ok', database: 'connected' } }),
  );
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
  await page.goto('/#events');
  await expect(page.getByText('0.0%', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Ver registro' }).click();
  await expect(page.getByText('-23.5, -46.6', { exact: true })).toBeVisible();
  await expect(page.getByText('-23.501, -46.601', { exact: true })).toBeVisible();
  await page.getByLabel('Estado', { exact: true }).selectOption('confirmed');
  await expect(
    page.getByRole('heading', { name: 'Nenhuma ocorrência neste recorte' }),
  ).toBeVisible();
  await page.getByRole('link', { name: 'Gêmeo digital 2D' }).click();
  await expect(
    page.getByText('Base cartográfica não configurada.', { exact: false }),
  ).toBeVisible();
  await expect(page.locator('.map canvas')).toBeVisible();
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
  await expect(page.getByRole('heading', { name: 'Um olhar atento à cidade.' })).toBeVisible();
  await page.getByRole('link', { name: 'Registrar evidência' }).click();
  await expect(page.getByRole('button', { name: 'Salvar rascunho local' })).toBeVisible();
});
