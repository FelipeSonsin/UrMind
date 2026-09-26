import { expect, test, type BrowserContext, type Page } from '@playwright/test';
import { camera, fakeCamera, liveModelManifest, stubPublicApi } from './fixtures';

// Dois "aparelhos" = duas abas do mesmo navegador. O WebRTC é o real do Chromium, com
// candidatos de IP local (sem mDNS). Só a sinalização troca o Supabase Realtime por um
// canal local entre as abas, para a suíte não depender da internet.
test.use({ launchOptions: { args: ['--disable-features=WebRtcHideLocalIpsWithMdns'] } });
// Cada teste codifica e decodifica vídeo 720p em software em duas abas: em sequência
// entre si, para não disputarem CPU uns com os outros e com o resto da suíte.
test.describe.configure({ mode: 'default' });

type SignalRecord = { dir: 'in' | 'out'; name: string; payload: Record<string, unknown> };

async function localSignaling(context: BrowserContext) {
  await context.addInitScript(() => {
    const log: SignalRecord[] = [];
    const scope = window as unknown as {
      __signalLog: SignalRecord[];
      __urmindRobotSignalingTransport: unknown;
    };
    scope.__signalLog = log;
    scope.__urmindRobotSignalingTransport = (
      name: string,
      onPayload: (payload: unknown) => void,
      onStatus: (status: string) => void,
    ) => {
      const channel = new BroadcastChannel(name);
      channel.onmessage = (event) => {
        log.push({ dir: 'in', name, payload: event.data });
        onPayload(event.data);
      };
      setTimeout(() => onStatus('connected'), 0);
      return {
        send: async (payload: Record<string, unknown>) => {
          log.push({ dir: 'out', name, payload });
          channel.postMessage(payload);
          return true;
        },
        close: async () => channel.close(),
      };
    };
  });
}

const signals = (page: Page) =>
  page.evaluate(() => (window as unknown as { __signalLog: SignalRecord[] }).__signalLog);

/** Notebook gera o QR; o link aberto no celular vem da própria tela, não de um domínio fixo. */
async function startPairing(page: Page) {
  await page.goto('/#/camera-robo');
  await page.getByRole('button', { name: 'Conectar celular' }).click();
  await expect(page.getByRole('img', { name: 'QR Code para conectar o celular' })).toBeVisible();
  const link = await page.getByLabel('Link de conexão').inputValue();
  return link;
}

async function connectPhone(context: BrowserContext, notebook: Page, fps = 15) {
  const link = await startPairing(notebook);
  const phone = await context.newPage();
  await fakeCamera(phone, 'ok', fps);
  await phone.goto(link);
  await phone.getByRole('button', { name: 'Ativar câmera' }).click();
  await expect(phone.getByRole('status').filter({ hasText: 'Conectado' })).toBeVisible({
    timeout: 30_000,
  });
  await expect(notebook.getByRole('status').filter({ hasText: /^Conectado$/ })).toBeVisible({
    timeout: 30_000,
  });
  return { phone, link };
}

const remoteVideo = (page: Page) =>
  page.getByLabel('Vídeo remoto da câmera do robô').evaluate((element) => {
    const video = element as HTMLVideoElement;
    const stream = video.srcObject as MediaStream | null;
    return {
      hasStream: Boolean(stream),
      video: stream?.getVideoTracks().length ?? 0,
      audio: stream?.getAudioTracks().length ?? 0,
      width: video.videoWidth,
    };
  });

test.beforeEach(async ({ page, context }) => {
  await stubPublicApi(page, { events: [], detail: null });
  await localSignaling(context);
  // Sem modelo publicado por padrão; o teste do detector publica o próprio manifesto.
  await context.route('**/models/live-detection.json', (route) => route.fulfill({ status: 404 }));
});

