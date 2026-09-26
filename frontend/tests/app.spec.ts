import { expect, test, type Page } from '@playwright/test';
import {
  signedIn,
  stubPublicApi,
  supabaseStorageKey,
  syntheticJpegWithGps,
  syntheticReportPhoto,
} from './fixtures';

test.beforeEach(async ({ page }) => {
  // Fixtures exclusivamente de teste. Nenhuma ocorrência fictícia entra no produto.
  await stubPublicApi(page, { events: [], detail: null });
});

/** Conta as leituras do GPS atual (getCurrentPosition) feitas pela página. */
async function countGpsReads(page: Page) {
  await page.addInitScript(() => {
    const geolocation = navigator.geolocation;
    const original = geolocation.getCurrentPosition.bind(geolocation);
    const state = window as unknown as { __gpsReads: number };
    state.__gpsReads = 0;
    geolocation.getCurrentPosition = (...args: Parameters<Geolocation['getCurrentPosition']>) => {
      state.__gpsReads += 1;
      return original(...args);
    };
  });
  return () => page.evaluate(() => (window as unknown as { __gpsReads: number }).__gpsReads);
}

const GALLERY_PNG = async (page: Page) => ({
  name: 'report.png',
  mimeType: 'image/png',
  buffer: Buffer.from(await syntheticReportPhoto(page), 'base64'),
});

test('A. foto tirada agora envia GPS, precisão e horário, e o ponto aparece no mapa do autor', async ({
  page,
}) => {
  await signedIn(page);
  await page.context().grantPermissions(['geolocation']);
  await page.context().setGeolocation({ latitude: -23.556, longitude: -46.637, accuracy: 12 });
  const id = '6f1c2b3a-4d5e-4f60-8a71-92b3c4d5e6f7';
  let body = '';
  await page.route('**/api/v1/public/auth-origin', (route) =>
    route.fulfill({
      json: {
        auth_origin: `https://${supabaseStorageKey().split('-')[1]}.supabase.co`,
        visitor_upload_enabled: true,
      },
    }),
  );
  await page.route('**/api/v1/captures/nearby-reports?*', (route) => route.fulfill({ json: [] }));
  await page.route('**/api/v1/captures/photo', (route) => {
    body = route.request().postData() ?? '';
    return route.fulfill({
      json: { id, capture_key: 'photo-gps', created: true, requires_manual_location: false },
    });
  });
  await page.route(`**/api/v1/captures/${id}/processing`, (route) =>
    route.fulfill({
      json: {
        capture_id: id,
        status: 'queued',
        requires_manual_location: false,
        event_ids: [],
        model_version_id: null,
        model_status: null,
        updated_at: null,
      },
    }),
  );
  await page.route(`**/api/v1/captures/${id}/image`, (route) =>
    route.fulfill({ status: 404, json: { detail: 'sem foto no teste' } }),
  );
  await page.route('**/api/v1/captures/markers*', (route) =>
    route.fulfill({
      json: body
        ? [
            {
              id,
              latitude: -23.556,
              longitude: -46.637,
              report_status: 'received',
              location_source: 'gps_device',
              accuracy_m: 12,
              created_at: new Date().toISOString(),
            },
          ]
        : [],
    }),
  );
  await page.goto('/#/registrar');
  await page.getByLabel('Tirar foto').setInputFiles({
    ...(await GALLERY_PNG(page)),
    name: 'camera.png',
  });
  await expect(page.getByText('Localização obtida')).toBeVisible();
  await expect(page.getByText('Precisão aproximada: 12 m')).toBeVisible();
  await expect(page.getByLabel(/Latitude|Longitude/)).toHaveCount(0);
  await expect(page.locator('.map canvas')).toHaveCount(0);
  await page.getByRole('checkbox', { name: /Li e aceito/ }).check();
  await page.getByRole('button', { name: 'Enviar relato' }).click();
  await expect.poll(() => body).toContain('name="latitude"');
  expect(body).toMatch(/name="latitude"\r\n\r\n-23\.556/);
  expect(body).toMatch(/name="longitude"\r\n\r\n-46\.637/);
  expect(body).toMatch(/name="accuracy_m"\r\n\r\n12/);
  expect(body).toMatch(/name="location_source"\r\n\r\ngps_device/);
  expect(body).toMatch(/name="location_timestamp"\r\n\r\n20\d\d-/);
  expect(body).toMatch(/name="captured_at"\r\n\r\n20\d\d-/);
  // O relato aparece na hora no mapa do próprio autor, com a situação dele.
  await expect(page).toHaveURL(new RegExp(`processando/${id}`));
  // O mapa é carregado sob demanda; sob carga da máquina pode levar mais que 5 s.
  await expect(page.locator(`[data-event-id="${id}"]`)).toHaveCount(1, { timeout: 20_000 });
  const detail = page.getByRole('complementary', { name: 'Detalhe do ponto' });
  await expect(detail).toContainText('Recebido');
  await expect(detail).toContainText('Precisão aproximada: 12 m');
  // Depois de recarregar a página, o ponto continua lá.
  await page.reload();
  await expect(page.locator(`[data-event-id="${id}"]`)).toHaveCount(1);
});

