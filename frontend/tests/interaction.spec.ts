import { expect, test, type Page } from '@playwright/test';
import { signedIn, stubPublicApi, syntheticJpegWithGps, transparency } from './fixtures';

const CAPTURE_ID = '11111111-1111-4111-8111-111111111111';

/** Relato desta sessão, com protocolo, para a paleta e o acompanhamento. */
async function ownReport(page: Page) {
  await page.route('**/api/v1/captures/markers*', (route) =>
    route.fulfill({
      json: [
        {
          id: CAPTURE_ID,
          latitude: -23.556,
          longitude: -46.637,
          report_status: 'processing',
          protocol_code: 'URM-2026-00042',
          user_description: 'Buraco perto da faixa',
          created_at: '2026-09-24T12:00:00Z',
        },
      ],
    }),
  );
  await page.route(`**/api/v1/captures/${CAPTURE_ID}/processing`, (route) =>
    route.fulfill({
      json: {
        capture_id: CAPTURE_ID,
        status: 'needs_review',
        protocol_code: 'URM-2026-00042',
        requires_manual_location: false,
        event_ids: [],
        event_public_ids: [],
        model_version_id: null,
        model_status: null,
        updated_at: null,
      },
    }),
  );
  await page.route(`**/api/v1/captures/*/timeline`, (route) => route.fulfill({ json: [] }));
  await page.route(`**/api/v1/captures/*/image`, (route) =>
    route.fulfill({ json: { image_url: '/icon.svg' } }),
  );
}

/** A busca abre pelo botão da lateral (computador) ou pela lupa do topo (celular). */
async function openPalette(page: Page) {
  await page
    .getByRole('button', { name: 'Buscar página ou relato' })
    .filter({ visible: true })
    .click();
  const dialog = page.getByRole('dialog', { name: 'Ir para' });
  await expect(dialog).toBeVisible();
  return dialog;
}

test('a paleta "Ir para" abre pelo botão e pelo Ctrl+K, filtra e leva à página', async ({
  page,
}) => {
  await stubPublicApi(page);
  await page.goto('/');
  let dialog = await openPalette(page);
  const search = dialog.getByRole('combobox', { name: 'Buscar página, relato ou protocolo' });
  await expect(search).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(dialog).toBeHidden();

  await page.keyboard.press('Control+k');
  dialog = page.getByRole('dialog', { name: 'Ir para' });
  await expect(dialog).toBeVisible();
  await page.keyboard.type('transparencia');
  await expect(dialog.getByRole('option')).toHaveCount(1);
  await expect(dialog.getByRole('option', { selected: true })).toHaveText('Como o UrMind funciona');
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(/#\/transparency$/);
  await expect(page.getByRole('heading', { name: 'Como o UrMind funciona' })).toBeVisible();
  await expect(dialog).toBeHidden();
});

test('a paleta encontra o relato desta sessão pelo protocolo', async ({ page }) => {
  await stubPublicApi(page);
  await signedIn(page);
  await ownReport(page);
  await page.goto('/#/meus-relatos');
  await expect(page.getByRole('heading', { name: 'Meus relatos' })).toBeVisible();
  const dialog = await openPalette(page);
  await page.keyboard.type('URM-2026-00042');
  await expect(dialog.getByRole('option', { selected: true })).toContainText(
    'Abrir o relato URM-2026-00042',
  );
  await page.keyboard.press('ArrowDown');
  await expect(dialog.getByRole('option', { selected: true })).toContainText(
    'Buraco perto da faixa',
  );
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(new RegExp(`#/processando/${CAPTURE_ID}$`));
  await expect(page.getByText('URM-2026-00042', { exact: true })).toBeVisible();
});

test('o caminho do relato mostra a análise automática como plano e navega por teclado', async ({
  page,
}) => {
  // Estado real de hoje: nenhum modelo registrado.
  await stubPublicApi(page, { transparency: { ...transparency, model_name: null } });
  await page.goto('/#/transparency');
  const steps = page.getByRole('tablist', { name: 'Etapas de um relato' });
  await expect(steps.getByRole('tab')).toHaveCount(7);
  await expect(steps.getByRole('tab', { name: /Análise automática/ })).toContainText('Planejado');
  // Etapa que já funciona não leva rótulo: só a planejada é marcada.
  await expect(steps.getByRole('tab', { name: /Revisão da equipe/ })).not.toContainText(
    'Planejado',
  );

  await steps.getByRole('tab', { name: /^Relato/ }).focus();
  await page.keyboard.press('ArrowRight');
  await page.keyboard.press('ArrowRight');
  const detection = steps.getByRole('tab', { name: /Análise automática/ });
  await expect(detection).toBeFocused();
  await expect(detection).toHaveAttribute('aria-selected', 'true');
  const panel = page.getByRole('tabpanel');
  await expect(panel).toContainText('Hoje não há análise automática');
  await expect(panel).toContainText('YOLO26s');
  await page.keyboard.press('End');
  await expect(page.getByRole('tabpanel')).toContainText('Mapa público');
});

test('modelo registrado sem classe liberada não liga a análise automática', async ({ page }) => {
  // A fixture traz um detector em Staging; a taxonomia não libera classe alguma.
  await stubPublicApi(page);
  await page.goto('/#/transparency');
  const detection = page
    .getByRole('tablist', { name: 'Etapas de um relato' })
    .getByRole('tab', { name: /Análise automática/ });
  await expect(detection).toContainText('Planejado');
  await detection.click();
  await expect(page.getByRole('tabpanel')).toContainText(
    'programa em avaliação (synthetic-detector)',
  );
});

test('a checagem da foto dá o resultado em uma linha e as medidas sob demanda', async ({
  page,
}) => {
  await stubPublicApi(page);
  await page.goto('/#/registrar');
  await page.getByLabel('Escolher foto').setInputFiles({
    name: 'rua.jpg',
    mimeType: 'image/jpeg',
    buffer: await syntheticJpegWithGps(page, -23.556, -46.637),
  });
  const check = page.getByRole('region', { name: 'Checagem da foto' });
  await expect(check).toContainText('Foto aprovada: boa resolução, luz e nitidez.');
  // Os números ficam recolhidos; quem quiser conferir abre as medidas.
  await expect(check.getByText('640 × 640 px')).toBeHidden();
  await check.getByText('Ver medidas').click();
  await expect(check.getByText('640 × 640 px')).toBeVisible();
  await expect(check).toContainText('mínimo de 640 px no lado menor');
  await expect(check).toContainText('aceita de 20 a 240');
  // O local achado na foto aparece em palavras, sem coordenadas soltas.
  const location = page.getByRole('status').filter({ hasText: 'Localização encontrada na foto' });
  await expect(location).toContainText('Se o ponto não estiver certo, corrija no mapa.');
  await expect(location).not.toContainText('-23.556');
});

test('copiar o protocolo confirma no próprio botão', async ({ page, browserName }) => {
  test.skip(browserName !== 'chromium', 'permissão de área de transferência do Chromium');
  await page.context().grantPermissions(['clipboard-read', 'clipboard-write']);
  await stubPublicApi(page);
  await signedIn(page);
  await ownReport(page);
  await page.goto(`/#/processando/${CAPTURE_ID}`);
  await page.getByRole('button', { name: 'Copiar protocolo' }).click();
  await expect(page.getByRole('button', { name: 'Copiado' })).toBeVisible();
  await expect(page.getByText('Protocolo copiado.')).toBeVisible();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe('URM-2026-00042');
});
