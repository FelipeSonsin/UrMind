import { expect, test } from '@playwright/test';
import { EVENT_ID, stubPublicApi } from './fixtures';

// Nenhuma tela pública pode rolar na horizontal — é o defeito mais comum quando um
// painel denso encontra um celular. As capturas ficam em test-results para inspeção.
const WIDTHS = [
  { name: 'desktop', width: 1680, height: 1050 },
  { name: 'notebook', width: 1366, height: 800 },
  { name: 'tablet', width: 834, height: 1112 },
  { name: 'celular', width: 390, height: 844 },
];
const ROUTES = [
  { name: 'home', path: '/' },
  { name: 'live', path: '/#/live' },
  { name: 'eventos', path: '/#/events' },
  { name: 'detalhe', path: `/#/events/${EVENT_ID}` },
  { name: 'transparencia', path: '/#/transparency' },
  { name: 'sistema', path: '/#/system' },
];

for (const size of WIDTHS) {
  test(`o painel público cabe na largura ${size.name}`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width: size.width, height: size.height });
    await stubPublicApi(page);
    for (const route of ROUTES) {
      await page.goto(route.path);
      await expect(page.locator('main')).toBeVisible();
      await page.screenshot({
        path: testInfo.outputPath(`${size.name}-${route.name}.png`),
        fullPage: true,
      });
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - window.innerWidth,
      );
      expect(overflow, `rolagem horizontal em ${route.path} (${size.name})`).toBeLessThanOrEqual(0);
    }
  });
}
