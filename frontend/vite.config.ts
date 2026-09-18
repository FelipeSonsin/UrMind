import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';
import basicSsl from '@vitejs/plugin-basic-ssl';
import { VitePWA } from 'vite-plugin-pwa';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  const proxy = {
    '/api': { target: env.API_PROXY_TARGET || 'http://127.0.0.1:8000', changeOrigin: true },
  };
  // Câmera e Geolocation exigem contexto seguro fora de localhost (§6.1). Para testar
  // no celular pela rede local: VITE_DEV_HTTPS=1 npm run dev -- --host 0.0.0.0.
  // Certificado local só de desenvolvimento; produção usa TLS do servidor/proxy.
  const devHttps = env.VITE_DEV_HTTPS === '1';
  return {
    plugins: [
      ...(devHttps ? [basicSsl()] : []),
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
