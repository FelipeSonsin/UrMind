import { existsSync } from 'node:fs';
import { expect, test, type Page } from '@playwright/test';
import { signedIn, signOut, stubPublicApi } from './fixtures';

// Câmera e modelo simulados SÓ no navegador de teste. Isto prova interface e
// contratos; não é teste de câmera física nem de detecção real.
type FakeCameraMode = 'ok' | 'NotAllowedError' | 'NotReadableError' | 'NotFoundError';

async function fakeCamera(page: Page, mode: FakeCameraMode = 'ok') {
  await page.addInitScript((failure) => {
    const state = {
      calls: [] as MediaStreamConstraints[],
      tracks: [] as MediaStreamTrack[],
      stopped: 0,
    };
    (window as unknown as { __camera: typeof state }).__camera = state;
    const devices = [
      { deviceId: 'cam-a', kind: 'videoinput', label: 'Webcam integrada', groupId: 'a' },
      { deviceId: 'cam-b', kind: 'videoinput', label: 'Câmera USB', groupId: 'b' },
    ];
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: {
        enumerateDevices: async () => devices.map((d) => ({ ...d, toJSON: () => d })),
        getUserMedia: async (constraints: MediaStreamConstraints) => {
          state.calls.push(constraints);
          if (failure !== 'ok') throw new DOMException('simulada', failure);
          const canvas = document.createElement('canvas');
          canvas.width = 1280;
          canvas.height = 720;
          const context = canvas.getContext('2d')!;
          const paint = () => {
            const image = context.createImageData(1280, 720);
            for (let i = 0; i < image.data.length; i += 4) {
              const v = (i * 2654435761) % 200;
              image.data[i] = image.data[i + 1] = image.data[i + 2] = 30 + v;
              image.data[i + 3] = 255;
            }
            context.putImageData(image, 0, 0);
          };
          paint();
          const timer = setInterval(paint, 100);
          const stream = canvas.captureStream(15);
          for (const track of stream.getVideoTracks()) {
            const stop = track.stop.bind(track);
            track.stop = () => {
              state.stopped += 1;
              clearInterval(timer);
              stop();
            };
            state.tracks.push(track);
          }
          return stream;
        },
      },
    });
  }, mode);
}

const camera = (page: Page) =>
  page.evaluate(() => {
    const s = (
      window as unknown as {
        __camera: { calls: MediaStreamConstraints[]; stopped: number; tracks: MediaStreamTrack[] };
      }
    ).__camera;
    return {
      calls: s.calls,
      stopped: s.stopped,
      live: s.tracks.filter((t) => t.readyState === 'live').length,
    };
  });

/** A câmera está no palco: uma track viva e o vídeo recebendo o stream. */
async function cameraLive(page: Page) {
  await expect.poll(async () => (await camera(page)).live).toBe(1);
  await expect(page.getByRole('button', { name: 'Encerrar câmera' })).toBeEnabled();
  await expect(page.getByRole('button', { name: 'Iniciar câmera' })).toBeDisabled();
}

const hasLocalModel = () =>
  existsSync(new URL('../public/models/live-detection.json', import.meta.url));

const manifest = (sha256: string, size: number) => ({
  schema_version: 1,
  model_id: 'yolox-s-model-v2',
  model_version: 'contrato-de-teste',
  scientific_status: 'EXPERIMENTAL',
  use_authorized: true,
  distribution_authorized: true,
  authorization_ref: 'tests/live-detection.spec.ts',
  onnx: { path: '/models/teste.onnx', sha256, size_bytes: size },
  input: {
    name: 'images',
    size: [640, 640],
    layout: 'NCHW',
    dtype: 'float32',
    color: 'BGR',
    range: '0..255',
    normalization: 'none',
    letterbox: { pad_value: 114, anchor: 'top-left' },
  },
  output: { name: 'output', format: 'yolox_decoded_cxcywh_obj_cls' },
  class_names: ['URMIND_ROAD_D00', 'URMIND_ROAD_D10', 'URMIND_ROAD_D20', 'URMIND_ROAD_D40'],
  postprocess: {
    score_threshold: 0.25,
    nms_threshold: 0.65,
    nms: 'class_agnostic',
    max_detections: 50,
  },
  provenance: {
    registration_manifest_sha256: 'd'.repeat(64),
    closure_manifest_sha256: null,
    contract_sha256: 'c'.repeat(64),
  },
});

const uploads: string[] = [];
test.beforeEach(async ({ page }) => {
  uploads.length = 0;
  await stubPublicApi(page, { events: [], detail: null });
  // Nenhuma Capture/Event pode nascer da prévia.
  await page.route('**/api/v1/captures**', (route) => {
    uploads.push(route.request().url());
    return route.fulfill({ status: 500, json: {} });
  });
});