test('B. sem permissão de localização, a foto tirada agora explica e abre o mapa', async ({
  page,
}) => {
  await page.context().clearPermissions();
  await page.goto('/#/registrar');
  await page.evaluate(() => {
    const denied = (_ok: unknown, fail: (error: { code: number }) => void) => fail({ code: 1 });
    Object.defineProperty(navigator, 'geolocation', {
      value: { getCurrentPosition: denied, watchPosition: () => 1, clearWatch: () => undefined },
    });
  });
  await page.getByLabel('Tirar foto').setInputFiles(await GALLERY_PNG(page));
  await expect(page.getByText('Localização indisponível')).toBeVisible();
  await expect(page.getByText(/Localização bloqueada para este site/).first()).toBeVisible();
  await expect(page.getByRole('button', { name: 'Tentar de novo' })).toBeVisible();
  await expect(
    page.getByRole('heading', { name: 'Selecione no mapa onde esta foto foi tirada' }),
  ).toBeVisible();
  await expect(page.locator('.map canvas')).toBeVisible();
});

test('C. GPS sem sinal leva ao mapa sem inventar posição', async ({ page }) => {
  await page.goto('/#/registrar');
  await page.evaluate(() => {
    const unavailable = (_ok: unknown, fail: (error: { code: number }) => void) =>
      fail({ code: 2 });
    Object.defineProperty(navigator, 'geolocation', {
      value: {
        getCurrentPosition: unavailable,
        watchPosition: () => 1,
        clearWatch: () => undefined,
      },
    });
  });
  await page.getByLabel('Tirar foto').setInputFiles(await GALLERY_PNG(page));
  await expect(page.getByText(/não conseguiu a posição agora/)).toBeVisible();
  await expect(page.getByText('Localização obtida')).toHaveCount(0);
  await expect(page.locator('.map canvas')).toBeVisible();
});

test('D. foto da galeria com GPS no EXIF usa a posição da foto, não o GPS atual', async ({
  page,
}) => {
  await page.context().grantPermissions(['geolocation']);
  // O aparelho está em outro lugar: esta posição não pode ir para a foto antiga.
  await page.context().setGeolocation({ latitude: -22.9, longitude: -43.2, accuracy: 5 });
  const gpsReads = await countGpsReads(page);
  await page.goto('/#/registrar');
  await page.getByLabel('Escolher foto').setInputFiles({
    name: 'rua-com-exif.jpg',
    mimeType: 'image/jpeg',
    buffer: await syntheticJpegWithGps(page, -23.556, -46.637),
  });
  await expect(page.getByText('Localização encontrada na foto')).toBeVisible();
  await expect(page.getByText('Localização obtida')).toHaveCount(0);
  // Sem precisão no EXIF, nenhuma é inventada.
  await expect(page.getByText(/Precisão aproximada/)).toHaveCount(0);
  await expect(page.locator('.map canvas')).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Corrigir no mapa' })).toBeVisible();
  expect(await gpsReads()).toBe(0);
});

test('E. foto da galeria sem EXIF exige o ponto no mapa e nunca lê o GPS atual', async ({
  page,
}) => {
  await page.context().grantPermissions(['geolocation']);
  await page.context().setGeolocation({ latitude: -22.9, longitude: -43.2, accuracy: 5 });
  const gpsReads = await countGpsReads(page);
  await page.goto('/#/registrar');
  await page.getByLabel('Escolher foto').setInputFiles(await GALLERY_PNG(page));
  await expect(page.getByText('Precisamos que você confirme onde a foto foi tirada')).toBeVisible();
  await expect(
    page.getByRole('heading', { name: 'Selecione no mapa onde esta foto foi tirada' }),
  ).toBeVisible();
  await expect(page.getByRole('button', { name: 'Tentar de novo' })).toHaveCount(0);
  await page.locator('.map canvas').click();
  await page.getByRole('button', { name: 'Confirmar local', exact: true }).click();
  await expect(page.getByText('Local marcado no mapa')).toBeVisible();
  await expect(page.getByLabel(/Latitude|Longitude/)).toHaveCount(0);
  expect(await gpsReads()).toBe(0);
});

test('E3. sem localização, busca o endereço, marca o ponto e confirma o local', async ({
  page,
}, testInfo) => {
  const queries: Array<{ method: string; body: unknown }> = [];
  await page.route('**/api/v1/public/geocode', (route) => {
    queries.push({ method: route.request().method(), body: route.request().postDataJSON() });
    return route.fulfill({
      json: {
        results: [
          {
            label: 'Rua Galvão Bueno, 100',
            detail: 'Liberdade, São Paulo, SP',
            latitude: -23.5584,
            longitude: -46.6352,
          },
        ],
        attribution: 'Dados © colaboradores do OpenStreetMap (ODbL), via Nominatim',
      },
    });
  });
  await page.goto('/#/registrar');
  await page.getByLabel('Escolher foto').setInputFiles(await GALLERY_PNG(page));
  await expect(
    page.getByRole('heading', { name: 'Selecione no mapa onde esta foto foi tirada' }),
  ).toBeVisible();
  // Sem localização, o mapa abre numa região útil (nível de bairro), não no Brasil inteiro.
  await expect(page.locator('.map canvas')).toBeVisible();
  const search = page.getByLabel('Buscar endereço ou CEP');
  await search.fill('Rua Galvão Bueno 100');
  // Enter busca o endereço; nunca envia o relato nem busca a cada tecla.
  expect(queries).toHaveLength(0);
  await search.press('Enter');
  const results = page.getByRole('list', { name: 'Endereços encontrados' });
  await expect(results.getByRole('button')).toHaveCount(1);
  expect(queries).toEqual([{ method: 'POST', body: { query: 'Rua Galvão Bueno 100' } }]);
  await expect(page.getByText(/via Nominatim/)).toBeVisible();
  const confirm = page.getByRole('button', { name: 'Confirmar local', exact: true });
  await expect(confirm).toBeDisabled();
  await results.getByRole('button', { name: /Rua Galvão Bueno, 100/ }).click();
  await expect(page.getByText('Confira o marcador e toque no mapa para ajustar')).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('busca-endereco.png'), fullPage: true });
  await confirm.click();
  await expect(page.getByText('Local marcado no mapa')).toBeVisible();
  await expect(page.getByLabel(/Latitude|Longitude/)).toHaveCount(0);
});

