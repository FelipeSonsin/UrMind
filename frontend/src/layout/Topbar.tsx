import { Search } from 'lucide-react';
import { useAppState } from '../app/AppState';
import { BrandMark } from '../components/ui';

/** Celular: marca e avisos de conexão. Computador: só aparece com algum aviso. */
export function Topbar({ onOpenPalette }: { onOpenPalette?: () => void }) {
  const { adminSurface, online, session, privatePage, team, teamData } = useAppState();
  const { realtime } = teamData;
  return (
    <header className="topbar">
      <a
        className="topbar-brand"
        href={adminSurface ? '#/app/dashboard' : '#/'}
        aria-label="UrMind, página inicial"
      >
        <BrandMark size={28} />
        <span className="brand-word" aria-hidden="true" translate="no">
          <b>ur</b>mind
        </span>
      </a>
      {/* Só o que muda o que a pessoa pode fazer: sem rede, o relato fica no aparelho. */}
      {!online && (
        <span className="network" role="status">
          <i />
          {adminSurface
            ? 'Sem rede: as decisões só são salvas quando a conexão voltar'
            : 'Sem rede: o relato fica salvo neste aparelho'}
        </span>
      )}
      {/* Ao vivo, só o ponto verde (o texto fica para o leitor de tela); sem atualização
          automática, o aviso aparece por escrito, porque muda o que a pessoa precisa fazer. */}
      {session && privatePage && team.canReview && (
        <span
          className={`network${realtime === 'connected' ? ' is-quiet' : ''}`}
          title={realtime === 'connected' ? 'Atualizando ao vivo' : undefined}
        >
          <i className={realtime === 'connected' ? 'online' : ''} aria-hidden="true" />
          <span className={realtime === 'connected' ? 'sr-only' : undefined}>
            {realtime === 'connected'
              ? 'Atualizando ao vivo'
              : realtime === 'connecting'
                ? 'Conectando…'
                : 'Sem atualização automática'}
          </span>
        </span>
      )}
      {onOpenPalette && (
        <button
          type="button"
          className="palette-icon"
          aria-label="Buscar página ou relato"
          onClick={onOpenPalette}
        >
          <Search size={20} strokeWidth={1.8} aria-hidden="true" />
        </button>
      )}
    </header>
  );
}
