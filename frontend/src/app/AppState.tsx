import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import { useRegisterSW } from 'virtual:pwa-register/react';
import { currentSurface } from '../surface';
import { isTeamPage, pageHref, parseRoute, type Page } from './routes';
import { useCaptureProcessing } from './hooks/useCaptureProcessing';
import { useDrafts } from './hooks/useDrafts';
import { useHashRoute } from './hooks/useHashRoute';
import { useOnline } from './hooks/useOnline';
import { usePublicEvents, useSystemStatus } from './hooks/usePublicData';
import { usePublicRefresh, useTaxonomyLabels } from './hooks/usePublicRefresh';
import { useReports } from './hooks/useReports';
import { useReportUpload } from './hooks/useReportUpload';
import { useSession } from './hooks/useSession';
import { useTeamAccess } from './hooks/useTeamAccess';
import { useTeamData } from './hooks/useTeamData';

/**
 * Estado compartilhado do app num lugar só: rota, sessão, permissões da equipe,
 * rascunhos, relatos, acompanhamento do envio e dados públicos. As páginas leem o que
 * precisam com `useAppState()`; cada assunto mora no próprio hook em `app/hooks/`.
 */
function useAppController() {
  const [route, setRoute] = useHashRoute();
  const page = route.page;
  const privatePage = isTeamPage(page);
  const adminSurface = currentSurface() === 'admin';
  const [notice, setNotice] = useState('');
  // Cada nova `revision` pede a releitura dos dados (relatos, ocorrências, métricas).
  const [revision, setRevision] = useState(0);
  const refresh = () => setRevision((value) => value + 1);
  const online = useOnline();
  const drafts = useDrafts(setNotice);

  // Havia sessão antes deste evento: só a saída de uma sessão real tira a pessoa do relato.
  const hadSession = useRef(false);
  const {
    session,
    setSession,
    isVisitor,
    ready: sessionReady,
  } = useSession({
    onChange: (next) => {
      const ended = !next && hadSession.current;
      hadSession.current = Boolean(next);
      processing.abort();
      upload.releaseFor(next);
      processing.clear();
      setNotice('');
      if (ended && parseRoute().page === 'processing') {
        window.location.replace('#/registrar');
      }
    },
    onDispose: () => {
      upload.abort();
      processing.abort();
    },
  });
  const team = useTeamAccess(session, isVisitor, privatePage);
  const processing = useCaptureProcessing({ route, session, setRoute, setNotice, refresh });
  const reports = useReports({
    page,
    route,
    session,
    canReview: team.canReview,
    revision,
    processingRetry: processing.retryCount,
    retryProcessing: processing.retry,
    refresh,
  });
  const teamData = useTeamData({
    session,
    page,
    privatePage,
    canReview: team.canReview,
    showReports: reports.showReports,
    revision,
    setRevision,
  });
  const upload = useReportUpload({
    session,
    setSession,
    setRoute,
    setNotice,
    setDraftError: drafts.setError,
    reloadDrafts: drafts.reload,
    refresh,
  });

  useEffect(() => {
    reports.setCursors([null]);
    teamData.setCursors([null]);
    // Página nova começa do topo, não na rolagem da anterior.
    window.scrollTo({ top: 0 });
    // Aviso de uma página não acompanha a pessoa para a próxima.
    setNotice('');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page]);

  useTaxonomyLabels();
  const [publicRevision, refreshPublic] = usePublicRefresh(revision);
  const [publicFilters, setPublicFilters] = useState({ urmind_class: '', status: '' });
  // Diagnóstico técnico (API, banco, detector) só na página Sistema, fora do fluxo do cidadão.
  const status = useSystemStatus(publicRevision, page === 'settings');
  const events = usePublicEvents(publicRevision, publicFilters);
  const {
    needRefresh: [needRefresh],
    updateServiceWorker,
  } = useRegisterSW();

  function navigate(next: Page) {
    location.hash = pageHref(next);
    setRoute({ page: next });
    setNotice('');
  }

  return {
    route,
    page,
    navigate,
    privatePage,
    adminSurface,
    online,
    notice,
    setNotice,
    session,
    /** A sessão já foi lida (com ou sem conta): antes disso, as telas mostram carregando. */
    sessionReady,
    isVisitor,
    team,
    revision,
    refresh,
    publicRevision,
    refreshPublic,
    publicData: {
      events: events.data,
      loading: events.loading,
      error: events.error,
      filters: publicFilters,
      setFilters: setPublicFilters,
      status: status.data,
      statusError: status.error,
    },
    drafts: {
      ...drafts,
      sending: upload.sending,
      send: upload.send,
      /** Abre o registro de um relato novo, sem rascunho em edição. */
      newCapture: () => {
        drafts.setEditing(undefined);
        navigate('capture');
      },
    },
    processing,
    reports,
    teamData,
    serviceWorker: {
      needRefresh,
      update: () => void updateServiceWorker(true),
    },
  };
}

export type AppState = ReturnType<typeof useAppController>;

const AppStateContext = createContext<AppState | null>(null);

export function AppStateProvider({ children }: { children: ReactNode }) {
  const state = useAppController();
  return <AppStateContext.Provider value={state}>{children}</AppStateContext.Provider>;
}

/** Estado compartilhado do app, para qualquer componente dentro de `AppStateProvider`. */
export function useAppState(): AppState {
  const state = useContext(AppStateContext);
  if (!state) throw new Error('useAppState precisa estar dentro de AppStateProvider.');
  return state;
}