test('E4. busca de endereço ocupada ou sem resultado explica o que fazer', async ({ page }) => {
  let busy = true;
  await page.route('**/api/v1/public/geocode', (route) =>
    busy
      ? route.fulfill({ status: 429, json: { detail: 'busy' } })
      : route.fulfill({ json: { results: [], attribution: 'OpenStreetMap' } }),
  );
  await page.goto('/#/registrar');
  await page.getByLabel('Escolher foto').setInputFiles(await GALLERY_PNG(page));
  const search = page.getByLabel('Buscar endereço ou CEP');
  await search.fill('01503-001');
  await page.getByRole('button', { name: 'Buscar', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('Aguarde um instante');
  busy = false;
  await page.getByRole('button', { name: 'Buscar', exact: true }).click();
  await expect(page.getByText(/Nenhum endereço encontrado/)).toBeVisible();
  // Menos de 3 letras: nada é consultado.
  await search.fill('ab');
  await expect(page.getByRole('button', { name: 'Buscar', exact: true })).toBeDisabled();
});

test('E2. sem mouse, o ponto é marcado pelo centro do mapa usando só o teclado', async ({
  page,
}) => {
  await page.goto('/#/registrar');
  await page.getByLabel('Escolher foto').setInputFiles(await GALLERY_PNG(page));
  const center = page.getByRole('button', { name: 'Marcar o centro do mapa' });
  await center.focus();
  await page.keyboard.press('Enter');
  const confirm = page.getByRole('button', { name: 'Confirmar local', exact: true });
  await expect(confirm).toBeEnabled();
  await confirm.focus();
  await page.keyboard.press('Enter');
  await expect(page.getByText('Local marcado no mapa')).toBeVisible();
});

test('F. resposta atrasada do GPS de uma foto anterior não vai para a foto atual', async ({
  page,
}) => {
  await page.addInitScript(() => {
    const state = window as unknown as { __pendingFixes: Array<(p: unknown) => void> };
    state.__pendingFixes = [];
    Object.defineProperty(navigator, 'geolocation', {
      configurable: true,
      value: {
        getCurrentPosition: (ok: (p: unknown) => void) => state.__pendingFixes.push(ok),
        watchPosition: () => 1,
        clearWatch: () => undefined,
      },
    });
  });
  await page.goto('/#/registrar');
  await page.getByLabel('Tirar foto').setInputFiles({
    ...(await GALLERY_PNG(page)),
    name: 'primeira.png',
  });
  await expect(page.getByText('Obtendo localização…')).toBeVisible();
  // Antes do GPS responder, a pessoa troca por uma foto da galeria.
  await page.getByLabel('Escolher foto').setInputFiles({
    ...(await GALLERY_PNG(page)),
    name: 'segunda.png',
  });
  await expect(page.getByText('Precisamos que você confirme onde a foto foi tirada')).toBeVisible();
  await page.evaluate(() => {
    const state = window as unknown as { __pendingFixes: Array<(p: unknown) => void> };
    for (const resolve of state.__pendingFixes)
      resolve({
        coords: { latitude: -23.55, longitude: -46.63, accuracy: 5, heading: null, speed: null },
        timestamp: Date.now(),
      });
  });
  await expect(page.getByText('Localização obtida')).toHaveCount(0);
  await expect(page.getByText('Precisamos que você confirme onde a foto foi tirada')).toBeVisible();
});
test('captura usa o tema escuro do UrMind e alvos de toque em 320px', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 740 });
  await page.emulateMedia({ colorScheme: 'light' });
  await page.goto('/#/registrar');
  // Identidade única, escura, qualquer que seja a preferência do aparelho.
  await expect(page.locator('html')).toHaveCSS('color-scheme', 'dark');
  await expect(page.locator('body')).toHaveCSS('background-color', 'rgb(12, 20, 17)');
  await expect(page.locator('.panel').first()).toHaveCSS('background-color', 'rgb(25, 41, 35)');
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(320);
  for (const button of await page.getByRole('button').all()) {
    // File input is intentionally transparent; its enclosing label is the touch target.
    const target = (await button.getAttribute('type')) === 'file' ? button.locator('..') : button;
    if (await target.isVisible())
      expect((await target.boundingBox())!.height).toBeGreaterThanOrEqual(44);
  }
  await page.emulateMedia({ colorScheme: 'dark' });
  await expect(page.locator('html')).toHaveCSS('color-scheme', 'dark');
});

