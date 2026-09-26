import { expect, test } from '@playwright/test';
import {
  EVENT_ID,
  eventDetail,
  publicEvents,
  publicStatus,
  urbanAnalysis,
  stubPublicApi,
} from './fixtures';

test('public primary navigation has six actions and keeps legacy routes', async ({ page }) => {
  await stubPublicApi(page);
  await page.goto('/');
  const links = page.getByRole('navigation', { name: 'Navegação principal' }).getByRole('link');
  await expect(links).toHaveText([
    /Início/,
    /Registrar/,
    /Detecção ao vivo/,
    /Câmera do robô/,
    /Meus relatos/,
    /Mapa/,
  ]);
  await expect(page.getByRole('link', { name: 'Sobre e privacidade' })).toBeVisible();
  await page.goto('/#/transparency');
  await expect(page.getByRole('heading', { name: 'Como o UrMind analisou' })).toBeVisible();
});

test('operational map excludes historical points outside the Brazil viewport', async ({ page }) => {
  const outsideId = 'e7ec6a81-52c6-4d7a-89a7-9bca42f232a1';
  await stubPublicApi(page, {
    events: [
      publicEvents[0],
      { ...publicEvents[0], id: outsideId, latitude: 38.72, longitude: -9.14 },
    ],
  });
  await page.goto('/#/mapa');
  await expect(page.getByText('1 ponto visível', { exact: true })).toBeVisible();
  await expect(page.locator(`[data-event-id="${outsideId}"]`)).toHaveCount(0);
});

