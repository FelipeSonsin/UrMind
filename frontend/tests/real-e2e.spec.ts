import { readFileSync } from 'node:fs';
import { expect, test } from '@playwright/test';

// E2E REAL: Supabase Auth, Storage, fila, Worker e modelo promovido de verdade.
// Nenhuma rota é interceptada. Só roda com credenciais de um usuário de teste
// e uma foto real informadas no ambiente; caso contrário é pulado.
const email = process.env.URMIND_E2E_EMAIL;
const password = process.env.URMIND_E2E_PASSWORD;
const photo = process.env.URMIND_E2E_PHOTO;
const latitude = process.env.URMIND_E2E_LATITUDE ?? '-23.5613';
const longitude = process.env.URMIND_E2E_LONGITUDE ?? '-46.6560';

test.skip(!email || !password || !photo, 'E2E real exige URMIND_E2E_EMAIL/PASSWORD/PHOTO');
test.describe.configure({ mode: 'serial' });

test('foto real → upload → Worker → evento no mapa e detalhe', async ({ page }) => {
  test.setTimeout(240_000);
  await page.goto('/#drafts');
  await page.getByLabel('E-mail').fill(email!);
  await page.getByLabel('Senha').fill(password!);
  await page.getByRole('button', { name: 'Entrar' }).click();
  await expect(page.getByRole('heading', { name: 'Entrar no UrMind' })).toHaveCount(0);

  await page.goto('/#capture');
  await page.getByLabel('Escolher foto').setInputFiles({
    name: 'evidencia-real.jpg',
    mimeType: 'image/jpeg',
    buffer: readFileSync(photo!),
  });
  await expect(page.getByAltText('Evidência selecionada')).toBeVisible();
  await page.getByLabel('Latitude', { exact: true }).fill(latitude);
  await page.getByLabel('Longitude', { exact: true }).fill(longitude);
  await page.getByRole('button', { name: 'Salvar e enviar' }).click();
  await expect(page.getByText(/Foto enviada e registrada|já estava registrada/)).toBeVisible({
    timeout: 60_000,
  });

  // Tempo real: o Worker processa de forma assíncrona e a lista deve se atualizar
  // sozinha por Postgres Changes, sem clicar em Atualizar.
  await page.goto('/#events');
  await expect(page.getByText('Tempo real ativo')).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole('button', { name: 'Ver registro' }).first()).toBeVisible({
    timeout: 180_000,
  });

  await page.getByRole('button', { name: 'Ver registro' }).first().click();
  const detail = page.getByLabel('Detalhes da ocorrência');
  await expect(detail.getByAltText('Evidência fotográfica da ocorrência')).toBeVisible({
    timeout: 30_000,
  });
  await expect(detail.locator('.bbox').first()).toBeVisible();
  await expect(detail.getByText('Severidade', { exact: true })).toBeVisible();
  await expect(detail.getByText('Relatório', { exact: true })).toBeVisible();
  await expect(detail.locator('pre.report')).not.toBeEmpty();
  await expect(detail.getByText(/Requer triagem|DNIT/).first()).toBeVisible();

  // Contexto externo (§13): presente ou explicitamente indisponível, nunca inventado.
  await expect(detail.getByText('Endereço aproximado (contexto)')).toBeVisible();
  await expect(
    detail.getByText(/Contexto indisponível|Não disponível|OpenStreetMap/).first(),
  ).toBeVisible({ timeout: 60_000 });

  // Revisão humana: o papel vem do servidor; a confirmação muda o estado do evento.
  await expect(detail.getByRole('button', { name: 'Confirmar' })).toBeVisible();
  await detail.getByRole('button', { name: 'Confirmar' }).click();
  await expect(detail.getByText(/confirm/).first()).toBeVisible({ timeout: 30_000 });
  await expect(detail.getByText('Confirmada', { exact: true })).toBeVisible({ timeout: 30_000 });

  await page.getByRole('link', { name: 'Gêmeo digital 2D' }).click();
  await expect(page.locator('.map canvas')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Ver registro' }).first()).toBeVisible();
});
