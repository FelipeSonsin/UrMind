import { expect, test } from '@playwright/test';
import { EVENT_ID, stubPublicApi } from './fixtures';

// Nenhuma tela pública pode rolar na horizontal — é o defeito mais comum quando um
// painel denso encontra um celular. As capturas ficam em test-results para inspeção.
const WIDTHS = [
  { name: 'celular-minimo', width: 320, height: 640 },
  { name: 'desktop', width: 1680, height: 1050 },
  { name: 'notebook', width: 1366, height: 800 },
  { name: 'tablet', width: 834, height: 1112 },
  { name: 'celular', width: 390, height: 844 },
];

test('entrada pública mantém o JavaScript inicial abaixo do orçamento mobile', async ({ page }) => {
  await stubPublicApi(page);
  const documentResponse = await page.goto('/');
  await expect(page.locator('main')).toBeVisible();
  // Read the server document, not links inserted later by the lazy map loader.
  const entries = await page.evaluate(
    (html) => {
      const document = new DOMParser().parseFromString(html, 'text/html');
      return [
        ...document.querySelectorAll('script[type="module"][src], link[rel="modulepreload"][href]'),
      ].map((node) => node.getAttribute('src') || node.getAttribute('href')!);
    },
    await documentResponse!.text(),
  );
  expect(entries.length).toBeGreaterThan(0);
  let bytes = 0;
  for (const entry of new Set(entries)) {
    const response = await page.request.get(entry);
    expect(response.ok()).toBeTruthy();
    bytes += (await response.body()).byteLength;
  }
  // Entry + preloads minificados: não mede downloads posteriores do PWA/mapa.
  // Reintroduzir páginas de captura/revisão no import eager quebra este limite.
  expect(bytes).toBeLessThan(650_000);
});
const ROUTES = [
  { name: 'home', path: '/' },
  { name: 'live', path: '/#/live' },
  { name: 'eventos', path: '/#/events' },
  { name: 'detalhe', path: `/#/events/${EVENT_ID}` },
  { name: 'transparencia', path: '/#/transparency' },
  { name: 'sistema', path: '/#/system' },
  { name: 'registrar', path: '/#/registrar' },
  { name: 'mapa', path: '/#/mapa' },
  { name: 'ao-vivo', path: '/#/deteccao-ao-vivo' },
  { name: 'meus-relatos', path: '/#/meus-relatos' },
  { name: 'camera-robo', path: '/#/camera-robo' },
  { name: 'camera-robo-celular', path: '/#/camera-robo/celular?session=x&token=y' },
];

for (const size of WIDTHS) {
  test(`o painel público cabe na largura ${size.name}`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width: size.width, height: size.height });
    await stubPublicApi(page);
    for (const route of ROUTES) {
      await page.goto(route.path);
      await expect(page.locator('main')).toBeVisible();
      // Páginas carregadas sob demanda: a captura só vale com o conteúdo na tela.
      await expect(page.locator('main h1').first()).toBeVisible();
      await page.screenshot({
        path: testInfo.outputPath(`${size.name}-${route.name}.png`),
        fullPage: true,
      });
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - window.innerWidth,
      );
      const outside =
        overflow > 0
          ? await page.locator('main *').evaluateAll((nodes) =>
              nodes
                .filter((node) => node.getBoundingClientRect().right > window.innerWidth)
                .slice(0, 10)
                .map((node) => `${node.tagName}.${node.className}`),
            )
          : [];
      expect(
        overflow,
        `rolagem horizontal em ${route.path} (${size.name}): ${outside.join(', ')}`,
      ).toBeLessThanOrEqual(0);
    }
  });
}