test('abre a rota pela navegação e sem modelo mostra indisponibilidade sem caixas', async ({
  page,
}) => {
  await page.route('**/models/live-detection.json', (route) => route.fulfill({ status: 404 }));
  await fakeCamera(page);
  await page.goto('/#/');
  await page.getByRole('link', { name: 'Detecção ao vivo' }).first().click();
  await expect(page).toHaveURL(/#\/deteccao-ao-vivo$/);
  await expect(page.getByRole('heading', { name: 'Detecção ao vivo' })).toBeVisible();
  await expect(page.getByText(/^Detecção indisponível\./)).toBeVisible();
  // Nada foi pedido à câmera antes de uma ação explícita.
  expect((await camera(page)).calls).toHaveLength(0);
  await page.getByRole('button', { name: 'Iniciar câmera' }).click();
  await cameraLive(page);
  const { calls } = await camera(page);
  expect(calls[0].audio).toBe(false);
  await expect(page.getByRole('button', { name: 'Iniciar detecção' })).toBeDisabled();
  await expect(page.getByText(/Iniciar detecção: Detecção indisponível/)).toBeVisible();
  // Nenhum dado técnico (latência, provedor, perfil) fica exposto fora dos detalhes.
  await expect(page.getByText('Pré · modelo · pós')).toBeHidden();
  await expect(
    page.getByText('As detecções aparecem aqui quando a análise começar.'),
  ).toBeVisible();
});

for (const [mode, message] of [
  ['NotAllowedError', /acesso à câmera foi negado/],
  ['NotReadableError', /em uso por outro aplicativo/],
  ['NotFoundError', /Nenhuma câmera foi encontrada/],
] as const) {
  test(`erro de câmera ${mode} é explicado`, async ({ page }) => {
    await fakeCamera(page, mode);
    await page.goto('/#/deteccao-ao-vivo');
    await page.getByRole('button', { name: 'Iniciar câmera' }).click();
    await expect(page.getByRole('alert').filter({ hasText: message })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Encerrar câmera' })).toBeDisabled();
  });
}

test('troca de câmera encerra a anterior; encerrar e sair da rota liberam as tracks', async ({
  page,
}) => {
  await fakeCamera(page);
  await page.goto('/#/deteccao-ao-vivo');
  await page.getByRole('button', { name: 'Iniciar câmera' }).click();
  await cameraLive(page);
  await page.getByLabel('Câmera do aparelho').selectOption('cam-b');
  await expect.poll(async () => (await camera(page)).calls.length).toBe(2);
  const afterSwitch = await camera(page);
  expect(afterSwitch.calls[1].video).toMatchObject({ deviceId: { exact: 'cam-b' } });
  expect(afterSwitch.stopped).toBeGreaterThanOrEqual(1);
  await expect.poll(async () => (await camera(page)).live).toBe(1);

  await page.getByRole('button', { name: 'Encerrar câmera' }).click();
  await expect.poll(async () => (await camera(page)).live).toBe(0);
  expect(
    await page
      .getByLabel('Imagem ao vivo da câmera')
      .evaluate((v) => (v as HTMLVideoElement).srcObject),
  ).toBeNull();

  await page.getByRole('button', { name: 'Iniciar câmera' }).click();
  await expect.poll(async () => (await camera(page)).live).toBe(1);
  await page.goto('/#/');
  await expect.poll(async () => (await camera(page)).live).toBe(0);
});

test('página oculta suspende a câmera e desconexão é informada, sem retomar sozinha', async ({
  page,
}) => {
  await fakeCamera(page);
  await page.goto('/#/deteccao-ao-vivo');
  await page.getByRole('button', { name: 'Iniciar câmera' }).click();
  await cameraLive(page);
  await page.evaluate(() => {
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => true });
    document.dispatchEvent(new Event('visibilitychange'));
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => false });
    document.dispatchEvent(new Event('visibilitychange'));
  });
  await expect(page.getByRole('alert').filter({ hasText: /página ficou oculta/ })).toBeVisible();
  expect((await camera(page)).live).toBe(0);
  expect((await camera(page)).calls).toHaveLength(1);

  await page.getByRole('button', { name: 'Iniciar câmera' }).click();
  await expect.poll(async () => (await camera(page)).live).toBe(1);
  await page.evaluate(() => {
    const s = (window as unknown as { __camera: { tracks: MediaStreamTrack[] } }).__camera;
    s.tracks.at(-1)!.dispatchEvent(new Event('ended'));
  });
  await expect(page.getByRole('alert').filter({ hasText: /foi desconectada/ })).toBeVisible();
});

