import { defineConfig, devices } from '@playwright/test';

const desktop = { ...devices['Desktop Chrome'], channel: 'msedge' };
const mobile = {
  ...devices['iPhone 13'],
  defaultBrowserType: 'chromium' as const,
  channel: 'msedge',
};
// Motor do Safari (WebKit), opcional: `npx playwright install webkit` e PW_WEBKIT=1.
// Roda as varreduras de linguagem simples e de acabamento num iPhone emulado. No WebKit,
// com o service worker do app no controle, os dados simulados (page.route) deixam de
// valer e a página chama o servidor de verdade: o Playwright recomenda bloqueá-lo.
const webkit = {
  name: 'webkit',
  use: { ...devices['iPhone 13'], serviceWorkers: 'block' as const },
  testMatch: /(plain-language|design-quality)\.spec\.ts/,
};

export default defineConfig({
  testDir: './tests',
  fullyParallel: true,
  // PLAYWRIGHT_BASE_URL aponta para um servidor já em pé (ex.: HTTPS de produção
  // local). Sem ela, o preview do Vite sobe automaticamente. ignoreHTTPSErrors
  // cobre o certificado de desenvolvimento autoassinado (§6.1).
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL || 'http://127.0.0.1:4173',
    ignoreHTTPSErrors: true,
    trace: 'retain-on-failure',
  },
  projects: [
    { name: 'desktop', use: desktop },
    { name: 'mobile', use: mobile },
    ...(process.env.PW_WEBKIT ? [webkit] : []),
  ],
  webServer: process.env.PLAYWRIGHT_BASE_URL
    ? undefined
    : {
        command: 'npm.cmd run preview -- --port 4173 --strictPort',
        url: 'http://127.0.0.1:4173',
        reuseExistingServer: !process.env.CI,
      },
});
