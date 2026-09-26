import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react';
import {
  BadgeCheck,
  Camera,
  ChartNoAxesCombined,
  ClipboardList,
  FileImage,
  LayoutDashboard,
  Map,
  ScanEye,
  ScanLine,
  Settings2,
} from 'lucide-react';
import { useRegisterSW } from 'virtual:pwa-register/react';
import { Photo } from './components/Photo';
import { api, UnauthorizedError } from './services/api';
import { auth, subscribeToEventChanges, type RealtimeStatus } from './services/auth';
import {
  PublicEventDetailPage,
  PublicEventsPage,
  OwnReportDetail,
  PublicHome,
  PublicMapPage,
  PublicSystemPage,
  PublicTransparencyPage,
  PublicDemoPage,
  TaxonomyPanel,
  usePublicEvents,
  useSystemStatus,
} from './pages/public/PublicPages';
import type { Session } from '@supabase/supabase-js';
import { drafts, type CaptureDraft } from './services/drafts';
import {
  reportLabels,
  type CaptureMarker,
  type CaptureProcessing,
  type UrbanEvent,
} from './domain/contracts';
import {
  registerTaxonomy,
  filterMapRecords,
  familyFor,
  labelFor,
  type MapFilters,
} from './domain/public';
import { publicApi } from './services/publicApi';

const CapturePage = lazy(() =>
  import('./pages/CapturePage').then((m) => ({ default: m.CapturePage })),
);
const LiveDetectionPage = lazy(() =>
  import('./pages/LiveDetectionPage').then((m) => ({ default: m.LiveDetectionPage })),
);
const UrbanMap = lazy(() => import('./components/UrbanMap'));
const EventsPage = lazy(() =>
  import('./pages/EventsPage').then((m) => ({ default: m.EventsPage })),
);
const EventDetail = lazy(() =>
  import('./components/EventDetail').then((m) => ({ default: m.EventDetail })),
);
const CaptureReviewPanel = lazy(() =>
  import('./components/EventDetail').then((m) => ({ default: m.CaptureReviewPanel })),
);
const OwnerReportTimeline = lazy(() =>
  import('./components/EventDetail').then((m) => ({ default: m.OwnerReportTimeline })),
);
const SignIn = lazy(() => import('./components/SignIn').then((m) => ({ default: m.SignIn })));
const OperationsPage = lazy(() =>
  import('./pages/OperationsPage').then((m) => ({ default: m.OperationsPage })),
);
const OperationalRegistryPage = lazy(() =>
  import('./pages/OperationsPage').then((m) => ({ default: m.OperationalRegistryPage })),
);
const ReportIndicators = lazy(() =>
  import('./pages/OperationsPage').then((m) => ({ default: m.ReportIndicators })),
);
const GroundTruthPage = lazy(() =>
  import('./pages/OperationsPage').then((m) => ({ default: m.GroundTruthPage })),
);

/** Releitura do painel público: curta o bastante para parecer vivo, longa o bastante
 * para não pesar na API. */
const PUBLIC_REFRESH_MS = 30_000;

/** Etapa do processamento em palavras de quem enviou; o código fica em `data-stage`. */
const processingLabels: Record<CaptureProcessing['status'], string> = {
  received: 'Recebido',
  queued: 'Recebido, na fila de análise',
  processing_detection: 'Processando: procurando o problema na foto',
  detection_completed: 'Processando: detecção concluída',
  building_event: 'Processando: consolidando a ocorrência',
  enriching_context: 'Processando: consultando o contexto do local',
  building_features: 'Processando: preparando a avaliação',
  assessing: 'Processando: avaliando a prioridade',
  completed: 'Analisado',
  no_supported_detection: 'Analisado: nenhum problema reconhecido',
  no_event: 'Analisado: sem ocorrência consolidada',
  needs_review: 'Aguardando revisão humana',
  failed: 'Falha no processamento',
  model_not_available: 'Recebido, sem análise automática disponível',
  location_required: 'Necessita localização',
};

const navigation = [
  {
    id: 'my-reports',
    label: 'Meus relatos',
    icon: ClipboardList,
    href: '#/meus-relatos',
    section: 'operação',
  },
  {
    id: 'about',
    label: 'Sobre e privacidade',
    icon: BadgeCheck,
    href: '#/sobre',
    section: 'público',
  },
  { id: 'overview', label: 'Início', icon: LayoutDashboard, href: '#/', section: 'público' },
  { id: 'map', label: 'Mapa operacional', icon: Map, href: '#/map', section: 'público' },
  { id: 'events', label: 'Ocorrências', icon: ClipboardList, href: '#/events', section: 'público' },
  {
    id: 'analysis',
    label: 'Transparência',
    icon: ChartNoAxesCombined,
    href: '#/transparency',
    section: 'público',
  },
  { id: 'demo', label: 'Exemplos revisados', icon: BadgeCheck, href: '#/demo', section: 'público' },
  { id: 'settings', label: 'Sistema', icon: Settings2, href: '#/system', section: 'público' },
  {
    id: 'capture',
    label: 'Registrar evidência',
    icon: Camera,
    href: '#/capture',
    section: 'operação',
  },
  {
    id: 'live-detection',
    label: 'Detecção ao vivo',
    icon: ScanEye,
    href: '#/deteccao-ao-vivo',
    section: 'operação',
  },
  {
    id: 'drafts',
    label: 'Rascunhos locais',
    icon: FileImage,
    href: '#/drafts',
    section: 'operação',
  },
  { id: 'review', label: 'Revisão', icon: ScanLine, href: '#/review', section: 'operação' },
] as const;
const privateNavigation = [
  { id: 'dashboard', label: 'Painel interno', href: '#/app/dashboard' },
  { id: 'private-events', label: 'Ocorrências internas', href: '#/app/eventos' },
  { id: 'review', label: 'Revisões', href: '#/app/reviews' },
  { id: 'ground-truth', label: 'Ground truth', href: '#/app/ground-truth' },
  { id: 'private-map', label: 'Mapa interno', href: '#/app/mapa' },
  { id: 'admin', label: 'Administração', href: '#/app/admin' },
  { id: 'models', label: 'Modelos', href: '#/app/modelos' },
  { id: 'audit', label: 'Auditoria', href: '#/app/auditoria' },
] as const;
const internalTabs = [
  { id: 'dashboard', label: 'Painel' },
  { id: 'review', label: 'Fila' },
  { id: 'private-map', label: 'Mapa' },
  { id: 'ground-truth', label: 'Relatos/GT' },
] as const;
type Page =
  | (typeof navigation)[number]['id']
  | (typeof privateNavigation)[number]['id']
  | 'event-detail'
  | 'private-detail'
  | 'private-not-found'
  | 'login'
  | 'processing';
