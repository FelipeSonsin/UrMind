import { createHash } from 'node:crypto';
import { readFileSync, realpathSync } from 'node:fs';
import { isAbsolute, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, test } from '@playwright/test';
import { loadEnv } from 'vite';

// E2E REAL: Supabase Auth, Storage, fila, Worker e modelo shadow autorizado no DEV.
// Nenhuma rota é interceptada. A própria UI cria uma identidade anônima individual.
// Foto e localização precisam de autorização registrada; ausência não é sucesso.
const photo = process.env.URMIND_E2E_PHOTO;
const photoSha256 = process.env.URMIND_E2E_PHOTO_SHA256;
const latitude = process.env.URMIND_E2E_LATITUDE;
const longitude = process.env.URMIND_E2E_LONGITUDE;
const devRef = 'impmeitwtusjtwjouggy';

test.skip(
  !photo || !photoSha256 || !latitude || !longitude,
  'E2E real exige foto independente revisada e localização confirmada',
);
test.describe.configure({ mode: 'serial' });

test('foto real → Anonymous Auth → Worker → resultado do proprietário', async ({
  page,
  request,
}, testInfo) => {
  test.skip(testInfo.project.name !== 'mobile', 'Uma única Capture nova por execução E2E');
  test.setTimeout(240_000);
  const frontendEnv = loadEnv('production', fileURLToPath(new URL('../', import.meta.url)), '');
  const localUrl = frontendEnv.VITE_SUPABASE_URL;
  const backendUrl = /^SUPABASE_URL=(.+)$/m
    .exec(readFileSync(new URL('../../backend/.env', import.meta.url), 'utf8'))?.[1]
    ?.trim();
  const baseUrl = new URL(process.env.PLAYWRIGHT_BASE_URL || 'http://127.0.0.1:4173');
  const proxyTarget = frontendEnv.API_PROXY_TARGET
    ? new URL(frontendEnv.API_PROXY_TARGET)
    : new URL('http://127.0.0.1:8000');
  if (
    !localUrl ||
    !backendUrl ||
    new URL(localUrl.replace(/^['"]|['"]$/g, '')).hostname !== `${devRef}.supabase.co` ||
    new URL(backendUrl.replace(/^['"]|['"]$/g, '')).hostname !== `${devRef}.supabase.co` ||
    !['127.0.0.1', 'localhost'].includes(baseUrl.hostname) ||
    !['127.0.0.1', 'localhost'].includes(proxyTarget.hostname)
  )
    throw new Error('E2E recusado: configuração local não aponta integralmente para Urmind DEV');
  const transparency = await request.get('/api/v1/public/transparency');
  if (!transparency.ok()) throw new Error('E2E recusado: backend DEV não respondeu');
  const model = await transparency.json();
  if (model.stage !== 'EXPERIMENTAL_SHADOW' || model.model_version !== 'final-epoch19-7d91f7f6f0c0')
    throw new Error('E2E recusado: backend não está no shadow DEV esperado');
  if (!isAbsolute(photo!)) throw new Error('E2E exige caminho absoluto para a foto');
  const photoPath = realpathSync(resolve(photo!));
  // A raiz não pode depender do CWD: Playwright pode ser invocado de outro diretório.
  const projectRoot = realpathSync(fileURLToPath(new URL('../../', import.meta.url)));
  for (const protectedTree of ['datasets', 'models', 'mlruns']) {
    const within = relative(resolve(projectRoot, protectedTree), photoPath);
    if (within === '' || (!within.startsWith('..') && !isAbsolute(within)))
      throw new Error('E2E recusado: foto pertence a artefatos científicos protegidos');
  }
  const image = readFileSync(photoPath);
  if (
    !/^[0-9a-f]{64}$/i.test(photoSha256!) ||
    createHash('sha256').update(image).digest('hex') !== photoSha256!.toLowerCase()
  )
    throw new Error('E2E recusado: hash da foto não corresponde ao hash informado');
  // A aprovação é versionada após revisão independente da foto e do local.
  // O hash informado pelo executor, sozinho, não autoriza usar um holdout copiado.
  const approved = JSON.parse(
    readFileSync(new URL('./approved-real-e2e-images.json', import.meta.url), 'utf8'),
  ) as Array<{
    sha256: string;
    latitude: number;
    longitude: number;
    provenance: string;
    location_evidence: string;
  }>;
  if (
    !approved.some(
      (entry) =>
        entry.sha256 === photoSha256!.toLowerCase() &&
        entry.latitude === Number(latitude) &&
        entry.longitude === Number(longitude) &&
        entry.provenance.length > 0 &&
        entry.location_evidence.length > 0,
    )
  )
    throw new Error('E2E recusado: foto/localização não constam na lista revisada');
  if (
    !Number.isFinite(Number(latitude)) ||
    !Number.isFinite(Number(longitude)) ||
    Math.abs(Number(latitude)) > 90 ||
    Math.abs(Number(longitude)) > 180
  )
    throw new Error('E2E recusado: localização inválida');
  await page.goto('/#/registrar');
  await page.getByLabel('Escolher foto').setInputFiles({
    name: 'evidencia-real.jpg',
    mimeType: 'image/jpeg',
    buffer: image,
  });
  await expect(page.getByAltText('Evidência selecionada')).toBeVisible();
  await page.getByLabel('Latitude', { exact: true }).fill(latitude!);
  await page.getByLabel('Longitude', { exact: true }).fill(longitude!);
  await page.getByRole('button', { name: 'Selecionar localização no mapa' }).click();
  const map = page.locator('.map canvas');
  const bounds = await map.boundingBox();
  if (!bounds) throw new Error('Mapa não carregou para confirmar a localização informada');
  // O mapa foi centrado nas coordenadas revisadas fornecidas ao teste.
  await map.click({ position: { x: bounds.width / 2, y: bounds.height / 2 } });
  await page.getByRole('button', { name: 'Confirmar localização no mapa' }).click();
  const uploadResponse = page.waitForResponse(
    (response) =>
      response.url().includes('/api/v1/captures/photo') && response.request().method() === 'POST',
  );
  await page.getByRole('button', { name: 'Salvar e enviar' }).click();
  const uploaded = await uploadResponse;
  const upload = await uploaded.json();
  if (!uploaded.ok() || upload.created !== true)
    throw new Error('E2E recusado: Capture não foi criada nesta execução');
  await expect(page.getByText('Foto enviada e registrada')).toBeVisible({
    timeout: 60_000,
  });

  await expect(page).toHaveURL(new RegExp(`#\/processando\/${upload.id}$`));
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Processamento da foto' })).toBeVisible();
  const ownerSession = await page.evaluate((ref) => {
    const value = localStorage.getItem(`sb-${ref}-auth-token`);
    return value ? JSON.parse(value) : null;
  }, devRef);
  if (!ownerSession?.user?.is_anonymous || !ownerSession.access_token)
    throw new Error('E2E exige sessão anônima real criada pelo frontend');
  const ownerHeaders = { Authorization: `Bearer ${ownerSession.access_token}` };
  let processing: { status: string; event_ids: string[] } | undefined;
  await expect
    .poll(
      async () => {
        const response = await request.get(`/api/v1/captures/${upload.id}/processing`, {
          headers: ownerHeaders,
        });
        if (!response.ok()) throw new Error('Proprietário não recuperou Capture após refresh');
        processing = await response.json();
        return [
          'completed',
          'no_supported_detection',
          'failed',
          'model_not_available',
          'needs_review',
        ].includes(processing!.status);
      },
      { timeout: 180_000 },
    )
    .toBe(true);
  if (processing!.status === 'no_supported_detection') {
    expect(processing!.event_ids).toEqual([]);
    await expect(page.getByText('Etapa: no_supported_detection')).toBeVisible();
    await expect(page.getByRole('link', { name: 'Ver ocorrência no mapa' })).toHaveCount(0);
    testInfo.annotations.push({
      type: 'real_outcome',
      description: 'no_supported_detection; Event/map path not exercised',
    });
    return;
  }
  if (processing!.status !== 'completed')
    throw new Error(`Pipeline não concluiu análise: ${processing!.status}`);
  await expect(page.getByText(/Análise experimental/)).toBeVisible({ timeout: 180_000 });
  const eventLink = page.getByRole('link', { name: 'Ver ocorrência no mapa' }).first();
  await expect(eventLink).toBeVisible({
    timeout: 180_000,
  });
  const eventHref = await eventLink.getAttribute('href');
  const eventId = /^#\/resultado\/([0-9a-f-]{36})$/i.exec(eventHref ?? '')?.[1];
  if (!eventId) throw new Error('Capture concluída sem Event identificável');
  const unpublished = await request.get(`/api/v1/public/events/${eventId}`);
  expect(unpublished.status()).toBe(404);
  const resultResponse = await request.get(`/api/v1/public/events/${eventId}`, {
    headers: ownerHeaders,
  });
  if (!resultResponse.ok()) throw new Error('Resultado indisponível para o proprietário');
  const result = await resultResponse.json();
  if (
    result.id !== eventId ||
    result.model_stage !== 'EXPERIMENTAL_SHADOW' ||
    !result.detections?.length ||
    !result.road ||
    result.snapped_latitude == null ||
    result.snapped_longitude == null ||
    !result.context?.length ||
    !result.risk ||
    !result.trace?.some(
      (step: { step: string; status: string }) =>
        step.step === 'decision' && step.status === 'done',
    )
  )
    throw new Error('E2E incompleto: Detection/RoadSegment/Context/Risk/DecisionTrace ausente');
  await eventLink.click();
  await expect(page).toHaveURL(new RegExp(`#\/resultado\/${eventId}$`));
  const detail = page.locator('main');
  // A imagem privada não é publicada sem sanitização; Detection estruturada permanece visível.
  await expect(detail.getByText(/imagem não publicada/)).toBeVisible();
  await expect(detail.locator('.detection-list li').first()).toBeVisible();
  await expect(page.getByLabel('Risco e explicação')).toBeVisible();
  await expect(page.getByLabel('Como o UrMind analisou')).toBeVisible();
  await expect(detail.getByText(/Análise experimental/)).toBeVisible();

  // Contexto externo (§13): presente ou explicitamente indisponível, nunca inventado.
  await expect(page.getByLabel('Contexto urbano')).toBeVisible();

  await page.goto('/#/map');
  await expect(page.getByRole('heading', { name: 'Mapa operacional' })).toBeVisible();
  await expect(page.locator('.map canvas')).toBeVisible();
  await expect(page.locator(`.map-panel [data-event-id="${eventId}"]`)).toHaveCount(0);
  testInfo.annotations.push({
    type: 'publication_pending',
    description: `Event ${eventId}: review humana e publicação explícita necessárias; mapa público não aprovado por este teste`,
  });
});
