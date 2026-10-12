import { expect, test, type Page } from '@playwright/test';
import {
  EVENT_ID,
  eventDetail,
  publicEvents,
  publicStatus,
  urbanAnalysis,
  stubPublicApi,
} from './fixtures';

test('public primary navigation has current actions and keeps legacy routes', async ({ page }) => {
  await stubPublicApi(page);
  await page.goto('/');
  const nav = page.getByRole('navigation', { name: 'Navegação principal' });
  // A ação principal primeiro; depois as três tarefas. No computador, a ferramenta
  // especializada fica num grupo à parte; no celular, dentro de "Mais".
  const mobile = (page.viewportSize()?.width ?? 0) < 900;
  await expect(nav.getByRole('link')).toHaveText(
    mobile
      ? [/Registrar/, /Início/, /Meus relatos/, /Mapa/]
      : [/Registrar relato/, /Início/, /Meus relatos/, /Mapa/, /Câmera do robô/],
  );
  if (mobile) {
    await nav.getByText('Mais', { exact: true }).click();
    await expect(nav.getByRole('link', { name: 'Câmera do robô' })).toBeVisible();
    await nav.getByText('Mais', { exact: true }).click();
  }
  await expect(page.getByRole('link', { name: 'Sobre e privacidade' })).toBeVisible();
  await page.goto('/#/transparency');
  await expect(page.getByRole('heading', { name: 'Como o UrMind funciona' })).toBeVisible();
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
  await expect(page.getByText('1 ponto no mapa', { exact: true })).toBeVisible();
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
  // O Início explica o fluxo real em três etapas, ao lado da ação principal.
  const hero = page.getByRole('region', { name: 'Viu um problema na rua? Registre com uma foto.' });
  await expect(hero.getByRole('list', { name: 'Como funciona' }).getByRole('listitem')).toHaveCount(
    3,
  );
  // A marca fica na moldura, uma vez por tela: barra lateral no computador, topo no celular.
  await expect(page.getByRole('link', { name: /^UrMind/ }).filter({ visible: true })).toHaveCount(
    1,
  );
  await expect(hero.getByRole('link', { name: 'Registrar relato' })).toBeVisible();
  // Sem relato enviado, o Início não mostra uma seção vazia de relatos.
  await expect(page.getByLabel('Seus relatos')).toHaveCount(0);
  // Diagnóstico técnico (API, banco, detector, trechos, Scout) não aparece para o cidadão.
  const main = page.locator('main');
  for (const technical of ['Scout', 'Banco', 'API operacional', 'trechos', 'Área piloto'])
    await expect(main).not.toContainText(technical);
  expect(statusRequests).toBe(0);
  // Diagnóstico rápido da ocorrência publicada mais recente.
  const diagnosis = page.getByLabel('Diagnóstico da ocorrência selecionada');
  await expect(diagnosis.getByRole('heading', { name: 'Buraco', exact: true })).toBeVisible();
  // Tipo e gravidade; a prioridade e a confiança do modelo ficam no detalhe.
  await expect(diagnosis).toContainText('Grave');
  await expect(diagnosis).not.toContainText('Prioridade');
  await expect(diagnosis).not.toContainText('61.0%');
  await expect(page.getByRole('list', { name: 'Reconhecidos automaticamente' })).toHaveCount(0);
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
  await expect(page.getByText('ANÁLISE EXPERIMENTAL', { exact: true })).toBeVisible();
  await expect(analysis.getByRole('heading', { name: 'Possíveis causas' })).toHaveCount(0);
  // A regra e o método ficam nos detalhes técnicos, recolhidos.
  await page.getByText('Detalhes técnicos').click();
  await expect(page.getByLabel('Origem do texto')).toContainText('versão 1');
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

  // Para quem consulta, a gravidade basta: a prioridade (que junta fatores do modelo)
  // fica nos detalhes técnicos, em frases.
  await expect(page.getByLabel('Prioridade', { exact: true })).toHaveCount(0);

  await expect(page.getByLabel('Solução indicada')).toContainText('Tapar buraco');

  // Imagem não sai sem sanitização registrada.
  const evidence = page.getByLabel('Evidência visual');
  await expect(evidence).toContainText('Sem foto publicada');
  await expect(evidence.locator('img')).toHaveCount(0);

  // Números do modelo, coordenadas e o rastro ficam nos detalhes técnicos, recolhidos.
  await expect(page.getByLabel('Caminho da análise')).toBeHidden();
  await page.getByText('Detalhes técnicos').click();

  // Previsão só com Prediction real: aqui não há, e os detalhes dizem que não há.
  // Pesos e fatores pelo nome simples ficam no cálculo, dentro dos detalhes técnicos.
  const calculation = page.getByLabel('Cálculo da prioridade');
  await expect(calculation).toContainText('Prioridade média.');
  await expect(calculation).toContainText(
    'Aumentou porque esse tipo de problema costuma ser grave.',
  );
  await expect(calculation).toContainText('Diminuiu porque a foto deixa dúvidas sobre o problema.');
  await expect(calculation).not.toContainText('severidade da classe');
  await expect(calculation).toContainText('gravidade típica deste tipo de problema');
  await expect(calculation).toContainText('certeza da análise da foto');
  await expect(calculation).toContainText('chuva recente');
  await expect(calculation).toContainText('Incerteza');
  const prediction = page.getByLabel('Dados da previsão');
  await expect(prediction).toContainText('Ainda não há casos revisados suficientes');
  await expect(prediction).not.toContainText('dias');

  // Coordenada informada e ponto ajustado permanecem separados.
  const location = page.getByLabel('Localização');
  await expect(location).toContainText('-23.557300, -46.639500');
  await expect(location).toContainText('-23.557400, -46.639600');
  await expect(location).toContainText('3,2 m');

  // O caminho chega da API com chaves e códigos (location_source, high, actions_catalog);
  // na tela, cada etapa e cada dado têm nome simples.
  const trace = page.getByLabel('Caminho da análise');
  await trace.getByText('Relato', { exact: true }).click();
  await expect(trace).toContainText('GPS do aparelho');
  await expect(trace).toContainText('foto tirada pelo site');
  await trace.getByText('Análise automática', { exact: true }).click();
  await expect(trace).toContainText('synthetic-detector');
  await expect(trace).toContainText('61%');
  await trace.getByText('Gravidade e prioridade', { exact: true }).click();
  await expect(trace).toContainText('Grave');
  await expect(trace).toContainText('regras de prioridade, versão 1');
  await trace.getByText('Solução indicada', { exact: true }).click();
  await expect(trace).toContainText('lista de ações, versão 1');
  for (const code of ['location_source', 'pwa_photo', 'high', 'actions_catalog', 'risk-v1'])
    await expect(page.locator('main')).not.toContainText(code);
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
    // Sem avaliação nem ação, essas etapas ficam sem registro.
    trace: eventDetail.trace.map((step) =>
      step.step === 'risk' || step.step === 'action'
        ? { ...step, status: 'unavailable', source: null, detail: {}, at: null }
        : step,
    ),
  };
  await stubPublicApi(page, { detail: bare });
  await page.goto(`/#/events/${publicEvents[1].id}`);
  const action = page.getByLabel('Solução indicada');
  await expect(action).toContainText('Ainda sem solução indicada');
  await expect(action).toContainText('a equipe ainda vai definir');
  // Confiança do modelo fica nos detalhes técnicos, recolhidos.
  const photo = page.getByLabel('Análise da foto');
  await expect(photo).toBeHidden();
  await page.getByText('Detalhes técnicos').click();
  const row = (term: string) => photo.locator('div').filter({ has: page.getByText(term) });
  await expect(row('Certeza da análise').locator('dd')).toHaveText('0%');
  await expect(row('Tipos detectados').locator('dd')).toHaveText('nenhum');
  await expect(page.getByLabel('Cálculo da prioridade')).toContainText('ainda não calculada');
  await expect(page.getByLabel('Caminho da análise').getByText('sem registro')).toHaveCount(2);
});