test('a página inicial leva a registrar, acompanhar relatos e ver o mapa', async ({
  page,
}, testInfo) => {
  let statusRequests = 0;
  page.on('request', (request) => {
    if (request.url().includes('/public/status')) statusRequests++;
  });
  await stubPublicApi(page);
  await page.goto('/');
  await expect(
    page.getByRole('heading', { name: 'Viu um problema na rua? Registre com uma foto.' }),
  ).toBeVisible();
  // A marca abre a página: o logo oficial (o mesmo arquivo da barra lateral) e a frase.
  const hero = page.getByRole('region', { name: 'Viu um problema na rua? Registre com uma foto.' });
  const heroLogo = hero.getByRole('img', { name: 'UrMind', exact: true });
  await expect(heroLogo).toBeVisible();
  await expect
    .poll(() => heroLogo.evaluate((image) => (image as HTMLImageElement).naturalWidth))
    .toBe(1200);
  await expect(hero).toContainText('Inteligência urbana que transforma ocorrências em ação.');
  // Um logo por região: no celular a barra do topo fica sem o seu no Início.
  const topLogo = page.getByRole('link', { name: 'UrMind', exact: true });
  if ((page.viewportSize()?.width ?? 0) <= 800) await expect(topLogo).toBeHidden();
  else await expect(topLogo).toBeVisible();
  await expect(page.getByRole('link', { name: 'Registrar evidência' }).first()).toBeVisible();
  await expect(page.getByLabel('Seus relatos')).toContainText('aparecem aqui');
  // Diagnóstico técnico (API, banco, detector, trechos, Scout) não aparece para o cidadão.
  const main = page.locator('main');
  for (const technical of ['Scout', 'Banco', 'API operacional', 'trechos', 'Área piloto'])
    await expect(main).not.toContainText(technical);
  expect(statusRequests).toBe(0);
  // Diagnóstico rápido da ocorrência publicada mais recente.
  const diagnosis = page.getByLabel('Diagnóstico da ocorrência selecionada');
  await expect(diagnosis.getByRole('heading', { name: 'Buraco', exact: true })).toBeVisible();
  // Tipo e gravidade separados; confiança do modelo só na análise completa.
  await expect(diagnosis).toContainText('Grave');
  await expect(diagnosis).toContainText('Média');
  await expect(diagnosis).not.toContainText('61.0%');
  // Só as classes que a detecção automática reconhece aparecem como automáticas.
  const automatic = page.getByRole('list', { name: 'Reconhecidos automaticamente' });
  await expect(automatic.getByRole('listitem')).toHaveText([
    'Buraco',
    'Trinca longitudinal',
    'Trinca transversal',
    'Trinca em malha',
  ]);
  await expect(page.locator('main')).not.toContainText('Árvore caída');
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
  await expect(page.getByRole('button', { name: 'Enviar relato' })).toBeVisible();
  await expect(
    page
      .getByRole('navigation', { name: 'Navegação principal' })
      // A página Scout de transmissão; a detecção ao vivo local não transmite nada.
      .getByRole('link', { name: 'Ao vivo', exact: true }),
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
  // Confiança do modelo fica nos detalhes técnicos, recolhidos.
  await expect(page.getByText('Confiança visual 0.0%')).toBeHidden();
  await page.getByText('Detalhes técnicos').click();
  await expect(page.getByText('Confiança visual 0.0%')).toBeVisible();
  await expect(page.getByText('prioridade não calculada')).toBeVisible();
  await expect(page.getByLabel('Evidência visual')).toContainText('Sem detecções publicadas.');
});

test('filtros do mapa público: tipo, gravidade e período reais, persistidos na URL', async ({
  page,
}) => {
  // Relógio fixo: as ocorrências da fixture (17/09/2026) caem nos últimos 7 dias.
  await page.clock.setFixedTime(new Date('2026-09-20T12:00:00Z'));
  await stubPublicApi(page);
  await page.goto('/#/mapa');
  const toolbar = page.getByRole('group', { name: 'Filtros do mapa' });
  // Nada de status técnico, família ou lista inteira da taxonomia no mapa público.
  await expect(toolbar.getByLabel('Classe do ponto')).toHaveCount(0);
  await expect(toolbar.getByLabel('Família do ponto')).toHaveCount(0);
  await expect(toolbar.getByLabel('Status do ponto')).toHaveCount(0);
  await expect(page.getByLabel('Tipo de problema').locator('option')).toHaveText([
    'Todos',
    'Buraco',
    'Trinca longitudinal',
  ]);
  await expect(page.getByLabel('Gravidade', { exact: true }).locator('option')).toHaveText([
    'Todas',
    'Grave',
    'Ainda não avaliada',
  ]);
  await page.getByLabel('Tipo de problema').selectOption('URMIND_ROAD_D40');
  await expect(page).toHaveURL(/map_class=URMIND_ROAD_D40/);
  await expect(page.getByText('1 ponto visível', { exact: true })).toBeVisible();
  // Sem resultado: o mapa continua na tela, com aviso e atalho para limpar.
  await page.getByLabel('Gravidade', { exact: true }).selectOption('unknown');
  await expect(page.getByText('0 pontos visíveis', { exact: true })).toBeVisible();
  await expect(page.getByText('Nenhuma ocorrência encontrada')).toBeVisible();
  await expect(page.locator('.map canvas')).toBeVisible();
  await page.reload();
  await expect(page.getByLabel('Gravidade', { exact: true })).toHaveValue('unknown');
  await page.getByRole('button', { name: 'Limpar filtros' }).click();
  await expect(page.getByText('2 pontos visíveis', { exact: true })).toBeVisible();
  // As duas ocorrências são da mesma semana: um período não mudaria nada, então não aparece.
  await expect(page.getByLabel('Período', { exact: true })).toHaveCount(0);
});

test('período aparece só quando separa pontos e filtra de verdade', async ({ page }) => {
  await page.clock.setFixedTime(new Date('2026-09-20T12:00:00Z'));
  await stubPublicApi(page, {
    events: [publicEvents[0], { ...publicEvents[1], occurred_at: '2026-06-01T12:00:00+00:00' }],
  });
  await page.goto('/#/mapa');
  const period = page.getByLabel('Período', { exact: true });
  // 7, 30 e 90 dias dariam o mesmo resultado: só o primeiro aparece.
  await expect(period.locator('option')).toHaveText(['Qualquer data', 'Últimos 7 dias']);
  await period.selectOption('7');
  await expect(page).toHaveURL(/map_period=7/);
  await expect(page.getByText('1 ponto visível', { exact: true })).toBeVisible();
});

const otherReport = {
  id: 'a1b2c3d4e5f6a7b8c9d0',
  latitude: -23.5584,
  longitude: -46.6352,
  report_status: 'processing',
};

test('mapa mostra visões só quando mudam os pontos e cada uma filtra de verdade', async ({
  page,
}) => {
  await stubPublicApi(page);
  // Só ocorrências confirmadas: "Confirmados" seria igual a "Tudo" e não aparece.
  await page.goto('/#/mapa');
  await expect(page.getByText('2 pontos visíveis', { exact: true })).toBeVisible();
  await expect(page.getByRole('radiogroup', { name: 'Mostrar no mapa' })).toHaveCount(0);
  // Com um relato de outra pessoa no mapa, "Confirmados" passa a separar pontos.
  await page.route('**/api/v1/public/capture-markers', (route) =>
    route.fulfill({ json: [otherReport] }),
  );
  await page.reload();
  const views = page.getByRole('radiogroup', { name: 'Mostrar no mapa' });
  await expect(views.getByRole('radio')).toHaveCount(2);
  await expect(views.getByRole('radio', { name: 'Tudo' })).toBeChecked();
  await expect(page.getByText('3 pontos visíveis', { exact: true })).toBeVisible();
  // Sem relatos próprios, "Meus relatos" devolveria sempre zero: não aparece.
  await expect(views.getByRole('radio', { name: 'Meus relatos' })).toHaveCount(0);
  await views.getByText('Confirmados').click();
  await expect(page).toHaveURL(/mostrar=published/);
  await expect(page.getByText('2 pontos visíveis', { exact: true })).toBeVisible();
  await views.getByText('Tudo').click();
  await expect(page.getByText('3 pontos visíveis', { exact: true })).toBeVisible();
});

test('mapa mostra pontos reais, legenda com forma e nome, e abre a análise', async ({ page }) => {
  await stubPublicApi(page);
  await page.goto('/#/map');
  await expect(page.getByRole('heading', { name: 'Mapa', exact: true })).toBeVisible();
  await expect(page.locator('.map canvas')).toBeVisible();
  // A canvas alone can hide a missing worker. Verify GeoJSON was processed and
  // its event marker actually rendered by MapLibre (fixture data, not real E2E).
  await expect(page.locator('.map').first()).toHaveAttribute(
    'data-rendered-event-ids',
    new RegExp(EVENT_ID),
  );
  // A legenda explica só as gravidades que estão no mapa (aqui: Grave e sem avaliação).
  const legend = page.getByLabel('Legenda de gravidade');
  await expect(legend).toContainText('Grave');
  await expect(legend).toContainText('Ainda não avaliada');
  await expect(legend).not.toContainText('Crítica');
  // Cada nível traz a forma de placa que o mapa desenha, não só uma cor.
  await expect(legend.locator('svg.map-sign')).toHaveCount(2);
  await page
    .getByRole('button', { name: /Buraco/ })
    .first()
    .click();
  await expect(page).toHaveURL(new RegExp(`#/events/${EVENT_ID}$`));
});

test('a lista pública filtra por classe e declara quando nada corresponde', async ({ page }) => {
  await stubPublicApi(page);
  await page.goto('/#/events');
  await expect(page.getByRole('heading', { name: 'Ocorrências confirmadas' })).toBeVisible();
  await expect(page.getByText('2 registros')).toBeVisible();
  await page.route(
    (url) => url.pathname === '/api/v1/public/events',
    (route) => route.fulfill({ json: [] }),
  );
  // Only classes a model can emit are filterable; DATA_REQUIRED classes are not offered.
  // Só os tipos que existem nas ocorrências; nada de taxonomia inteira nem estado técnico.
  const classFilter = page.getByLabel('Tipo de problema');
  await expect(classFilter.locator('option')).toHaveText([
    'Todos os tipos',
    'Buraco',
    'Trinca longitudinal',
  ]);
  await expect(page.getByLabel('Situação')).toHaveCount(0);
  await classFilter.selectOption('URMIND_ROAD_D00');
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
  // O diagnóstico técnico continua disponível aqui, fora da navegação; sem Scout.
  await expect(page.getByRole('status').first()).toContainText('API');
  await expect(page.locator('main')).not.toContainText('Scout');
  await expect(page.getByLabel('Cobertura')).toContainText('832');
});

test('sem ocorrência publicada, a página inicial convida a registrar e não parece quebrada', async ({
  page,
}, testInfo) => {
  // Estado real de hoje: nenhuma captura publicada.
  await stubPublicApi(page, {
    events: [],
    status: { ...publicStatus, events_total: 0, last_event_at: null },
  });
  await page.goto('/');
  await expect(
    page.getByRole('heading', { name: 'Viu um problema na rua? Registre com uma foto.' }),
  ).toBeVisible();

  // Um aviso só, sobre o mapa: sem painel lateral vazio nem lista vazia repetindo o mesmo.
  await expect(page.getByText('Nenhuma ocorrência publicada ainda.')).toHaveCount(1);
  await expect(page.getByLabel('Diagnóstico da ocorrência selecionada')).toHaveCount(0);
  await expect(page.getByLabel('Ocorrências recentes')).toHaveCount(0);
  await expect(page.locator('.map canvas')).toBeVisible();
  await expect(page.getByRole('link', { name: 'Registrar evidência' }).first()).toBeVisible();
  await expect(page.locator('main')).not.toContainText('Scout');
  // Nenhum número aparece sem origem: zero é zero, não um traço decorativo.
  await expect(page.locator('body')).not.toContainText('NaN');
  await page.screenshot({ path: testInfo.outputPath('vazio.png'), fullPage: true });
  // No Mapa operacional, sem pontos não há filtros vazios: só o estado vazio explicado.
  await page.goto('/#/mapa');
  await expect(page.getByText('Nenhuma ocorrência publicada ainda.').first()).toBeVisible();
  await expect(page.getByRole('group', { name: 'Filtros do mapa' })).toHaveCount(0);
});

test('o antigo endereço de exemplos abre o mapa nas ocorrências confirmadas', async ({ page }) => {
  await stubPublicApi(page);
  await page.route('**/api/v1/public/capture-markers', (route) =>
    route.fulfill({ json: [otherReport] }),
  );
  await page.goto('/#/demo');
  await expect(page.getByRole('heading', { name: 'Mapa', exact: true })).toBeVisible();
  await expect(
    page.getByRole('radiogroup', { name: 'Mostrar no mapa' }).getByRole('radio', {
      name: 'Confirmados',
    }),
  ).toBeChecked();
  await expect(page.getByText('EXEMPLO REVISADO')).toHaveCount(0);
});

test('transparência mostra só as classes da detecção automática, sem categorias futuras', async ({
  page,
}) => {
  await stubPublicApi(page);
  for (const route of ['/#/transparency', '/#/sobre']) {
    await page.goto(route);
    const taxonomy = page.getByLabel('Classes de problemas urbanos');
    await expect(taxonomy.locator('li')).toHaveText([
      /Buraco/,
      /Trinca longitudinal/,
      /Trinca transversal/,
      /Trinca em malha/,
    ]);
    await expect(taxonomy).not.toContainText('Árvore caída');
    await expect(taxonomy).not.toContainText('Em desenvolvimento');
    await expect(taxonomy).not.toContainText('Reconhecida por modelo aprovado');
  }
  // "Sobre" é para o cidadão: privacidade e o que é reconhecido, sem métricas do modelo.
  await expect(page.getByLabel('Métricas medidas')).toHaveCount(0);
});

test('a Home leva a cada função pública que ela mostra', async ({ page }) => {
  await stubPublicApi(page);
  const targets: Array<[string, RegExp, string]> = [
    ['Registrar evidência', /#\/registrar$/, 'Registrar evidência'],
    ['Detecção ao vivo', /#\/deteccao-ao-vivo$/, 'Detecção ao vivo'],
    ['Câmera do robô', /#\/camera-robo$/, 'Câmera do robô'],
  ];
  for (const [name, url, heading] of targets) {
    await page.goto('/');
    await page.locator('main').getByRole('link', { name, exact: true }).click();
    await expect(page).toHaveURL(url);
    await expect(page.getByRole('heading', { name: heading, level: 1 })).toBeVisible();
  }
  await page.goto('/');
  await page.getByRole('link', { name: 'Abrir mapa' }).click();
  await expect(page.getByRole('heading', { name: 'Mapa', exact: true })).toBeVisible();
  // Sem promessas de funções futuras na Home.
  const main = page.locator('main');
  for (const future of ['Em breve', 'Árvore caída', 'Alagamento', 'Scout']) {
    await expect(main).not.toContainText(future);
  }
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
