import { lazy, Suspense, useEffect, useMemo, useState } from 'react';
import {
  ContextPanel,
  DecisionTrace,
  EventTraceability,
  ExperimentalBadge,
  LocationPanel,
  PredictionPanel,
  QuickDiagnosis,
  RecommendedAction,
  RiskExplanation,
} from '../../components/public/Diagnosis';
import { EventFeed } from '../../components/public/EventFeed';
import { ScoutLivePanel } from '../../components/public/ScoutLivePanel';
import { SystemStatusBar } from '../../components/public/SystemStatusBar';
import {
  filterableClasses,
  labelFor,
  modelSupportLabel,
  priorityBand,
  severityOf,
  type IssueTaxonomy,
  type PublicEvent,
  type PublicEventDetail,
  type PublicScout,
  type PublicStatus,
  type Transparency,
} from '../../domain/public';
import { statuses, type CaptureMarker } from '../../domain/contracts';
import { publicApi } from '../../services/publicApi';

const UrbanMap = lazy(() => import('../../components/UrbanMap'));

/** Carrega um recurso público com cancelamento e estados de carga/erro reais. */
function usePublicData<T>(
  loader: (signal: AbortSignal) => Promise<T>,
  deps: unknown[],
): { data: T | null; error: string; loading: boolean } {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    loader(controller.signal)
      .then((value) => {
        if (controller.signal.aborted) return;
        setData(value);
        setError('');
      })
      .catch((reason: Error) => {
        if (!controller.signal.aborted) setError(reason.message);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return { data, error, loading };
}

function Skeleton({ lines = 3 }: { lines?: number }) {
  return (
    <div className="skeleton" aria-hidden="true">
      {Array.from({ length: lines }, (_, index) => (
        <span key={index} />
      ))}
    </div>
  );
}

export function useSystemStatus(revision: number) {
  return usePublicData<PublicStatus>((signal) => publicApi.status(signal), [revision]);
}

export function usePublicEvents(
  revision: number,
  filters: { urmind_class?: string; status?: string },
) {
  return usePublicData<PublicEvent[]>(
    (signal) => publicApi.events({ limit: 50, ...filters }, signal),
    [revision, filters.urmind_class, filters.status],
  );
}

export function useScout(revision: number) {
  return usePublicData<PublicScout>((signal) => publicApi.scout(signal), [revision]);
}

function useEventDetail(id: string | null, revision: number) {
  return usePublicData<PublicEventDetail | null>(
    (signal) => (id ? publicApi.event(id, signal) : Promise.resolve(null)),
    [id, revision],
  );
}

const SCOUT_STATE: Record<string, string> = {
  live: 'transmitindo',
  degraded: 'transmissão degradada',
  offline: 'fora do ar',
  no_device: 'nenhum dispositivo registrado',
};

function openEvent(id: string) {
  location.hash = `#/events/${id}`;
}

// ------------------------------------------------------------------ home

export function PublicHome({
  revision,
  status,
  events,
  scout,
  loading,
  error,
}: {
  revision: number;
  status: PublicStatus | null;
  events: PublicEvent[];
  scout: PublicScout | null;
  loading: boolean;
  error: string;
}) {
  const [selected, setSelected] = useState<string | null>(null);
  const current = selected ?? events[0]?.id ?? null;
  const { data: detail } = useEventDetail(current, revision);
  return (
    <>
      <div className="page-heading public">
        <div>
          <p className="eyebrow">CENTRO PÚBLICO DE INTELIGÊNCIA URBANA</p>
          <h1>O que o UrMind está vendo na cidade</h1>
          <p>
            Cada ocorrência abaixo foi detectada por visão computacional, situada na malha viária e
            avaliada por regras auditáveis. Nada aqui é estimado por inteligência artificial
            generativa.
          </p>
        </div>
      </div>
      <SystemStatusBar status={status} error={error} />
      <div className="command-grid">
        <ScoutLivePanel scout={scout} latest={detail} />
        <section className="panel diagnosis" aria-label="Diagnóstico da ocorrência selecionada">
          {loading && !detail ? (
            <Skeleton lines={5} />
          ) : detail ? (
            <>
              <QuickDiagnosis event={detail} />
              <RecommendedAction event={detail} />
              <button className="secondary" onClick={() => openEvent(detail.id)}>
                Ver análise completa
              </button>
            </>
          ) : (
            <div className="empty">
              <h3>Nenhuma ocorrência publicada ainda</h3>
              <p>
                Quando uma evidência for processada, o diagnóstico aparece aqui com confiança,
                severidade, prioridade e ação sugerida.
              </p>
              <a className="text-button" href="#/transparency">
                Ver como o UrMind analisa
              </a>
            </div>
          )}
        </section>
      </div>
      <Suspense fallback={<p role="status">Carregando mapa…</p>}>
        <UrbanMap events={events} selectedId={current} onSelect={setSelected} />
      </Suspense>
      <div className="command-bottom">
        <EventFeed
          events={events}
          selectedId={current}
          onSelect={(event) => setSelected(event.id)}
        />
        <section className="panel" aria-label="Contexto da ocorrência">
          <div className="section-heading">
            <h2>Contexto urbano</h2>
          </div>
          {detail ? (
            <ContextPanel event={detail} />
          ) : (
            <p className="muted">Sem ocorrência selecionada.</p>
          )}
        </section>
      </div>
    </>
  );
}

// ------------------------------------------------------------------ live

export function PublicLive({
  revision,
  scout,
  events,
}: {
  revision: number;
  scout: PublicScout | null;
  events: PublicEvent[];
}) {
  const [selected, setSelected] = useState<string | null>(null);
  const current = selected ?? events[0]?.id ?? null;
  const { data: detail } = useEventDetail(current, revision);
  return (
    <>
      <div className="page-heading public">
        <div>
          <p className="eyebrow">OPERAÇÃO AO VIVO</p>
          <h1>Scout e diagnóstico</h1>
          <p>
            A câmera só aparece quando há fonte real conectada; caso contrário, o estado é
            declarado.
          </p>
        </div>
      </div>
      <div className="command-grid">
        <ScoutLivePanel scout={scout} latest={detail} />
        <section className="panel diagnosis" aria-label="Diagnóstico ao vivo">
          {detail ? (
            <>
              <QuickDiagnosis event={detail} />
              <LocationPanel event={detail} />
            </>
          ) : (
            <p className="muted">Nenhuma ocorrência para diagnosticar.</p>
          )}
        </section>
      </div>
      <EventFeed events={events} selectedId={current} onSelect={(event) => setSelected(event.id)} />
    </>
  );
}

// ------------------------------------------------------------------ mapa e lista

function Filters({
  urmindClass,
  status,
  onClass,
  onStatus,
}: {
  urmindClass: string;
  status: string;
  onClass: (value: string) => void;
  onStatus: (value: string) => void;
}) {
  return (
    <div className="filters panel">
      <div>
        <label htmlFor="public-class">Classe</label>
        <select id="public-class" value={urmindClass} onChange={(e) => onClass(e.target.value)}>
          <option value="">Todas as classes</option>
          {filterableClasses().map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </div>
      <div>
        <label htmlFor="public-status">Estado</label>
        <select id="public-status" value={status} onChange={(e) => onStatus(e.target.value)}>
          <option value="">Todos os estados</option>
          {Object.entries(statuses).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </div>
    </div>
  );
}

export function PublicMapPage({
  events,
  reports = [],
}: {
  events: PublicEvent[];
  reports?: CaptureMarker[];
}) {
  const [selected, setSelected] = useState<string | null>(() =>
    new URLSearchParams(location.hash.split('?')[1] ?? '').get('ponto'),
  );
  const [pointDetail, setPointDetail] = useState<PublicEventDetail | null>(null);
  const [pointError, setPointError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    setPointDetail(null);
    setPointError('');
    // A deep link may refer to a publication newer than the cached map page,
    // or outside its bounded listing. The publication endpoint is the authority.
    if (selected) {
      publicApi
        .publishedEvent(selected, controller.signal)
        .then((value) => {
          if (!controller.signal.aborted) setPointDetail(value);
        })
        .catch(() => {
          if (!controller.signal.aborted) setPointError('Detalhe publicado indisponível.');
        });
    }
    return () => controller.abort();
  }, [selected]);
  const { data: generic } = usePublicData((signal) => publicApi.captureMarkers(signal), []);
  const markers = useMemo(() => {
    const reportIds = new Set(reports.map((report) => report.public_id));
    const eventIds = new Set(reports.map((report) => report.event_public_id));
    return [
      ...events.filter((event) => !eventIds.has(event.id)),
      ...(generic ?? []).filter((report) => !reportIds.has(report.id)),
      ...reports,
    ];
  }, [events, reports, generic]);
  return (
    <>
      <div className="page-heading public">
        <div>
          <p className="eyebrow">TERRITÓRIO</p>
          <h1>Mapa operacional</h1>
          <p>Coordenada informada e ponto ajustado à via permanecem separados.</p>
        </div>
      </div>
      <Suspense fallback={<p role="status">Carregando mapa…</p>}>
        <UrbanMap
          events={markers}
          selectedId={selected}
          onSelect={(id) => {
            setSelected(id);
            if (events.some((event) => event.id === id)) {
              const query = new URLSearchParams(location.hash.split('?')[1] ?? '');
              query.set('ponto', id);
              history.replaceState(null, '', `#/mapa?${query}`);
            }
          }}
          onCloseDetail={() => setSelected(null)}
          detail={
            selected && (
              <>
                {pointError && <p role="alert">{pointError}</p>}
                {pointDetail && pointDetail.id === selected ? (
                  <>
                    <h2>{labelFor(pointDetail.urmind_class)}</h2>
                    <ExperimentalBadge stage={pointDetail.model_stage} />
                    {pointDetail.image.available && pointDetail.image.url && (
                      <img
                        src={pointDetail.image.url}
                        alt="Foto publicada e sanitizada da ocorrência"
                      />
                    )}
                    <p>{pointDetail.road_name ?? 'Endereço aproximado indisponível'}</p>
                    {pointDetail.latitude != null && pointDetail.longitude != null && (
                      <>
                        <p>
                          {pointDetail.latitude.toFixed(4)}, {pointDetail.longitude.toFixed(4)}
                        </p>
                        <a
                          href={`https://www.google.com/maps/dir/?api=1&destination=${pointDetail.latitude.toFixed(4)},${pointDetail.longitude.toFixed(4)}`}
                          target="_blank"
                          rel="noopener noreferrer"
                        >
                          Como chegar
                        </a>
                      </>
                    )}
                    <a href={`#/resultado/${pointDetail.id}`}>Abrir resultado</a>
                  </>
                ) : (
                  <p>Relato do cidadão. Foto e descrição não publicadas.</p>
                )}
              </>
            )
          }
        />
      </Suspense>
      <EventFeed events={events} selectedId={selected} onSelect={(event) => openEvent(event.id)} />
    </>
  );
}

export function PublicEventsPage({
  events,
  loading,
  error,
  filters,
  onFilters,
}: {
  events: PublicEvent[];
  loading: boolean;
  error: string;
  filters: { urmind_class: string; status: string };
  onFilters: (value: { urmind_class: string; status: string }) => void;
}) {
  return (
    <>
      <div className="page-heading public">
        <div>
          <p className="eyebrow">OCORRÊNCIAS</p>
          <h1>Tudo que o UrMind registrou</h1>
          <p>Lista pública com classe, confiança, severidade e prioridade de cada ocorrência.</p>
        </div>
      </div>
      <Filters
        urmindClass={filters.urmind_class}
        status={filters.status}
        onClass={(urmind_class) => onFilters({ ...filters, urmind_class })}
        onStatus={(status) => onFilters({ ...filters, status })}
      />
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {loading ? (
        <Skeleton lines={4} />
      ) : (
        <EventFeed
          events={events}
          onSelect={(event) => openEvent(event.id)}
          title="Registros"
          emptyHint="Nenhuma ocorrência corresponde a este filtro."
        />
      )}
    </>
  );
}

// ------------------------------------------------------------------ detalhe

export function PublicEventDetailPage({ id, revision }: { id: string; revision: number }) {
  const { data: event, error, loading } = useEventDetail(id, revision);
  const severity = useMemo(() => severityOf(event?.risk?.severity), [event]);
  if (loading && !event) return <Skeleton lines={6} />;
  if (error)
    return (
      <p className="error" role="alert">
        {error}
      </p>
    );
  if (!event) return <p className="muted">Ocorrência não encontrada.</p>;
  return (
    <>
      <div className="page-heading public">
        <div>
          <p className="eyebrow">ANÁLISE COMPLETA</p>
          <ExperimentalBadge stage={event.model_stage} />
          <h1>{labelFor(event.urmind_class)}</h1>
          <p>
            <span className={`risk-tag risk-${severity.level}`}>
              <i aria-hidden="true">{severity.shape}</i> {severity.label}
            </span>{' '}
            · prioridade {priorityBand(event.risk?.priority_score)} · confiança visual{' '}
            {event.visual_confidence != null
              ? `${(event.visual_confidence * 100).toFixed(1)}%`
              : 'não disponível'}{' '}
            · {statuses[event.status as keyof typeof statuses] ?? event.status}
          </p>
        </div>
        <a className="secondary button" href="#/events">
          Voltar às ocorrências
        </a>
      </div>

      <section className="panel" aria-label="Evidência visual">
        <div className="section-heading">
          <h2>Evidência</h2>
        </div>
        {event.image.available && event.image.url ? (
          <figure className="evidence-figure">
            <div className="evidence-frame">
              <img src={event.image.url} alt="Evidência da ocorrência" />
              {event.detections.map(
                (detection, index) =>
                  detection.bbox && (
                    <span
                      key={index}
                      className="bbox"
                      style={{
                        left: `${detection.bbox.x * 100}%`,
                        top: `${detection.bbox.y * 100}%`,
                        width: `${detection.bbox.width * 100}%`,
                        height: `${detection.bbox.height * 100}%`,
                      }}
                      title={`${detection.urmind_class} ${(detection.confidence * 100).toFixed(1)}%`}
                    />
                  ),
              )}
            </div>
          </figure>
        ) : (
          <p className="notice">{event.image.reason}</p>
        )}
        <ul className="detection-list">
          {event.detections.map((detection, index) => (
            <li key={index}>
              <strong>{labelFor(detection.urmind_class)}</strong>
              <span>{(detection.confidence * 100).toFixed(1)}%</span>
              <small>{detection.urmind_class}</small>
            </li>
          ))}
          {!event.detections.length && <li className="muted">Sem detecções publicadas.</li>}
        </ul>
      </section>

      <div className="detail-grid">
        <section className="panel" aria-label="Risco e explicação">
          <RiskExplanation event={event} />
        </section>
        <section className="panel" aria-label="Ação recomendada">
          <RecommendedAction event={event} />
        </section>
        <section className="panel" aria-label="Localização">
          <div className="section-heading">
            <h2>Localização</h2>
          </div>
          <LocationPanel event={event} />
        </section>
        <section className="panel" aria-label="Contexto urbano">
          <div className="section-heading">
            <h2>Contexto urbano</h2>
          </div>
          <ContextPanel event={event} />
        </section>
        <section className="panel" aria-label="Previsão">
          <PredictionPanel event={event} />
        </section>
        <section className="panel" aria-label="Rastreabilidade">
          <div className="section-heading">
            <h2>Rastreabilidade</h2>
          </div>
          <EventTraceability event={event} />
        </section>
      </div>

      <section className="panel" aria-label="Como o UrMind analisou">
        <div className="section-heading">
          <h2>Como o UrMind analisou</h2>
          <a className="text-button" href="#/transparency">
            Ver transparência do modelo
          </a>
        </div>
        <DecisionTrace event={event} />
      </section>
    </>
  );
}

// ------------------------------------------------------------------ transparência

export function PublicTransparencyPage({ revision }: { revision: number }) {
  const { data, error, loading } = usePublicData<Transparency>(
    (signal) => publicApi.transparency(signal),
    [revision],
  );
  if (loading && !data) return <Skeleton lines={6} />;
  if (error)
    return (
      <p className="error" role="alert">
        {error}
      </p>
    );
  if (!data) return null;
  const metrics = data.metrics;
  return (
    <>
      <div className="page-heading public">
        <div>
          <p className="eyebrow">TRANSPARÊNCIA</p>
          <h1>Como o UrMind analisou</h1>
          <p>Modelo, dados, métricas medidas e limites declarados. Sem número estimado.</p>
        </div>
      </div>
      <section className="panel" aria-label="Pipeline de análise">
        <ol className="pipeline">
          <li>
            <strong>Imagem</strong>
            <span>captura enviada ou registrada pelo Scout</span>
          </li>
          <li>
            <strong>YOLOX</strong>
            <span>{data.model_version ?? 'nenhum modelo autorizado neste modo'}</span>
          </li>
          <li>
            <strong>Contexto urbano</strong>
            <span>malha viária, equipamentos próximos e chuva</span>
          </li>
          <li>
            <strong>Risco</strong>
            <span>regras versionadas e auditáveis</span>
          </li>
          <li>
            <strong>Competência e ação</strong>
            <span>tabelas versionadas; sem regra, vai para triagem</span>
          </li>
        </ol>
        <p className="muted">
          Nenhuma etapa usa inteligência artificial generativa. Texto vem de modelo de documento, e
          número vem de medição registrada.
        </p>
      </section>
      <TaxonomyPanel revision={revision} />
      <div className="detail-grid">
        <section className="panel" aria-label="Modelo">
          <div className="section-heading">
            <h2>Modelo de visão</h2>
            {data.stage && <span className="risk-tag risk-medium">{data.stage}</span>}
          </div>
          <dl className="data-list">
            <div>
              <dt>Nome e versão</dt>
              <dd>
                {data.model_name ?? 'não disponível'}
                <small>{data.model_version}</small>
              </dd>
            </div>
            <div>
              <dt>Classes</dt>
              <dd>{data.classes.length ? data.classes.join(', ') : 'não disponível'}</dd>
            </div>
            <div>
              <dt>Entrada</dt>
              <dd>{data.input_size.length ? data.input_size.join(' × ') : 'não disponível'}</dd>
            </div>
            <div>
              <dt>Limiar de publicação</dt>
              <dd>{data.score_threshold ?? 'não disponível'}</dd>
            </div>
          </dl>
          {data.stage_note && <p className="notice">{data.stage_note}</p>}
        </section>
        <section className="panel" aria-label="Métricas medidas">
          <div className="section-heading">
            <h2>Métricas medidas</h2>
            {metrics.samples != null && <span className="muted">{metrics.samples} imagens</span>}
          </div>
          <dl className="data-list">
            <div>
              <dt>mAP50</dt>
              <dd>{metrics.map50?.toFixed(4) ?? 'não disponível'}</dd>
            </div>
            <div>
              <dt>mAP50-95</dt>
              <dd>{metrics.map50_95?.toFixed(4) ?? 'não disponível'}</dd>
            </div>
            <div>
              <dt>Precisão / recall</dt>
              <dd>
                {metrics.precision?.toFixed(3) ?? '—'} / {metrics.recall?.toFixed(3) ?? '—'}
              </dd>
            </div>
            <div>
              <dt>F1</dt>
              <dd>{metrics.f1?.toFixed(3) ?? 'não disponível'}</dd>
            </div>
          </dl>
          {metrics.not_computed.length > 0 && (
            <p className="muted">Não calculadas: {metrics.not_computed.join(', ')}.</p>
          )}
        </section>
        <section className="panel" aria-label="Desempenho">
          <div className="section-heading">
            <h2>Latência</h2>
          </div>
          <dl className="data-list">
            <div>
              <dt>Média / p50 / p95</dt>
              <dd>
                {data.latency.mean_ms ?? '—'} / {data.latency.p50_ms ?? '—'} /{' '}
                {data.latency.p95_ms ?? '—'} ms
              </dd>
            </div>
            <div>
              <dt>Quadros por segundo</dt>
              <dd>{data.latency.fps_approx ?? 'não disponível'}</dd>
            </div>
            <div>
              <dt>Execução</dt>
              <dd>
                {data.latency.execution_provider ?? 'não disponível'}
                <small>{data.latency.hardware}</small>
              </dd>
            </div>
          </dl>
        </section>
        <section className="panel" aria-label="Dados de treino">
          <div className="section-heading">
            <h2>Dados de treino</h2>
          </div>
          <dl className="data-list">
            <div>
              <dt>Conjunto</dt>
              <dd>
                {data.dataset_name ?? 'não disponível'}
                <small>{data.dataset_version}</small>
              </dd>
            </div>
            <div>
              <dt>Licença</dt>
              <dd>{data.dataset_license ?? 'não disponível'}</dd>
            </div>
            <div>
              <dt>Origem</dt>
              <dd className="break">{data.dataset_source ?? 'não disponível'}</dd>
            </div>
          </dl>
        </section>
      </div>
      <section className="panel" aria-label="Fontes e limites">
        <div className="section-heading">
          <h2>Fontes externas e limites</h2>
        </div>
        <ul className="context-list">
          {data.context_sources.map((source) => (
            <li key={source.source}>
              <strong>{source.source}</strong>
              <span>{source.use}</span>
            </li>
          ))}
        </ul>
        <dl className="data-list">
          {Object.entries(data.rules).map(([name, value]) => (
            <div key={name}>
              <dt>{name.replace(/_/g, ' ')}</dt>
              <dd>{String(value)}</dd>
            </div>
          ))}
        </dl>
        <ul className="limitations">
          {data.limitations.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      </section>
    </>
  );
}

// ------------------------------------------------------------------ sistema

export function PublicSystemPage({
  status,
  scout,
  error,
  onReload,
}: {
  status: PublicStatus | null;
  scout: PublicScout | null;
  error: string;
  onReload: () => void;
}) {
  const telemetry = Object.entries(scout?.telemetry ?? {});
  return (
    <>
      <div className="page-heading public">
        <div>
          <p className="eyebrow">SISTEMA</p>
          <h1>Estado público</h1>
          <p>Cada componente com seu estado real, sem reduzir tudo a online ou offline.</p>
        </div>
        <button className="secondary" onClick={onReload}>
          Atualizar
        </button>
      </div>
      <SystemStatusBar status={status} error={error} />
      <div className="detail-grid">
        <section className="panel" aria-label="Scout">
          <div className="section-heading">
            <h2>Scout</h2>
          </div>
          <dl className="data-list">
            <div>
              <dt>Estado</dt>
              <dd>
                {SCOUT_STATE[scout?.status ?? ''] ?? 'não disponível'}
                {status?.scout.detail && <small>{status.scout.detail}</small>}
              </dd>
            </div>
            <div>
              <dt>Câmera</dt>
              <dd>
                {scout?.camera.mode === 'unavailable'
                  ? 'indisponível'
                  : scout?.camera.mode === 'live_video'
                    ? 'vídeo ao vivo'
                    : scout?.camera.mode === 'live_snapshots'
                      ? 'imagens ao vivo'
                      : 'não disponível'}
                {scout?.camera.reason && <small>{scout.camera.reason}</small>}
              </dd>
            </div>
            <div>
              <dt>Última captura</dt>
              <dd>
                {scout?.last_seen
                  ? new Date(scout.last_seen).toLocaleString('pt-BR')
                  : 'não disponível'}
              </dd>
            </div>
          </dl>
          {telemetry.length ? (
            <dl className="data-list">
              {telemetry.map(([key, value]) => (
                <div key={key}>
                  <dt>{key}</dt>
                  <dd>{String(value)}</dd>
                </div>
              ))}
            </dl>
          ) : (
            <p className="muted">
              Sem telemetria publicada: nenhum sensor é exibido enquanto não houver leitura real.
            </p>
          )}
        </section>
        <section className="panel" aria-label="Cobertura">
          <div className="section-heading">
            <h2>Cobertura</h2>
          </div>
          <dl className="data-list">
            <div>
              <dt>Área piloto</dt>
              <dd>{status?.pilot_area ?? 'não definida'}</dd>
            </div>
            <div>
              <dt>Trechos viários</dt>
              <dd>{status?.road_segments_total ?? 'não disponível'}</dd>
            </div>
            <div>
              <dt>Ocorrências publicadas</dt>
              <dd>{status?.events_total ?? 'não disponível'}</dd>
            </div>
            <div>
              <dt>Detector</dt>
              <dd>{status?.detector.detail ?? 'não disponível'}</dd>
            </div>
          </dl>
        </section>
      </div>
    </>
  );
}

/** Classes da taxonomia canônica e o que o modelo atual realmente suporta. */
export function TaxonomyPanel({ revision }: { revision: number }) {
  const { data, error } = usePublicData<IssueTaxonomy>(
    (signal) => publicApi.taxonomy(signal),
    [revision],
  );
  if (error) return null;
  if (!data) return <Skeleton lines={3} />;
  return (
    <section className="panel" aria-label="Classes de problemas urbanos">
      <div className="section-heading">
        <h2>Problemas urbanos</h2>
        <span className="muted">{data.taxonomy_version}</span>
      </div>
      <p className="muted">
        Só classes com modelo são detectadas automaticamente. As demais estão em desenvolvimento:
        precisam de dados revisados antes de qualquer detecção.
      </p>
      <ul className="taxonomy-list">
        {data.issues.map((issue) => (
          <li key={issue.issue_code} data-support={issue.model_support_status}>
            <strong>{issue.display_name_pt}</strong>
            <span className="badge">{modelSupportLabel(issue)}</span>
            {issue.limitations?.map((limitation) => (
              <small key={limitation}>{limitation}</small>
            ))}
          </li>
        ))}
      </ul>
    </section>
  );
}

/**
 * Exemplos reais já revisados por humano. Nunca representam o resultado de uma
 * foto recém-enviada: cada cartão leva o selo EXEMPLO REVISADO.
 */
export function PublicDemoPage({ revision }: { revision: number }) {
  const { data, error, loading } = usePublicData<PublicEvent[]>(
    (signal) => publicApi.events({ limit: 12, status: 'confirmed' }, signal),
    [revision],
  );
  return (
    <>
      <div className="page-heading public">
        <div>
          <p className="eyebrow">DEMONSTRAÇÃO</p>
          <h1>Exemplos revisados</h1>
          <p>
            Ocorrências reais confirmadas por revisão humana. Não são o resultado da sua foto; o
            resultado de um envio aparece em “Processando”.
          </p>
        </div>
      </div>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {loading && !data && <Skeleton lines={4} />}
      {data && data.length === 0 && (
        <p className="panel muted">Nenhum exemplo revisado disponível ainda.</p>
      )}
      {data && data.length > 0 && (
        <ul className="demo-list">
          {data.map((event) => (
            <li key={event.id} className="panel">
              <span className="badge demo-badge">EXEMPLO REVISADO</span>
              <strong>{labelFor(event.urmind_class)}</strong>
              <span className="muted">
                {new Date(event.occurred_at).toLocaleDateString('pt-BR')} · severidade{' '}
                {severityOf(event.severity).label}
              </span>
              <a href={`#/events/${event.id}`}>Ver diagnóstico</a>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