interface Route {
  page: Page;
  eventId?: string;
  captureId?: string;
  protocol?: string;
}
/** Rotas por hash: `#/`, `#/live`, `#/events`, `#/events/<id>`… sem dependência nova. */
function parseRoute(): Route {
  const [, first = '', second = '', third = ''] = location.hash
    .split('?')[0]
    .replace(/^#\/?/, '/')
    .split('/');
  const path = `#/${first}`;
  if ((first === 'events' || first === 'resultado') && second)
    return { page: 'event-detail', eventId: second };
  if (first === 'processando' && second) return { page: 'processing', captureId: second };
  if (first === 'relato' && second) return { page: 'processing', protocol: second };
  if (first === 'registrar') return { page: 'capture' };
  // A antiga página do Scout (hardware fora do escopo) leva à detecção no próprio aparelho.
  if (first === 'live') return { page: 'live-detection' };
  if (first === 'mapa') return { page: 'map' };
  if (first === 'transparency') return { page: 'analysis' };
  if (first === 'system') return { page: 'settings' };
  if (first === 'login') return { page: 'login' };
  if (first === 'app') {
    if (!second) return { page: 'dashboard' };
    if (second === 'eventos' && third) return { page: 'private-detail', eventId: third };
    if (second === 'relato' && third) return { page: 'review', captureId: third };
    return {
      page:
        privateNavigation.find((item) => item.href === `#/app/${second}`)?.id ??
        'private-not-found',
    };
  }
  const found = navigation.find((item) => item.href === path);
  return { page: found?.id ?? 'overview' };
}
function useOnline() {
  const [online, setOnline] = useState(navigator.onLine);
  useEffect(() => {
    const update = () => setOnline(navigator.onLine);
    addEventListener('online', update);
    addEventListener('offline', update);
    return () => {
      removeEventListener('online', update);
      removeEventListener('offline', update);
    };
  }, []);
  return online;
}
export default function App() {
  const [route, setRoute] = useState<Route>(parseRoute);
  const page = route.page;
  const [publicFilters, setPublicFilters] = useState({ urmind_class: '', status: '' });
  const [localDrafts, setLocalDrafts] = useState<CaptureDraft[]>([]);
  const [draftError, setDraftError] = useState('');
  const [draftsLoaded, setDraftsLoaded] = useState(false);
  const [editing, setEditing] = useState<CaptureDraft>();
  const [notice, setNotice] = useState('');
  const [events, setEvents] = useState<UrbanEvent[] | null>(null);
  const [eventCursors, setEventCursors] = useState<Array<string | null>>([null]);
  const [reportCursors, setReportCursors] = useState<Array<string | null>>([null]);
  const [loading, setLoading] = useState(false);
  const [apiError, setApiError] = useState('');
  const [revision, setRevision] = useState(0);
  const [publicRevision, setPublicRevision] = useState(0);
  const [session, setSession] = useState<Session | null>(null);
  const isVisitor = session?.user.is_anonymous === true;
  const [access, setAccess] = useState<{ token: string; review: boolean; admin: boolean } | null>(
    null,
  );
  const [accessError, setAccessError] = useState('');
  const [metrics, setMetrics] = useState<Awaited<ReturnType<typeof api.opsMetrics>> | null>(null);
  const [metricsError, setMetricsError] = useState('');
  const privatePage =
    page === 'login' ||
    page === 'private-detail' ||
    page === 'private-not-found' ||
    privateNavigation.some((item) => item.id === page);
  const accessCurrent = Boolean(session && access?.token === session.access_token);
  const canReview = accessCurrent && access?.review === true && !isVisitor;
  const canAdmin = canReview && access?.admin === true;
  const [realtime, setRealtime] = useState<RealtimeStatus>('unavailable');
  const [changed, setChanged] = useState<{ eventId: string | null; at: number }>({
    eventId: null,
    at: 0,
  });
  const [sending, setSending] = useState('');
  const uploadOperation = useRef<{ controller: AbortController; ownerId: string | null } | null>(
    null,
  );
  const processingController = useRef<AbortController | null>(null);
  const [storedCaptureStatus, setCaptureStatus] = useState<CaptureProcessing | null>(null);
  const [storedProcessingError, setProcessingError] = useState('');
  const [processingScope, setProcessingScope] = useState('');
  const currentProcessingScope = session ? `${session.user.id}:${route.captureId ?? ''}` : '';
  const captureStatus =
    currentProcessingScope && processingScope === currentProcessingScope
      ? storedCaptureStatus
      : null;
  const processingError =
    currentProcessingScope && processingScope === currentProcessingScope
      ? storedProcessingError
      : '';
  const [processingRetry, setProcessingRetry] = useState(0);
  const [reports, setReports] = useState<{
    owner: string;
    onlyMine: boolean;
    data: CaptureMarker[];
  } | null>(null);
  const [reportError, setReportError] = useState('');
  const [selectedReport, setSelectedReport] = useState<string | null>(null);
  const [queueFilters, setQueueFilters] = useState<MapFilters>({
    status: '',
    family: '',
    issue: '',
    from: '',
    to: '',
  });
  const [queueConflict, setQueueConflict] = useState(false);
  const [queuePending, setQueuePending] = useState(false);
  const [reportPhoto, setReportPhoto] = useState<{ owner: string; id: string; url: string } | null>(
    null,
  );
  const [manualPoint, setManualPoint] = useState<{ latitude: number; longitude: number } | null>(
    null,
  );
  const locationOperation = useRef<AbortController | null>(null);
  // Páginas do cidadão mostram só os relatos da própria sessão (propriedade conferida
  // no servidor); a equipe vê os de todos apenas na área interna.
  const onlyMine = ['my-reports', 'map', 'overview', 'processing'].includes(page);
  const ownReports =
    session && reports?.owner === session.user.id && reports.onlyMine === onlyMine
      ? reports.data
      : [];
  const showReports =
    page === 'processing' ||
    page === 'private-map' ||
    page === 'map' ||
    page === 'overview' ||
    page === 'my-reports' ||
    (canReview && (page === 'review' || page === 'dashboard'));
  // Início e Mapa mostram os relatos dentro das próprias páginas.
  const reportSection = showReports && page !== 'map' && page !== 'overview';
  const listedReports =
    page === 'review'
      ? filterMapRecords(ownReports, queueFilters)
          .filter(
            (row) =>
              (!queueConflict || row.location_conflict) &&
              (!queuePending || row.photo_gate?.status === 'NEEDS_REVIEW'),
          )
          .sort(
            (a, b) =>
              (b.priority_score ?? -1) - (a.priority_score ?? -1) ||
              (a.created_at ?? '').localeCompare(b.created_at ?? ''),
          )
      : ownReports;
  useEffect(() => {
    if (page === 'review' && canReview && route.captureId) setSelectedReport(route.captureId);
    // O relato que acabou de ser enviado (ou reaberto pelo link) fica em foco no mapa.
    if (page === 'processing' && route.captureId) setSelectedReport(route.captureId);
  }, [page, canReview, route.captureId]);
  useEffect(() => {
    locationOperation.current?.abort();
    setManualPoint(null);
    setSelectedReport(null);
    setReportPhoto(null);
    setReports(null);
    setReportCursors([null]);
    setEventCursors([null]);
  }, [session?.user.id]);
  useEffect(() => {
    setReportCursors([null]);
    setEventCursors([null]);
  }, [page]);
  useEffect(() => {
    const controller = new AbortController();
    if (!session || !showReports) return () => controller.abort();
    const owner = session.user.id;
    api
      .captureMarkers(controller.signal, onlyMine, reportCursors.at(-1) ?? null)
      .then((data) => {
        if (!controller.signal.aborted) {
          setReports({ owner, onlyMine, data });
          setReportError('');
        }
      })
      .catch(() => {
        if (!controller.signal.aborted) setReportError('Não foi possível consultar os relatos.');
      });
    return () => controller.abort();
  }, [session?.user.id, showReports, onlyMine, page, revision, processingRetry, reportCursors]);
  useEffect(() => {
    const controller = new AbortController();
    setReportPhoto(null);
    if (!session || !selectedReport) return () => controller.abort();
    const owner = session.user.id;
    api
      .captureImage(selectedReport, controller.signal)
      .then(({ image_url }) => {
        if (!controller.signal.aborted)
          setReportPhoto({ owner, id: selectedReport, url: image_url });
      })
      .catch(() => undefined);
    return () => controller.abort();
  }, [session?.user.id, selectedReport]);
  async function confirmMissingLocation() {
    if (!manualPoint || !route.captureId || !session) return;
    const controller = new AbortController();
    locationOperation.current?.abort();
    locationOperation.current = controller;
    try {
      await api.captureLocation(
        route.captureId,
        manualPoint.latitude,
        manualPoint.longitude,
        controller.signal,
      );
      if (!controller.signal.aborted) {
        setManualPoint(null);
        setProcessingRetry((v) => v + 1);
        setRevision((v) => v + 1);
      }
    } catch (error) {
      if (!controller.signal.aborted) setReportError((error as Error).message);
    }
  }
  const online = useOnline();
  const [, setTaxonomyVersion] = useState('');
  useEffect(() => {
    // Class labels come from the canonical taxonomy; static labels are only an offline fallback.
    const controller = new AbortController();
    publicApi
      .taxonomy(controller.signal)
      .then((taxonomy) => {
        registerTaxonomy(taxonomy);
        setTaxonomyVersion(taxonomy.taxonomy_version);
      })
      .catch(() => undefined);
    return () => controller.abort();
  }, []);
  // Diagnóstico técnico (API, banco, detector) só na página Sistema, fora do fluxo do cidadão.
  const { data: publicStatus, error: statusError } = useSystemStatus(
    publicRevision,
    page === 'settings',
  );
  const {
    data: publicEvents,
    loading: publicLoading,
    error: publicError,
  } = usePublicEvents(publicRevision, publicFilters);
  const {
    needRefresh: [needRefresh],
    updateServiceWorker,
  } = useRegisterSW();
  useEffect(() => {
    const change = () => setRoute(parseRoute());
    addEventListener('hashchange', change);
    return () => removeEventListener('hashchange', change);
  }, []);
  const reloadDrafts = useCallback(async () => {
    try {
      setLocalDrafts(await drafts.list());
      setDraftError('');
      setDraftsLoaded(true);
    } catch {
      setDraftsLoaded(false);
      setDraftError(
        'Armazenamento local indisponível. Verifique as permissões e o espaço do navegador.',
      );
    }
  }, []);
  useEffect(() => {
    void reloadDrafts();
  }, [reloadDrafts]);
  useEffect(() => {
    let changed = false;
    let disposed = false;
    void auth.session().then((initial) => {
      if (!changed && !disposed) setSession(initial);
    });
    const unsubscribe = auth.onChange((nextSession) => {
      changed = true;
      processingController.current?.abort();
      const upload = uploadOperation.current;
      if (upload && (!nextSession || upload.ownerId !== nextSession.user.id)) {
        upload.controller.abort();
        uploadOperation.current = null;
        setSending('');
      }
      setSession(nextSession);
      setCaptureStatus(null);
      setProcessingError('');
      setProcessingScope('');
      setNotice('');
      if (!nextSession && parseRoute().page === 'processing') {
        window.location.replace('#/registrar');
      }
    });
    return () => {
      disposed = true;
      unsubscribe();
      uploadOperation.current?.controller.abort();
      processingController.current?.abort();
    };
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    setAccess(null);
    setAccessError('');
    if (session && !isVisitor && privatePage) {
      api
        .me(controller.signal)
        .then((me) => {
          if (!controller.signal.aborted)
            setAccess({ token: session.access_token, review: me.can_review, admin: me.can_admin });
        })
        .catch((error: Error) => {
          if (!controller.signal.aborted) setAccessError(error.message);
        });
    }
    return () => controller.abort();
  }, [session, isVisitor, privatePage]);
  useEffect(() => {
    if (!session || (!privatePage && !showReports)) {
      setRealtime('unavailable');
      return;
    }
    let timer: number | undefined;
    const unsubscribe = subscribeToEventChanges(({ eventId }) => {
      // Rajadas (evento + risco + contexto) viram uma única recarga HTTP.
      window.clearTimeout(timer);
      timer = window.setTimeout(() => {
        setRevision((value) => value + 1);
        setChanged({ eventId, at: Date.now() });
      }, 600);
    }, setRealtime);
    return () => {
      window.clearTimeout(timer);
      unsubscribe();
    };
  }, [session?.user.id, canReview, privatePage, showReports]);
  useEffect(() => {
    const controller = new AbortController();
    setEvents(null);
    if (!canReview || !privatePage) return () => controller.abort();
    async function load() {
      setLoading(true);
      setApiError('');
      setEvents(null);
      try {
        const result = await api.health(controller.signal);
        if (controller.signal.aborted) return;
        if (result.database === 'not_configured') {
          setApiError('O Supabase/PostGIS ainda não está configurado.');
          return;
        }
        const data = await api.events(controller.signal, eventCursors.at(-1) ?? null);
        if (!controller.signal.aborted) setEvents(data);
      } catch (error) {
        if (!controller.signal.aborted) {
          setApiError(error instanceof Error ? error.message : 'Falha ao consultar API.');
        }
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }
    void load();
    return () => controller.abort();
  }, [revision, canReview, privatePage, eventCursors]);
  useEffect(() => {
    const controller = new AbortController();
    setMetrics(null);
    setMetricsError('');
    if (canReview && (page === 'dashboard' || page === 'login')) {
      api
        .opsMetrics(controller.signal)
        .then((data) => {
          if (!controller.signal.aborted) setMetrics(data);
        })
        .catch((error: Error) => {
          if (!controller.signal.aborted) setMetricsError(error.message);
        });
    }
    return () => controller.abort();
  }, [canReview, page, revision]);
  // O Realtime do Supabase exige sessão (as políticas de SELECT valem para
  // `authenticated`), então o visitante público não recebe aviso de mudança.
  // Uma releitura periódica cobre isso; pausada com a aba oculta para não
  // consultar a API em segundo plano por horas.
  useEffect(() => {
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') setPublicRevision((value) => value + 1);
    }, PUBLIC_REFRESH_MS);
    return () => window.clearInterval(timer);
  }, []);
  // Quem tem sessão recebe o aviso do Realtime: o painel público acompanha na hora.
  useEffect(() => setPublicRevision((value) => value + 1), [revision]);
  // Capture has no authenticated SELECT policy in Realtime. A bounded check for
  // the one active upload reports Worker failure without exposing other users'
  // captures or subscribing to a broad table publication.
  useEffect(() => {
    setCaptureStatus(null);
    setProcessingError('');
    setProcessingScope(currentProcessingScope);
    if (route.page !== 'processing' || !route.captureId || !session) return;
    const controller = new AbortController();
    processingController.current = controller;
    let attempts = 0;
    let finished = false;
    async function refresh() {
      if (controller.signal.aborted || finished) return;
      attempts += 1;
      try {
        const result = await api.captureProcessing(route.captureId!, controller.signal);
        if (controller.signal.aborted) return;
        setCaptureStatus(result);
        const notices: Record<string, string> = {
          received: 'Foto recebida.',
          location_required: 'Foto preservada. Selecione e confirme a localização no mapa.',
          queued: 'Foto recebida; aguardando processamento.',
          processing_detection: 'Foto em processamento visual experimental.',
          detection_completed: 'Detecção concluída; análise ainda em andamento.',
          building_event: 'Consolidando a ocorrência a partir das detecções.',
          enriching_context: 'Consultando o contexto disponível.',
          building_features: 'Construindo features e avaliação.',
          assessing: 'Concluindo a avaliação da ocorrência.',
          completed: 'Processamento concluído. A ocorrência já pode ser consultada.',
          no_supported_detection:
            'Nenhuma ocorrência das classes suportadas foi detectada nesta foto. A captura foi preservada.',
          no_event:
            'Houve detecção visual, mas nenhuma ocorrência foi consolidada. A captura foi preservada.',
          needs_review: 'A evidência precisa de revisão humana.',
          model_not_available:
            'Foto preservada, mas não há modelo experimental autorizado para inferência.',
          failed: 'O processamento falhou. A foto permanece preservada para verificação.',
        };
        setNotice(
          result.additional_evidence
            ? 'Foto anexada como evidência adicional. O ponto existente foi preservado; a equipe pode revisar o vínculo.'
            : notices[result.status],
        );
        if (
          [
            'completed',
            'no_supported_detection',
            'no_event',
            'needs_review',
            'model_not_available',
            'failed',
            'location_required',
          ].includes(result.status)
        ) {
          finished = true;
          setRevision((value) => value + 1);
        }
      } catch (error) {
        if (
          !controller.signal.aborted &&
          error instanceof Error &&
          error.message === 'Captura não encontrada ou sem acesso.'
        ) {
          setProcessingError(error.message);
          finished = true;
        }
        if (!controller.signal.aborted && attempts >= 24) {
          setProcessingError(
            error instanceof Error ? error.message : 'Falha ao consultar captura.',
          );
          finished = true;
        }
      }
      if (attempts >= 24 && !finished) {
        finished = true;
        if (!controller.signal.aborted) {
          setProcessingError('A análise ainda não foi concluída. Consulte novamente.');
        }
      }
    }
    void refresh();
    const timer = window.setInterval(() => void refresh(), 5_000);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, [route.page, route.captureId, session, processingRetry]);

  useEffect(() => {
    const controller = new AbortController();
    if (route.protocol && session) {
      api
        .captureByProtocol(route.protocol, controller.signal)
        .then((report) => {
          if (!controller.signal.aborted)
            setRoute((current) => ({ ...current, captureId: report.capture_id }));
        })
        .catch(() => {
          if (!controller.signal.aborted) setNotice('Relato não encontrado nesta sessão.');
        });
    }
    return () => controller.abort();
  }, [route.protocol, session]);
  function navigate(next: Page) {
    const target = navigation.find((item) => item.id === next);
    location.hash = target?.href ?? '#/';
    setRoute({ page: next });
    setNotice('');
  }
  function newCapture() {
    setEditing(undefined);
    navigate('capture');
  }
  async function send(draft: CaptureDraft) {
    uploadOperation.current?.controller.abort();
    const operation = { controller: new AbortController(), ownerId: session?.user.id ?? null };
    uploadOperation.current = operation;
    const isCurrent = () =>
      uploadOperation.current === operation && !operation.controller.signal.aborted;
    setSending(draft.id);
    setDraftError('');
    try {
      // Supabase anonymous Auth provides an owner-scoped JWT without giving a
      // visitor reviewer/admin privileges. The browser never receives a secret key.
      const active = await auth.ensureVisitorSession(
        operation.controller.signal,
        operation.ownerId,
        (id) => {
          if (isCurrent()) operation.ownerId = id;
        },
      );
      if (!isCurrent()) return;
      operation.ownerId = active.user.id;
      if (session?.access_token !== active.access_token) setSession(active);
      let additionalTo: string | undefined;
      if (draft.coordinate) {
        const nearby = await api.nearbyReports(
          draft.coordinate.latitude,
          draft.coordinate.longitude,
          operation.controller.signal,
          active.access_token,
        );
        if (!isCurrent()) return;
        if (
          nearby.length &&
          window.confirm(
            `Já existe um relato a aproximadamente ${nearby[0].distance_m} m. É o mesmo problema? Confirmar anexa sua foto como evidência, sem criar outro ponto.`,
          )
        ) {
          additionalTo = nearby[0].public_id;
        }
      }
      if (!isCurrent()) return;
      const result = await api.uploadPhoto(
        { ...draft, additional_to: additionalTo },
        operation.controller.signal,
        active.access_token,
      );
      if (!isCurrent()) return;
      location.hash = `#/processando/${result.id}`;
      setRoute({ page: 'processing', captureId: result.id });
      await drafts.remove(draft.id);
      if (!isCurrent()) return;
      await reloadDrafts();
      if (!isCurrent()) return;
      setNotice(
        result.additional_evidence
          ? 'Foto anexada como evidência adicional. O ponto existente foi preservado; a equipe pode revisar o vínculo.'
          : result.requires_manual_location
            ? 'Foto enviada. Sem localização: marque o ponto antes que ela vire ocorrência.'
            : result.created
              ? 'Foto enviada e registrada. O ponto já aparece no seu mapa; a análise segue abaixo.'
              : 'Esta foto já estava registrada; nada foi duplicado.',
      );
      setRevision((value) => value + 1);
    } catch (reason) {
      if (!isCurrent()) return;
      if (reason instanceof UnauthorizedError) setSession(null);
      setDraftError(
        `${(reason as Error).message} O rascunho continua salvo neste navegador para reenviar.`,
      );
    } finally {
      if (isCurrent()) {
        uploadOperation.current = null;
        setSending('');
      }
    }
  }
  async function remove(draft: CaptureDraft) {
    if (
      !window.confirm(
        'Excluir este rascunho e sua foto deste navegador? Esta ação não pode ser desfeita.',
      )
    )
      return;
    try {
      await drafts.remove(draft.id);
      await reloadDrafts();
      setNotice('Rascunho e foto excluídos deste navegador.');
    } catch {
      setDraftError('Não foi possível excluir o rascunho. Tente novamente.');
    }
  }
  return (
    <div className={`app-shell${privatePage && canReview ? ' internal-shell' : ''}`}>
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
      <aside className="sidebar">
        <a className="brand" href="#/">
          <img src="/icon.svg" alt="" />
          <span>
            ur<span className="brand-light">mind</span>
            <small>INTELIGÊNCIA URBANA</small>
          </span>
        </a>
        <div className="workspace-label">
          ESPAÇO DE TRABALHO <span>V1</span>
        </div>
        <nav aria-label="Navegação principal">
          {(['overview', 'capture', 'live-detection', 'my-reports', 'map'] as const)
            .map((id) => navigation.find((item) => item.id === id))
            .filter((item): item is NonNullable<typeof item> => item != null)
            .map((item) => (
              <a
                key={item.id}
                href={item.href}
                aria-current={page === item.id ? 'page' : undefined}
                onClick={() => {
                  setNotice('');
                  if (item.id === 'capture') setEditing(undefined);
                }}
              >
                <item.icon size={19} strokeWidth={1.6} />
                {item.label}
                {item.id === 'drafts' && draftsLoaded && localDrafts.length > 0 && (
                  <span className="nav-count">{localDrafts.length}</span>
                )}
              </a>
            ))}
        </nav>
        <div className="sidebar-bottom">
          <p>
            FECART <span>Projeto UrMind</span>
          </p>
        </div>
      </aside>
      <div className="workspace">
        <header className="topbar">
          <span>
            Observatório urbano <span className="topbar-separator">/</span>{' '}
            <strong>
              {privateNavigation.find((item) => item.id === page)?.label ??
                navigation.find((item) => item.id === page)?.label ??
                'Análise da ocorrência'}
            </strong>
          </span>
          <span className="network">
            <i className={online ? 'online' : ''} />
            {online ? 'Rede disponível' : 'Sem rede'}
          </span>
          {session && privatePage && canReview && (
            <span className="network" title="Supabase Realtime (Postgres Changes)">
              <i className={realtime === 'connected' ? 'online' : ''} />
              {realtime === 'connected'
                ? 'Tempo real ativo'
                : realtime === 'connecting'
                  ? 'Conectando tempo real…'
                  : 'Tempo real indisponível: use Atualizar'}
            </span>
          )}
        </header>
        <main id="main" tabIndex={-1}>
          <Suspense fallback={<p role="status">Carregando página…</p>}>
            {needRefresh && (
              <div className="notice">
                Uma atualização está disponível. Salve seu rascunho antes de atualizar.{' '}
                <button
                  className="secondary compact"
                  onClick={() => void updateServiceWorker(true)}
                >
                  Atualizar aplicativo
                </button>
              </div>
            )}
            {notice && (
              <p className="success" role="status">
                {notice}
              </p>
            )}
            {draftError && (
              <p className="error" role="alert">
                {draftError}
              </p>
            )}
            {page === 'overview' && (
              <PublicHome
                revision={publicRevision}
                events={publicEvents ?? []}
                reports={ownReports}
                hasSession={Boolean(session)}
                loading={publicLoading}
                error={publicError}
              />
            )}
            {page === 'map' && (
              <PublicMapPage
                key={session?.user.id ?? 'no-session'}
                events={publicEvents ?? []}
                reports={ownReports}
              />
            )}
            {page === 'events' && (
              <PublicEventsPage
                events={publicEvents ?? []}
                loading={publicLoading}
                error={publicError}
                filters={publicFilters}
                onFilters={setPublicFilters}
              />
            )}
            {page === 'event-detail' && route.eventId && (
              <PublicEventDetailPage
                key={`${route.eventId}:${session?.user.id ?? 'none'}`}
                id={route.eventId}
                revision={publicRevision}
              />
            )}
            {page === 'processing' && (
              <section className="panel" aria-live="polite">
                <h1>Seu relato</h1>
                {!session ? <p>Recuperando sessão segura desta captura…</p> : null}
                {captureStatus && (
                  <p role="status" className="stage" data-stage={captureStatus.status}>
                    Situação: <strong>{processingLabels[captureStatus.status]}</strong>
                  </p>
                )}
                {captureStatus?.protocol_code && (
                  <p>
                    Protocolo: <strong>{captureStatus.protocol_code}</strong>{' '}
                    <button
                      type="button"
                      onClick={() => {
                        void navigator.clipboard.writeText(captureStatus.protocol_code!).then(
                          () => setNotice('Protocolo copiado.'),
                          () => setNotice('Selecione o protocolo e copie manualmente.'),
                        );
                      }}
                    >
                      Copiar protocolo
                    </button>
                  </p>
                )}
                {captureStatus?.model_status === 'EXPERIMENTAL_SHADOW' && (
                  <p className="notice">
                    Análise experimental: o modelo ainda não foi aprovado para uso oficial.
                  </p>
                )}
                {captureStatus &&
                  ['completed', 'needs_review'].includes(captureStatus.status) &&
                  captureStatus.event_public_ids.map((id) => (
                    <p key={id}>
                      <a href={`#/resultado/${id}`}>Ver ocorrência no mapa</a>
                    </p>
                  ))}
                {processingError && <p role="alert">{processingError}</p>}
                {captureStatus && (
                  <OwnerReportTimeline
                    key={`${session?.user.id}:${captureStatus.capture_id}`}
                    id={captureStatus.capture_id}
                    revision={revision}
                  />
                )}
                <button
                  type="button"
                  className="secondary"
                  onClick={() => setProcessingRetry((value) => value + 1)}
                >
                  Consultar novamente
                </button>
              </section>
            )}
            {reportSection && session && (page !== 'private-map' || canReview) && (
              <section className="panel">
                {page === 'private-map' ? (
                  <h1>Gêmeo digital 2D</h1>
                ) : (
                  <h2>
                    {canReview && page !== 'my-reports' && page !== 'processing'
                      ? 'Relatos recebidos'
                      : 'Meus relatos'}
                  </h2>
                )}
                <p className="muted">
                  Um relato recebido ainda não é um problema confirmado. O ponto é a posição
                  informada, com a precisão do aparelho.
                </p>
                {page !== 'review' && (
                  <UrbanMap
                    events={ownReports}
                    allowExport={canReview}
                    selectedId={selectedReport}
                    onSelect={setSelectedReport}
                    onCloseDetail={() => setSelectedReport(null)}
                    detail={(() => {
                      const report = ownReports.find((row) => row.id === selectedReport);
                      if (!report) return null;
                      return (
                        <OwnReportDetail
                          report={report}
                          photoUrl={
                            reportPhoto?.owner === session.user.id && reportPhoto.id === report.id
                              ? reportPhoto.url
                              : null
                          }
                        >
                          {canReview && privatePage && (
                            <CaptureReviewPanel
                              key={report.id}
                              id={report.id}
                              onChanged={() => setRevision((value) => value + 1)}
                            />
                          )}
                          {!canReview && page !== 'processing' && (
                            <a href={`#/processando/${report.id}`}>Acompanhar relato</a>
                          )}
                        </OwnReportDetail>
                      );
                    })()}
                    onPickLocation={
                      captureStatus?.status === 'location_required'
                        ? (latitude, longitude) => setManualPoint({ latitude, longitude })
                        : undefined
                    }
                  />
                )}
                {captureStatus?.status === 'location_required' && (
                  <button disabled={!manualPoint} onClick={() => void confirmMissingLocation()}>
                    Confirmar localização do relato
                  </button>
                )}
                {reportError && <p role="alert">{reportError}</p>}
                {page === 'review' && canReview && (
                  <div className="filters panel" aria-label="Filtros da fila de relatos">
                    <label>
                      Status do relato
                      <select
                        value={queueFilters.status}
                        onChange={(e) =>
                          setQueueFilters({ ...queueFilters, status: e.target.value })
                        }
                      >
                        <option value="">Todos</option>
                        {Object.entries(reportLabels).map(([code, label]) => (
                          <option key={code} value={code}>
                            {label}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label>
                      Família do relato
                      <select
                        value={queueFilters.family}
                        onChange={(e) =>
                          setQueueFilters({ ...queueFilters, family: e.target.value })
                        }
                      >
                        <option value="">Todas</option>
                        {[
                          ...new Set(
                            ownReports.map((r) => familyFor(r.urmind_class)).filter(Boolean),
                          ),
                        ].map((family) => (
                          <option key={family}>{family}</option>
                        ))}
                      </select>
                    </label>
                    <label>
                      Classe do relato
                      <select
                        value={queueFilters.issue}
                        onChange={(e) =>
                          setQueueFilters({ ...queueFilters, issue: e.target.value })
                        }
                      >
                        <option value="">Todas</option>
                        {[
                          ...new Set(
                            ownReports
                              .map((r) => r.urmind_class)
                              .filter((v): v is string => Boolean(v)),
                          ),
                        ].map((code) => (
                          <option key={code} value={code}>
                            {labelFor(code)}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label>
                      Recebido desde (UTC)
                      <input
                        type="date"
                        value={queueFilters.from}
                        onChange={(e) => setQueueFilters({ ...queueFilters, from: e.target.value })}
                      />
                    </label>
                    <label>
                      Recebido até (UTC)
                      <input
                        type="date"
                        value={queueFilters.to}
                        onChange={(e) => setQueueFilters({ ...queueFilters, to: e.target.value })}
                      />
                    </label>
                    <label>
                      <input
                        type="checkbox"
                        checked={queueConflict}
                        onChange={(e) => setQueueConflict(e.target.checked)}
                      />
                      Conflito de localização
                    </label>
                    <label>
                      <input
                        type="checkbox"
                        checked={queuePending}
                        onChange={(e) => setQueuePending(e.target.checked)}
                      />
                      Verificação pendente
                    </label>
                    <p>
                      {listedReports.length} relatos carregados. Prioridade disponível primeiro; em
                      seguida, mais antigos.
                    </p>
                  </div>
                )}
                {page === 'review' &&
                  canReview &&
                  selectedReport &&
                  (route.captureId === selectedReport ||
                    ownReports.some((report) => report.id === selectedReport)) && (
                    <CaptureReviewPanel
                      key={selectedReport}
                      id={selectedReport}
                      onChanged={() => setRevision((value) => value + 1)}
                    />
                  )}
                <ul>
                  {listedReports.map((report) => (
                    <li key={report.id}>
                      <button onClick={() => setSelectedReport(report.id)}>
                        {reportLabels[report.report_status]}
                      </button>
                      {report.user_description && <p>{report.user_description}</p>}
                      {report.protocol_code && (
                        <a
                          href={
                            canReview && privatePage
                              ? `#/app/relato/${report.id}`
                              : `#/relato/${report.protocol_code}`
                          }
                        >
                          {report.protocol_code}
                        </a>
                      )}
                      {report.created_at && (
                        <time dateTime={report.created_at}>
                          {new Date(report.created_at).toLocaleString('pt-BR')}
                        </time>
                      )}
                      {report.report_status === 'location_required' && (
                        <a href={`#/processando/${report.id}`}>Informar localização</a>
                      )}
                      {report.location_conflict && (
                        <p>GPS do dispositivo e EXIF divergentes — necessita revisão.</p>
                      )}
                      {report.event_public_id && (
                        <a href={`#/resultado/${report.event_public_id}`}>Ver análise</a>
                      )}
                      {page === 'my-reports' && (
                        <OwnerReportTimeline
                          key={`${session.user.id}:${report.id}`}
                          id={report.id}
                          revision={revision}
                        />
                      )}
                    </li>
                  ))}
                </ul>
                <div className="actions" aria-label="Páginas de relatos">
                  <button
                    disabled={reportCursors.length === 1}
                    onClick={() => setReportCursors(reportCursors.slice(0, -1))}
                  >
                    Anterior
                  </button>
                  <button
                    disabled={ownReports.length < 100 || !ownReports.at(-1)?.created_at}
                    onClick={() => {
                      const last = ownReports.at(-1)!;
                      setReportCursors([...reportCursors, `${last.created_at}|${last.id}`]);
                    }}
                  >
                    Próxima
                  </button>
                  <span>
                    Página {reportCursors.length}; filtros visuais aplicados a esta página. A
                    exportação consulta todos os relatos correspondentes.
                  </span>
                </div>
              </section>
            )}
            {page === 'analysis' && <PublicTransparencyPage revision={publicRevision} />}
            {page === 'my-reports' && !session && (
              <section className="panel">
                <h1>Meus relatos</h1>
                <p>
                  Abra a sessão usada para enviar os relatos. Relatos de outro visitante não são
                  exibidos.
                </p>
                <a href="#/registrar">Registrar problema</a>
              </section>
            )}
            {page === 'about' && (
              <>
                <section className="panel">
                  <h1>Sobre o UrMind</h1>
                  <p>
                    Fotos e localização são armazenadas como evidência privada. Você e a equipe
                    autorizada podem consultá-las. A publicação depende de revisão humana e usa uma
                    cópia sem metadados sensíveis. Não fotografe documentos, rostos em primeiro
                    plano ou placas de veículos.
                  </p>
                  <p>
                    Um relato recebido não é um problema confirmado pela IA. Classes em
                    desenvolvimento não são reconhecidas automaticamente.
                  </p>
                </section>
                <PublicTransparencyPage revision={publicRevision} />
              </>
            )}
            {page === 'demo' && <PublicDemoPage revision={publicRevision} />}
            {page === 'settings' && (
              <PublicSystemPage
                status={publicStatus}
                error={statusError}
                onReload={() => setPublicRevision((value) => value + 1)}
              />
            )}
            {page === 'capture' && (
              <CapturePage
                key={editing?.id || 'new'}
                initial={editing}
                onSaved={async (saved) => {
                  setEditing(undefined);
                  navigate('drafts');
                  await reloadDrafts();
                  await send(saved);
                }}
              />
            )}
            {page === 'live-detection' && (
              // Remontada por sessão: logout ou troca de conta encerra câmera e modelo.
              <LiveDetectionPage
                key={session?.user.id ?? 'no-session'}
                onOpenDraft={async (draft) => {
                  setEditing(draft);
                  navigate('capture');
                  await reloadDrafts();
                }}
              />
            )}
            {page === 'drafts' && (
              <>
                <div className="page-heading">
                  <div>
                    <p className="eyebrow">EVIDÊNCIAS / ARMAZENAMENTO LOCAL</p>
                    <h1>Rascunhos locais</h1>
                    <p>Fotos reais, guardadas neste navegador para continuar depois.</p>
                  </div>
                  <button onClick={newCapture}>Novo rascunho</button>
                </div>
                <p className="notice">
                  Fotos que ainda não chegaram ao UrMind: sem sessão ou sem rede. Rascunho não é
                  ocorrência nem detecção. Limpar os dados do navegador remove as fotos.
                </p>
                {!session && <SignIn />}
                {!localDrafts.length ? (
                  <section className="panel empty">
                    <FileImage size={32} />
                    <h2>
                      {draftsLoaded
                        ? 'Seu próximo registro começa aqui'
                        : 'Consultando armazenamento local'}
                    </h2>
                    <p>
                      {draftsLoaded
                        ? 'Adicione uma fotografia para criar seu primeiro rascunho.'
                        : 'Os rascunhos serão exibidos quando a leitura estiver disponível.'}
                    </p>
                    <button onClick={newCapture}>Registrar evidência</button>
                  </section>
                ) : (
                  <div className="draft-grid">
                    {localDrafts.map((draft) => (
                      <article className="panel draft-card" key={draft.id}>
                        <Photo blob={draft.photo} alt={`Evidência: ${draft.filename}`} />
                        <div>
                          <span className="badge">Rascunho local</span>
                          <h2>{draft.filename}</h2>
                          <p>{new Date(draft.created_at).toLocaleString('pt-BR')}</p>
                          <p>
                            {draft.coordinate ? 'Localização definida' : 'Localização pendente'}
                          </p>
                          <div className="actions">
                            <button
                              className="secondary"
                              onClick={() => {
                                setEditing(draft);
                                navigate('capture');
                              }}
                            >
                              Continuar edição
                            </button>
                            <button
                              disabled={sending === draft.id}
                              onClick={() => void send(draft)}
                            >
                              {sending === draft.id ? 'Enviando…' : 'Enviar'}
                            </button>
                            <button
                              className="text-button danger"
                              onClick={() => void remove(draft)}
                            >
                              Excluir
                            </button>
                          </div>
                        </div>
                      </article>
                    ))}
                  </div>
                )}
              </>
            )}
            {privatePage && (!session || isVisitor) && <SignIn />}
            {privatePage && session && !isVisitor && !accessCurrent && !accessError && (
              <p role="status">Verificando acesso interno…</p>
            )}
            {privatePage && accessError && (
              <p className="error" role="alert">
                {accessError}{' '}
                <button className="secondary" onClick={() => void auth.signOut()}>
                  Sair
                </button>
              </p>
            )}
            {privatePage && accessCurrent && !canReview && (
              <section className="panel">
                <h1>Acesso restrito</h1>
                <p>Sua conta não tem permissão de revisor ou administrador.</p>
                <button className="secondary" onClick={() => void auth.signOut()}>
                  Sair
                </button>
              </section>
            )}
            {privatePage && canReview && (
              <>
                <nav className="internal-navigation" aria-label="Navegação interna">
                  <span className="internal-badge">Área interna</span>
                  {internalTabs.map((item) => (
                    <a
                      key={item.id}
                      href={privateNavigation.find((entry) => entry.id === item.id)!.href}
                      aria-current={page === item.id ? 'page' : undefined}
                    >
                      {item.label}
                    </a>
                  ))}
                  <details>
                    <summary>Mais</summary>
                    <div className="internal-more">
                      {privateNavigation
                        .filter(
                          (item) =>
                            !['dashboard', 'review', 'private-map', 'ground-truth'].includes(
                              item.id,
                            ) &&
                            (item.id !== 'admin' || canAdmin),
                        )
                        .map((item) => (
                          <a key={item.id} href={item.href}>
                            {item.label}
                          </a>
                        ))}
                      <a href="#/">Área do cliente</a>
                      <button className="text-button" onClick={() => void auth.signOut()}>
                        Sair
                      </button>
                    </div>
                  </details>
                </nav>
                {(page === 'dashboard' || page === 'login') && (
                  <section className="panel">
                    <h1>Painel interno</h1>
                    <p>
                      Página de até 100 ocorrências. Use os cursores para percorrer o histórico; os
                      indicadores agregados abaixo consultam o banco.
                    </p>
                    {loading && <p role="status">Carregando ocorrências…</p>}
                    {apiError && <p role="alert">{apiError}</p>}
                    {events && (
                      <dl>
                        <dt>Ocorrências no recorte</dt>
                        <dd>{events.length}</dd>
                        <dt>Em revisão ou triagem</dt>
                        <dd>
                          {
                            events.filter((event) =>
                              ['review', 'triage_required'].includes(event.status),
                            ).length
                          }
                        </dd>
                        <dt>Confirmadas</dt>
                        <dd>{events.filter((event) => event.status === 'confirmed').length}</dd>
                      </dl>
                    )}
                    <a href="#/app/reviews">Abrir revisões</a>
                    <h2>Fila de processamento</h2>
                    {metricsError && <p role="alert">Métricas indisponíveis: {metricsError}</p>}
                    {!metrics && !metricsError && <p role="status">Consultando fila…</p>}
                    {metrics && (
                      <>
                        <dl>
                          <dt>Pendentes</dt>
                          <dd>{metrics.queue.pending}</dd>
                          <dt>Em processamento</dt>
                          <dd>{metrics.queue.processing}</dd>
                          <dt>Arquivados</dt>
                          <dd>{metrics.queue.archived}</dd>
                          <dt>Arquivados com repetição</dt>
                          <dd>{metrics.queue.retried}</dd>
                          <dt>Latência de detecção p50</dt>
                          <dd>
                            {metrics.latency_ms.p50_ms == null
                              ? 'Não disponível'
                              : `${metrics.latency_ms.p50_ms.toFixed(0)} ms`}
                          </dd>
                          <dt>Latência de detecção p95</dt>
                          <dd>
                            {metrics.latency_ms.p95_ms == null
                              ? 'Não disponível'
                              : `${metrics.latency_ms.p95_ms.toFixed(0)} ms`}
                          </dd>
                          <dt>Amostras de latência</dt>
                          <dd>{metrics.latency_ms.samples}</dd>
                        </dl>
                        <h3>Resultados de processamento</h3>
                        {Object.keys(metrics.outcomes).length ? (
                          <dl>
                            {Object.entries(metrics.outcomes).map(([status, total]) => (
                              <div key={status}>
                                <dt>{status}</dt>
                                <dd>{total}</dd>
                              </div>
                            ))}
                          </dl>
                        ) : (
                          <p>Nenhum resultado registrado.</p>
                        )}
                      </>
                    )}
                    <button className="secondary" onClick={() => setRevision((value) => value + 1)}>
                      Atualizar painel
                    </button>
                  </section>
                )}
                {(page === 'dashboard' || page === 'login') && (
                  <>
                    <ReportIndicators key={`${session?.access_token}:${revision}`} />
                    <TaxonomyPanel revision={revision} />
                  </>
                )}
                {page === 'ground-truth' && <GroundTruthPage key={session?.access_token} />}
                {(page === 'models' || page === 'audit') && (
                  <OperationalRegistryPage key={`${session?.access_token}:${page}`} mode={page} />
                )}
                {page === 'admin' &&
                  (canAdmin ? (
                    <OperationsPage key={session?.access_token} />
                  ) : (
                    <section className="panel">
                      <h1>Acesso restrito</h1>
                      <p>Esta página exige permissão de administrador.</p>
                    </section>
                  ))}
                {page === 'private-not-found' && (
                  <section className="panel">
                    <h1>Página interna não encontrada</h1>
                    <a href="#/app/dashboard">Abrir painel interno</a>
                  </section>
                )}
                {page === 'private-detail' && route.eventId && (
                  <EventDetail
                    key={route.eventId}
                    id={route.eventId}
                    onClose={() => {
                      location.hash = '#/app/eventos';
                    }}
                    onChanged={() => setRevision((value) => value + 1)}
                    refreshAt={changed.at}
                  />
                )}
              </>
            )}
            {['review', 'private-events', 'private-map'].includes(page) && canReview && (
              <>
                <EventsPage
                  key={page}
                  map={false}
                  linkDetails={page !== 'review'}
                  events={events}
                  loading={loading}
                  error={apiError}
                  onReload={() => setRevision((value) => value + 1)}
                  changed={changed}
                />
                <div className="actions" aria-label="Páginas de ocorrências">
                  <button
                    disabled={loading || eventCursors.length === 1}
                    onClick={() => setEventCursors(eventCursors.slice(0, -1))}
                  >
                    Anterior
                  </button>
                  <button
                    disabled={loading || !events || events.length < 100}
                    onClick={() => {
                      const last = events!.at(-1)!;
                      setEventCursors([...eventCursors, `${last.occurred_at}|${last.id}`]);
                    }}
                  >
                    Próxima
                  </button>
                  <span>Página {eventCursors.length}</span>
                </div>
              </>
            )}
          </Suspense>
          <footer>
            <a href="#/sobre">Sobre e privacidade</a>
            UrMind <span>Percepção e decisão urbana auditável.</span>
            <span className="footer-right">FECART · Desenvolvimento</span>
          </footer>
        </main>
      </div>
    </div>
  );
}