test('foto e descrição viram relato no mapa sem modelo e localização pode vir depois', async ({
  page,
}) => {
  await signedIn(page);
  const id = '2b120c24-7ff1-4f58-bda8-c2f82a94fc05';
  const description = '<script>alert(1)</script>';
  let located = false;
  let uploaded = false;
  await page.route('**/api/v1/public/auth-origin', (route) =>
    route.fulfill({
      json: {
        auth_origin: `https://${supabaseStorageKey().split('-')[1]}.supabase.co`,
        visitor_upload_enabled: true,
      },
    }),
  );
  await page.route('**/api/v1/captures/photo', (route) => {
    expect(route.request().postData()).toContain('name="user_description"');
    expect(route.request().postData()).toContain(description);
    uploaded = true;
    return route.fulfill({
      json: { id, capture_key: 'photo-fixture', created: true, requires_manual_location: true },
    });
  });
  await page.route(`**/api/v1/captures/${id}/processing`, (route) =>
    route.fulfill({
      json: {
        capture_id: id,
        status: located ? 'model_not_available' : 'location_required',
        requires_manual_location: !located,
        event_ids: [],
        model_version_id: null,
        model_status: null,
        updated_at: null,
      },
    }),
  );
  await page.route(`**/api/v1/captures/${id}/location`, (route) => {
    expect(route.request().method()).toBe('PATCH');
    const point = route.request().postDataJSON();
    expect(point.latitude === 0 && point.longitude === 0).toBe(false);
    located = true;
    return route.fulfill({ json: { capture_id: id, location_source: 'manual' } });
  });
  await page.route('**/api/v1/captures/markers*', (route) =>
    route.fulfill({
      json: located
        ? [
            {
              id,
              latitude: -23.55,
              longitude: -46.63,
              report_status: 'model_not_available',
              user_description: description,
              urmind_class: null,
              severity: null,
            },
          ]
        : [],
    }),
  );
  await page.goto('/#/registrar');
  await page.getByLabel('Descreva o que você observou (opcional)').fill(description);
  await expect(page.getByText(`${description.length}/500 caracteres`)).toBeVisible();
  const png = await syntheticReportPhoto(page);
  await page.getByLabel('Escolher foto').setInputFiles({
    name: 'report.png',
    mimeType: 'image/png',
    buffer: Buffer.from(png, 'base64'),
  });
  await page.getByRole('button', { name: 'Enviar relato' }).click();
  await expect(page.getByRole('alert')).toContainText('Leia e aceite o aviso de privacidade');
  expect(uploaded).toBe(false);
  await page.getByRole('checkbox', { name: /Li e aceito/ }).check();
  await page.getByRole('button', { name: 'Enviar relato' }).click();
  await expect.poll(() => uploaded).toBe(true);
  await expect(page).toHaveURL(new RegExp(`processando/${id}`));
  await expect(page.getByText('Situação: Precisa de localização', { exact: true })).toBeVisible();
  // O mapa enquadra o Brasil inteiro; o centro do quadro fica em território
  // brasileiro, enquanto os cantos caem na máscara e são recusados.
  await page.locator('.map canvas').click();
  await page.getByRole('button', { name: 'Confirmar localização do relato' }).click();
  await expect(page.getByText('Situação: Em análise', { exact: true })).toBeVisible();
  // A descrição aparece na lista e no detalhe do ponto recém-enviado, sempre como texto.
  await expect(page.getByText(description, { exact: true }).first()).toBeVisible();
  await expect(page.locator(`[data-event-id="${id}"]`)).toHaveCount(1);
  await expect(page.getByRole('list', { name: 'Legenda de relatos' })).toContainText('Em análise');
});

test('confirma relato próximo e envia evidência adicional com aceite', async ({ page }) => {
  await signedIn(page);
  const id = '2b120c24-7ff1-4f58-bda8-c2f82a94fc05';
  const parent = 'a'.repeat(32);
  await page.route('**/api/v1/public/auth-origin', (route) =>
    route.fulfill({
      json: {
        auth_origin: `https://${supabaseStorageKey().split('-')[1]}.supabase.co`,
        visitor_upload_enabled: true,
      },
    }),
  );
  await page.route('**/api/v1/captures/nearby-reports?*', (route) =>
    route.fulfill({ json: [{ public_id: parent, distance_m: 12 }] }),
  );
  let linked = false;
  await page.route('**/api/v1/captures/photo', (route) => {
    const body = route.request().postData() ?? '';
    expect(body).toContain('name="additional_to"');
    expect(body).toContain(parent);
    expect(body).toContain('urmind-capture-privacy-v1');
    linked = true;
    return route.fulfill({
      json: {
        id,
        capture_key: 'photo-test',
        created: true,
        additional_evidence: true,
        requires_manual_location: false,
      },
    });
  });
  await page.route(`**/api/v1/captures/${id}/processing`, (route) =>
    route.fulfill({
      json: {
        capture_id: id,
        status: 'needs_review',
        additional_evidence: true,
        requires_manual_location: false,
        event_ids: [],
        model_version_id: null,
        model_status: null,
        updated_at: null,
      },
    }),
  );
  page.on('dialog', async (dialog) => {
    expect(dialog.message()).toContain('É o mesmo problema?');
    await dialog.accept();
  });
  await page.goto('/#/registrar');
  await page.getByLabel('Escolher foto').setInputFiles({
    name: 'report.png',
    mimeType: 'image/png',
    buffer: Buffer.from(await syntheticReportPhoto(page), 'base64'),
  });
  await page.locator('.map canvas').click();
  await page.getByRole('button', { name: 'Confirmar local', exact: true }).click();
  await page.getByRole('checkbox', { name: /Li e aceito/ }).check();
  await page.getByRole('button', { name: 'Enviar relato' }).click();
  await expect.poll(() => linked).toBe(true);
  await expect(page.getByText(/Foto anexada como evidência adicional/)).toBeVisible();
});

