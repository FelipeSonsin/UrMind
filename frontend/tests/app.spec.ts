import { readFileSync } from 'node:fs';
import { expect, test, type Page } from '@playwright/test';
import { stubPublicApi, syntheticReportPhoto } from './fixtures';

// Sessão Supabase simulada SÓ no navegador de teste: a chave de armazenamento é a
// do projeto configurado no build (frontend/.env.local), e a API é interceptada.
function supabaseStorageKey(): string {
  const env = readFileSync(new URL('../.env.local', import.meta.url), 'utf8');
  const url = /VITE_SUPABASE_URL=(.+)/.exec(env)?.[1]?.trim();
  if (!url) throw new Error('VITE_SUPABASE_URL ausente em frontend/.env.local');
  return `sb-${new URL(url).hostname.split('.')[0]}-auth-token`;
}
async function signedIn(page: Page) {
  const session = {
    access_token: 'token-de-teste',
    token_type: 'bearer',
    expires_in: 3600,
    expires_at: Math.floor(Date.now() / 1000) + 3600,
    refresh_token: 'refresh-de-teste',
    user: {
      id: '0b0e4c7e-1111-4222-8333-944445555666',
      aud: 'authenticated',
      role: 'authenticated',
    },
  };
  await page.addInitScript(
    ([key, value]) => localStorage.setItem(key, value),
    [supabaseStorageKey(), JSON.stringify(session)],
  );
}

test.beforeEach(async ({ page }) => {
  // Fixtures exclusivamente de teste. Nenhuma ocorrência fictícia entra no produto.
  await stubPublicApi(page, { events: [], detail: null });
});

test('captura respeita tema do dispositivo e alvos de toque em 320px', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 740 });
  await page.emulateMedia({ colorScheme: 'dark' });
  await page.goto('/#/registrar');
  await expect(page.locator('html')).toHaveCSS('color-scheme', 'dark');
  await expect(page.locator('.panel').first()).toHaveCSS('background-color', 'rgb(28, 48, 42)');
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(320);
  for (const button of await page.getByRole('button').all()) {
    // File input is intentionally transparent; its enclosing label is the touch target.
    const target = (await button.getAttribute('type')) === 'file' ? button.locator('..') : button;
    if (await target.isVisible())
      expect((await target.boundingBox())!.height).toBeGreaterThanOrEqual(44);
  }
  await page.emulateMedia({ colorScheme: 'light' });
  await expect(page.locator('html')).toHaveCSS('color-scheme', 'light');
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
  await page.getByLabel('Descreva o problema (opcional)').fill(description);
  await expect(page.getByText(`${description.length}/500 caracteres`)).toBeVisible();
  const png = await syntheticReportPhoto(page);
  await page.getByLabel('Escolher foto').setInputFiles({
    name: 'report.png',
    mimeType: 'image/png',
    buffer: Buffer.from(png, 'base64'),
  });
  await page.getByRole('button', { name: 'Salvar e enviar' }).click();
  await expect(page.getByRole('alert')).toContainText('Leia e aceite o aviso de privacidade');
  expect(uploaded).toBe(false);
  await page.getByRole('checkbox', { name: /Li e aceito/ }).check();
  await page.getByRole('button', { name: 'Salvar e enviar' }).click();
  await expect.poll(() => uploaded).toBe(true);
  await expect(page).toHaveURL(new RegExp(`processando/${id}`));
  await expect(page.getByText('Etapa: location_required')).toBeVisible();
  await page.locator('.map canvas').click({ position: { x: 120, y: 100 } });
  await page.getByRole('button', { name: 'Confirmar localização do relato' }).click();
  await expect(page.getByText('Etapa: model_not_available')).toBeVisible();
  await expect(page.getByText(description, { exact: true })).toBeVisible();
  await expect(page.locator(`[data-event-id="${id}"]`)).toHaveCount(1);
  await expect(page.getByRole('list', { name: 'Legenda de relatos' })).toContainText(
    'Análise indisponível',
  );
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
  await page.locator('.map canvas').click({ position: { x: 120, y: 100 } });
  await page.getByRole('button', { name: 'Confirmar localização no mapa' }).click();
  await page.getByRole('checkbox', { name: /Li e aceito/ }).check();
  await page.getByRole('button', { name: 'Salvar e enviar' }).click();
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
  await expect(page.getByText('Etapa: detection_completed')).toBeVisible();
  await expect(page.getByText(/Análise experimental/)).toBeVisible();
  await expect(page.getByRole('link', { name: 'Ver ocorrência no mapa' })).toHaveCount(0);
  await page.reload();
  await expect(page.getByText('Etapa: detection_completed')).toBeVisible();
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
  await expect(page.getByText('Etapa: model_not_available')).toBeVisible();
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
  await expect(page.getByText('Etapa: completed')).toHaveCount(0);
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
  await page.locator('.map canvas').click({ position: { x: 120, y: 100 } });
  await page.getByRole('button', { name: 'Confirmar localização no mapa' }).click();
  await page.getByRole('checkbox', { name: /Li e aceito/ }).check();
  await page.getByRole('button', { name: 'Salvar e enviar' }).click();
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
  await page.locator('.map canvas').click({ position: { x: 120, y: 100 } });
  await page.getByRole('button', { name: 'Confirmar localização no mapa' }).click();
  await page.getByRole('checkbox', { name: /Li e aceito/ }).check();
  await page.getByRole('button', { name: 'Salvar e enviar' }).click();
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
  await expect(page.getByText('Etapa: queued')).toBeVisible();
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
  await expect(page.getByText('Etapa: completed')).toHaveCount(0);
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
      await page.locator('.map canvas').click({ position: { x: 120, y: 100 } });
      await page.getByRole('button', { name: 'Confirmar localização no mapa' }).click();
      await page.getByRole('checkbox', { name: /Li e aceito/ }).check();
      await page.getByRole('button', { name: 'Salvar e enviar' }).click();
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
      if (action === 'switch')
        await expect(page.locator('[title="Supabase Realtime (Postgres Changes)"]')).toBeVisible();
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
  await expect(page.getByText('Etapa: no_supported_detection')).toBeVisible();
  await expect(
    page.getByText(/Nenhuma ocorrência das classes suportadas foi detectada/),
  ).toBeVisible();
  await expect(page.getByRole('link', { name: 'Ver ocorrência no mapa' })).toHaveCount(0);
});

