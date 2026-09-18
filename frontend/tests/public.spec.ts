import { expect, test } from '@playwright/test';
import {
  EVENT_ID,
  eventDetail,
  publicEvents,
  publicStatus,
  scoutSnapshots,
  stubPublicApi,
} from './fixtures';

test('a página inicial explica em segundos o que o sistema viu', async ({ page }, testInfo) => {
  await stubPublicApi(page);
  await page.goto('/');
  await expect(
    page.getByRole('heading', { name: 'O que o UrMind está vendo na cidade' }),
  ).toBeVisible();
  // Estado por componente, nunca um único "online".
  const statusBar = page.getByRole('status').first();
  await expect(statusBar).toContainText('Scout');
  await expect(statusBar).toContainText('indisponível');
  await expect(statusBar).toContainText('parcial');
  await expect(statusBar).toContainText('832 trechos');
  // Diagnóstico rápido da ocorrência mais recente.
  const diagnosis = page.getByLabel('Diagnóstico da ocorrência selecionada');
  await expect(diagnosis.getByRole('heading', { name: 'Buraco', exact: true })).toBeVisible();
  await expect(diagnosis).toContainText('MÉDIA');
  await expect(diagnosis).toContainText('61.0%');
  await expect(diagnosis).toContainText('Tapar buraco');
  await expect(diagnosis).toContainText('Prefeitura — zeladoria viária');
  await page.screenshot({ path: testInfo.outputPath('public-home.png'), fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
});

test('sem câmera conectada o painel declara o estado em vez de simular vídeo', async ({ page }) => {
  await stubPublicApi(page);
  await page.goto('/#/live');
  const scout = page.getByLabel('Câmera do Scout');
  await expect(scout.getByText('Sem câmera conectada').first()).toBeVisible();
  await expect(scout.getByText('nenhuma câmera conectada a este ambiente')).toBeVisible();
  await expect(scout.locator('img')).toHaveCount(0);
  await expect(scout.getByText('não medida')).toBeVisible();
});

test('com fonte real a câmera conecta e desenha só as caixas da detecção', async ({ page }) => {
  await stubPublicApi(page, { scout: scoutSnapshots });
  // Frame real de teste gerado no navegador; nenhum vídeo de demonstração no produto.
  await page.route('**/api/v1/public/scout/frame*', async (route) => {
    const pixel =
      'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==';
    await route.fulfill({ body: Buffer.from(pixel, 'base64'), contentType: 'image/png' });
  });
  await page.goto('/#/live');
  const scout = page.getByLabel('Câmera do Scout');
  await expect(scout.getByText('Ao vivo').first()).toBeVisible();
  await expect(scout.getByText('IMAGENS AO VIVO')).toBeVisible();
  await expect(scout.locator('img')).toBeVisible();
  // Uma detecção com bbox na fixture: uma caixa, nem mais nem menos.
  await expect(scout.locator('svg.overlay rect')).toHaveCount(1);
  await expect(scout.getByText('180 ms')).toBeVisible();
});

test('a câmera degradada aparece como degradada, sem inventar continuidade', async ({ page }) => {
  await stubPublicApi(page, { scout: { ...scoutSnapshots, status: 'degraded' } });
  await page.route('**/api/v1/public/scout/frame*', (route) => route.abort());
  await page.goto('/#/live');
  await expect(
    page.getByLabel('Câmera do Scout').getByText('Sinal degradado').first(),
  ).toBeVisible();
});

test('a análise completa mostra explicação, ação, previsão ausente e rastro', async ({ page }) => {
  await stubPublicApi(page);
  await page.goto(`/#/events/${EVENT_ID}`);
  await expect(page.getByRole('heading', { name: 'Buraco', exact: true })).toBeVisible();

  const risk = page.getByLabel('Risco e explicação');
  await expect(risk.getByText('severidade da classe')).toBeVisible();
  await expect(risk.getByText('confiança da detecção')).toBeVisible();
  await expect(risk.getByText('condição ambiental (chuva)')).toBeVisible();
  await expect(risk).toContainText('incerteza');
  await expect(risk).toContainText('média');

  await expect(page.getByLabel('Ação recomendada')).toContainText('Tapar buraco');

  // Previsão só com Prediction real: aqui não há, e o painel diz por quê.
  const prediction = page.getByLabel('Previsão');
  await expect(prediction.getByRole('heading', { name: 'Ainda não disponível' })).toBeVisible();
  await expect(prediction).toContainText('histórico validado');
  await expect(prediction).not.toContainText('dias');

  // Imagem não sai sem sanitização registrada.
  const evidence = page.getByLabel('Evidência visual');
  await expect(evidence).toContainText('sanitização');
  await expect(evidence.locator('img')).toHaveCount(0);

  // Coordenada informada e ponto ajustado permanecem separados.
  const location = page.getByLabel('Localização');
  await expect(location).toContainText('-23.557300, -46.639500');
  await expect(location).toContainText('-23.557400, -46.639600');
  await expect(location).toContainText('3.2 m');

  const trace = page.getByLabel('Como o UrMind analisou');
  await expect(trace.getByText('Captura')).toBeVisible();
  await expect(trace.getByText('sem dado')).toBeVisible();
  await trace.getByText('Detecção').click();
  await expect(trace).toContainText('baseline_early');
});

test('ocorrência sem avaliação não recebe severidade nem prioridade plausível', async ({
  page,
}) => {
  const bare = {
    ...eventDetail,
    id: publicEvents[1].id,
    urmind_class: 'URMIND_ROAD_D00',
    visual_confidence: 0,
    severity: null,
    priority_score: null,
    risk: null,
    action: null,
    responsibility: {
      status: 'requires_triage',
      responsible: null,
      source: null,
      version: null,
      note: 'sem regra de competência para o trecho',
    },
    detections: [],
  };
  await stubPublicApi(page, { detail: bare });
  await page.goto(`/#/events/${publicEvents[1].id}`);
  const diagnosis = page.getByLabel('Risco e explicação');
  await expect(diagnosis).toContainText('Ainda não há avaliação de risco publicada');
  const action = page.getByLabel('Ação recomendada');
  await expect(action).toContainText('Ação ainda não sugerida');
  await expect(action).toContainText('Em triagem');
  await expect(page.getByText('confiança visual 0.0%')).toBeVisible();
  await expect(page.getByText('prioridade não calculada')).toBeVisible();
  await expect(page.getByLabel('Evidência visual')).toContainText('Sem detecções publicadas.');
});

test('mapa mostra pontos reais, legenda com forma e nome, e abre a análise', async ({ page }) => {
  await stubPublicApi(page);
  await page.goto('/#/map');
  await expect(page.getByRole('heading', { name: 'Mapa operacional' })).toBeVisible();
  await expect(page.locator('.map canvas')).toBeVisible();
  const legend = page.getByLabel('Legenda de severidade');
  await expect(legend).toContainText('Crítica');
  await expect(legend).toContainText('Não determinada');
  await expect(legend).toContainText('▲');
  await page
    .getByRole('button', { name: /Buraco/ })
    .first()
    .click();
  await expect(page).toHaveURL(new RegExp(`#/events/${EVENT_ID}$`));
});

test('a lista pública filtra por classe e declara quando nada corresponde', async ({ page }) => {
  await stubPublicApi(page);
  await page.goto('/#/events');
  await expect(page.getByRole('heading', { name: 'Tudo que o UrMind registrou' })).toBeVisible();
  await expect(page.getByText('2 registros')).toBeVisible();
  await page.route(
    (url) => url.pathname === '/api/v1/public/events',
    (route) => route.fulfill({ json: [] }),
  );
  await page.getByLabel('Classe').selectOption('URMIND_SIGNAGE');
  await expect(page.getByText('Nenhuma ocorrência corresponde a este filtro.')).toBeVisible();
});

test('a transparência publica métricas medidas e o que não foi calculado', async ({ page }) => {
  await stubPublicApi(page);
  await page.goto('/#/transparency');
  await expect(page.getByRole('heading', { name: 'Como o UrMind analisou' })).toBeVisible();
  const metrics = page.getByLabel('Métricas medidas');
  await expect(metrics).toContainText('mAP');
  await expect(metrics).toContainText('precision');
  await expect(page.getByLabel('Dados de treino')).toContainText('RDD2022');
  await expect(page.getByLabel('Desempenho')).toContainText('CPUExecutionProvider');
  const sources = page.getByLabel('Fontes e limites');
  await expect(sources).toContainText('OpenStreetMap / Overpass');
  await expect(sources).toContainText('sem LLM');
  await expect(sources).toContainText('limiares ainda não calibrados');
});

test('falha da API aparece como aviso, não como painel vazio silencioso', async ({ page }) => {
  await stubPublicApi(page, { eventsError: 503 });
  await page.goto('/#/events');
  await expect(page.getByRole('alert')).toContainText('serviço indisponível');
});

test('o painel não expõe campo interno algum nas telas públicas', async ({ page }) => {
  await stubPublicApi(page);
  for (const route of ['/', '/#/live', '/#/events', `/#/events/${EVENT_ID}`, '/#/system']) {
    await page.goto(route);
    await expect(page.locator('main')).toBeVisible();
    const text = (await page.locator('body').innerText()).toLowerCase();
    for (const forbidden of [
      'object_path',
      'service_role',
      'reviewer_id',
      'user_id',
      'traceback',
    ]) {
      expect(text, `${forbidden} visível em ${route}`).not.toContain(forbidden);
    }
  }
});

test('navegação por teclado alcança e abre uma ocorrência', async ({ page }) => {
  await stubPublicApi(page);
  await page.goto('/#/events');
  const first = page.getByRole('button', { name: /Buraco/ }).first();
  await first.focus();
  await expect(first).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(new RegExp(`#/events/${EVENT_ID}$`));
  await expect(page.getByLabel('Ação recomendada')).toBeVisible();
});

test('estado do sistema é auditável na rota pública dedicada', async ({ page }) => {
  await stubPublicApi(page, { status: { ...publicStatus, events_total: 2 } });
  await page.goto('/#/system');
  await expect(page.getByRole('heading', { name: 'Estado público' })).toBeVisible();
  await expect(page.getByLabel('Scout')).toContainText('nenhum dispositivo registrado');
  await expect(page.getByLabel('Scout')).not.toContainText('no_device');
  await expect(page.getByLabel('Cobertura')).toContainText('832');
});
