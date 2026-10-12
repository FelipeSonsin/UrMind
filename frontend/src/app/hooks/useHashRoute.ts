import { useEffect, useState } from 'react';
import { flushSync } from 'react-dom';
import { parseRoute, type Route } from '../routes';

type TransitionDocument = Document & {
  startViewTransition?: (update: () => void) => {
    ready: Promise<void>;
    finished: Promise<void>;
  };
};

/**
 * Motor WebKit (Safari e todo navegador do iPhone): nos testes com o WebKit, a transição
 * derrubava a página quando a próxima ainda carregava o próprio código (Rascunhos). Como
 * a transição é só acabamento, nesse motor a página troca direto; Chrome, Edge e Firefox
 * mantêm a transição.
 */
const webkitEngine =
  /AppleWebKit/.test(navigator.userAgent) && !/(Chrome|Chromium|Edg)\//.test(navigator.userAgent);

/**
 * Rota atual, relida a cada mudança do endereço (`#/…`). Onde o navegador permite, a
 * troca de página é uma transição curta só do conteúdo (menu e topo ficam parados);
 * com "reduzir movimento" ligado, a página troca na hora. Estilo em styles/base.css.
 */
export function useHashRoute() {
  const [route, setRoute] = useState<Route>(parseRoute);
  useEffect(() => {
    const change = () => {
      const next = parseRoute();
      const doc = document as TransitionDocument;
      if (
        webkitEngine ||
        !doc.startViewTransition ||
        matchMedia('(prefers-reduced-motion: reduce)').matches
      ) {
        setRoute(next);
        return;
      }
      const root = document.documentElement;
      root.dataset.pageTransition = '';
      const transition = doc.startViewTransition(() => flushSync(() => setRoute(next)));
      // Trocar de página antes da anterior terminar só pula a animação: não é erro.
      transition.ready.catch(() => undefined);
      void transition.finished
        .catch(() => undefined)
        .finally(() => delete root.dataset.pageTransition);
    };
    addEventListener('hashchange', change);
    return () => removeEventListener('hashchange', change);
  }, []);
  return [route, setRoute] as const;
}