/** No celular, os filtros do mapa ficam recolhidos em "Filtros" (a contagem fica à vista). */
async function openMapFilters(page: Page, project: string) {
  if (project === 'mobile') await page.locator('.map-filters > summary').click();
}

test('filtros do mapa público: tipo, gravidade e período reais, persistidos na URL', async ({
  page,
}, info) => {
  // Relógio fixo: as ocorrências da fixture (17/09/2026) caem nos últimos 7 dias.
  await page.clock.setFixedTime(new Date('2026-09-20T12:00:00Z'));
  await stubPublicApi(page);
  await page.goto('/#/mapa');
  await openMapFilters(page, info.project.name);
  const toolbar = page.getByRole('group', { name: 'Filtros do mapa' });
  // Nada de status técnico, família ou lista inteira da taxonomia no mapa público.
  await expect(toolbar.getByLabel('Classe do ponto')).toHaveCount(0);
  await expect(toolbar.getByLabel('Família do ponto')).toHaveCount(0);
  await expect(toolbar.getByLabel('Status do ponto')).toHaveCount(0);
  await expect(page.getByLabel('Tipo de problema').locator('option')).toHaveText([
    'Todos os tipos',
    'Buraco',
    'Rachadura ao longo da rua',
  ]);
  await expect(page.getByLabel('Gravidade', { exact: true }).locator('option')).toHaveText([
    'Todas',
    'Grave',
    'Gravidade a definir',
  ]);
  await page.getByLabel('Tipo de problema').selectOption('URMIND_ROAD_D40');
  await expect(page).toHaveURL(/map_class=URMIND_ROAD_D40/);
  await expect(page.getByText('1 ponto no mapa', { exact: true })).toBeVisible();
  // Sem resultado: o mapa continua na tela, com aviso e atalho para limpar.
  await page.getByLabel('Gravidade', { exact: true }).selectOption('unknown');
  await expect(page.getByText('0 pontos no mapa', { exact: true })).toBeVisible();
  await expect(page.getByText('Nenhuma ocorrência encontrada')).toBeVisible();
  await expect(page.locator('.map canvas')).toBeVisible();
  await page.reload();
  await expect(page.getByLabel('Gravidade', { exact: true })).toHaveValue('unknown');
  await page.getByRole('button', { name: 'Limpar filtros' }).click();
  await expect(page.getByText('2 pontos no mapa', { exact: true })).toBeVisible();
  // As duas ocorrências são da mesma semana: um período não mudaria nada, então não aparece.
  await expect(page.getByLabel('Período', { exact: true })).toHaveCount(0);
});

