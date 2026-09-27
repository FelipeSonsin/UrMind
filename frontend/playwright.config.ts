import { defineConfig, devices } from '@playwright/test';

const desktop = { ...devices['Desktop Chrome'], channel: 'msedge' };
const mobile = {
  ...devices['iPhone 13'],
  defaultBrowserType: 'chromium' as const,
  channel: 'msedge',
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
  ],
  webServer: process.env.PLAYWRIGHT_BASE_URL
    ? undefined
    : {
        command: 'npm.cmd run preview -- --port 4173 --strictPort',
        url: 'http://127.0.0.1:4173',
        reuseExistingServer: !process.env.CI,
      },
});
