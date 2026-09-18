import { useCallback, useEffect, useState } from 'react';
import {
  Camera,
  ChartNoAxesCombined,
  ClipboardList,
  FileImage,
  LayoutDashboard,
  Map,
  Radio,
  ScanLine,
  Settings2,
} from 'lucide-react';
import { useRegisterSW } from 'virtual:pwa-register/react';
import { CapturePage } from './pages/CapturePage';
import { EventsPage } from './pages/EventsPage';
import { Photo } from './components/Photo';
import { api, UnauthorizedError } from './services/api';
import { auth, subscribeToEventChanges, type RealtimeStatus } from './services/auth';
import { SignIn } from './components/SignIn';
import {
  PublicEventDetailPage,
  PublicEventsPage,
  PublicHome,
  PublicLive,
  PublicMapPage,
  PublicSystemPage,
  PublicTransparencyPage,
  usePublicEvents,
  useScout,
  useSystemStatus,
} from './pages/public/PublicPages';
import type { Session } from '@supabase/supabase-js';
import { drafts, type CaptureDraft } from './services/drafts';
import type { UrbanEvent } from './domain/contracts';

/** Releitura do painel público: curta o bastante para parecer vivo, longa o bastante
 * para não pesar na API. */
const PUBLIC_REFRESH_MS = 30_000;

const navigation = [
  { id: 'overview', label: 'Visão geral', icon: LayoutDashboard, href: '#/', section: 'público' },
  { id: 'live', label: 'Ao vivo', icon: Radio, href: '#/live', section: 'público' },
  { id: 'map', label: 'Mapa operacional', icon: Map, href: '#/map', section: 'público' },
  { id: 'events', label: 'Ocorrências', icon: ClipboardList, href: '#/events', section: 'público' },
  {
    id: 'analysis',
    label: 'Transparência',
    icon: ChartNoAxesCombined,
    href: '#/transparency',
    section: 'público',
  },
  { id: 'settings', label: 'Sistema', icon: Settings2, href: '#/system', section: 'público' },
  {
    id: 'capture',
    label: 'Registrar evidência',
    icon: Camera,
    href: '#/capture',
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
type Page = (typeof navigation)[number]['id'] | 'event-detail';
interface Route {
  page: Page;
  eventId?: string;
}
/** Rotas por hash: `#/`, `#/live`, `#/events`, `#/events/<id>`… sem dependência nova. */
function parseRoute(): Route {
  const [, first = '', second = ''] = location.hash.replace(/^#\/?/, '/').split('/');
  const path = `#/${first}`;
  if (first === 'events' && second) return { page: 'event-detail', eventId: second };
  if (first === 'transparency') return { page: 'analysis' };
  if (first === 'system') return { page: 'settings' };
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
  const [loading, setLoading] = useState(false);
  const [apiError, setApiError] = useState('');
  const [revision, setRevision] = useState(0);
  const [publicRevision, setPublicRevision] = useState(0);
  const [session, setSession] = useState<Session | null>(null);
  const [realtime, setRealtime] = useState<RealtimeStatus>('unavailable');
  const [changed, setChanged] = useState<{ eventId: string | null; at: number }>({
    eventId: null,
    at: 0,
  });
  const [sending, setSending] = useState('');
  const online = useOnline();
  const { data: publicStatus, error: statusError } = useSystemStatus(publicRevision);
  const { data: scout } = useScout(publicRevision);
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
    void auth.session().then(setSession);
    return auth.onChange(setSession);
  }, []);
  useEffect(() => {
    if (!session) {
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
  }, [session]);
  useEffect(() => {
    const controller = new AbortController();
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
        if (!session) return;
        const data = await api.events(controller.signal);
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
  }, [revision, session]);
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
    if (!session) {
      setNotice('Rascunho guardado neste navegador. Entre para enviar ao UrMind.');
      return;
    }
    setSending(draft.id);
    setDraftError('');
    try {
      const result = await api.uploadPhoto(draft);
      await drafts.remove(draft.id);
      await reloadDrafts();
      setNotice(
        result.requires_manual_location
          ? 'Foto enviada. Sem localização: marque o ponto antes que ela vire ocorrência.'
          : result.created
            ? 'Foto enviada e registrada. A detecção roda no processamento do backend.'
            : 'Esta foto já estava registrada; nada foi duplicado.',
      );
      setRevision((value) => value + 1);
    } catch (reason) {
      if (reason instanceof UnauthorizedError) setSession(null);
      setDraftError(
        `${(reason as Error).message} O rascunho continua salvo neste navegador para reenviar.`,
      );
    } finally {
      setSending('');
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
    <div className="app-shell">
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
        <a className="brand" href="#overview">
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
          {navigation.map((item) => (
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
          <div className="scout">
            <Radio size={21} />
            <strong>Scout</strong>
            <span>Fase futura</span>
            <p>O mesmo núcleo. Novas formas de observar.</p>
          </div>
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
              {navigation.find((item) => item.id === page)?.label ?? 'Análise da ocorrência'}
            </strong>
          </span>
          <span className="network">
            <i className={online ? 'online' : ''} />
            {online ? 'Rede disponível' : 'Sem rede'}
          </span>
          {session && (
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
          {needRefresh && (
            <div className="notice">
              Uma atualização está disponível. Salve seu rascunho antes de atualizar.{' '}
              <button className="secondary compact" onClick={() => void updateServiceWorker(true)}>
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
              status={publicStatus}
              events={publicEvents ?? []}
              scout={scout}
              loading={publicLoading}
              error={publicError || statusError}
            />
          )}
          {page === 'live' && (
            <PublicLive revision={publicRevision} scout={scout} events={publicEvents ?? []} />
          )}
          {page === 'map' && <PublicMapPage events={publicEvents ?? []} />}
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
            <PublicEventDetailPage id={route.eventId} revision={publicRevision} />
          )}
          {page === 'analysis' && <PublicTransparencyPage revision={publicRevision} />}
          {page === 'settings' && (
            <PublicSystemPage
              status={publicStatus}
              scout={scout}
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
                          {draft.coordinate
                            ? `${draft.coordinate.latitude.toFixed(5)}, ${draft.coordinate.longitude.toFixed(5)}`
                            : 'Localização pendente'}
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
                            disabled={!session || sending === draft.id}
                            onClick={() => void send(draft)}
                          >
                            {sending === draft.id ? 'Enviando…' : 'Enviar'}
                          </button>
                          <button className="text-button danger" onClick={() => void remove(draft)}>
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
          {page === 'review' && !session && <SignIn />}
          {page === 'review' && session && (
            <EventsPage
              key="review"
              events={events}
              loading={loading}
              error={apiError}
              onReload={() => setRevision((value) => value + 1)}
              changed={changed}
            />
          )}
          <footer>
            UrMind <span>Percepção e decisão urbana auditável.</span>
            <span className="footer-right">FECART · Desenvolvimento</span>
          </footer>
        </main>
      </div>
    </div>
  );
}