test('download do modelo que falha não desenha caixas', async ({ page }) => {
  await page.route('**/models/live-detection.json', (route) =>
    route.fulfill({ json: manifest('a'.repeat(64), 16) }),
  );
  await page.route('**/models/teste.onnx', (route) => route.fulfill({ status: 404 }));
  await fakeCamera(page);
  await page.goto('/#/deteccao-ao-vivo');
  await expect(page.getByText('Prévia experimental', { exact: true })).toBeVisible();
  // Versão e perfil do modelo ficam nos detalhes técnicos, recolhidos por padrão.
  await expect(page.getByText('contrato-de-teste')).toBeHidden();
  await page.getByText('Detalhes técnicos').click();
  await expect(page.getByText('contrato-de-teste')).toBeVisible();
  await page.getByRole('button', { name: 'Iniciar câmera' }).click();
  await page.getByRole('button', { name: 'Iniciar detecção' }).click();
  await expect(page.getByText(/Download do modelo falhou \(HTTP 404\)/)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Pausar detecção' })).toBeDisabled();
  await expect(
    page.getByText('As detecções aparecem aqui quando a análise começar.'),
  ).toBeVisible();
});

test('modelo com checksum divergente é recusado', async ({ page }) => {
  await page.route('**/models/live-detection.json', (route) =>
    route.fulfill({ json: manifest('a'.repeat(64), 16) }),
  );
  await page.route('**/models/teste.onnx', (route) =>
    route.fulfill({ body: Buffer.alloc(16, 7), contentType: 'application/octet-stream' }),
  );
  await fakeCamera(page);
  await page.goto('/#/deteccao-ao-vivo');
  await page.getByRole('button', { name: 'Iniciar câmera' }).click();
  await page.getByRole('button', { name: 'Iniciar detecção' }).click();
  await expect(page.getByText(/não confere com o checksum registrado/)).toBeVisible();
});

test('capturar e registrar leva o quadro ao registro com o GPS deste instante, sem enviar', async ({
  page,
}) => {
  await page.context().grantPermissions(['geolocation']);
  await page.context().setGeolocation({ latitude: -23.556, longitude: -46.637, accuracy: 8 });
  await fakeCamera(page);
  await page.goto('/#/deteccao-ao-vivo');
  await page.getByRole('button', { name: 'Iniciar câmera' }).click();
  await cameraLive(page);
  await page.getByRole('button', { name: 'Capturar e registrar' }).click();
  await expect(page).toHaveURL(/#\/capture$/);
  await expect(page.getByAltText('Evidência selecionada')).toBeVisible();
  await expect(page.getByText('Localização obtida')).toBeVisible();
  await expect(page.getByText('Precisão aproximada: 8 m')).toBeVisible();
  // Coordenadas nunca viram campos para digitar.
  await expect(page.getByLabel(/Latitude|Longitude/)).toHaveCount(0);
  expect((await camera(page)).live).toBe(0);
  expect(uploads).toEqual([]);
});

test('capturar e registrar sem permissão de localização pede o ponto no mapa', async ({ page }) => {
  await page.context().clearPermissions();
  await fakeCamera(page);
  await page.addInitScript(() => {
    const denied = (_ok: unknown, fail: (error: { code: number }) => void) => fail({ code: 1 });
    Object.defineProperty(navigator, 'geolocation', {
      configurable: true,
      value: { getCurrentPosition: denied, watchPosition: () => 1, clearWatch: () => undefined },
    });
  });
  await page.goto('/#/deteccao-ao-vivo');
  await page.getByRole('button', { name: 'Iniciar câmera' }).click();
  await cameraLive(page);
  await page.getByRole('button', { name: 'Capturar e registrar' }).click();
  await expect(page.getByText('Localização indisponível')).toBeVisible();
  await expect(page.getByText(/Localização bloqueada para este site/)).toBeVisible();
  await expect(
    page.getByRole('heading', { name: 'Selecione no mapa onde esta foto foi tirada' }),
  ).toBeVisible();
  expect(uploads).toEqual([]);
});

// Os dois testes abaixo carregam o modelo real (35 MB, ONNX Runtime): um de cada vez,
// para não disputarem CPU/GPU entre si com o restante da suíte paralela.
test.describe('com o modelo real publicado localmente', () => {
  test.describe.configure({ mode: 'serial' });

  test('o vídeo segue no ritmo da câmera enquanto o modelo analisa quadros', async ({ page }) => {
    // Modelo real publicado localmente; a câmera é simulada (15 quadros/s), não física.
    test.skip(!hasLocalModel(), 'modelo do navegador não publicado neste checkout');
    test.setTimeout(150_000);
    await fakeCamera(page);
    await page.goto('/#/deteccao-ao-vivo');
    await page.getByRole('button', { name: 'Iniciar câmera' }).click();
    await cameraLive(page);
    await page.getByRole('button', { name: 'Iniciar detecção' }).click();
    await expect(page.getByText('Detecção pronta', { exact: true })).toBeVisible({
      timeout: 120_000,
    });
    // O palco continua sendo o vídeo, em tamanho cheio: nenhum quadro congelado no lugar dele.
    const video = page.getByLabel('Imagem ao vivo da câmera');
    const videoBox = await video.boundingBox();
    const stageBox = await page.locator('.live-frame').boundingBox();
    expect(videoBox!.width).toBeGreaterThan(stageBox!.width * 0.95);
    await expect(page.getByLabel('Imagem analisada')).toBeHidden();
    // Quadros apresentados pelo <video> em 2 s, contados pelo próprio navegador.
    await page.getByText('Detalhes técnicos').click();
    const frameCell = page.locator('.live-readout div', { hasText: 'Quadro' }).locator('dd');
    await expect(frameCell).toContainText('nº', { timeout: 60_000 });
    const analyzedFrame = async () => Number((await frameCell.innerText()).replace(/\D/g, ''));
    const firstAnalyzed = await analyzedFrame();
    // Quadros apresentados pelo <video> em 3 s, contados pelo próprio navegador.
    const presented = await video.evaluate(
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
            else resolve(frames);
          };
          player.requestVideoFrameCallback(onFrame);
        }),
    );
    const analyzed = (await analyzedFrame()) - firstAnalyzed;
    // No mesmo intervalo, o vídeo apresenta muito mais quadros do que o modelo analisa:
    // a reprodução não espera a inferência (valores absolutos variam com a carga da máquina).
    expect(presented).toBeGreaterThan(Math.max(2 * analyzed, 8));
    await expect(
      page.locator('.live-readout div', { hasText: 'Análises' }).locator('dd'),
    ).toContainText('/s');
    expect(uploads).toEqual([]);
  });

  test('imagem do aparelho vira só prévia visual: sem câmera, envio, GPS ou registro', async ({
    page,
  }) => {
    // Modelo real publicado localmente (gitignored); sem ele não há o que analisar.
    test.skip(!hasLocalModel(), 'modelo do navegador não publicado neste checkout');
    test.setTimeout(120_000);
    await fakeCamera(page);
    await page.goto('/#/deteccao-ao-vivo');
    // PNG cinza gerado aqui: exercita o caminho, não é evidência de detecção.
    const png = await page.evaluate(() => {
      const canvas = document.createElement('canvas');
      canvas.width = 320;
      canvas.height = 240;
      const context = canvas.getContext('2d')!;
      context.fillStyle = '#777';
      context.fillRect(0, 0, 320, 240);
      return canvas.toDataURL('image/png').split(',')[1];
    });
    await page
      .locator('input[type="file"][aria-label="Escolher imagem para prévia"]')
      .setInputFiles({
        name: 'rua-sem-gps.png',
        mimeType: 'image/png',
        buffer: Buffer.from(png, 'base64'),
      });
    await expect(page.getByText('Imagem do aparelho · prévia sem localização')).toBeVisible({
      timeout: 90_000,
    });
    await expect(page.getByText('Arquivo: rua-sem-gps.png')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Capturar e registrar' })).toBeDisabled();
    await expect(page.getByText(/vale só para a câmera/)).toBeVisible();
    expect((await camera(page)).calls).toHaveLength(0);
    expect(uploads).toEqual([]);
    // Abrir a câmera descarta a prévia da imagem.
    await page.getByRole('button', { name: 'Iniciar câmera' }).click();
    await expect(page.getByText('Arquivo: rua-sem-gps.png')).toHaveCount(0);
  });
});

test('logout com a câmera aberta desliga a câmera e não a reabre', async ({ page }) => {
  await signedIn(page);
  await fakeCamera(page);
  await page.goto('/#/deteccao-ao-vivo');
  await page.getByRole('button', { name: 'Iniciar câmera' }).click();
  await expect.poll(async () => (await camera(page)).live).toBe(1);
  await signOut(page);
  await expect.poll(async () => (await camera(page)).live).toBe(0);
  expect((await camera(page)).calls).toHaveLength(1);
  await expect(page.getByRole('button', { name: 'Encerrar câmera' })).toBeDisabled();
});
