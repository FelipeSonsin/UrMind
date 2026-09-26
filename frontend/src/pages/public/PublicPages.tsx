import { lazy, Suspense, useEffect, useMemo, useState, type ReactNode } from 'react';
import { Camera, ScanEye, Webcam } from 'lucide-react';
import { activeCapabilities } from '../../domain/capabilities';
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
import { SystemStatusBar } from '../../components/public/SystemStatusBar';
import {
  automaticClassCodes,
  issueHeadline,
  labelFor,
  modelSupportLabel,
  priorityBand,
  publicSituation,
  severityOf,
  situationLabel,
  type IssueTaxonomy,
  type PublicEvent,
  type PublicEventDetail,
  type PublicStatus,
  type Transparency,
} from '../../domain/public';
import { statuses, type CaptureMarker } from '../../domain/contracts';
import { api } from '../../services/api';
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

export function useSystemStatus(revision: number, enabled = true) {
  return usePublicData<PublicStatus | null>(
    (signal) => (enabled ? publicApi.status(signal) : Promise.resolve(null)),
    [revision, enabled],
  );
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

function useEventDetail(id: string | null, revision: number) {
  return usePublicData<PublicEventDetail | null>(
    (signal) => (id ? publicApi.event(id, signal) : Promise.resolve(null)),
    [id, revision],
  );
}

function openEvent(id: string) {
  location.hash = `#/events/${id}`;
}

const LOCATION_SOURCES: Record<string, string> = {
  gps_device: 'GPS do aparelho',
  exif: 'localização gravada na foto',
  manual: 'ponto marcado no mapa',
};

function SeverityTag({ severity }: { severity: string | null | undefined }) {
  const level = severityOf(severity);
  return (
    <span className={`risk-tag risk-${level.level}`}>
      <i aria-hidden="true">{level.shape}</i> {level.label}
    </span>
  );
}

function reportPlace(report: CaptureMarker): string | null {
  const address = report.address;
  if (address?.status !== 'ok') return null;
  return [address.road, address.suburb, address.city].filter(Boolean).join(', ') || null;
}

/**
 * Relato do próprio autor no mapa: tipo, gravidade, local, data, situação e foto.
 * O ponto informado e o trecho de via associado continuam separados.
 */
export function OwnReportDetail({
  report,
  photoUrl,
  children,
}: {
  report: CaptureMarker;
  photoUrl?: string | null;
  children?: ReactNode;
}) {
  const source = report.location_source ? LOCATION_SOURCES[report.location_source] : undefined;
  const severity = severityOf(report.severity);
  return (
    <>
      <h2>{report.urmind_class ? labelFor(report.urmind_class) : 'Seu relato'}</h2>
      <p>
        <span className={`report-chip situation-${publicSituation(report.report_status)}`}>
          {situationLabel(report.report_status)}
        </span>
      </p>
      <dl className="data-list report-facts">
        <div>
          <dt>Tipo</dt>
          <dd>{report.urmind_class ? labelFor(report.urmind_class) : 'Ainda não identificado'}</dd>
        </div>
        <div>
          <dt>Gravidade</dt>
          <dd>
            {severity.level === 'unknown' ? (
              severity.label
            ) : (
              <span className={`risk-tag risk-${severity.level}`}>
                <i aria-hidden="true">{severity.shape}</i> {severity.label}
              </span>
            )}
          </dd>
        </div>
        <div>
          <dt>Local</dt>
          <dd>
            {reportPlace(report) ?? 'Endereço aproximado indisponível'}
            <small>{source ?? 'origem não registrada'}</small>
            {report.accuracy_m != null && (
              <small>Precisão aproximada: {Math.round(report.accuracy_m)} m</small>
            )}
          </dd>
        </div>
        <div>
          <dt>Data</dt>
          <dd>
            {report.created_at ? (
              <time dateTime={report.created_at}>
                {new Date(report.created_at).toLocaleString('pt-BR')}
              </time>
            ) : (
              'não registrada'
            )}
          </dd>
        </div>
        {report.snapped_latitude != null && report.snapped_longitude != null && (
          <div>
            <dt>Trecho de via associado</dt>
            <dd>
              {report.road_name ?? 'via sem nome no mapa'}
              {report.distance_to_road_m != null && (
                <small>a {Math.round(report.distance_to_road_m)} m do ponto informado</small>
              )}
            </dd>
          </div>
        )}
      </dl>
      {(report.reporters_count ?? 1) > 1 && (
        <p>{report.reporters_count} pessoas relataram este ponto</p>
      )}
      {report.photo_gate?.status === 'NEEDS_REVIEW' && (
        <p className="notice">
          A equipe ainda vai conferir a foto (cena e privacidade). Isso não indica que há um
          problema.
        </p>
      )}
      {report.user_description && <p className="report-note">{report.user_description}</p>}
      {photoUrl && (
        <>
          <img src={photoUrl} alt="Foto privada do relato selecionado" />
          <a href={photoUrl} target="_blank" rel="noopener noreferrer">
            Ver foto original
          </a>
        </>
      )}
      {children}
    </>
  );
}

// ------------------------------------------------------------------ home

export function PublicHome({
  revision,
  events,
  reports,
  hasSession,
  loading,
  error,
}: {
  revision: number;
  events: PublicEvent[];
  reports: CaptureMarker[];
  hasSession: boolean;
  loading: boolean;
  error: string;
}) {
  const [selected, setSelected] = useState<string | null>(null);
  const current = selected ?? events[0]?.id ?? null;
  const { data: detail } = useEventDetail(current, revision);
  const automatic = automaticClassCodes().map(labelFor);
  return (
    <>
      <section className="home-hero" aria-labelledby="home-title">
        <div>
          <h1 id="home-title">Viu um problema na rua? Registre com uma foto.</h1>
          <p>
            A localização vem do aparelho ou da própria foto. A equipe revisa cada relato antes de
            ele aparecer no mapa público.
          </p>
          {activeCapabilities.liveDetection && (
            <div className="home-capabilities">
              <span>Reconhece automaticamente:</span>
              <ul aria-label="Reconhecidos automaticamente">
                {automatic.map((label) => (
                  <li key={label} className="badge">
                    {label}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
        <div className="actions">
          <a className="button" href="#/registrar">
            <Camera size={16} aria-hidden="true" /> Registrar evidência
          </a>
          {activeCapabilities.liveDetection && (
            <a className="button secondary" href="#/deteccao-ao-vivo">
              <ScanEye size={16} aria-hidden="true" /> Detecção ao vivo
            </a>
          )}
          {activeCapabilities.robotCamera && (
            <a className="button secondary" href="#/camera-robo">
              <Webcam size={16} aria-hidden="true" /> Câmera do robô
            </a>
          )}
        </div>
      </section>
      <section className="panel home-reports" aria-label="Seus relatos">
        <div className="section-heading">
          <h2>Seus relatos</h2>
          {reports.length > 0 && (
            <a className="text-button" href="#/meus-relatos">
              Ver todos
            </a>
          )}
        </div>
        {reports.length === 0 ? (
          <p className="muted">
            {hasSession
              ? 'Você ainda não enviou relatos por este aparelho.'
              : 'Os relatos que você enviar por este aparelho aparecem aqui, com a situação de cada um.'}
          </p>
        ) : (
          <ul className="report-list">
            {reports.slice(0, 3).map((report) => (
              <li key={report.id}>
                <span className={`report-chip situation-${publicSituation(report.report_status)}`}>
                  {situationLabel(report.report_status)}
                </span>
                <span className="report-place">
                  {report.urmind_class
                    ? issueHeadline(report.urmind_class, report.severity)
                    : (reportPlace(report) ?? report.user_description ?? 'Relato enviado')}
                </span>
                {report.created_at && (
                  <time dateTime={report.created_at}>
                    {new Date(report.created_at).toLocaleDateString('pt-BR')}
                  </time>
                )}
                <a href={`#/processando/${report.id}`}>Acompanhar</a>
              </li>
            ))}
          </ul>
        )}
      </section>
      <div className="section-heading home-section">
        <h2>Ocorrências confirmadas</h2>
        <a className="text-button" href="#/map">
          Abrir mapa
        </a>
      </div>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <div className="home-lead">
        <Suspense fallback={<p role="status">Carregando mapa…</p>}>
          <UrbanMap
            events={events}
            // Só uma escolha explícita leva o mapa até o ponto; de início ele enquadra todos.
            selectedId={selected}
            onSelect={setSelected}
            showFilters={false}
            emptyMessage="Nenhuma ocorrência publicada ainda."
          />
        </Suspense>
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
                Depois da revisão da equipe, cada ocorrência aparece aqui com tipo, gravidade e o
                que fazer.
              </p>
              <a className="text-button" href="#/registrar">
                Registrar a primeira evidência
              </a>
            </div>
          )}
        </section>
      </div>
      <EventFeed events={events} selectedId={current} onSelect={(event) => setSelected(event.id)} />
    </>
  );
}

// ------------------------------------------------------------------ mapa e lista

/** Tipo de problema com as opções que existem nas ocorrências carregadas. */
function Filters({
  urmindClass,
  options,
  onClass,
}: {
  urmindClass: string;
  options: [string, string][];
  onClass: (value: string) => void;
}) {
  if (options.length < 2 && !urmindClass) return null;
  return (
    <div className="filters panel">
      <div>
        <label htmlFor="public-class">Tipo de problema</label>
        <select id="public-class" value={urmindClass} onChange={(e) => onClass(e.target.value)}>
          <option value="">Todos os tipos</option>
          {options.map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </div>
    </div>
  );
}

type MapKind = 'all' | 'mine' | 'published';
const MAP_KINDS: [MapKind, string][] = [
  ['all', 'Tudo'],
  ['mine', 'Meus relatos'],
  ['published', 'Confirmados'],
];

export function PublicMapPage({
  events,
  reports = [],
}: {
  events: PublicEvent[];
  reports?: CaptureMarker[];
}) {
  const query = () => new URLSearchParams(location.hash.split('?')[1] ?? '');
  const [selected, setSelected] = useState<string | null>(() => query().get('ponto'));
  const [kind, setKind] = useState<MapKind>(() => {
    // O antigo endereço de exemplos revisados abre direto nas ocorrências confirmadas.
    if (location.hash.startsWith('#/demo')) return 'published';
    const requested = query().get('mostrar');
    return MAP_KINDS.some(([value]) => value === requested) ? (requested as MapKind) : 'all';
  });
  const [pointDetail, setPointDetail] = useState<PublicEventDetail | null>(null);
  const [pointError, setPointError] = useState('');
  const [photo, setPhoto] = useState<{ id: string; url: string } | null>(null);
  const { data: generic } = usePublicData((signal) => publicApi.captureMarkers(signal), []);
  const own = reports.find((report) => report.id === selected) ?? null;
  const isGeneric = Boolean(selected && generic?.some((marker) => marker.id === selected));
  useEffect(() => {
    const controller = new AbortController();
    setPointDetail(null);
    setPointError('');
    // A deep link may refer to a publication newer than the cached map page,
    // or outside its bounded listing. The publication endpoint is the authority.
    // Own reports and other people's generic markers are not publications.
    if (selected && !own && !isGeneric) {
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
  }, [selected, own, isGeneric]);
  // The author's own original photo: private, served only to the owner's session.
  const ownId = own?.id;
  useEffect(() => {
    const controller = new AbortController();
    setPhoto(null);
    if (ownId)
      api
        .captureImage(ownId, controller.signal)
        .then(({ image_url }) => {
          if (!controller.signal.aborted) setPhoto({ id: ownId, url: image_url });
        })
        .catch(() => undefined);
    return () => controller.abort();
  }, [ownId]);
  const markers = useMemo(() => {
    if (kind === 'mine' && reports.length) return reports;
    if (kind === 'published' && events.length) return events;
    const reportIds = new Set(reports.map((report) => report.public_id));
    const eventIds = new Set(reports.map((report) => report.event_public_id));
    return [
      ...events.filter((event) => !eventIds.has(event.id)),
      ...(generic ?? []).filter((report) => !reportIds.has(report.id)),
      ...reports,
    ];
  }, [events, reports, generic, kind]);
  // "Meus relatos" e "Confirmados" só aparecem quando têm pontos e diferem de "Tudo".
  const located = (rows: Array<{ latitude?: number | null; longitude?: number | null }>) =>
    rows.filter((row) => row.latitude != null && row.longitude != null).length;
  const everything = located([...events, ...(generic ?? []), ...reports]);
  const views = MAP_KINDS.filter(([value]) => {
    if (value === 'all') return true;
    const count = located(value === 'mine' ? reports : events);
    return count > 0 && count < everything;
  });
  const shownKind: MapKind = views.some(([value]) => value === kind) ? kind : 'all';
  function changeKind(next: MapKind) {
    setKind(next);
    setSelected(null);
    const params = query();
    params.delete('ponto');
    if (next === 'all') params.delete('mostrar');
    else params.set('mostrar', next);
    history.replaceState(null, '', `#/mapa${params.size ? `?${params}` : ''}`);
  }
  return (
    <>
      <div className="page-heading public">
        <div>
          <h1>Mapa</h1>
          <p>Ocorrências confirmadas pela equipe e, se você enviou, os seus relatos.</p>
        </div>
      </div>
      {views.length > 1 && (
        <div className="segmented" role="radiogroup" aria-label="Mostrar no mapa">
          {views.map(([value, label]) => (
            <label key={value} className={shownKind === value ? 'active' : undefined}>
              <input
                type="radio"
                name="map-kind"
                value={value}
                checked={shownKind === value}
                onChange={() => changeKind(value)}
              />
              {label}
            </label>
          ))}
        </div>
      )}
      <Suspense fallback={<p role="status">Carregando mapa…</p>}>
        <UrbanMap
          key={shownKind}
          events={markers}
          selectedId={selected}
          emptyMessage={
            shownKind === 'mine'
              ? 'Seus relatos com localização aparecem aqui.'
              : 'Nenhuma ocorrência publicada ainda.'
          }
          audience="public"
          onSelect={(id) => {
            setSelected(id);
            if (events.some((event) => event.id === id)) {
              const params = query();
              params.set('ponto', id);
              history.replaceState(null, '', `#/mapa?${params}`);
            }
          }}
          onCloseDetail={() => setSelected(null)}
          detail={
            selected &&
            (own ? (
              <OwnReportDetail report={own} photoUrl={photo?.id === own.id ? photo.url : null}>
                <a href={`#/processando/${own.id}`}>Acompanhar relato</a>
              </OwnReportDetail>
            ) : isGeneric ? (
              <p>Relato de outra pessoa, ainda em análise. Foto e descrição não são públicas.</p>
            ) : (
              <>
                {pointError && <p role="alert">{pointError}</p>}
                {pointDetail && pointDetail.id === selected ? (
                  <>
                    <h2>{labelFor(pointDetail.urmind_class)}</h2>
                    <p>
                      <SeverityTag severity={pointDetail.risk?.severity ?? pointDetail.severity} />
                    </p>
                    <ExperimentalBadge stage={pointDetail.model_stage} />
                    {pointDetail.image.available && pointDetail.image.url && (
                      <img
                        src={pointDetail.image.url}
                        alt="Foto publicada e sanitizada da ocorrência"
                      />
                    )}
                    <p>{pointDetail.road_name ?? 'Endereço aproximado indisponível'}</p>
                    {pointDetail.latitude != null && pointDetail.longitude != null && (
                      <a
                        href={`https://www.google.com/maps/dir/?api=1&destination=${pointDetail.latitude.toFixed(4)},${pointDetail.longitude.toFixed(4)}`}
                        target="_blank"
                        rel="noopener noreferrer"
                      >
                        Como chegar
                      </a>
                    )}
                    <a href={`#/resultado/${pointDetail.id}`}>Abrir resultado</a>
                  </>
                ) : (
                  !pointError && <p role="status">Carregando detalhe…</p>
                )}
              </>
            ))
          }
        />
      </Suspense>
      {shownKind !== 'mine' && (
        <EventFeed
          events={events}
          selectedId={selected}
          onSelect={(event) => openEvent(event.id)}
          title="Ocorrências confirmadas"
          emptyHint="Nenhuma ocorrência publicada ainda. Relatos aparecem aqui depois da revisão."
        />
      )}
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
  // Só tipos presentes; com um tipo escolhido, ele continua na lista para voltar a "Todos".
  const present = new Set(events.map((event) => event.urmind_class));
  if (filters.urmind_class) present.add(filters.urmind_class);
  const classOptions = [...present]
    .map((code): [string, string] => [code, labelFor(code)])
    .sort((a, b) => a[1].localeCompare(b[1], 'pt-BR'));
  return (
    <>
      <div className="page-heading public">
        <div>
          <h1>Ocorrências confirmadas</h1>
          <p>Cada ocorrência com tipo, gravidade, local e situação.</p>
        </div>
      </div>
      <Filters
        urmindClass={filters.urmind_class}
        options={classOptions}
        onClass={(urmind_class) => onFilters({ ...filters, urmind_class, status: '' })}
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
          <ExperimentalBadge stage={event.model_stage} />
          <h1>{labelFor(event.urmind_class)}</h1>
          <p>
            <span className={`risk-tag risk-${severity.level}`}>
              <i aria-hidden="true">{severity.shape}</i> {severity.label}
            </span>{' '}
            · {situationLabel(event.status)} · prioridade {priorityBand(event.risk?.priority_score)}
          </p>
          <details className="technical-details">
            <summary>Detalhes técnicos</summary>
            <p>
              Confiança visual{' '}
              {event.visual_confidence != null
                ? `${(event.visual_confidence * 100).toFixed(1)}%`
                : 'não disponível'}
              {' · '}
              {statuses[event.status as keyof typeof statuses] ?? event.status}
            </p>
          </details>
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
          <h1>Como o UrMind analisou</h1>
          <p>Modelo, dados, métricas medidas e limites declarados. Sem número estimado.</p>
        </div>
      </div>
      <section className="panel" aria-label="Pipeline de análise">
        <ol className="pipeline">
          <li>
            <strong>Imagem</strong>
            <span>foto enviada pelo celular ou capturada na detecção ao vivo</span>
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
              <dd>
                {data.classes.length ? data.classes.map(labelFor).join(', ') : 'não disponível'}
              </dd>
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

/** Diagnóstico técnico, fora da navegação do cidadão: API, banco, detector e cobertura. */
export function PublicSystemPage({
  status,
  error,
  onReload,
}: {
  status: PublicStatus | null;
  error: string;
  onReload: () => void;
}) {
  return (
    <>
      <div className="page-heading public">
        <div>
          <h1>Estado público</h1>
          <p>Cada componente com seu estado real, sem reduzir tudo a online ou offline.</p>
        </div>
        <button className="secondary" onClick={onReload}>
          Atualizar
        </button>
      </div>
      <SystemStatusBar status={status} error={error} />
      <div className="detail-grid">
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

/**
 * O que o modelo atual reconhece sozinho, em destaque; as demais categorias ficam
 * recolhidas, como relato com foto avaliado pela equipe, nunca como detecção automática.
 */
export function TaxonomyPanel({
  revision,
  includeDevelopment = false,
}: {
  revision: number;
  /** Só a área da equipe lista as categorias ainda sem detecção automática. */
  includeDevelopment?: boolean;
}) {
  const { data, error } = usePublicData<IssueTaxonomy>(
    (signal) => publicApi.taxonomy(signal),
    [revision],
  );
  if (error) return null;
  if (!data) return <Skeleton lines={3} />;
  const order = automaticClassCodes();
  const rank = (code: string) => (order.includes(code) ? order.indexOf(code) : order.length);
  const automatic = data.issues
    .filter((issue) => issue.model_may_emit)
    .sort((a, b) => rank(a.issue_code) - rank(b.issue_code));
  const others = data.issues.filter((issue) => !issue.model_may_emit);
  const item = (issue: IssueTaxonomy['issues'][number]) => (
    <li key={issue.issue_code} data-support={issue.model_support_status}>
      <strong>{issue.display_name_pt}</strong>
      <span className="badge">{modelSupportLabel(issue)}</span>
      {issue.limitations?.map((limitation) => (
        <small key={limitation}>{limitation}</small>
      ))}
    </li>
  );
  return (
    <section className="panel" aria-label="Classes de problemas urbanos">
      <div className="section-heading">
        <h2>Problemas urbanos</h2>
        <span className="muted">{data.taxonomy_version}</span>
      </div>
      <p className="muted">
        A detecção automática reconhece só os tipos abaixo. Outros problemas podem ser relatados com
        foto e são avaliados pela equipe.
      </p>
      <ul className="taxonomy-list">{automatic.map(item)}</ul>
      {includeDevelopment && others.length > 0 && (
        <details>
          <summary>Outros problemas, avaliados pela equipe ({others.length})</summary>
          <ul className="taxonomy-list">{others.map(item)}</ul>
        </details>
      )}
    </section>
  );
}
