import { useCallback, useEffect, useState } from 'react';
import {
  ArrowUpRight,
  Camera,
  ChartNoAxesCombined,
  CircleHelp,
  ClipboardList,
  Database,
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
import { api } from './services/api';
import { drafts, type CaptureDraft } from './services/drafts';
import type { UrbanEvent } from './domain/contracts';

const navigation = [
  { id: 'overview', label: 'Visão geral', icon: LayoutDashboard },
  { id: 'capture', label: 'Registrar evidência', icon: Camera },
  { id: 'drafts', label: 'Rascunhos locais', icon: FileImage },
  { id: 'events', label: 'Ocorrências', icon: ClipboardList },
  { id: 'map', label: 'Gêmeo digital 2D', icon: Map },
  { id: 'analysis', label: 'Análises e previsões', icon: ChartNoAxesCombined },
  { id: 'settings', label: 'Integrações', icon: Settings2 },
] as const;
type Page = (typeof navigation)[number]['id'];
function currentPage(): Page {
  const id = location.hash.slice(1);
  return navigation.find((item) => item.id === id)?.id || 'overview';
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
  const [page, setPage] = useState<Page>(currentPage);
  const [localDrafts, setLocalDrafts] = useState<CaptureDraft[]>([]);
  const [draftError, setDraftError] = useState('');
  const [draftsLoaded, setDraftsLoaded] = useState(false);
  const [editing, setEditing] = useState<CaptureDraft>();
  const [notice, setNotice] = useState('');
  const [events, setEvents] = useState<UrbanEvent[] | null>(null);
  const [health, setHealth] = useState('Ainda não consultado');
  const [loading, setLoading] = useState(false);
  const [apiError, setApiError] = useState('');
  const [revision, setRevision] = useState(0);
  const online = useOnline();
  const {
    needRefresh: [needRefresh],
    offlineReady: [offlineReady],
    updateServiceWorker,
  } = useRegisterSW();
  useEffect(() => {
    const change = () => setPage(currentPage());
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
    const controller = new AbortController();
    async function load() {
      setLoading(true);
      setApiError('');
      setEvents(null);
      try {
        const result = await api.health(controller.signal);
        if (controller.signal.aborted) return;
        setHealth(
          result.database === 'not_configured'
            ? 'Banco não configurado'
            : result.database === 'connected'
              ? 'API e banco disponíveis'
              : `API: ${result.status} · Banco: ${result.database || 'não informado'}`,
        );
        if (result.database === 'not_configured') {
          setApiError('O Supabase/PostGIS ainda não está configurado.');
          return;
        }
        const data = await api.events(controller.signal);
        if (!controller.signal.aborted) setEvents(data);
      } catch (error) {
        if (!controller.signal.aborted) {
          setApiError(error instanceof Error ? error.message : 'Falha ao consultar API.');
          setHealth('Conexão indisponível');
        }
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }
    void load();
    return () => controller.abort();
  }, [revision]);
  function navigate(next: Page) {
    location.hash = next;
    setPage(next);
    setNotice('');
  }
  function newCapture() {
    setEditing(undefined);
    navigate('capture');
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
              href={`#${item.id}`}
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
            <strong>{navigation.find((item) => item.id === page)?.label}</strong>
          </span>
          <span className="network">
            <i className={online ? 'online' : ''} />
            {online ? 'Rede disponível' : 'Sem rede'}
          </span>
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
            <>
              <div className="page-heading">
                <div>
                  <p className="eyebrow">OBSERVAR. COMPREENDER. AGIR.</p>
                  <h1>Um olhar atento à cidade.</h1>
                  <p>Da evidência à decisão, com rastreabilidade em cada etapa.</p>
                </div>
                <button onClick={newCapture}>
                  <Camera size={17} /> Registrar evidência
                </button>
              </div>
              <section className="hero">
                <div>
                  <span className="hero-label">
                    <span /> FASE ATUAL · FOTO-FIRST
                  </span>
                  <h2>
                    A transformação começa
                    <br />
                    com uma observação.
                  </h2>
                  <p>
                    Registre fotografias e organize evidências enquanto as integrações do projeto
                    são preparadas.
                  </p>
                  <button className="light-button" onClick={newCapture}>
                    Criar primeiro registro <ArrowUpRight size={17} />
                  </button>
                </div>
                <div className="city-art" aria-hidden="true">
                  <div className="city-grid" />
                  <div className="building b1" />
                  <div className="building b2" />
                  <div className="building b3" />
                  <div className="building b4" />
                  <div className="scan-target">
                    <ScanLine size={54} strokeWidth={1} />
                  </div>
                  <span className="city-label">EVIDÊNCIA → DECISÃO</span>
                </div>
              </section>
              <div className="metrics">
                <Metric
                  label="Rascunhos neste navegador"
                  value={draftsLoaded ? String(localDrafts.length) : '—'}
                  caption="Fotos salvas localmente"
                  icon={<FileImage size={19} />}
                />
                <Metric
                  label="Ocorrências consultadas"
                  value={events ? String(events.length) : '—'}
                  caption={events ? 'Até 500 registros recentes' : 'Aguardando conexão com o banco'}
                  icon={<ClipboardList size={19} />}
                />
                <Metric
                  label="Aguardando revisão"
                  value={
                    events
                      ? String(events.filter((event) => event.status === 'review').length)
                      : '—'
                  }
                  caption="No conjunto consultado"
                  icon={<ScanLine size={19} />}
                />
              </div>
              <div className="overview-grid">
                <section className="panel">
                  <div className="section-heading">
                    <h2>Do registro à decisão</h2>
                    <span className="badge">Fluxo do projeto</span>
                  </div>
                  <div className="pipeline">
                    {[
                      ['01', 'Capturar', 'Foto e localização', true],
                      ['02', 'Detectar', 'YOLOX + dados reais', false],
                      ['03', 'Compreender', 'Contexto e regras', false],
                      ['04', 'Agir', 'Revisão e ação sugerida', false],
                    ].map(([step, title, text, active]) => (
                      <div className={active ? 'active' : ''} key={String(step)}>
                        <span>{step}</span>
                        <h3>{title}</h3>
                        <p>{text}</p>
                        <small>
                          {active ? 'Rascunho local disponível' : 'Integração pendente'}
                        </small>
                      </div>
                    ))}
                  </div>
                </section>
                <section className="panel readiness">
                  <Database size={23} />
                  <h2>Ambiente em preparação</h2>
                  <p>{loading ? 'Consultando backend…' : health}</p>
                  <p className="muted">
                    Supabase, detector e APIs externas serão conectados nas próximas etapas.
                  </p>
                  <button className="text-button" onClick={() => navigate('settings')}>
                    Ver integrações <ArrowUpRight size={17} />
                  </button>
                </section>
              </div>
            </>
          )}
          {page === 'capture' && (
            <CapturePage
              key={editing?.id || 'new'}
              initial={editing}
              onSaved={() => {
                void reloadDrafts();
                setEditing(undefined);
                navigate('drafts');
                setNotice(
                  'Rascunho salvo neste navegador. Envio e processamento ainda não realizados.',
                );
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
                Envio indisponível até integrar Auth e Storage. Estes rascunhos não são ocorrências
                nem detecções. Limpar os dados do navegador remove as fotos.
              </p>
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
          {(page === 'events' || page === 'map') && (
            <EventsPage
              key={page}
              events={events}
              loading={loading}
              error={apiError}
              onReload={() => setRevision((value) => value + 1)}
              map={page === 'map'}
            />
          )}
          {page === 'analysis' && (
            <>
              <div className="page-heading">
                <div>
                  <p className="eyebrow">DECISÃO / ANÁLISES</p>
                  <h1>Descrever, priorizar e orientar.</h1>
                  <p>Cada resultado precisa de evidência, origem e limites explícitos.</p>
                </div>
                <span className="badge">Integração pendente</span>
              </div>
              <div className="analysis-grid">
                {[
                  [
                    'Descrição',
                    'Relatórios estruturados',
                    'O backend já possui templates Jinja2. A exibição depende de um contrato HTTP para o relatório de cada ocorrência.',
                  ],
                  [
                    'Prescrição',
                    'Prioridade e ação sugerida',
                    'O motor de severidade e prioridade existe no backend. Responsável e ação exigem regras e catálogo verificáveis, além da integração HTTP.',
                  ],
                  [
                    'Previsão',
                    'Histórico antes de projeções',
                    'Ainda indisponível. Recorrência, risco de trecho e evolução precisam de histórico suficiente, validação temporal, horizonte e incerteza.',
                  ],
                ].map(([label, title, text]) => (
                  <section className="panel analysis-card" key={label}>
                    <span className="eyebrow">{label}</span>
                    <h2>{title}</h2>
                    <p>{text}</p>
                    <span className="badge">Sem resultado disponível</span>
                  </section>
                ))}
              </div>
              <section className="panel principle">
                <CircleHelp size={24} />
                <div>
                  <h2>Uma ausência também é uma informação.</h2>
                  <p>
                    A interface não calcula prioridades paralelas ao backend, não inventa
                    responsáveis e não apresenta previsões sem histórico. Os resultados serão
                    exibidos a partir dos contratos oficiais.
                  </p>
                </div>
              </section>
            </>
          )}
          {page === 'settings' && (
            <>
              <div className="page-heading">
                <div>
                  <p className="eyebrow">PROJETO / AMBIENTE</p>
                  <h1>Integrações e disponibilidade</h1>
                  <p>O que já funciona localmente e o que depende das próximas etapas.</p>
                </div>
                <button
                  className="secondary"
                  disabled={loading}
                  onClick={() => setRevision((value) => value + 1)}
                >
                  Verificar backend
                </button>
              </div>
              <section className="panel">
                <dl className="integration-list">
                  <dt>API FastAPI</dt>
                  <dd>{loading ? 'Consultando…' : health}</dd>
                  <dt>Rascunhos no navegador</dt>
                  <dd>{draftsLoaded ? 'Disponível · IndexedDB' : 'Indisponível ou carregando'}</dd>
                  <dt>Aplicativo offline</dt>
                  <dd>
                    {offlineReady
                      ? 'Pronto neste navegador'
                      : 'Disponível após instalação do service worker no build de produção'}
                  </dd>
                  <dt>Supabase Auth / Storage / Realtime</dt>
                  <dd>Integração pendente</dd>
                  <dt>YOLOX / datasets / Worker</dt>
                  <dd>Integração pendente · sem inferência</dd>
                  <dt>Base cartográfica OSM</dt>
                  <dd>
                    {import.meta.env.VITE_MAP_STYLE_URL
                      ? 'Estilo configurado · requer rede'
                      : 'Não configurada · mapa sem requisições externas'}
                  </dd>
                  <dt>Contexto / APIs externas</dt>
                  <dd>Integração pendente</dd>
                  <dt>Scout / sensores</dt>
                  <dd>Fase futura</dd>
                </dl>
              </section>
              <p className="notice">
                Nenhuma credencial de servidor deve ser inserida no frontend. A configuração de
                desenvolvimento está documentada em frontend/README.md.
              </p>
            </>
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
function Metric({
  label,
  value,
  caption,
  icon,
}: {
  label: string;
  value: string;
  caption: string;
  icon: React.ReactNode;
}) {
  return (
    <section className="panel metric">
      <div>
        <span>{label}</span>
        {icon}
      </div>
      <strong>{value}</strong>
      <p>{caption}</p>
    </section>
  );
}