test('recupera a captura pela URL após refresh sem antecipar análise', async ({ page }) => {
  await signedIn(page);
  const captureId = '2b120c24-7ff1-4f58-bda8-c2f82a94fc05';
  const eventId = 'cc12fe42-66d6-4791-a6c3-5a6f2a8734b9';
  const publicId = 'a732cb867fd24d188f0f234afde8a664';
  let stage: 'detection_completed' | 'completed' = 'detection_completed';
  let reads = 0;
  await page.route(`**/api/v1/captures/${captureId}/processing`, async (route) => {
    reads += 1;
    await route.fulfill({
      json: {
        capture_id: captureId,
        status: stage,
        requires_manual_location: false,
        event_ids: [eventId],
        event_public_ids: [publicId],
        protocol_code: 'URM-7K3Q9XYZ',
        model_version_id: null,
        model_status: 'EXPERIMENTAL_SHADOW',
        updated_at: null,
      },
    });
  });
  await page.goto(`/#/processando/${captureId}`);
  await expect(page.getByText('Situação: Em análise', { exact: true })).toBeVisible();
  await expect(page.getByText(/Resultado automático em teste/)).toBeVisible();
  await expect(page.getByRole('link', { name: 'Ver ocorrência no mapa' })).toHaveCount(0);
  await page.reload();
  await expect(page.getByText('Situação: Em análise', { exact: true })).toBeVisible();
  expect(reads).toBeGreaterThanOrEqual(2);
  stage = 'completed';
  await page.reload();
  await expect(page.getByRole('link', { name: 'Ver ocorrência no mapa' })).toHaveAttribute(
    'href',
    `#/resultado/${publicId}`,
  );
});

test('rota de captura alheia mostra acesso negado', async ({ page }) => {
  await signedIn(page);
  const captureId = '2b120c24-7ff1-4f58-bda8-c2f82a94fc05';
  await page.route(`**/api/v1/captures/${captureId}/processing`, (route) =>
    route.fulfill({ status: 404, json: { detail: 'Captura não encontrada' } }),
  );
  await page.goto(`/#/processando/${captureId}`);
  await expect(page.getByRole('alert')).toContainText('Captura não encontrada ou sem acesso');
});

test('protocolo recupera apenas o relato da sessão e sobrevive ao refresh', async ({ page }) => {
  await signedIn(page);
  const captureId = '2b120c24-7ff1-4f58-bda8-c2f82a94fc05';
  await page.route('**/api/v1/captures/by-protocol/URM-7K3Q9XYZ', (route) =>
    route.fulfill({ json: { capture_id: captureId, protocol_code: 'URM-7K3Q9XYZ' } }),
  );
  await page.route(`**/api/v1/captures/${captureId}/processing`, (route) =>
    route.fulfill({
      json: {
        capture_id: captureId,
        protocol_code: 'URM-7K3Q9XYZ',
        status: 'model_not_available',
        requires_manual_location: false,
        event_ids: [],
        event_public_ids: [],
        model_version_id: null,
        model_status: null,
        updated_at: null,
      },
    }),
  );
  await page.goto('/#/relato/URM-7K3Q9XYZ');
  await expect(page.getByRole('button', { name: 'Copiar protocolo' })).toBeVisible();
  await page.reload();
  await expect(page.getByText('Situação: Em análise', { exact: true })).toBeVisible();
  await expect(page.getByText('URM-7K3Q9XYZ', { exact: true })).toBeVisible();
});

test('logout em outra aba remove o resultado e interrompe consultas da captura', async ({
  page,
}) => {
  await signedIn(page);
  const captureId = '2b120c24-7ff1-4f58-bda8-c2f82a94fc05';
  let reads = 0;
  await page.route(`**/api/v1/captures/${captureId}/processing`, (route) => {
    reads += 1;
    return route.fulfill({
      json: {
        capture_id: captureId,
        status: 'completed',
        requires_manual_location: false,
        event_ids: ['cc12fe42-66d6-4791-a6c3-5a6f2a8734b9'],
        event_public_ids: ['a732cb867fd24d188f0f234afde8a664'],
        model_version_id: null,
        model_status: 'EXPERIMENTAL_SHADOW',
        updated_at: null,
      },
    });
  });
  await page.goto(`/#/processando/${captureId}`);
  await expect(page.getByRole('link', { name: 'Ver ocorrência no mapa' })).toBeVisible();
  await page.evaluate((key) => {
    localStorage.removeItem(key);
    const channel = new BroadcastChannel(key);
    channel.postMessage({ event: 'SIGNED_OUT', session: null });
    channel.close();
  }, supabaseStorageKey());
  await expect(page.getByRole('link', { name: 'Ver ocorrência no mapa' })).toHaveCount(0);
  await expect(page.getByText('Situação: Analisado', { exact: true })).toHaveCount(0);
  await expect(page.getByText(/modelo rejeitado para produção/)).toHaveCount(0);
  await expect(page).not.toHaveURL(new RegExp(captureId));
  const stoppedAt = reads;
  await page.clock.install();
  await page.clock.fastForward(10_000);
  expect(reads).toBe(stoppedAt);
});

test('bloqueia signup público se frontend e backend apontam para projetos diferentes', async ({
  page,
}) => {
  let authCalls = 0;
  await page.route('**/api/v1/public/auth-origin', (route) =>
    route.fulfill({ json: { auth_origin: 'https://different.supabase.co' } }),
  );
  await page.route('**/*.supabase.co/auth/v1/**', (route) => {
    authCalls += 1;
    return route.abort();
  });
  await page.goto('/#/registrar');
  const png = await syntheticReportPhoto(page);
  await page.getByLabel('Escolher foto').setInputFiles({
    name: 'evidencia.png',
    mimeType: 'image/png',
    buffer: Buffer.from(png, 'base64'),
  });
  await page.locator('.map canvas').click();
  await page.getByRole('button', { name: 'Confirmar local', exact: true }).click();
  await page.getByRole('checkbox', { name: /Li e aceito/ }).check();
  await page.getByRole('button', { name: 'Enviar relato' }).click();
  await expect(page.getByRole('alert')).toContainText('projetos diferentes');
  expect(authCalls).toBe(0);
});