test('período aparece só quando separa pontos e filtra de verdade', async ({ page }, info) => {
  await page.clock.setFixedTime(new Date('2026-09-20T12:00:00Z'));
  await stubPublicApi(page, {
    events: [publicEvents[0], { ...publicEvents[1], occurred_at: '2026-06-01T12:00:00+00:00' }],
  });
  await page.goto('/#/mapa');
  await openMapFilters(page, info.project.name);
  const period = page.getByLabel('Período', { exact: true });
  // 7, 30 e 90 dias dariam o mesmo resultado: só o primeiro aparece.
  await expect(period.locator('option')).toHaveText(['Qualquer data', 'Últimos 7 dias']);
  await period.selectOption('7');
  await expect(page).toHaveURL(/map_period=7/);
  await expect(page.getByText('1 ponto no mapa', { exact: true })).toBeVisible();
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
  await expect(page.getByText('2 pontos no mapa', { exact: true })).toBeVisible();
  await expect(page.getByRole('radiogroup', { name: 'Mostrar no mapa' })).toHaveCount(0);
  // Com um relato de outra pessoa no mapa, "Confirmados" passa a separar pontos.
  await page.route('**/api/v1/public/capture-markers', (route) =>
    route.fulfill({ json: [otherReport] }),
  );
  await page.reload();
  const views = page.getByRole('radiogroup', { name: 'Mostrar no mapa' });
  await expect(views.getByRole('radio')).toHaveCount(2);
  await expect(views.getByRole('radio', { name: 'Todos' })).toBeChecked();
  await expect(page.getByText('3 pontos no mapa', { exact: true })).toBeVisible();
  // Sem relatos próprios, "Meus relatos" devolveria sempre zero: não aparece.
  await expect(views.getByRole('radio', { name: 'Meus relatos' })).toHaveCount(0);
  await views.getByText('Confirmados').click();
  await expect(page).toHaveURL(/mostrar=published/);
  await expect(page.getByText('2 pontos no mapa', { exact: true })).toBeVisible();
  await views.getByText('Todos', { exact: true }).click();
  await expect(page.getByText('3 pontos no mapa', { exact: true })).toBeVisible();
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
  await expect(legend).toContainText('A definir');
  await expect(legend).not.toContainText('Crítica');
  // Cada nível traz a forma de placa que o mapa desenha, não só uma cor.
  await expect(legend.locator('svg.map-sign')).toHaveCount(2);
  // O mapa não repete a lista de ocorrências embaixo: o ponto abre o detalhe.
  await expect(page.getByLabel('Ocorrências', { exact: true })).toHaveCount(0);
  await page.getByText(/pontos? em lista/).click();
  await page.getByRole('button', { name: /Selecionar ponto.*Buraco/ }).click();
  await page
    .getByRole('complementary', { name: 'Detalhe do ponto' })
    .getByRole('link', { name: 'Ver ocorrência' })
    .click();
  await expect(page).toHaveURL(new RegExp(`#/resultado/${EVENT_ID}$`));
  await expect(page.getByRole('heading', { name: 'Buraco', level: 1 })).toBeVisible();
});

test('a lista pública filtra por classe e declara quando nada corresponde', async ({ page }) => {
  await stubPublicApi(page);
  await page.goto('/#/events');
  await expect(page.getByRole('heading', { name: 'Ocorrências confirmadas' })).toBeVisible();
  await expect(page.getByText('2 ocorrências')).toBeVisible();
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
    'Rachadura ao longo da rua',
  ]);
  await expect(page.getByLabel('Situação')).toHaveCount(0);
  await classFilter.selectOption('URMIND_ROAD_D00');
  await expect(page.getByText('Nenhuma ocorrência corresponde a este filtro.')).toBeVisible();
});

