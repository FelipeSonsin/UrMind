import { expect, test } from '@playwright/test';
import {
  EVENT_ID,
  fakeCamera,
  signedIn,
  stubPublicApi,
  syntheticJpegWithGps,
  syntheticReportPhoto,
} from './fixtures';
import {
  CITIZEN_ROUTES,
  CITIZEN_SCREENS,
  citizenData,
  TEAM_ROUTES,
  openScreen,
  TEAM_SCREENS,
  teamData,
} from './screens';
import { expectPlain } from './plain-text';

/*
 * Linguagem simples em toda a interface: nenhum termo de bastidor, código interno,
 * texto em inglês ou número sem contexto no que uma pessoa vê ou ouve. A varredura
 * abre tudo o que está recolhido ("Detalhes técnicos", "Ver medidas", listas) e lê
 * também dicas (title), nomes acessíveis (aria-label), textos de exemplo
 * (placeholder), descrições de imagem (alt) e o título da aba. Ficam de fora só os
 * identificadores que a pessoa copia como estão (protocolo, código do registro) e os
 * créditos legais do mapa, escritos pelo próprio provedor.
 */

test('o site do cidadão fala em linguagem simples em todas as rotas, com tudo aberto', async ({
  page,
}) => {
  test.setTimeout(240_000);
  expect(CITIZEN_ROUTES.length, 'rotas lidas de routes.ts').toBeGreaterThanOrEqual(10);
  await citizenData(page);
  await signedIn(page);
  for (const path of CITIZEN_SCREENS) {
    await openScreen(page, path);
    await expectPlain(page, path);
  }
});

test('estados do cidadão: busca, menu, mapa, foto escolhida e câmera', async ({ page }, info) => {
  test.setTimeout(180_000);
  await citizenData(page);
  await signedIn(page);
  await fakeCamera(page);
  // Busca aberta, com resultado.
  await openScreen(page, '/');
  await expect(page.locator('main h1')).toBeVisible();
  await page
    .getByRole('button', { name: 'Buscar página ou relato' })
    .filter({ visible: true })
    .click();
  await page.keyboard.type('buraco');
  await expectPlain(page, 'busca aberta');
  await page.keyboard.press('Escape');
  // Menu "Mais" do celular.
  if (info.project.name === 'mobile') {
    await page.getByRole('navigation', { name: 'Navegação principal' }).getByText('Mais').click();
    await expectPlain(page, 'menu Mais');
    await page.keyboard.press('Escape');
  }
  // Detalhe de um ponto do mapa.
  await openScreen(page, '/#/mapa');
  await page.getByText(/pontos? em lista/).click();
  await page.getByRole('button', { name: /Selecionar ponto.*Buraco/ }).click();
  await expect(page.getByRole('complementary', { name: 'Detalhe do ponto' })).toBeVisible();
  await expectPlain(page, 'detalhe do ponto no mapa');
  // Foto com localização na própria foto: checagem e local em palavras.
  await openScreen(page, '/#/registrar');
  await page.getByLabel('Escolher foto').setInputFiles({
    name: 'rua.jpg',
    mimeType: 'image/jpeg',
    buffer: await syntheticJpegWithGps(page, -23.556, -46.637),
  });
  await expect(page.getByText('Localização encontrada na foto')).toBeVisible();
  await expectPlain(page, 'registrar com foto localizada');
  // Foto sem localização: o mapa para marcar o ponto.
  const png = await syntheticReportPhoto(page);
  await page.getByLabel('Escolher foto').setInputFiles({
    name: 'rua.png',
    mimeType: 'image/png',
    buffer: Buffer.from(png, 'base64'),
  });
  await expect(page.getByText('Falta o local da foto')).toBeVisible();
  await expectPlain(page, 'registrar com foto sem local');
  // Câmera do computador aberta.
  if (info.project.name === 'desktop') {
    await page.getByRole('button', { name: 'Tirar foto' }).click();
    await expect(page.getByLabel('Prévia da câmera')).toBeVisible();
    await expectPlain(page, 'câmera aberta');
  }
});

test('sem sessão, o envio vira rascunho e tudo segue em linguagem simples', async ({ page }) => {
  await stubPublicApi(page);
  for (const path of ['/', '/#/meus-relatos', '/#/mapa']) {
    await openScreen(page, path);
    await expectPlain(page, `visitante ${path}`);
  }
  await openScreen(page, '/#/registrar');
  const png = await syntheticReportPhoto(page);
  await page.getByLabel('Escolher foto').setInputFiles({
    name: 'evidencia.png',
    mimeType: 'image/png',
    buffer: Buffer.from(png, 'base64'),
  });
  await page.getByRole('checkbox', { name: /Li e aceito/ }).check();
  await page.getByRole('button', { name: 'Enviar relato' }).click();
  await expect(page.getByText('Falta o local', { exact: true })).toBeVisible();
  await expectPlain(page, 'rascunho guardado com aviso de envio');
});