test('logout durante upload cancela resposta tardia sem navegar para captura antiga', async ({
  page,
}) => {
  await signedIn(page);
  await page.route('**/api/v1/public/auth-origin', (route) =>
    route.fulfill({
      json: {
        auth_origin: `https://${supabaseStorageKey().split('-')[1]}.supabase.co`,
        visitor_upload_enabled: true,
      },
    }),
  );
  const captureId = '2b120c24-7ff1-4f58-bda8-c2f82a94fc05';
  let release!: () => void;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  let started = false;
  await page.route('**/api/v1/captures/photo', async (route) => {
    started = true;
    await held;
    await route
      .fulfill({
        json: {
          id: captureId,
          created: true,
          requires_manual_location: false,
          sha256: 'a'.repeat(64),
          storage_path: 'captures/test-only.png',
        },
      })
      .catch(() => undefined); // Aborting the actual request is expected after logout.
  });
  await page.goto('/#/registrar');
  const png = await syntheticReportPhoto(page);
  await page.getByLabel('Escolher foto').setInputFiles({
    name: 'logout-test.png',
    mimeType: 'image/png',
    buffer: Buffer.from(png, 'base64'),
  });
  await page.locator('.map canvas').click();
  await page.getByRole('button', { name: 'Confirmar local', exact: true }).click();
  await page.getByRole('checkbox', { name: /Li e aceito/ }).check();
  await page.getByRole('button', { name: 'Enviar relato' }).click();
  await expect.poll(() => started).toBe(true);
  await page.evaluate((key) => {
    localStorage.removeItem(key);
    const channel = new BroadcastChannel(key);
    channel.postMessage({ event: 'SIGNED_OUT', session: null });
    channel.close();
  }, supabaseStorageKey());
  await expect(page.getByRole('heading', { name: 'Entrar no UrMind' })).toBeVisible();
  release();
  await expect(page.getByText('Foto enviada e registrada', { exact: false })).toHaveCount(0);
  // Give the released HTTP continuation a visible, deterministic checkpoint.
  await expect(page.getByRole('button', { name: 'Enviar', exact: true })).toBeEnabled();
  await expect(page.getByRole('heading', { name: 'logout-test.png' })).toBeVisible();
  await expect(page).not.toHaveURL(new RegExp(captureId));
});

