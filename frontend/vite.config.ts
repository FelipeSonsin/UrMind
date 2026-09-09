import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';
import { VitePWA } from 'vite-plugin-pwa';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  const proxy = {
    '/api': { target: env.API_PROXY_TARGET || 'http://127.0.0.1:8000', changeOrigin: true },
  };
  return {
    plugins: [
      react(),
      VitePWA({
        registerType: 'prompt',
        includeAssets: ['icon.svg'],
        manifest: {
          name: 'UrMind — Observatório urbano',
          short_name: 'UrMind',
          lang: 'pt-BR',
          description: 'Registro de evidências e acompanhamento urbano.',
          theme_color: '#123f36',
          background_color: '#f5f6f2',
          display: 'standalone',
          start_url: '/',
          icons: [{ src: '/icon.svg', sizes: 'any', type: 'image/svg+xml', purpose: 'any' }],
        },
        workbox: {
          globPatterns: ['**/*.{js,css,html,svg,png,woff2}'],
          navigateFallbackDenylist: [/^\/api(?:\/|$)/],
          // Somente o app shell: nunca guardar API, fotos privadas ou tiles no cache HTTP.
        },
      }),
    ],
    server: { proxy },
    preview: { proxy },
  };
});
