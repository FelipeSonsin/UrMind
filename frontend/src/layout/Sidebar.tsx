import { Globe, LogOut, Search } from 'lucide-react';
import { useAppState } from '../app/AppState';
import { Brand } from '../components/ui';
import { auth } from '../services/auth';
import { PublicNav } from './PublicNav';
import { TeamNav } from './TeamNav';
import { useMoreMenus } from './useMoreMenus';

/**
 * Computador: barra lateral fixa. Celular: a mesma navegação vira barra inferior.
 * A busca também abre com Ctrl+K (⌘K no Mac), anunciado a leitores de tela.
 */
export function Sidebar({ onOpenPalette }: { onOpenPalette?: () => void }) {
  const { page, adminSurface, privatePage, team, session, isVisitor } = useAppState();
  useMoreMenus(page);
  return (
    <aside className="sidebar">
      <a className="brand" href={adminSurface ? '#/app/dashboard' : '#/'} aria-label="UrMind">
        <Brand size={30} />
      </a>
      {adminSurface && <span className="surface-tag">Área da equipe</span>}
      {onOpenPalette && (
        <button
          type="button"
          className="palette-trigger"
          aria-keyshortcuts="Control+K Meta+K"
          onClick={onOpenPalette}
        >
          <Search size={16} strokeWidth={1.8} aria-hidden="true" />
          Buscar página ou relato
        </button>
      )}
      {/* A área da equipe não mostra o menu do cidadão: navega só pelo menu interno. */}
      {!adminSurface && <PublicNav />}
      {/* Menu da equipe: estável na lateral; no celular, barra inferior com "Mais". */}
      {adminSurface && privatePage && team.canReview && <TeamNav />}
      {adminSurface && session && !isVisitor && (
        <div className="sidebar-foot">
          <a href="/">
            <Globe size={16} strokeWidth={1.8} aria-hidden="true" /> Site do cidadão
          </a>
          <button type="button" className="ghost" onClick={() => void auth.signOut()}>
            <LogOut size={16} strokeWidth={1.8} aria-hidden="true" /> Sair
          </button>
        </div>
      )}
    </aside>
  );
}