test('sem ocorrências e com o servidor fora do ar, os avisos também são simples', async ({
  page,
}) => {
  test.setTimeout(120_000);
  await stubPublicApi(page, { events: [] });
  for (const path of ['/', '/#/events', '/#/mapa']) {
    await openScreen(page, path);
    await expectPlain(page, `sem ocorrências ${path}`);
  }
  // Rota registrada depois tem prioridade: toda a API passa a responder 503.
  await page.route('**/api/v1/**', (route) =>
    route.fulfill({ status: 503, json: { detail: 'serviço indisponível' } }),
  );
  for (const path of [
    '/',
    '/#/events',
    `/#/events/${EVENT_ID}`,
    '/#/mapa',
    '/#/transparency',
    '/#/system',
    '/#/sobre',
    '/#/registrar',
  ]) {
    await openScreen(page, path);
    await expectPlain(page, `servidor fora do ar ${path}`);
  }
});

test('a área da equipe fala em linguagem simples em todas as rotas, com tudo aberto', async ({
  page,
}) => {
  test.setTimeout(240_000);
  expect(TEAM_ROUTES.length, 'rotas lidas de routes.ts').toBeGreaterThanOrEqual(6);
  await teamData(page);
  for (const path of TEAM_SCREENS) {
    await openScreen(page, path);
    await expectPlain(page, path);
  }
  // Configuração salva: a confirmação também é simples.
  await openScreen(page, '/admin/#/app/admin');
  await page.getByLabel('Tamanho mínimo da foto').fill('800');
  await expect(page.getByText('Há alterações não salvas.')).toBeVisible();
  await expectPlain(page, 'alteração não salva');
  await page.getByRole('button', { name: 'Salvar configuração' }).click();
  await expect(page.getByText('Configuração salva e registrada na auditoria.')).toBeVisible();
  await expectPlain(page, 'configuração salva');
});

test('conta sem permissão vê um aviso simples', async ({ page }) => {
  await teamData(page, { can_review: false, can_admin: false });
  await openScreen(page, '/admin/#/app/dashboard');
  await expect(page.getByRole('heading', { name: 'Acesso restrito' })).toBeVisible();
  await expectPlain(page, 'acesso restrito');
});

test('a entrada da equipe, inclusive com senha errada, fala em linguagem simples', async ({
  page,
}) => {
  await stubPublicApi(page);
  await page.route('**/auth/v1/token*', (route) =>
    route.fulfill({
      status: 400,
      json: { error: 'invalid_grant', error_description: 'Invalid login credentials' },
    }),
  );
  await openScreen(page, '/admin/');
  await expect(page.getByRole('heading', { name: 'Entrar na área da equipe' })).toBeVisible();
  await expectPlain(page, 'entrada da equipe');
  await page.getByLabel('E-mail').fill('equipe@exemplo.org');
  await page.getByLabel('Senha').fill('senha-errada');
  await page.getByRole('button', { name: 'Entrar', exact: true }).click();
  await expect(page.getByRole('alert')).toBeVisible();
  await expectPlain(page, 'senha errada');
});

test('GPS negado ou sem sinal: os avisos também são simples', async ({ page }) => {
  await citizenData(page);
  await signedIn(page);
  for (const [code, notice] of [
    [1, /Localização bloqueada para este site/],
    [2, /não conseguiu a posição agora/],
  ] as const) {
    await openScreen(page, '/#/registrar');
    // GPS simulado: 1 = permissão negada, 2 = sem sinal.
    await page.evaluate((errorCode) => {
      const fail = (_ok: unknown, error: (reason: { code: number }) => void) =>
        error({ code: errorCode });
      Object.defineProperty(navigator, 'geolocation', {
        value: { getCurrentPosition: fail, watchPosition: () => 1, clearWatch: () => undefined },
      });
    }, code);
    const photo = await syntheticReportPhoto(page);
    await page.getByLabel('Tirar foto').setInputFiles({
      name: 'agora.png',
      mimeType: 'image/png',
      buffer: Buffer.from(photo, 'base64'),
    });
    await expect(page.getByText(notice).first()).toBeVisible();
    await expectPlain(page, code === 1 ? 'GPS negado' : 'GPS sem sinal');
  }
});

for (const mode of ['NotAllowedError', 'NotFoundError', 'NotReadableError'] as const) {
  test(`câmera do computador com falha (${mode}) explica em linguagem simples`, async ({
    page,
  }, info) => {
    test.skip(
      Boolean(info.project.use.hasTouch),
      'no celular a foto usa o app de câmera do aparelho',
    );
    await stubPublicApi(page);
    await fakeCamera(page, mode);
    await openScreen(page, '/#/registrar');
    await page.getByRole('button', { name: 'Tirar foto' }).click();
    await expect(page.getByRole('alert')).toContainText('Câmera indisponível');
    await expectPlain(page, `câmera com falha ${mode}`);
  });
}
