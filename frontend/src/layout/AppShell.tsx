import { Suspense, useEffect, useRef, useState, type ReactNode } from 'react';
import { useAppState } from '../app/AppState';
import { CommandPalette, isPaletteShortcut } from './CommandPalette';
import { Footer } from './Footer';
import { Sidebar } from './Sidebar';
import { Topbar } from './Topbar';

/**
 * Casca de todas as páginas: link de pular, menu lateral (barra inferior no celular),
 * barra do topo, conteúdo e rodapé. Estilos em styles/layout/shell.css.
 */
export function AppShell({ children }: { children: ReactNode }) {
  const { page, privatePage, adminSurface, team } = useAppState();
  // Na área da equipe, a paleta só existe depois do acesso confirmado.
  const paletteEnabled = !adminSurface || team.canReview;
  const [paletteOpen, setPaletteOpen] = useState(false);
  const enabled = useRef(paletteEnabled);
  enabled.current = paletteEnabled;
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (!isPaletteShortcut(event) || !enabled.current) return;
      event.preventDefault();
      setPaletteOpen((value) => !value);
    }
    addEventListener('keydown', onKey);
    return () => removeEventListener('keydown', onKey);
  }, []);
  const openPalette = paletteEnabled ? () => setPaletteOpen(true) : undefined;
  return (
    <div
      className={`app-shell${page === 'overview' ? ' is-home' : ''}${
        privatePage && team.canReview ? ' internal-shell' : ''
      }`}
    >
      <a
        className="skip-link"
        href="#main"
        onClick={(event) => {
          event.preventDefault();
          document.getElementById('main')?.focus();
        }}
      >
        Pular para o conteúdo
      </a>
      <Sidebar onOpenPalette={openPalette} />
      <div className="workspace">
        <Topbar onOpenPalette={openPalette} />
        <main id="main" tabIndex={-1}>
          <Suspense fallback={<p role="status">Carregando página…</p>}>{children}</Suspense>
          <Footer />
        </main>
      </div>
      {paletteEnabled && (
        <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} />
      )}
    </div>
  );
}