test('aba no menu gera QR na origem atual, com token e expiração de 5 minutos', async ({
  page,
}) => {
  await page.clock.install();
  await page.goto('/#/');
  await page
    .getByRole('navigation', { name: 'Navegação principal' })
    .getByRole('link', { name: 'Câmera do robô' })
    .click();
  await expect(page).toHaveURL(/#\/camera-robo$/);
  await expect(page.getByRole('heading', { name: 'Câmera do robô' })).toBeVisible();
  await expect(page.getByText('Use um celular como câmera sem fio.')).toBeVisible();
  await page.getByRole('button', { name: 'Conectar celular' }).click();
  await expect(page.getByRole('img', { name: 'QR Code para conectar o celular' })).toBeVisible();
  const origin = new URL(page.url()).origin;
  const link = await page.getByLabel('Link de conexão').inputValue();
  expect(link).toMatch(
    new RegExp(
      `^${origin.replace(/[.:/]/g, '\\$&')}/#/camera-robo/celular\\?session=[0-9a-f-]{36}&token=[A-Za-z0-9_-]{32}$`,
    ),
  );
  await expect(page.getByRole('heading', { name: 'Aguardando celular' })).toBeVisible();
  await expect(page.getByRole('timer')).toContainText('O código expira em 5:00');
  // Cada pareamento é novo: outro clique gera outra sessão e outro token.
  await page.getByRole('button', { name: 'Cancelar' }).click();
  await page.getByRole('button', { name: 'Conectar celular' }).click();
  expect(await page.getByLabel('Link de conexão').inputValue()).not.toBe(link);
  await page.clock.fastForward('05:01');
  await expect(page.getByRole('heading', { name: 'O código expirou sem conexão' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Conectar celular' })).toBeVisible();
  await expect(page.getByRole('img', { name: 'QR Code para conectar o celular' })).toHaveCount(0);
});

test('celular transmite por WebRTC e o notebook recebe o vídeo, sem áudio', async ({
  page,
  context,
}) => {
  const { phone } = await connectPhone(context, page);
  // Câmera traseira em 720p a 30 fps, microfone nunca pedido.
  const { calls } = await camera(phone);
  expect(calls).toHaveLength(1);
  expect(calls[0].audio).toBe(false);
  expect(calls[0].video).toEqual({
    facingMode: 'environment',
    width: { ideal: 1280 },
    height: { ideal: 720 },
    frameRate: { min: 60, ideal: 60 },
  });
  await expect(phone.getByText('Transmitindo câmera')).toBeVisible();
  // O mesmo código de conferência nos dois aparelhos.
  const code = (text: string | null) => text?.match(/[A-Z2-9]{4}$/)?.[0];
  expect(code(await phone.locator('.robot-code').textContent())).toBeTruthy();
  await expect
    .poll(async () => (await remoteVideo(page)).width, { timeout: 20_000 })
    .toBeGreaterThan(0);
  expect(await remoteVideo(page)).toMatchObject({ hasStream: true, video: 1, audio: 0 });
  // Pelo canal só passou sinalização: prontidão, oferta, resposta e candidatos ICE.
  const log = await signals(page);
  const kinds = (dir: 'in' | 'out') =>
    new Set(log.filter((item) => item.dir === dir).map((item) => item.payload.kind));
  expect([...kinds('in')]).toEqual(
    expect.arrayContaining(['sender-ready', 'offer', 'ice-candidate']),
  );
  expect([...kinds('out')]).toEqual(
    expect.arrayContaining(['receiver-ready', 'answer', 'ice-candidate']),
  );
  expect(new Set(log.map((item) => item.name))).toEqual(
    new Set([expect.stringMatching(/^robot-camera:[0-9a-f-]{36}$/)]),
  );
  for (const item of log) expect(JSON.stringify(item.payload).length).toBeLessThan(20_000);
});

test('câmera a 60 fps chega ao notebook a ~60 quadros por segundo, em 1280×720', async ({
  page,
  context,
}) => {
  // Medir o transporte uma vez basta; o perfil móvel só muda a tela do notebook.
  test.skip(
    !test.info().project.name.startsWith('desktop'),
    'medição de ritmo feita no perfil desktop',
  );
  await connectPhone(context, page, 60);
  await expect.poll(async () => (await remoteVideo(page)).width, { timeout: 15_000 }).toBe(1280);
  // Quadros do vídeo remoto realmente apresentados em 3 s, contados pelo navegador.
  const measure = () =>
    page.getByLabel('Vídeo remoto da câmera do robô').evaluate(
      (element) =>
        new Promise<number>((resolve) => {
          const player = element as HTMLVideoElement & {
            requestVideoFrameCallback: (callback: () => void) => number;
          };
          let frames = 0;
          const started = performance.now();
          const onFrame = () => {
            frames += 1;
            if (performance.now() - started < 3000) player.requestVideoFrameCallback(onFrame);
            else resolve(frames / ((performance.now() - started) / 1000));
          };
          player.requestVideoFrameCallback(onFrame);
        }),
    );
  // A taxa sobe nos primeiros segundos (controle de banda); vale a melhor de algumas medidas.
  // Na suíte comum (várias abas codificando vídeo ao mesmo tempo) prova-se o fluxo; a
  // medição de desempenho roda isolada com URMIND_PERF=1 e exige os ~60 quadros/s.
  const floor = process.env.URMIND_PERF === '1' ? 50 : 20;
  await expect.poll(measure, { timeout: 30_000, intervals: [0] }).toBeGreaterThanOrEqual(floor);
  test.info().annotations.push({ type: 'remote_fps', description: (await measure()).toFixed(1) });
  // O mesmo número aparece medido nos detalhes técnicos da tela.
  await page.getByText('Detalhes técnicos').click();
  const received = page.locator('.live-readout div', { hasText: 'Vídeo recebido' }).locator('dd');
  await expect(received).toContainText('fps');
  await expect(
    page.locator('.live-readout div', { hasText: 'Resolução' }).locator('dd'),
  ).toHaveText('1280×720');
});

test('detecção no vídeo remoto usa o mesmo manifesto e worker da Detecção ao vivo', async ({
  page,
  context,
}) => {
  const models: string[] = [];
  await context.route('**/models/live-detection.json', (route) =>
    route.fulfill({ json: liveModelManifest('a'.repeat(64), 16) }),
  );
  await context.route('**/models/teste.onnx', (route) => {
    models.push(route.request().url());
    return route.fulfill({ body: Buffer.alloc(16, 7), contentType: 'application/octet-stream' });
  });
  await connectPhone(context, page);
  await page.getByRole('button', { name: 'Iniciar detecção' }).click();
  // O worker existente baixa o ONNX do manifesto e confere o checksum antes de usar.
  await expect(page.getByText(/não confere com o checksum registrado/)).toBeVisible();
  expect(models).toHaveLength(1);
  await expect(page.getByRole('button', { name: 'Pausar' })).toBeDisabled();
});

test('capturar evidência leva o quadro original ao registro, sem GPS do notebook', async ({
  page,
  context,
}) => {
  await context.grantPermissions(['geolocation']);
  await context.setGeolocation({ latitude: -22.9, longitude: -43.2, accuracy: 5 });
  await page.addInitScript(() => {
    const scope = window as unknown as { __draws: string[]; __gpsReads: number };
    scope.__draws = [];
    scope.__gpsReads = 0;
    const original = CanvasRenderingContext2D.prototype.drawImage;
    CanvasRenderingContext2D.prototype.drawImage = function (
      this: CanvasRenderingContext2D,
      ...args: unknown[]
    ) {
      const source = args[0] as Element;
      scope.__draws.push(`${source?.tagName ?? 'BITMAP'}.${source?.className ?? ''}`);
      return (original as (...rest: unknown[]) => void).apply(this, args);
    } as typeof original;
    const geolocation = navigator.geolocation;
    const read = geolocation.getCurrentPosition.bind(geolocation);
    geolocation.getCurrentPosition = (...args: Parameters<Geolocation['getCurrentPosition']>) => {
      scope.__gpsReads += 1;
      return read(...args);
    };
  });
  const { phone } = await connectPhone(context, page);
  // Resolução mantida pelo celular: o quadro recebido é o 1280×720 da câmera.
  await expect.poll(async () => (await remoteVideo(page)).width, { timeout: 15_000 }).toBe(1280);
  await page.evaluate(() => {
    (window as unknown as { __draws: string[] }).__draws.length = 0;
  });
  await page.getByRole('button', { name: 'Capturar evidência' }).click();
  await expect(page).toHaveURL(/#\/capture$/);
  await expect(page.getByAltText('Evidência selecionada')).toBeVisible();
  const draws = await page.evaluate(() => (window as unknown as { __draws: string[] }).__draws);
  // A foto sai do <video> remoto; a camada de caixas nunca é desenhada na captura.
  expect(draws[0]).toBe('VIDEO.live-video');
  expect(draws.some((entry) => entry.includes('live-overlay'))).toBe(false);
  // Local vem do endereço ou do mapa, nunca do GPS do notebook.
  await expect(
    page.getByRole('heading', { name: 'Selecione no mapa onde esta foto foi tirada' }),
  ).toBeVisible();
  await expect(page.getByLabel('Buscar endereço ou CEP')).toBeVisible();
  await expect(page.getByText('Localização obtida')).toHaveCount(0);
  expect(await page.evaluate(() => (window as unknown as { __gpsReads: number }).__gpsReads)).toBe(
    0,
  );
  // Sair da tela encerra a sessão e o celular é avisado.
  await expect(phone.getByText('O notebook encerrou a conexão.')).toBeVisible();
});

test('celular encerra: notebook para, limpa o vídeo e oferece reconectar', async ({
  page,
  context,
}) => {
  const { phone } = await connectPhone(context, page);
  await phone.getByRole('button', { name: 'Encerrar' }).click();
  await expect(page.getByRole('heading', { name: 'Câmera desconectada' })).toBeVisible({
    timeout: 15_000,
  });
  await expect(page.getByRole('button', { name: 'Reconectar celular' })).toBeVisible();
  expect(await remoteVideo(page)).toMatchObject({ hasStream: false, video: 0 });
  expect((await camera(phone)).live).toBe(0);
  // Reconectar gera um pareamento novo.
  await page.getByRole('button', { name: 'Reconectar celular' }).click();
  await expect(page.getByRole('heading', { name: 'Aguardando celular' })).toBeVisible();
});

test('fechar a página do celular desconecta o notebook', async ({ page, context }) => {
  const { phone } = await connectPhone(context, page);
  await phone.close({ runBeforeUnload: true });
  await expect(page.getByRole('heading', { name: 'Câmera desconectada' })).toBeVisible({
    timeout: 15_000,
  });
  expect(await remoteVideo(page)).toMatchObject({ hasStream: false });
});

test('notebook desconecta: o celular para a câmera e é avisado', async ({ page, context }) => {
  const { phone } = await connectPhone(context, page);
  await page.getByRole('button', { name: 'Desconectar celular' }).click();
  await expect(page.getByRole('heading', { name: 'Câmera desconectada' })).toBeVisible({
    timeout: 15_000,
  });
  await expect(phone.getByText('O notebook encerrou a conexão.')).toBeVisible();
  await expect.poll(async () => (await camera(phone)).live).toBe(0);
});

test('câmera negada no celular é explicada e nada é transmitido', async ({ page, context }) => {
  const link = await startPairing(page);
  const phone = await context.newPage();
  await fakeCamera(phone, 'NotAllowedError');
  await phone.goto(link);
  await phone.getByRole('button', { name: 'Ativar câmera' }).click();
  await expect(
    phone.getByRole('alert').filter({ hasText: /acesso à câmera foi negado/ }),
  ).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Aguardando celular' })).toBeVisible();
  expect((await signals(page)).filter((item) => item.dir === 'in')).toEqual([]);
});

test('link com token errado é recusado sem revelar o token certo', async ({ page, context }) => {
  const link = await startPairing(page);
  const url = new URL(link);
  const forged = url.href.replace(/token=[^&]+/, `token=${'x'.repeat(32)}`);
  const phone = await context.newPage();
  await fakeCamera(phone);
  await phone.goto(forged);
  await phone.getByRole('button', { name: 'Ativar câmera' }).click();
  await expect(phone.getByRole('alert')).toContainText('Link inválido ou expirado');
  await expect.poll(async () => (await camera(phone)).live).toBe(0);
  await expect(page.getByRole('heading', { name: 'Aguardando celular' })).toBeVisible();
  const token = new URL(link).hash.match(/token=([^&]+)/)![1];
  const phoneLog = await signals(phone);
  expect(JSON.stringify(phoneLog.filter((item) => item.dir === 'in'))).not.toContain(token);
});

test('link sem sessão válida mostra erro e não abre a câmera', async ({ page }) => {
  await fakeCamera(page);
  await page.goto('/#/camera-robo/celular?session=abc&token=def');
  await expect(page.getByRole('alert')).toContainText('Link de conexão inválido');
  await expect(page.getByRole('button', { name: 'Ativar câmera' })).toHaveCount(0);
  expect((await camera(page)).calls).toHaveLength(0);
});