test('a transparência declara revisão humana e fontes', async ({ page }) => {
  await stubPublicApi(page);
  await page.goto('/#/transparency');
  await expect(page.getByRole('heading', { name: 'Como o UrMind funciona' })).toBeVisible();
  await expect(page.getByRole('region', { name: 'Caminho de um relato' })).toContainText(
    'Revisão da equipe',
  );
  await expect(page.getByLabel('Dados de treino')).toHaveCount(0);
  // Fontes identificadas pelo nome; regras em linguagem simples, sem jargão.
  const sources = page.getByRole('region', { name: 'Dados e regras' });
  await expect(sources).toContainText('OpenStreetMap');
  await expect(sources).toContainText('Nenhuma inteligência artificial escreve ou decide');
  await expect(sources).not.toContainText('LLM');
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
  const first = page.getByRole('link', { name: /Buraco/ }).first();
  await first.focus();
  await expect(first).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(new RegExp(`#/events/${EVENT_ID}$`));
  await expect(page.getByLabel('Solução indicada')).toBeVisible();
});

test('estado do sistema é auditável na rota pública dedicada', async ({ page }) => {
  await stubPublicApi(page, { status: { ...publicStatus, events_total: 2 } });
  await page.goto('/#/system');
  await expect(page.getByRole('heading', { name: 'Situação do serviço' })).toBeVisible();
  // Cada parte com o próprio estado, fora da navegação; sem Scout.
  const parts = page.getByRole('region', { name: 'Partes do serviço' });
  await expect(parts).toContainText('Envio e consulta de relatos');
  await expect(parts).toContainText('Funcionando');
  await expect(page.locator('main')).not.toContainText('Scout');
  // Contagem interna de trechos de rua não diz nada a quem consulta: fica fora da tela.
  const area = page.getByRole('region', { name: 'Área atendida' });
  await expect(area).toContainText('Ocorrências confirmadas');
  await expect(area).not.toContainText('Trechos');
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
  await expect(page.getByRole('link', { name: 'Registrar relato' }).first()).toBeVisible();
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

test('transparência informa a ausência de detector publicado', async ({ page }) => {
  await stubPublicApi(page);
  for (const route of ['/#/transparency', '/#/sobre']) {
    await page.goto(route);
    await expect(page.locator('main h1')).toBeVisible();
    // Sem tipo reconhecido automaticamente, nenhuma lista de categorias aparece ao público.
    await expect(page.getByRole('region', { name: 'Tipos de problema' })).toHaveCount(0);
    for (const hidden of ['Árvore caída', 'Em desenvolvimento', 'Reconhecida por modelo aprovado'])
      await expect(page.locator('main')).not.toContainText(hidden);
  }
  // "Sobre" diz, em palavras simples, que não há análise automática.
  await expect(page.locator('main')).toContainText('Hoje não há análise automática das fotos');
  // "Sobre" é para o cidadão: privacidade e o que é reconhecido, sem métricas do modelo.
  await expect(page.getByLabel('Métricas medidas')).toHaveCount(0);
  // Na Transparência, a etapa automática aparece como plano, nunca como ativa.
  await page.goto('/#/transparency');
  await expect(
    page
      .getByRole('tablist', { name: 'Etapas de um relato' })
      .getByRole('tab', { name: /Análise automática/ }),
  ).toContainText('Planejado');
});

test('a Home leva a cada função pública que ela mostra', async ({ page }) => {
  await stubPublicApi(page);
  // A câmera do robô é ferramenta da operação: fica no menu, não no Início.
  const targets: Array<[string, RegExp, string]> = [
    ['Registrar relato', /#\/registrar$/, 'Registrar relato'],
  ];
  for (const [name, url, heading] of targets) {
    await page.goto('/');
    await page.locator('main').getByRole('link', { name, exact: true }).click();
    await expect(page).toHaveURL(url);
    await expect(page.getByRole('heading', { name: heading, level: 1 })).toBeVisible();
  }
  await page.goto('/');
  await page.getByRole('link', { name: 'Abrir mapa completo' }).click();
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
  await page.getByText(/pontos? em lista/).click();
  await page.getByRole('button', { name: /Selecionar ponto.*Buraco/ }).click();
  const detail = page.getByRole('complementary', { name: 'Detalhe do ponto' });
  await expect(detail).toBeVisible();
  await expect(detail.getByRole('link', { name: 'Como chegar' })).toHaveAttribute('href', /maps/);
  await expect(detail.getByRole('link', { name: 'Ver ocorrência' })).toBeVisible();
  await expect(
    detail.getByRole('img', { name: 'Foto publicada da ocorrência, revisada para privacidade' }),
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
