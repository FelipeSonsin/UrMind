import { expect, test } from '@playwright/test';
import {
  EVENT_ID,
  eventDetail,
  publicEvents,
  publicStatus,
  scoutOffline,
  urbanAnalysis,
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

test('o fluxo móvel não consulta Scout nem oferece transmissão ativa', async ({ page }) => {
  await stubPublicApi(page);
  let scoutRequests = 0;
  page.on('request', (request) => {
    if (request.url().includes('/public/scout')) scoutRequests++;
  });
  await page.goto('/#/registrar');
  await expect(page.getByRole('button', { name: 'Salvar e enviar' })).toBeVisible();
  await expect(
    page
      .getByRole('navigation', { name: 'Navegação principal' })
      .getByRole('link', { name: 'Ao vivo' }),
  ).toHaveCount(0);
  expect(scoutRequests).toBe(0);
});

test('análise urbana mostra texto persistido, consequências condicionais e limitações', async ({
  page,
}) => {
  await stubPublicApi(page, { detail: { ...eventDetail, analysis: urbanAnalysis } });
  await page.goto(`/#/events/${EVENT_ID}`);
  const analysis = page.getByLabel('Análise urbana');
  await expect(analysis).toContainText(urbanAnalysis.description);
  await expect(analysis).toContainText(urbanAnalysis.diagnosis);
  await expect(analysis).toContainText(urbanAnalysis.potential_consequences[0].statement);
  await expect(analysis).toContainText('não são uma previsão');
  await expect(analysis).toContainText(urbanAnalysis.limitations[0]);
  await expect(analysis).toContainText('risk-v1');
  await expect(page.getByText('ANÁLISE EXPERIMENTAL', { exact: true })).toBeVisible();
  await expect(analysis.getByRole('heading', { name: 'Possíveis causas' })).toHaveCount(0);
});

test('detalhe anterior sem análise mantém diagnóstico sem inventar consequências', async ({
  page,
}) => {
  await stubPublicApi(page);
  await page.goto(`/#/events/${EVENT_ID}`);
  await expect(page.getByRole('heading', { name: 'Buraco', exact: true })).toBeVisible();
  await expect(page.getByLabel('Análise urbana')).toHaveCount(0);
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
  // A canvas alone can hide a missing worker. Verify GeoJSON was processed and
  // its event marker actually rendered by MapLibre (fixture data, not real E2E).
  await expect(page.locator('.map').first()).toHaveAttribute(
    'data-rendered-event-ids',
    new RegExp(EVENT_ID),
  );
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
  // Only classes a model can emit are filterable; DATA_REQUIRED classes are not offered.
  const classFilter = page.getByLabel('Classe');
  await expect(classFilter.locator('option[value="URMIND_FALLEN_TREE"]')).toHaveCount(0);
  await expect(classFilter.locator('option[value="URMIND_SIGNAGE"]')).toHaveCount(0);
  await classFilter.selectOption('URMIND_ROAD_D10');
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

test('sem ocorrência e sem câmera, o painel parece proposital e não quebrado', async ({
  page,
}, testInfo) => {
  // Estado real de hoje: nenhuma captura publicada e nenhum Scout registrado.
  await stubPublicApi(page, {
    events: [],
    scout: scoutOffline,
    status: { ...publicStatus, events_total: 0, last_event_at: null },
  });
  await page.goto('/');
  await expect(
    page.getByRole('heading', { name: 'O que o UrMind está vendo na cidade' }),
  ).toBeVisible();

  const diagnosis = page.getByLabel('Diagnóstico da ocorrência selecionada');
  await expect(
    diagnosis.getByRole('heading', { name: 'Nenhuma ocorrência publicada ainda' }),
  ).toBeVisible();
  await expect(diagnosis).toContainText('Quando uma evidência for processada');

  await expect(page.getByLabel('Câmera do Scout')).toContainText('Câmera do Scout indisponível');
  await expect(page.getByLabel('Ocorrências recentes')).toContainText('0 registros');
  await expect(page.getByRole('status').first()).toContainText('nenhuma ainda');
  // Nenhum número aparece sem origem: zero é zero, não um traço decorativo.
  await expect(page.locator('body')).not.toContainText('NaN');
  await page.screenshot({ path: testInfo.outputPath('vazio.png'), fullPage: true });
});

test('demo mostra só exemplos revisados e nunca como resultado da foto enviada', async ({
  page,
}) => {
  await stubPublicApi(page);
  await page.route(
    (url) => url.pathname === '/api/v1/public/events',
    (route) =>
      route.fulfill({
        json:
          new URL(route.request().url()).searchParams.get('status') === 'confirmed'
            ? publicEvents.slice(0, 1)
            : [],
      }),
  );
  await page.goto('/#/demo');
  await expect(page.getByRole('heading', { name: 'Exemplos revisados' })).toBeVisible();
  await expect(page.getByText('Não são o resultado da sua foto')).toBeVisible();
  await expect(page.getByText('EXEMPLO REVISADO')).toHaveCount(1);
});

test('transparência lista classes em desenvolvimento sem afirmar reconhecimento', async ({
  page,
}) => {
  await stubPublicApi(page);
  await page.goto('/#/transparency');
  const taxonomy = page.getByLabel('Classes de problemas urbanos');
  const fallenTree = taxonomy.locator('li', { hasText: 'Árvore caída' });
  await expect(fallenTree).toContainText('Em desenvolvimento');
  await expect(taxonomy.locator('li')).toHaveCount(35);
  await expect(taxonomy.getByText('Em desenvolvimento', { exact: true })).toHaveCount(31);
  await expect(taxonomy.locator('li', { hasText: 'Buraco' })).toContainText('Análise experimental');
  await expect(taxonomy).not.toContainText('Reconhecida por modelo aprovado');
});

test('resultado de modelo shadow leva o selo ANÁLISE EXPERIMENTAL', async ({ page }) => {
  await stubPublicApi(page, { detail: { ...eventDetail, model_stage: 'EXPERIMENTAL_SHADOW' } });
  await page.goto(`/#/events/${EVENT_ID}`);
  await expect(page.getByRole('note').filter({ hasText: 'ANÁLISE EXPERIMENTAL' })).toBeVisible();
});

test('resultado de modelo não-shadow não recebe o selo experimental', async ({ page }) => {
  await stubPublicApi(page);
  await page.goto(`/#/events/${EVENT_ID}`);
  await expect(page.getByRole('heading', { name: 'Buraco' }).first()).toBeVisible();
  await expect(page.getByText('ANÁLISE EXPERIMENTAL')).toHaveCount(0);
});
test('mapa oferece seleção acessível e detalhe da foto publicada sem sair do mapa', async ({
  page,
}, testInfo) => {
  await stubPublicApi(page, {
    detail: {
      ...eventDetail,
      image: {
        available: true,
        privacy_redacted: true,
        reason: null,
        url: `/api/v1/public/events/${EVENT_ID}/image`,
      },
    },
  });
  await page.route('**/api/v1/public/events/*/image', (route) =>
    route.fulfill({
      contentType: 'image/svg+xml',
      body: '<svg xmlns="http://www.w3.org/2000/svg" width="640" height="480"><rect width="640" height="480" fill="#66766f"/></svg>',
    }),
  );
  await page.goto('/#/mapa');
  await page.getByText(/Lista acessível de pontos/).click();
  await page.getByRole('button', { name: /Selecionar ponto.*Buraco/ }).click();
  const detail = page.getByRole('complementary', { name: 'Detalhe do ponto' });
  await expect(detail).toBeVisible();
  await expect(detail.getByRole('link', { name: 'Como chegar' })).toHaveAttribute('href', /maps/);
  await expect(detail.getByRole('link', { name: 'Abrir resultado' })).toBeVisible();
  await expect(
    detail.getByRole('img', { name: 'Foto publicada e sanitizada da ocorrência' }),
  ).toHaveAttribute('src', `/api/v1/public/events/${EVENT_ID}/image`);
  if (page.viewportSize()!.width < 900) {
    const box = await detail.boundingBox();
    expect(box!.y).toBeGreaterThanOrEqual(0);
    expect(box!.y + box!.height).toBeLessThanOrEqual(page.viewportSize()!.height);
  }
  await page.screenshot({ path: testInfo.outputPath('map-detail.png'), fullPage: true });
  await detail.getByRole('button', { name: 'Fechar detalhe' }).click();
  await expect(detail).not.toBeVisible();
});
