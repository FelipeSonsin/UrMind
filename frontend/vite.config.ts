import { defineConfig, loadEnv, type Plugin } from 'vite';
import react from '@vitejs/plugin-react';
import basicSsl from '@vitejs/plugin-basic-ssl';
import { VitePWA } from 'vite-plugin-pwa';
import { liveModelDelivery } from './build/liveModel';

/**
 * A área da equipe tem endereço próprio (/admin/), separado do site do cidadão: o
 * mesmo app, com a mesma página, publicada também em admin/index.html (o app decide
 * a superfície pelo caminho; ver src/surface.ts).
 */
function adminEntry(): Plugin {
  return {
    name: 'urmind-admin-entry',
    apply: 'build',
    enforce: 'post',
    generateBundle(_options, bundle) {
      const page = bundle['index.html'];
      if (page?.type !== 'asset') throw new Error('index.html ausente no build');
      this.emitFile({ type: 'asset', fileName: 'admin/index.html', source: page.source });
    },
  };
}

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
      liveModelDelivery(env.LIVE_MODEL_ONNX_URL),
      adminEntry(),
      VitePWA({
        registerType: 'prompt',
        includeAssets: ['icon.svg'],
        manifest: {
          name: 'UrMind — Observatório urbano',
          short_name: 'UrMind',
          lang: 'pt-BR',
          description: 'Registro de evidências e acompanhamento urbano.',
          theme_color: '#050507',
          background_color: '#050507',
          display: 'standalone',
          start_url: '/',
          icons: [{ src: '/icon.svg', sizes: 'any', type: 'image/svg+xml', purpose: 'any' }],
        },
        workbox: {
          globPatterns: ['**/*.{js,mjs,css,html,svg,png,woff2}'],
          // ONNX Runtime (worker + .wasm) e pesos só descem quando a detecção ao vivo é usada.
          globIgnores: ['**/liveDetection.worker-*.js'],
          navigateFallbackDenylist: [/^\/api(?:\/|$)/, /^\/models\//],
          // Somente o app shell: nunca guardar API, fotos privadas ou tiles no cache HTTP.
        },
      }),
    ],
    // O bundle do ONNX Runtime resolve o .wasm por import.meta.url: worker precisa ser ES module.
    worker: { format: 'es' },
    server: { proxy },
    preview: { proxy },
  };
});
