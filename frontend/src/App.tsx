import { AppStateProvider } from './app/AppState';
import { PageOutlet } from './app/PageOutlet';
import { AppShell } from './layout/AppShell';

/**
 * UrMind no navegador: estado compartilhado (app/AppState.tsx), casca com menu, topo e
 * rodapé (layout/) e a página da rota atual (app/PageOutlet.tsx). O mapa de onde mexer
 * em cada parte está no README do frontend.
 */
export default function App() {
  return (
    <AppStateProvider>
      <AppShell>
        <PageOutlet />
      </AppShell>
    </AppStateProvider>
  );
}
