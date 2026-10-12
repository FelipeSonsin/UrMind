import { lazy } from 'react';
import { LogOut } from 'lucide-react';
import { useAppState } from '../../app/AppState';
import { auth } from '../../services/auth';
import { AutomaticQueue, IntegrationHealth, PhotoGateStats, TeamDashboard } from './DashboardPage';
import { TeamReportsSection } from './TeamReportsSection';

const SignIn = lazy(() => import('../../components/SignIn').then((m) => ({ default: m.SignIn })));
const EventDetail = lazy(() =>
  import('../../components/EventDetail').then((m) => ({ default: m.EventDetail })),
);
const EventsPage = lazy(() => import('./EventsPage').then((m) => ({ default: m.EventsPage })));
const OperationsPage = lazy(() =>
  import('./OperationsPage').then((m) => ({ default: m.OperationsPage })),
);
const OperationalRegistryPage = lazy(() =>
  import('./OperationsPage').then((m) => ({ default: m.OperationalRegistryPage })),
);

/**
 * Área da equipe (/admin/). Sem permissão de revisor, mostra o acesso (entrar,
 * verificação, erro ou acesso restrito). Com permissão, cada página é composta por
 * blocos: o Painel resume e aponta para a Fila; a Fila e o Mapa interno listam os relatos.
 */
export function TeamArea() {
  const { page, route, session, team } = useAppState();
  if (!team.canReview) return <TeamAccess />;
  const dashboard = page === 'dashboard' || page === 'login';
  // Só a troca de sessão remonta o Painel; "Atualizar" e a atualização ao vivo releem os
  // números sem apagar a tela nem o período escolhido.
  const totalsKey = session?.access_token;
  return (
    <>
      {/* Painel: números, os próximos a revisar e a saúde do serviço; sem mapa nem lista longa. */}
      {dashboard && (
        <>
          <TeamDashboard key={totalsKey} />
          <PhotoGateStats key={totalsKey} />
          <IntegrationHealth key={session?.access_token} />
          <AutomaticQueue />
        </>
      )}
      {(page === 'review' || page === 'private-map') && <TeamReportsSection />}
      {page === 'audit' && <OperationalRegistryPage key={session?.access_token} />}
      {page === 'admin' &&
        (team.canAdmin ? (
          <OperationsPage key={session?.access_token} />
        ) : (
          <section className="panel narrow">
            <h1>Acesso restrito</h1>
            <p>Esta página exige permissão de administrador.</p>
            <a className="button secondary" href="#/app/dashboard">
              Voltar ao painel
            </a>
          </section>
        ))}
      {page === 'private-not-found' && (
        <section className="panel narrow">
          <h1>Página não encontrada</h1>
          <p>Esta página não existe na área da equipe.</p>
          <a className="button" href="#/app/dashboard">
            Abrir o painel
          </a>
        </section>
      )}
      {page === 'private-detail' && route.eventId && <PrivateEventDetail id={route.eventId} />}
      {page === 'private-events' && <TeamEventsList />}
    </>
  );
}

/** Antes da área da equipe: entrar, conferir a permissão ou explicar o bloqueio. */
function TeamAccess() {
  const { session, sessionReady, isVisitor, team } = useAppState();
  return (
    <>
      {/* Antes de ler a sessão, não mostra o formulário de entrada a quem já entrou. */}
      {!sessionReady && (
        <p role="status" className="loading-line">
          Verificando acesso…
        </p>
      )}
      {sessionReady && (!session || isVisitor) && <SignIn team />}
      {session && !isVisitor && !team.accessCurrent && !team.accessError && (
        <p role="status" className="loading-line">
          Verificando acesso…
        </p>
      )}
      {team.accessError && (
        <p className="error" role="alert">
          {team.accessError}{' '}
          <button className="secondary compact" onClick={() => void auth.signOut()}>
            Sair
          </button>
        </p>
      )}
      {team.accessCurrent && (
        <section className="panel narrow">
          <h1>Acesso restrito</h1>
          <p>
            Sua conta não tem permissão de revisor ou administrador. Peça acesso a quem administra o
            UrMind ou entre com outra conta.
          </p>
          <button className="secondary" onClick={() => void auth.signOut()}>
            <LogOut size={16} aria-hidden="true" /> Sair
          </button>
        </section>
      )}
    </>
  );
}

function PrivateEventDetail({ id }: { id: string }) {
  const { teamData, refresh } = useAppState();
  return <EventDetail key={id} id={id} onChanged={refresh} refreshAt={teamData.changed.at} />;
}

/** Ocorrências criadas a partir dos relatos, em páginas de 100. */
function TeamEventsList() {
  const { teamData, refresh } = useAppState();
  const { events, loading, apiError, cursors, setCursors, realtime } = teamData;
  return (
    <>
      <EventsPage
        events={events}
        loading={loading}
        error={apiError}
        onReload={realtime === 'connected' ? undefined : refresh}
      />
      {(cursors.length > 1 || (events?.length ?? 0) >= 100) && (
        <nav className="actions pager" aria-label="Páginas de ocorrências">
          <button
            className="secondary"
            disabled={loading || cursors.length === 1}
            onClick={() => setCursors(cursors.slice(0, -1))}
          >
            Anterior
          </button>
          <button
            className="secondary"
            disabled={loading || !events || events.length < 100}
            onClick={() => {
              const last = events!.at(-1)!;
              setCursors([...cursors, `${last.occurred_at}|${last.id}`]);
            }}
          >
            Próxima
          </button>
          <span>Página {cursors.length}</span>
        </nav>
      )}
    </>
  );
}