test('logout na fila aborta polling pendente e ignora resposta tardia', async ({ page }) => {
  await signedIn(page);
  const captureId = '2b120c24-7ff1-4f58-bda8-c2f82a94fc05';
  let reads = 0;
  let release!: () => void;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route(`**/api/v1/captures/${captureId}/processing`, async (route) => {
    reads += 1;
    const second = reads > 1;
    if (second) await held;
    await route
      .fulfill({
        json: {
          capture_id: captureId,
          status: second ? 'completed' : 'queued',
          requires_manual_location: false,
          event_ids: second ? ['cc12fe42-66d6-4791-a6c3-5a6f2a8734b9'] : [],
          model_version_id: null,
          model_status: 'EXPERIMENTAL_SHADOW',
          updated_at: null,
        },
      })
      .catch(() => undefined);
  });
  await page.clock.install();
  await page.goto(`/#/processando/${captureId}`);
  await expect(page.getByText('Situação: Recebido', { exact: true })).toBeVisible();
  await page.clock.fastForward(5_100);
  await expect.poll(() => reads).toBe(2);
  await page.evaluate((key) => {
    localStorage.removeItem(key);
    const channel = new BroadcastChannel(key);
    channel.postMessage({ event: 'SIGNED_OUT', session: null });
    channel.close();
  }, supabaseStorageKey());
  await expect(page).toHaveURL(/#\/registrar$/);
  release();
  await page.clock.fastForward(15_000);
  expect(reads).toBe(2);
  await expect(page.getByText('Situação: Analisado', { exact: true })).toHaveCount(0);
  await expect(page.getByRole('link', { name: 'Ver ocorrência no mapa' })).toHaveCount(0);
});

for (const boundary of ['origin', 'signup'] as const) {
  for (const action of ['switch', 'logout', 'success'] as const) {
    test(`sessão ${action} durante ${boundary} mantém isolamento do envio`, async ({ page }) => {
      const key = supabaseStorageKey();
      const origin = `https://${key.split('-')[1]}.supabase.co`;
      let release!: () => void;
      const held = new Promise<void>((resolve) => {
        release = resolve;
      });
      let started = false;
      let uploads = 0;
      const nextSession = {
        access_token: 'replacement-test-token',
        refresh_token: 'replacement-test-refresh',
        token_type: 'bearer',
        expires_in: 3600,
        expires_at: Math.floor(Date.now() / 1000) + 3600,
        user: {
          id: 'b0000000-1111-4222-8333-944445555666',
          aud: 'authenticated',
          role: 'authenticated',
          is_anonymous: false,
        },
      };
      await page.route('**/api/v1/public/auth-origin', async (route) => {
        if (boundary === 'origin') {
          started = true;
          await held;
        }
        await route
          .fulfill({ json: { auth_origin: origin, visitor_upload_enabled: true } })
          .catch(() => undefined);
      });
      await page.route('**/auth/v1/signup', async (route) => {
        started = true;
        await held;
        await route
          .fulfill({
            json: {
              ...nextSession,
              access_token: 'stale-anonymous-token',
              refresh_token: 'stale-refresh',
              user: {
                ...nextSession.user,
                id: 'a0000000-1111-4222-8333-944445555666',
                is_anonymous: true,
              },
            },
          })
          .catch(() => undefined);
      });
      await page.route('**/api/v1/captures/photo', (route) => {
        uploads++;
        return route.fulfill({ status: 401, json: { detail: 'test-only' } });
      });
      await page.goto('/#/registrar');
      const png = await syntheticReportPhoto(page);
      await page.getByLabel('Escolher foto').setInputFiles({
        name: 'auth-boundary.png',
        mimeType: 'image/png',
        buffer: Buffer.from(png, 'base64'),
      });
      await page.locator('.map canvas').click();
      await page.getByRole('button', { name: 'Confirmar local', exact: true }).click();
      await page.getByRole('checkbox', { name: /Li e aceito/ }).check();
      await page.getByRole('button', { name: 'Enviar relato' }).click();
      await expect.poll(() => started).toBe(true);
      const completed = Promise.race([
        page.waitForEvent('requestfinished', (r) =>
          r.url().endsWith(boundary === 'origin' ? '/public/auth-origin' : '/auth/v1/signup'),
        ),
        page.waitForEvent('requestfailed', (r) =>
          r.url().endsWith(boundary === 'origin' ? '/public/auth-origin' : '/auth/v1/signup'),
        ),
      ]);
      if (action !== 'success')
        await page.evaluate(
          ({ key, session, action }) => {
            if (action === 'logout') localStorage.removeItem(key);
            else localStorage.setItem(key, JSON.stringify(session));
            const channel = new BroadcastChannel(key);
            channel.postMessage(
              action === 'logout'
                ? { event: 'SIGNED_OUT', session: null }
                : { event: 'SIGNED_IN', session },
            );
            channel.close();
          },
          { key, session: nextSession, action },
        );
      // A nova sessão chegou ao app: o convite para entrar some da página de rascunhos.
      if (action === 'switch')
        await expect(page.getByRole('heading', { name: 'Entrar no UrMind' })).toHaveCount(0);
      if (action === 'logout')
        await expect(page.getByRole('button', { name: 'Enviar', exact: true })).toBeEnabled();
      release();
      await completed;
      await page.evaluate(
        () => new Promise<void>((resolve) => requestAnimationFrame(() => resolve())),
      );
      if (action === 'success') await expect.poll(() => uploads).toBe(1);
      expect(
        await page.evaluate((key) => {
          const value = localStorage.getItem(key);
          return value ? JSON.parse(value).user.id : null;
        }, key),
      ).toBe(
        action === 'logout'
          ? null
          : action === 'switch'
            ? nextSession.user.id
            : 'a0000000-1111-4222-8333-944445555666',
      );
      expect(uploads).toBe(action === 'success' ? 1 : 0);
      await expect(page.getByRole('heading', { name: 'auth-boundary.png' })).toBeVisible();
    });
  }
}

test('foto sem detecção termina sem anunciar revisão de Event inexistente', async ({ page }) => {
  await signedIn(page);
  const captureId = '2b120c24-7ff1-4f58-bda8-c2f82a94fc05';
  await page.route(`**/api/v1/captures/${captureId}/processing`, (route) =>
    route.fulfill({
      json: {
        capture_id: captureId,
        status: 'no_supported_detection',
        requires_manual_location: false,
        event_ids: [],
        model_version_id: null,
        model_status: 'EXPERIMENTAL_SHADOW',
        updated_at: null,
      },
    }),
  );
  await page.goto(`/#/processando/${captureId}`);
  await expect(page.getByText('Situação: Em análise', { exact: true })).toBeVisible();
  await expect(page.getByText(/não reconheceu buraco nem trinca/)).toBeVisible();
  await expect(page.getByRole('link', { name: 'Ver ocorrência no mapa' })).toHaveCount(0);
});

test('separa o centro público da operação e navega entre os dois', async ({ page }, testInfo) => {
  await page.goto('/');
  await expect(
    page.getByRole('heading', { name: 'Viu um problema na rua? Registre com uma foto.' }),
  ).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('overview.png'), fullPage: true });
  await expect(page.getByRole('navigation', { name: 'Navegação principal' })).not.toContainText(
    'Revisão',
  );
  await page.goto('/#/review');
  // Sem sessão, a revisão pede login em vez de mostrar números.
  await expect(page.getByRole('heading', { name: 'Entrar no UrMind' })).toBeVisible();
  await page.goto('/#/transparency');
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
  const png = await syntheticReportPhoto(page);
  await page.getByLabel('Escolher foto').setInputFiles({
    name: 'evidencia.png',
    mimeType: 'image/png',
    buffer: Buffer.from(png, 'base64'),
  });
  await expect(page.getByAltText('Evidência selecionada')).toBeVisible();
  await page.getByRole('checkbox', { name: /Li e aceito/ }).check();
  await page.getByRole('button', { name: 'Enviar relato' }).click();
  await expect(page.getByText('Localização pendente', { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByRole('heading', { name: 'evidencia.png' })).toBeVisible();
  await page.getByRole('button', { name: 'Continuar edição' }).click();
  // Rascunho da galeria sem local: o mapa abre sozinho; nenhum campo de coordenada.
  await expect(page.getByLabel(/Latitude|Longitude/)).toHaveCount(0);
  await expect(
    page.getByRole('heading', { name: 'Selecione no mapa onde esta foto foi tirada' }),
  ).toBeVisible();
  const confirm = page.getByRole('button', { name: 'Confirmar local', exact: true });
  await expect(confirm).toBeDisabled();
  await page.locator('.map canvas').click();
  await confirm.click();
  await expect(page.getByText('Local marcado no mapa')).toBeVisible();
  await page.getByRole('checkbox', { name: /Li e aceito/ }).check();
  await page.getByRole('button', { name: 'Enviar relato' }).click();
  await expect(page.getByRole('heading', { name: 'evidencia.png' })).toBeVisible();
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
  await page.route('**/api/v1/me', (route) =>
    route.fulfill({
      json: {
        id: '0b0e4c7e-1111-4222-8333-944445555666',
        email: null,
        can_review: true,
        can_admin: false,
      },
    }),
  );
  await page.route('**/api/v1/events?limit=100', (route) =>
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
  // Evento ainda em revisão: o score do modelo não aparece antes da decisão humana.
  await expect(page.getByText('Oculto até a revisão', { exact: true })).toBeVisible();
  await expect(page.getByText('0.0%', { exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: 'Ver registro' }).click();
  await expect(page.getByText('-23.5, -46.6', { exact: true })).toBeVisible();
  // Severidade e prioridade ficam ocultas até a primeira decisão humana.
  await expect(page.getByText('Oculto até a decisão humana')).toHaveCount(2);
  // Rejeitar exige motivo; só "erro visual" vira rótulo negativo no export.
  await expect(page.getByRole('button', { name: 'Rejeitar' })).toBeDisabled();
  await page.getByLabel('Motivo da rejeição').selectOption('duplicidade');
  await expect(page.getByRole('button', { name: 'Rejeitar' })).toBeEnabled();
  await expect(page.getByText(/Na dúvida, não decida/)).toBeVisible();
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
    page.getByRole('heading', { name: 'Viu um problema na rua? Registre com uma foto.' }),
  ).toBeVisible();
  await page
    .getByRole('navigation', { name: 'Navegação principal' })
    .getByRole('link', { name: 'Registrar evidência' })
    .click();
  await expect(page.getByRole('button', { name: 'Enviar relato' })).toBeVisible();
});
test('meus relatos inclui captura sem localização e preserva descrição como texto', async ({
  page,
}) => {
  await signedIn(page);
  await page.route('**/api/v1/captures/markers?*', (route) =>
    route.fulfill({
      json: [
        {
          id: '2b120c24-7ff1-4f58-bda8-c2f82a94fc05',
          latitude: null,
          longitude: null,
          report_status: 'location_required',
          user_description: '<b>Calçada</b>',
          created_at: '2026-09-24T12:00:00Z',
        },
      ],
    }),
  );
  await page.goto('/#/meus-relatos');
  await expect(page.getByRole('heading', { name: 'Meus relatos', exact: true })).toBeVisible();
  await expect(page.getByText('<b>Calçada</b>', { exact: true })).toBeVisible();
  // Uma situação só: o filtro seria redundante e não aparece.
  await expect(page.getByRole('radiogroup', { name: 'Situação do relato' })).toHaveCount(0);
  await expect(page.getByRole('link', { name: 'Informar localização' })).toHaveAttribute(
    'href',
    /processando/,
  );
});
test('meus relatos filtra por situação só com as situações que existem', async ({ page }) => {
  await signedIn(page);
  const report = (id: string, status: string, extra: Record<string, unknown> = {}) => ({
    id,
    latitude: -23.556,
    longitude: -46.637,
    report_status: status,
    created_at: '2026-09-24T12:00:00Z',
    ...extra,
  });
  await page.route('**/api/v1/captures/markers?*', (route) =>
    route.fulfill({
      json: [
        report('11111111-1111-4111-8111-111111111111', 'processing', {
          user_description: 'Relato em análise',
        }),
        report('22222222-2222-4222-8222-222222222222', 'published', {
          urmind_class: 'URMIND_ROAD_D40',
          severity: 'high',
        }),
        report('33333333-3333-4333-8333-333333333333', 'model_not_available', {
          user_description: 'Outro em análise',
        }),
      ],
    }),
  );
  await page.goto('/#/meus-relatos');
  const filter = page.getByRole('radiogroup', { name: 'Situação do relato' });
  // Sem relato sem localização, "Precisa de localização" não aparece.
  await expect(filter.getByRole('radio')).toHaveCount(3);
  await expect(filter.getByRole('radio', { name: 'Precisa de localização' })).toHaveCount(0);
  const list = page.getByRole('list', { name: 'Lista de relatos' });
  await expect(list.getByRole('listitem')).toHaveCount(3);
  // Situação pública, nunca o estado técnico do processamento.
  await expect(list).not.toContainText('model_not_available');
  await filter.getByText('Confirmado').click();
  await expect(list.getByRole('listitem')).toHaveCount(1);
  await expect(list).toContainText('Buraco · Grave');
  await filter.getByText('Em análise').click();
  await expect(list.getByRole('listitem')).toHaveCount(2);
  await filter.getByText('Todos').click();
  await expect(list.getByRole('listitem')).toHaveCount(3);
});

test('porteiro local recusa foto escura sem perder a descrição', async ({ page }) => {
  await page.goto('/#/registrar');
  await page.getByLabel('Descreva o que você observou (opcional)').fill('Minha observação');
  const png = await page.evaluate(() => {
    const canvas = document.createElement('canvas');
    canvas.width = 640;
    canvas.height = 640;
    canvas.getContext('2d')!.fillRect(0, 0, 640, 640);
    return canvas.toDataURL().split(',')[1];
  });
  await page
    .getByLabel('Escolher foto')
    .setInputFiles({ name: 'dark.png', mimeType: 'image/png', buffer: Buffer.from(png, 'base64') });
  await expect(page.getByRole('alert')).toContainText('escura');
  await expect(page.getByLabel('Descreva o que você observou (opcional)')).toHaveValue(
    'Minha observação',
  );
});