test('separa o centro público da operação e navega entre os dois', async ({ page }, testInfo) => {
  await page.goto('/');
  await expect(
    page.getByRole('heading', { name: 'O que o UrMind está vendo na cidade' }),
  ).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('overview.png'), fullPage: true });
  await page.getByRole('link', { name: 'Revisão', exact: true }).click();
  // Sem sessão, a revisão pede login em vez de mostrar números.
  await expect(page.getByRole('heading', { name: 'Entrar no UrMind' })).toBeVisible();
  await page.getByRole('link', { name: 'Transparência' }).click();
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
  await page.getByRole('button', { name: 'Salvar e enviar' }).click();
  await expect(page.getByText('Localização pendente', { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByRole('heading', { name: 'evidencia.png' })).toBeVisible();
  await page.getByRole('button', { name: 'Continuar edição' }).click();
  await page.getByLabel('Latitude', { exact: true }).fill('0');
  await page.getByLabel('Longitude', { exact: true }).fill('0');
  await page.getByRole('checkbox', { name: /Li e aceito/ }).check();
  await page.getByRole('button', { name: 'Salvar e enviar' }).click();
  await expect(page.getByRole('alert')).toContainText('Selecione e confirme a localização no mapa');
  await page.getByRole('button', { name: 'Selecionar localização no mapa' }).click();
  await page.locator('.map canvas').click({ position: { x: 120, y: 100 } });
  await page.getByRole('button', { name: 'Confirmar localização no mapa' }).click();
  await page.getByRole('checkbox', { name: /Li e aceito/ }).check();
  await page.getByRole('button', { name: 'Salvar e enviar' }).click();
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
  await expect(page.getByText('0.0%', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Ver registro' }).click();
  await expect(page.getByText('-23.5, -46.6', { exact: true })).toBeVisible();
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
    page.getByRole('heading', { name: 'O que o UrMind está vendo na cidade' }),
  ).toBeVisible();
  await page.getByRole('link', { name: 'Registrar evidência' }).click();
  await expect(page.getByRole('button', { name: 'Salvar e enviar' })).toBeVisible();
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
  await expect(page.getByRole('link', { name: 'Informar localização' })).toHaveAttribute(
    'href',
    /processando/,
  );
});
test('porteiro local recusa foto escura sem perder a descrição', async ({ page }) => {
  await page.goto('/#/registrar');
  await page.getByLabel('Descreva o problema (opcional)').fill('Minha observação');
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
  await expect(page.getByLabel('Descreva o problema (opcional)')).toHaveValue('Minha observação');
});
