import {
  lazy,
  Suspense,
  useEffect,
  useId,
  useRef,
  useState,
  type ReactNode,
  type RefObject,
} from 'react';
import { flushSync } from 'react-dom';
import { ImageOff, MapPin } from 'lucide-react';
import { api } from '../services/api';
import { publicApi } from '../services/publicApi';
import { type IssueDefinition, labelFor } from '../domain/public';
import { ConfirmButton } from './ConfirmButton';
import { plain } from '../content/plainLanguage';
import { photoReasonList } from '../content/photoReasons';
import { useAppState } from '../app/AppState';
import { ExperimentalBadge } from './public/Diagnosis';
import { PageHeader, Skeleton } from './ui';
import { LOCATION_SOURCES } from './reports/format';
import {
  severities,
  statuses,
  statusTones,
  type Coordinate,
  type EventDetail as Detail,
  type ReviewPayload,
} from '../domain/contracts';

const UrbanMap = lazy(() => import('./UrbanMap'));

// Mesmos códigos de backend/app/ml/tabular.py (REJECTION_REASONS): só erro visual
// vira rótulo negativo de review_confirmed.
const rejectionReasons = {
  erro_visual: 'A foto não mostra o problema indicado',
  duplicidade: 'Relato repetido',
  localizacao: 'Localização ausente ou errada',
  imagem_inconclusiva: 'Foto insuficiente para decidir',
} as const;

/** Motivos de recusa de um relato (os mesmos códigos; "repetido" tem ação própria). */
const reportRejectionReasons = {
  erro_visual: rejectionReasons.erro_visual,
  imagem_inconclusiva: rejectionReasons.imagem_inconclusiva,
  localizacao: rejectionReasons.localizacao,
} as const;

/** Decisões de revisão, como aparecem no histórico: o voto de uma pessoa da equipe. */
const DECISIONS: Record<string, string> = {
  confirm: 'Uma pessoa da equipe confirmou',
  correct: 'Uma pessoa da equipe corrigiu',
  reject: 'Uma pessoa da equipe recusou',
  duplicate: 'Marcado como repetido',
};

/** Área que costuma resolver cada tipo de problema (responsibility_domain da taxonomia). */
const DOMAINS: Record<string, string> = {
  ROAD_MAINTENANCE: 'manutenção de vias',
  URBAN_FORESTRY: 'arborização',
  DRAINAGE: 'drenagem',
  URBAN_CLEANING: 'limpeza urbana',
  PEDESTRIAN_INFRASTRUCTURE: 'calçadas e acessibilidade',
  TRAFFIC_AUTHORITY: 'trânsito',
  PUBLIC_LIGHTING: 'iluminação pública',
  CIVIL_DEFENSE: 'defesa civil',
  GENERAL_INSPECTION: 'fiscalização',
  PUBLIC_TRANSPORT: 'transporte público',
  POWER_NETWORK: 'rede elétrica',
};

/** Resultado da checagem automática da foto (porteiro); os motivos vêm de photoReasons. */
const GATE: Record<string, string> = {
  ACCEPTED: 'Aceita',
  NEEDS_REVIEW: 'A conferir pela equipe',
  REJECTED: 'Recusada',
};

/** Serviços perto do ponto (Overpass), como aparecem na ocorrência. */
const NEARBY: Record<'school' | 'health' | 'crossing', string> = {
  school: 'escola',
  health: 'saúde',
  crossing: 'travessia',
};

const when = (iso: string) =>
  new Date(iso).toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' });
/** Primeira letra maiúscula: frases montadas de pedaços começam como frase. */
const sentence = (text: string) => text.charAt(0).toUpperCase() + text.slice(1);

/** Números da tela no formato brasileiro (vírgula decimal). */
const fixed = (value: number, digits: number) =>
  new Intl.NumberFormat('pt-BR', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(value);
const meters = (value: number) =>
  `${new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 1 }).format(value)} m`;
const percent = new Intl.NumberFormat('pt-BR', { style: 'percent', maximumFractionDigits: 1 });

/**
 * Coordenada com 5 casas (cerca de 1 m), com ponto decimal: o formato que os mapas
 * aceitam ao colar. Os campos aceitam vírgula ou ponto.
 */
const coordinate = (value: number) => value.toFixed(5);

type LocationErrors = { latitude?: string; longitude?: string };

/**
 * Local digitado: o ponto, ou o erro de cada campo (só o campo errado fica marcado).
 * Os dois vazios valem: não há o que corrigir.
 */
function checkLocation(
  latitude: string,
  longitude: string,
): { point: Coordinate | null; errors: LocationErrors } {
  const lat = latitude.trim();
  const lon = longitude.trim();
  if (!lat && !lon) return { point: null, errors: {} };
  const latValue = Number(lat.replace(',', '.'));
  const lonValue = Number(lon.replace(',', '.'));
  const errors: LocationErrors = {};
  if (!lat) errors.latitude = 'Falta a latitude.';
  else if (!Number.isFinite(latValue) || latValue < -90 || latValue > 90)
    errors.latitude = 'Esse número não é uma latitude. Exemplo: -23.55052. Ou marque no mapa.';
  if (!lon) errors.longitude = 'Falta a longitude.';
  else if (!Number.isFinite(lonValue) || lonValue < -180 || lonValue > 180)
    errors.longitude = 'Esse número não é uma longitude. Exemplo: -46.63331. Ou marque no mapa.';
  if (errors.latitude || errors.longitude) return { point: null, errors };
  return { point: { latitude: latValue, longitude: lonValue, accuracy_m: null }, errors };
}

/** O que falta no local, em uma frase para a barra de decisão. */
function locationProblem(errors: LocationErrors, missing: boolean): string {
  if (missing) return 'Marque o local antes de confirmar.';
  if (errors.latitude && errors.longitude) return 'Confira a latitude e a longitude digitadas.';
  return errors.latitude ? 'Confira a latitude digitada.' : 'Confira a longitude digitada.';
}

/** Leva a tela até o campo com erro (no meio dela, acima da barra fixa) e põe o foco nele. */
function showField(field: HTMLInputElement | null) {
  if (!field) return;
  const still = matchMedia('(prefers-reduced-motion: reduce)').matches;
  field.scrollIntoView({ block: 'center', behavior: still ? 'auto' : 'smooth' });
  field.focus({ preventScroll: true });
}

/** Endereço do Nominatim encurtado para o título: rua, número e bairro. */
function shortAddress(displayName: string): string {
  return displayName.split(',').slice(0, 3).join(',').trim();
}

/**
 * Notas de uma decisão. A recusa de ocorrência guarda o motivo como "[motivo:código]"
 * no começo das notas; na tela, vira o motivo em palavras.
 */
function decisionNotes(notes: string | null): string | null {
  if (!notes) return null;
  const match = /^\[motivo:([a-z_]+)\]\s*([\s\S]*)$/.exec(notes);
  if (!match) return notes;
  const reason = rejectionReasons[match[1] as keyof typeof rejectionReasons];
  return [reason ? `Motivo: ${reason}.` : null, match[2] || null].filter(Boolean).join(' ');
}

/** Cada etapa do histórico do relato, numa frase para quem enviou. */
function historyLine(stage: string, status: string): string {
  switch (stage) {
    case 'received':
      return 'Relato recebido';
    case 'photo_gate':
      return status === 'ACCEPTED'
        ? 'Foto aprovada na checagem'
        : status === 'NEEDS_REVIEW'
          ? 'Foto separada para a equipe conferir'
          : status === 'REJECTED'
            ? 'Foto recusada na checagem'
            : 'Checagem da foto sem registro';
    case 'location':
      return status === 'adjusted'
        ? 'Local ajustado'
        : status === 'location_required'
          ? 'Faltou o local da foto'
          : 'Local informado';
    case 'analysis':
      if (status === 'model_not_available') return 'Foto enviada direto para a equipe';
      if (status === 'experimental') return 'Análise automática em teste, sem publicação';
      if (['no_supported_detection', 'no_detection', 'no_event'].includes(status))
        return 'A análise automática não reconheceu um problema';
      if (['failed', 'inference_failed'].includes(status))
        return 'A análise automática não terminou';
      if (['needs_review', 'triage_required'].includes(status))
        return 'Encaminhado para a revisão da equipe';
      if (status === 'location_required') return 'Análise à espera do local';
      if (['completed', 'analysis_completed', 'inference_completed'].includes(status))
        return 'Análise automática concluída';
      return status === 'unknown' ? 'Análise sem registro' : 'Análise automática em andamento';
    case 'review':
      return status === 'confirmed'
        ? 'Confirmado pela equipe'
        : status === 'rejected'
          ? 'A equipe não confirmou um problema'
          : status === 'duplicate'
            ? 'Marcado como relato repetido'
            : status === 'triage_required'
              ? 'A equipe está definindo quem resolve'
              : status === 'attached'
                ? 'Foto anexada a outro relato'
                : status === 'detached'
                  ? 'A foto voltou a ser um relato próprio'
                  : 'Em revisão pela equipe';
    case 'publication':
      return status === 'withdrawn' ? 'Retirado do mapa público' : 'Publicado no mapa';
    default:
      return 'Etapa registrada';
  }
}

/** Histórico do relato para quem enviou: cada etapa, quando e de onde veio. */
export function OwnerReportTimeline({ id, revision }: { id: string; revision: number }) {
  const [rows, setRows] = useState<Awaited<ReturnType<typeof api.captureTimeline>> | null>(null);
  const [error, setError] = useState('');
  const [expanded, setExpanded] = useState(false);
  useEffect(() => {
    if (!expanded) return;
    const controller = new AbortController();
    setRows(null);
    setError('');
    void api
      .captureTimeline(id, controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) setRows(value);
      })
      .catch((reason: Error) => {
        if (!controller.signal.aborted) setError(reason.message);
      });
    return () => controller.abort();
  }, [id, revision, expanded]);
  const sources: Record<string, string> = {
    ...LOCATION_SOURCES,
    pwa_photo: 'foto tirada no aplicativo',
    exif_upload: 'foto da galeria',
    human_review: 'pela equipe',
  };
  return (
    <details
      className="report-timeline"
      onToggle={(event) => setExpanded(event.currentTarget.open)}
    >
      <summary>Histórico do relato</summary>
      {error && <p role="alert">{error}</p>}
      {!rows && !error && expanded && <p role="status">Consultando histórico…</p>}
      <ol>
        {rows?.map((row, index) => (
          <li key={index}>
            <strong>{historyLine(row.stage, row.status)}</strong>
            <small>
              {row.at ? <time dateTime={row.at}>{when(row.at)}</time> : 'Data não registrada'}
              {row.source && sources[row.source] && `, ${sources[row.source]}`}
            </small>
            {row.reasons?.length ? <p>{photoReasonList(row.reasons)}</p> : null}
          </li>
        ))}
      </ol>
    </details>
  );
}

/** Uma linha rótulo/valor; `null` some, para não encher a tela de "não disponível". */
function Fact({ term, children }: { term: string; children: ReactNode }) {
  if (children == null || children === false) return null;
  return (
    <div>
      <dt>{term}</dt>
      <dd>{children}</dd>
    </div>
  );
}

/**
 * Corrigir o local: um toque no mapa marca o ponto, ou as coordenadas são digitadas.
 * O erro aparece junto do campo errado, marcado como inválido para o leitor de tela.
 */
function LocationCorrection({
  current,
  source,
  latitude,
  longitude,
  onChange,
  errors,
  latitudeField,
  longitudeField,
}: {
  current: { latitude: number; longitude: number } | null;
  /** De onde veio o ponto atual (GPS do aparelho, dados da foto…), em palavras. */
  source?: string;
  latitude: string;
  longitude: string;
  onChange: (latitude: string, longitude: string) => void;
  errors: LocationErrors;
  latitudeField: RefObject<HTMLInputElement | null>;
  longitudeField: RefObject<HTMLInputElement | null>;
}) {
  const [showMap, setShowMap] = useState(false);
  // O mapa só vai até o que foi digitado (ou volta ao ponto atual); arrastar já marca.
  const [typed, setTyped] = useState<Coordinate | null>(null);
  const [typing, setTyping] = useState(false);
  const id = useId();
  const type = (nextLatitude: string, nextLongitude: string) => {
    onChange(nextLatitude, nextLongitude);
    setTyped(checkLocation(nextLatitude, nextLongitude).point);
  };
  const picked = checkLocation(latitude, longitude).point;
  const invalid = Boolean(errors.latitude || errors.longitude);
  return (
    <>
      <p className="location-now">
        {current ? (
          <>
            Ponto atual:{' '}
            <span translate="no">
              {coordinate(current.latitude)}, {coordinate(current.longitude)}
            </span>
            {source && <small>{source}</small>}
          </>
        ) : (
          'Ainda sem ponto: marque no mapa onde fica o problema.'
        )}
      </p>
      {showMap ? (
        <div className="location-picker">
          <p className="field-note">Arraste o mapa até o alvo ficar sobre o local certo.</p>
          <Suspense fallback={<Skeleton lines={4} />}>
            <UrbanMap
              events={[]}
              audience="team"
              showFilters={false}
              initialCenter={current ?? undefined}
              initialZoom={17}
              pickedPoint={typed}
              onPickLocation={(lat, lon) => {
                const same =
                  current &&
                  coordinate(lat) === coordinate(current.latitude) &&
                  coordinate(lon) === coordinate(current.longitude);
                onChange(same ? '' : coordinate(lat), same ? '' : coordinate(lon));
              }}
            />
          </Suspense>
        </div>
      ) : (
        <button type="button" className="secondary" onClick={() => setShowMap(true)}>
          <MapPin size={16} aria-hidden="true" /> {current ? 'Conferir no mapa' : 'Marcar no mapa'}
        </button>
      )}
      {picked && (
        <p className="location-new">
          Ponto novo:{' '}
          <span translate="no">
            {coordinate(picked.latitude)}, {coordinate(picked.longitude)}
          </span>
          , salvo junto com a decisão.{' '}
          <button
            type="button"
            className="text-button"
            onClick={() => {
              onChange('', '');
              setTyped(current ? { ...current, accuracy_m: null } : null);
            }}
          >
            {current ? 'Manter o ponto atual' : 'Desfazer'}
          </button>
        </p>
      )}
      <details
        className="coordinates"
        open={typing || invalid}
        onToggle={(event) => setTyping(event.currentTarget.open)}
      >
        <summary>Digitar coordenadas</summary>
        <div className="field-row">
          <div>
            <label htmlFor={`${id}-lat`}>Latitude</label>
            <input
              id={`${id}-lat`}
              ref={latitudeField}
              inputMode="decimal"
              autoComplete="off"
              spellCheck={false}
              placeholder="Ex.: -23.55052"
              value={latitude}
              aria-invalid={errors.latitude ? true : undefined}
              aria-describedby={errors.latitude ? `${id}-lat-error` : undefined}
              onChange={(e) => type(e.target.value, longitude)}
            />
            {errors.latitude && (
              <p id={`${id}-lat-error`} className="field-error is-block">
                {errors.latitude}
              </p>
            )}
          </div>
          <div>
            <label htmlFor={`${id}-lon`}>Longitude</label>
            <input
              id={`${id}-lon`}
              ref={longitudeField}
              inputMode="decimal"
              autoComplete="off"
              spellCheck={false}
              placeholder="Ex.: -46.63331"
              value={longitude}
              aria-invalid={errors.longitude ? true : undefined}
              aria-describedby={errors.longitude ? `${id}-lon-error` : undefined}
              onChange={(e) => type(latitude, e.target.value)}
            />
            {errors.longitude && (
              <p id={`${id}-lon-error`} className="field-error is-block">
                {errors.longitude}
              </p>
            )}
          </div>
        </div>
      </details>
    </>
  );
}

/** Histórico de decisões da equipe, com o motivo em palavras. */
function DecisionHistory({
  reviews,
  report = false,
  duplicateOf,
}: {
  reviews: Array<{
    id?: string;
    decision: string;
    corrected_class?: string | null;
    notes?: string | null;
    created_at: string;
  }>;
  /** Revisão de relato: confirmar grava "correct" com o tipo, que é uma confirmação. */
  report?: boolean;
  /** Protocolo de que o relato é repetido (vale para a última decisão). */
  duplicateOf?: string;
}) {
  const name = (item: (typeof reviews)[number], last: boolean) => {
    if (item.decision === 'reject' && last && duplicateOf)
      return `Marcado como repetido de ${duplicateOf}`;
    if (report && item.decision === 'correct')
      return item.corrected_class
        ? `Uma pessoa da equipe confirmou como ${labelFor(item.corrected_class)}`
        : 'Uma pessoa da equipe confirmou';
    if (item.decision === 'correct')
      return item.corrected_class
        ? `Uma pessoa da equipe corrigiu para ${labelFor(item.corrected_class)}`
        : 'Uma pessoa da equipe corrigiu o local';
    return DECISIONS[item.decision] ?? 'Decisão registrada';
  };
  return (
    <ul className="review-history">
      {reviews.map((item, index) => (
        <li key={item.id ?? item.created_at}>
          <time dateTime={item.created_at}>{when(item.created_at)}</time>{' '}
          {name(item, index === reviews.length - 1)}
          {decisionNotes(item.notes ?? null) && <small>{decisionNotes(item.notes ?? null)}</small>}
        </li>
      ))}
    </ul>
  );
}

/**
 * Revisão de um relato pela equipe: tipo, local, publicação da foto e histórico.
 * `level` é o nível do título do painel: 1 quando ele é a página inteira (o h1 fica no
 * cabeçalho da página), 2 logo abaixo do título da página e 3 dentro do detalhe do mapa.
 * As mesmas palavras do detalhe da ocorrência: "Confirmar", "Recusar", "Notas".
 */
export function CaptureReviewPanel({
  id,
  onChanged,
  level = 3,
}: {
  id: string;
  onChanged: () => void;
  level?: 1 | 2 | 3;
}) {
  const Title = level === 3 ? 'h3' : 'h2';
  const Subtitle = level === 1 ? 'h2' : level === 2 ? 'h3' : 'h4';
  const [detail, setDetail] = useState<Awaited<ReturnType<typeof api.captureReview>> | null>(null);
  const [issues, setIssues] = useState<IssueDefinition[]>([]);
  const [issue, setIssue] = useState('');
  const [notes, setNotes] = useState('');
  const [duplicate, setDuplicate] = useState('');
  const [latitude, setLatitude] = useState('');
  const [longitude, setLongitude] = useState('');
  const [locationErrors, setLocationErrors] = useState<LocationErrors>({});
  const [privacy, setPrivacy] = useState(false);
  const [adjudicate, setAdjudicate] = useState(false);
  const [admin, setAdmin] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [decisionError, setDecisionError] = useState('');
  const [done, setDone] = useState('');
  const [revision, setRevision] = useState(0);
  const [originalUrl, setOriginalUrl] = useState<string | null>(null);
  // Enquanto a recusa pergunta, "Confirmar relato" sai da barra.
  const [rejecting, setRejecting] = useState(false);
  const [rejectReason, setRejectReason] = useState<keyof typeof reportRejectionReasons | ''>('');
  // Situação da publicação (registrada na ocorrência): "published", "withdrawn" ou nada.
  const [publication, setPublication] = useState<{ status?: string; photo: boolean } | null>(null);
  const doneMessage = useRef<HTMLParagraphElement>(null);
  const latitudeField = useRef<HTMLInputElement>(null);
  const longitudeField = useRef<HTMLInputElement>(null);
  const issueField = useRef<HTMLSelectElement>(null);
  const issueId = useId();
  useEffect(() => {
    const controller = new AbortController();
    setError('');
    setPrivacy(false);
    setOriginalUrl(null);
    Promise.all([
      api.captureReview(id, controller.signal),
      publicApi.taxonomy(controller.signal),
      api.me(controller.signal),
      api.captureImage(id, controller.signal).catch(() => null),
    ])
      .then(([report, taxonomy, me, image]) => {
        if (!controller.signal.aborted) {
          setDetail(report);
          setIssues(taxonomy.issues);
          setAdmin(me.can_admin);
          setOriginalUrl(image?.image_url ?? null);
          // Publicação: lida da ocorrência, quando ela existe.
          const eventId = report.events[0]?.id;
          if (eventId)
            void api
              .event(eventId, controller.signal)
              .then((item) => {
                if (controller.signal.aborted) return;
                const state = (item.factors as { publication?: Record<string, unknown> })
                  .publication;
                setPublication({
                  status: typeof state?.status === 'string' ? state.status : undefined,
                  photo: Boolean(state?.public_image),
                });
              })
              .catch(() => undefined);
          // O tipo da última decisão da equipe já vem escolhido: quem revisa só confere.
          const decided =
            report.reviews.at(-1)?.corrected_class ??
            (typeof report.human_review?.class === 'string' ? report.human_review.class : '');
          setIssue((current) => current || decided || '');
        }
      })
      .catch((reason: Error) => {
        if (!controller.signal.aborted) setError(reason.message);
      });
    return () => controller.abort();
  }, [id, revision]);
  const run = async (operation: () => Promise<unknown>, message: string) => {
    setBusy(true);
    setError('');
    setDecisionError('');
    setDone('');
    try {
      await operation();
      setRevision((value) => value + 1);
      setDone(message);
      onChanged();
    } catch (reason) {
      setDecisionError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  };
  useEffect(() => {
    if (done) doneMessage.current?.focus();
  }, [done]);
  const event = detail?.events[0];
  const lastReview = detail?.reviews.at(-1);
  const routing = issues.find((item) => item.issue_code === (issue || lastReview?.corrected_class));
  const chosen = issue ? routing : undefined;
  const publishedIssueCode =
    lastReview?.corrected_class || detail?.human_review?.class || event?.urmind_class;
  const publishedIssue = issues.find((item) => item.issue_code === publishedIssueCode);
  const gate = detail?.photo_gate as { status?: string; reasons?: string[] } | null | undefined;
  // Ocorrência confirmada vai para o mapa público; só ela pode ser retirada ou ter foto.
  const onPublicMap = event?.status === 'confirmed';
  // Uma confirmação já feita e o relato ainda sem confirmar: falta a segunda pessoa.
  const waitingSecond =
    lastReview && ['correct', 'confirm'].includes(lastReview.decision) && !onPublicMap;

  /** Confirma o tipo (e o local, se marcado); cada falta aparece junto do que falta. */
  const confirm = () => {
    setDecisionError('');
    setLocationErrors({});
    if (!issue) {
      setDecisionError('Escolha o tipo de problema antes de confirmar.');
      issueField.current?.focus();
      return;
    }
    if (routing?.photo_review_allowed !== true) {
      setDecisionError(
        routing?.photo_review_block_reason
          ? plain(routing.photo_review_block_reason)
          : 'Este tipo de problema não pode ser confirmado só pela foto.',
      );
      issueField.current?.focus();
      return;
    }
    const { point, errors } = checkLocation(latitude, longitude);
    if (!detail?.location && !point && !errors.latitude && !errors.longitude)
      errors.latitude = 'Marque o local no mapa ou digite a latitude e a longitude.';
    if (errors.latitude || errors.longitude) {
      flushSync(() => setLocationErrors(errors));
      setDecisionError(locationProblem(errors, !detail?.location && !point));
      showField((errors.latitude ? latitudeField : longitudeField).current);
      return;
    }
    void run(
      () =>
        api.reviewCapture(id, {
          decision: 'correct',
          notes,
          adjudicate,
          corrected_class: issue,
          ...(point ? { corrected_location: point } : {}),
        }),
      'Decisão registrada.',
    );
  };
  const reject = (duplicateOf?: string, reason?: string) =>
    void run(
      () =>
        api.reviewCapture(id, {
          decision: 'reject',
          // O motivo segue o mesmo formato da recusa de ocorrência ("[motivo:código]").
          notes: [reason && `[motivo:${reason}]`, notes].filter(Boolean).join(' '),
          adjudicate,
          ...(duplicateOf ? { duplicate_of_protocol: duplicateOf } : {}),
        }),
      duplicateOf ? 'Relato marcado como repetido.' : 'Relato recusado.',
    );

  // Local a conferir (GPS e foto em lugares diferentes) ou faltando: aviso curto.
  const locationFlag = !detail
    ? null
    : !detail.location
      ? { alone: 'Falta o local: marque abaixo.', fact: 'Falta marcar: veja abaixo.' }
      : detail.location_conflict
        ? { alone: 'Local a conferir: veja abaixo.', fact: 'A conferir: veja abaixo.' }
        : null;
  const locationSource = detail?.location
    ? sentence(
        [
          LOCATION_SOURCES[detail.location_source],
          detail.location.accuracy_m != null &&
            `precisão de ${Math.round(detail.location.accuracy_m)} m`,
        ]
          .filter(Boolean)
          .join(', '),
      ) || undefined
    : undefined;

  return (
    <section
      aria-label="Revisão do relato"
      className={`review-panel${level === 1 ? ' is-page' : ''}`}
    >
      {level !== 1 && <Title>Revisar relato</Title>}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {!detail ? (
        <p role="status" className="loading-line">
          Carregando relato…
        </p>
      ) : (
        <>
          {/* Celular: o aviso de local vem antes da foto, já na primeira tela. */}
          {locationFlag && <p className="notice review-flag">{locationFlag.alone}</p>}
          <div className="review-evidence">
            {originalUrl ? (
              <figure className="review-photo">
                <img src={originalUrl} alt="Foto original privada do relato" />
                <figcaption>
                  <a
                    className="text-button"
                    href={originalUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    Abrir a foto original
                  </a>
                </figcaption>
              </figure>
            ) : (
              <div className="photo-placeholder review-photo">
                <ImageOff size={26} strokeWidth={1.6} aria-hidden="true" />
                <strong>Foto indisponível</strong>
              </div>
            )}
            <dl className="data-list">
              {/* Em tela própria, o protocolo já está no cabeçalho da página. */}
              {level !== 1 && (
                <Fact term="Protocolo">
                  <span className="record-id" translate="no">
                    {detail.protocol_code}
                  </span>
                </Fact>
              )}
              <Fact term="Descrição">{detail.user_description || 'Sem descrição'}</Fact>
              {locationFlag && (
                <div className="flag-fact">
                  <dt>Local</dt>
                  <dd>{locationFlag.fact}</dd>
                </div>
              )}
              {gate?.status && (
                <Fact term="Checagem automática da foto">
                  {GATE[gate.status] ?? 'Sem registro'}
                  {gate.reasons?.length ? <small>{photoReasonList(gate.reasons)}</small> : null}
                </Fact>
              )}
            </dl>
          </div>
          {/* O que já foi decidido vem antes do formulário: quem revisa parte daí. */}
          {detail.reviews.length > 0 && (
            <div className="review-past">
              <Subtitle>Decisões anteriores</Subtitle>
              <DecisionHistory
                reviews={detail.reviews}
                report
                duplicateOf={
                  typeof detail.human_review?.duplicate_of === 'string'
                    ? detail.human_review.duplicate_of
                    : undefined
                }
              />
              {waitingSecond && (
                <p className="muted">
                  Falta a decisão de mais uma pessoa da equipe para o relato ser confirmado.
                </p>
              )}
            </div>
          )}
          {detail.urban_auxiliary?.suggestions?.length ? (
            <p className="field-note">
              Sugestão automática, ainda em teste:{' '}
              {detail.urban_auxiliary.suggestions
                .map(
                  (item) =>
                    issues.find((entry) => entry.issue_code === item.code)?.display_name_pt ??
                    'tipo fora da lista',
                )
                .join(', ')}
              . Confira a foto; o tipo é sempre escolhido por você.
            </p>
          ) : null}
          {detail.additional_evidence && (
            <div className="notice" role="note">
              <p>Esta foto foi anexada a outro relato e não cria um ponto próprio.</p>
              <ConfirmButton
                label="Desvincular a foto"
                question="A foto deixa de fazer parte do outro relato e volta a ser um relato próprio, com ponto no mapa."
                confirmLabel="Desvincular"
                disabled={busy}
                onConfirm={() => void run(() => api.detachEvidence(id), 'Foto desvinculada.')}
              />
            </div>
          )}

          <div className="review-step">
            <div>
              <label htmlFor={issueId}>Tipo de problema</label>
              <select
                id={issueId}
                ref={issueField}
                value={issue}
                onChange={(e) => {
                  setIssue(e.target.value);
                  setDecisionError('');
                }}
              >
                <option value="">Escolha depois de olhar a foto</option>
                {issues.map((item) => (
                  <option key={item.issue_code} value={item.issue_code}>
                    {item.display_name_pt}
                  </option>
                ))}
              </select>
            </div>
            {routing?.photo_review_block_reason && (
              <p className="notice" role="status">
                {plain(routing.photo_review_block_reason)}
              </p>
            )}
            {chosen && (
              <div className="routing-note" role="note">
                <strong>Quem costuma resolver</strong>
                <p>
                  {chosen.display_name_pt} costuma ficar com a área de{' '}
                  {DOMAINS[chosen.responsibility_domain] ?? 'serviços da prefeitura'}. O UrMind não
                  encaminha pedidos automaticamente: registre no{' '}
                  <a
                    href="https://sp156.prefeitura.sp.gov.br/portal"
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    SP156
                  </a>
                  , o canal oficial da cidade de São Paulo.
                </p>
              </div>
            )}
          </div>

          <fieldset className="review-step">
            <legend>Local</legend>
            {detail.location_conflict && (
              <p className="notice">
                O GPS do aparelho e o local gravado na foto não coincidem. Confira no mapa e, se o
                ponto estiver errado, marque o local certo.
              </p>
            )}
            <LocationCorrection
              current={detail.location}
              source={locationSource}
              latitude={latitude}
              longitude={longitude}
              onChange={(nextLatitude, nextLongitude) => {
                setLatitude(nextLatitude);
                setLongitude(nextLongitude);
                setLocationErrors({});
              }}
              errors={locationErrors}
              latitudeField={latitudeField}
              longitudeField={longitudeField}
            />
          </fieldset>

          <label>
            Notas para a equipe (opcional)
            <textarea
              rows={2}
              value={notes}
              maxLength={2000}
              onChange={(e) => setNotes(e.target.value)}
            />
          </label>
          {admin && (
            <label>
              <input
                type="checkbox"
                checked={adjudicate}
                onChange={(e) => setAdjudicate(e.target.checked)}
              />
              {detail.reviews.length === 0
                ? 'Decidir como administrador, sem precisar de outra revisão'
                : 'Decidir como administrador: sua decisão substitui a anterior'}
            </label>
          )}
          {/* A decisão fica sempre à vista na tela própria (barra fixa no rodapé). */}
          <div className="review-actions">
            {decisionError && (
              <p className="error" role="alert">
                {decisionError}
              </p>
            )}
            <div className="actions">
              {/* No celular, só o verbo aparece; o nome completo segue para o leitor de tela. */}
              {!rejecting && (
                <button type="button" disabled={busy} onClick={confirm}>
                  <span>
                    Confirmar<span className="wide-only"> relato</span>
                  </span>
                </button>
              )}
              <ConfirmButton
                className="danger"
                label="Recusar relato"
                display={
                  <span>
                    Recusar<span className="wide-only"> relato</span>
                  </span>
                }
                question="O relato será recusado e não vai para o mapa público; quem enviou verá “Recusado” em Meus relatos. Escolha o motivo."
                confirmLabel="Recusar relato"
                confirmDisabled={!rejectReason}
                disabled={busy}
                onOpenChange={setRejecting}
                onConfirm={() => reject(undefined, rejectReason)}
              >
                <label htmlFor={`${issueId}-reason`}>Motivo</label>
                <select
                  id={`${issueId}-reason`}
                  value={rejectReason}
                  onChange={(e) =>
                    setRejectReason(e.target.value as keyof typeof reportRejectionReasons)
                  }
                >
                  <option value="">Escolha o motivo</option>
                  {Object.entries(reportRejectionReasons).map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </select>
              </ConfirmButton>
            </div>
            {done && (
              <p className="success" role="status" tabIndex={-1} ref={doneMessage}>
                {done}
              </p>
            )}
          </div>
          {detail.reviews.length === 0 && (
            <p className="muted">
              Um relato é confirmado com a decisão de duas pessoas da equipe ou de um administrador.
            </p>
          )}
          <details>
            <summary>Marcar como relato repetido</summary>
            <div className="inline-form">
              <label>
                Protocolo do relato original
                <input
                  value={duplicate}
                  autoComplete="off"
                  spellCheck={false}
                  placeholder="Ex.: URM-2026-000123…"
                  onChange={(e) => setDuplicate(e.target.value.toUpperCase())}
                />
              </label>
              <ConfirmButton
                label="Marcar como repetido"
                question={`O relato vai ficar como repetido do ${duplicate || 'protocolo informado'} e sai da fila.`}
                confirmLabel="Marcar como repetido"
                disabled={busy || !duplicate.trim()}
                confirmDisabled={!duplicate.trim()}
                onConfirm={() => reject(duplicate.trim())}
              />
            </div>
          </details>

          <fieldset className="review-step">
            <legend>Publicação da foto</legend>
            {onPublicMap && publication && (
              <p className="publication-state">
                {publication.status === 'withdrawn'
                  ? 'Retirada do mapa público.'
                  : publication.photo
                    ? 'No mapa público, com a foto.'
                    : 'No mapa público, ainda sem a foto.'}
              </p>
            )}
            {onPublicMap ? (
              <>
                <label>
                  <input
                    type="checkbox"
                    checked={privacy}
                    onChange={(e) => setPrivacy(e.target.checked)}
                  />
                  Confirmo que a foto pode ser publicada: sem rostos identificáveis, placas de
                  veículos ou dados pessoais.
                </label>
                <div className="actions">
                  <button
                    type="button"
                    disabled={
                      busy ||
                      !privacy ||
                      !lastReview ||
                      publishedIssue?.photo_review_allowed !== true
                    }
                    onClick={() =>
                      void run(
                        () =>
                          api.publishEvent(event!.id, {
                            publish: true,
                            review_id: lastReview!.id,
                            visible_content_reviewed: privacy,
                            reason: notes || 'Publicação após revisão humana',
                          }),
                        'Foto publicada.',
                      )
                    }
                  >
                    {publication?.status === 'withdrawn'
                      ? 'Publicar de novo, com a foto'
                      : 'Publicar foto'}
                  </button>
                  {publication?.status !== 'withdrawn' && (
                    <ConfirmButton
                      label="Retirar do mapa público"
                      question="A ocorrência sai do mapa público. O registro continua na área da equipe."
                      confirmLabel="Retirar do mapa"
                      disabled={busy}
                      onConfirm={() =>
                        void run(
                          () =>
                            api.publishEvent(event!.id, {
                              publish: false,
                              reason: notes || 'Retirada pela equipe revisora',
                            }),
                          'Ocorrência retirada do mapa público.',
                        )
                      }
                    />
                  )}
                </div>
              </>
            ) : (
              <p className="muted">
                A foto só pode ser publicada depois que o relato for confirmado, e só sem rostos,
                placas de veículos ou dados pessoais.
              </p>
            )}
          </fieldset>
        </>
      )}
    </section>
  );
}

/**
 * Ocorrência na área da equipe: evidência, dados, contexto e a decisão humana, num
 * formulário só (tipo, local e notas), com as mesmas palavras da revisão do relato.
 */
export function EventDetail({
  id,
  onChanged,
  refreshAt = 0,
}: {
  id: string;
  onChanged: () => void;
  refreshAt?: number;
}) {
  const [detail, setDetail] = useState<Detail | null>(null);
  const [issues, setIssues] = useState<IssueDefinition[]>([]);
  const [error, setError] = useState('');
  const [decisionError, setDecisionError] = useState('');
  const [done, setDone] = useState('');
  const [busy, setBusy] = useState(false);
  const [corrected, setCorrected] = useState('');
  const [revision, setRevision] = useState(0);
  const [latitude, setLatitude] = useState('');
  const [longitude, setLongitude] = useState('');
  const [locationErrors, setLocationErrors] = useState<LocationErrors>({});
  const [notes, setNotes] = useState('');
  const [rejectReason, setRejectReason] = useState<keyof typeof rejectionReasons | ''>('');
  const [rejecting, setRejecting] = useState(false);
  const eventDone = useRef<HTMLParagraphElement>(null);
  useEffect(() => {
    if (done) eventDone.current?.focus();
  }, [done]);
  const latitudeField = useRef<HTMLInputElement>(null);
  const longitudeField = useRef<HTMLInputElement>(null);
  const typeField = useRef<HTMLSelectElement>(null);
  const fieldId = useId();

  // Papel decidido no servidor (JWT app_metadata) e já lido pela área da equipe.
  const canReview = useAppState().team.canReview;

  useEffect(() => {
    const controller = new AbortController();
    setError('');
    Promise.all([api.event(id, controller.signal), publicApi.taxonomy(controller.signal)])
      .then(([data, taxonomy]) => {
        if (!controller.signal.aborted) {
          setDetail(data);
          setIssues(taxonomy.issues);
          // O tipo indicado já vem escolhido: confirmar é o caso comum.
          setCorrected((current) => current || data.urmind_class);
        }
      })
      .catch((reason: Error) => !controller.signal.aborted && setError(reason.message));
    return () => controller.abort();
  }, [id, revision, refreshAt]);

  async function review(payload: ReviewPayload, message: string) {
    setBusy(true);
    setDecisionError('');
    setDone('');
    try {
      await api.review(id, payload);
      setRevision((value) => value + 1);
      setDone(message);
      onChanged();
    } catch (reason) {
      setDecisionError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }

  /** Confirma: sem mudança, a confirmação; com tipo ou local novo, a correção. */
  function confirmOccurrence() {
    if (!detail) return;
    setDecisionError('');
    setLocationErrors({});
    const type = corrected || detail.urmind_class;
    const selected = issues.find((item) => item.issue_code === type);
    if (selected?.photo_review_allowed !== true) {
      setDecisionError(
        selected?.photo_review_block_reason
          ? plain(selected.photo_review_block_reason)
          : 'Este tipo de problema não pode ser confirmado só pela foto.',
      );
      typeField.current?.focus();
      return;
    }
    const { point, errors } = checkLocation(latitude, longitude);
    if (errors.latitude || errors.longitude) {
      flushSync(() => setLocationErrors(errors));
      // A ocorrência sempre tem ponto: só o que foi digitado pode estar errado.
      setDecisionError(locationProblem(errors, false));
      showField((errors.latitude ? latitudeField : longitudeField).current);
      return;
    }
    const typeChanged = type !== detail.urmind_class;
    void review(
      typeChanged || point
        ? {
            decision: 'correct',
            ...(typeChanged ? { corrected_class: type } : {}),
            ...(point ? { corrected_location: point } : {}),
            notes,
          }
        : { decision: 'confirm', notes },
      'Decisão registrada.',
    );
  }

  // Protocolo de revisão: antes da primeira decisão humana, quem revisa não vê
  // score do modelo, severidade, prioridade nem relatório gerado a partir deles.
  const blind = canReview && (detail?.reviews.length ?? 0) === 0;
  const responsibility = detail?.responsibility;
  const context = Object.fromEntries((detail?.context ?? []).map((item) => [item.source, item]));
  const address = context.nominatim_reverse;
  const rain = context.open_meteo_rain;
  const pois = context.overpass_pois;
  const nearest = (pois?.data.nearest ?? {}) as Record<string, { distance_m: number } | null>;
  const selectedIssue = issues.find((item) => item.issue_code === corrected);
  const place =
    address?.status === 'ok' && address.data.display_name
      ? shortAddress(String(address.data.display_name))
      : null;
  const point =
    detail?.latitude != null && detail.longitude != null
      ? { latitude: detail.latitude, longitude: detail.longitude }
      : null;
  return (
    <>
      <PageHeader
        title={detail ? labelFor(detail.urmind_class) : 'Ocorrência'}
        description={
          detail
            ? sentence(
                [place, `registrada em ${when(detail.occurred_at)}`].filter(Boolean).join(', '),
              )
            : undefined
        }
        back={{ href: '#/app/eventos', label: 'Ocorrências' }}
      />
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {!detail && !error && (
        <p role="status" className="loading-line">
          Carregando ocorrência…
        </p>
      )}
      {detail && (
        <section className="panel detail" aria-label="Detalhes da ocorrência">
          <ExperimentalBadge stage={detail.model_status} />
          {detail.image_url ? (
            <figure className="evidence-figure">
              <div className="evidence-frame">
                <img src={detail.image_url} alt="Evidência fotográfica da ocorrência" />
                {detail.detections.map((d) => (
                  <span
                    key={d.id}
                    className="bbox"
                    style={{
                      left: `${d.bbox.x * 100}%`,
                      top: `${d.bbox.y * 100}%`,
                      width: `${d.bbox.width * 100}%`,
                      height: `${d.bbox.height * 100}%`,
                    }}
                    title={
                      blind
                        ? labelFor(d.urmind_class)
                        : `${labelFor(d.urmind_class)} ${percent.format(d.confidence)}`
                    }
                  />
                ))}
              </div>
              {detail.detections.length > 0 && (
                <figcaption>As caixas marcam o que a análise automática apontou.</figcaption>
              )}
            </figure>
          ) : (
            <div className="photo-placeholder">
              <ImageOff size={28} strokeWidth={1.6} aria-hidden="true" />
              <strong>Foto indisponível</strong>
            </div>
          )}
          <dl className="data-list detail-facts">
            <Fact term="Situação">
              <span className={`badge ${statusTones[detail.status] ?? ''}`}>
                {statuses[detail.status]}
              </span>
            </Fact>
            {detail.detections.length > 0 && (
              <Fact term="Análise automática">
                {detail.detections
                  .map((d) =>
                    blind
                      ? labelFor(d.urmind_class)
                      : `${labelFor(d.urmind_class)}, ${percent.format(d.confidence)} de confiança`,
                  )
                  .join('; ')}
              </Fact>
            )}
            {/* Quem revisa vê o ponto na seção Local, junto da correção. */}
            {!canReview && (
              <Fact term="Local informado">
                {point ? `${coordinate(point.latitude)}, ${coordinate(point.longitude)}` : null}
              </Fact>
            )}
            {detail.snapped_latitude != null && detail.snapped_longitude != null && (
              <Fact term="Ponto na via">
                {coordinate(detail.snapped_latitude)}, {coordinate(detail.snapped_longitude)}
                {detail.distance_to_road_m != null && (
                  <small>a {meters(detail.distance_to_road_m)} do ponto informado</small>
                )}
              </Fact>
            )}
            {/* Antes da primeira decisão, gravidade e prioridade não aparecem (revisão às cegas). */}
            {!blind && (
              <>
                <Fact term="Gravidade">
                  {detail.risk ? (severities[detail.risk.severity] ?? severities.unknown) : null}
                </Fact>
                {detail.risk?.priority_score != null && (
                  <Fact term="Prioridade">{fixed(detail.risk.priority_score, 2)} de 1</Fact>
                )}
              </>
            )}
            <Fact term="Responsável sugerido">
              {responsibility == null
                ? null
                : responsibility === 'requires_triage'
                  ? 'A definir: nenhuma regra indica quem cuida deste trecho'
                  : responsibility.responsible}
            </Fact>
            <Fact term="Solução indicada">{detail.action ? detail.action.label : null}</Fact>
            <Fact term="Endereço aproximado">
              {address?.status === 'ok' ? String(address.data.display_name) : null}
            </Fact>
            <Fact term="Chuva nas 24 horas anteriores">
              {rain?.status === 'ok' ? `${fixed(Number(rain.data.rain_mm_24h), 1)} mm` : null}
            </Fact>
            <Fact term="Serviços próximos">
              {pois?.status === 'ok'
                ? sentence(
                    (['school', 'health', 'crossing'] as const)
                      .map((kind) =>
                        nearest[kind]
                          ? `${NEARBY[kind]} a ${meters(nearest[kind]!.distance_m)}`
                          : `${NEARBY[kind]} a mais de ${meters(Number(pois.data.radius_m))}`,
                      )
                      .join('; '),
                  )
                : null}
            </Fact>
          </dl>
        </section>
      )}
      {detail && (
        <section className="panel review-box" aria-labelledby="decision-title">
          <h2 id="decision-title">Decisão da equipe</h2>
          {detail.reviews.length > 0 && <DecisionHistory reviews={detail.reviews} />}
          {!canReview ? (
            <p className="notice">Sua conta não tem permissão de revisor.</p>
          ) : (
            <>
              {blind && (
                <p className="muted">
                  {detail.image_url
                    ? 'Decida pela foto. Se tiver dúvida, deixe pendente.'
                    : 'Sem foto, não dá para conferir o tipo. Se tiver dúvida, deixe pendente.'}
                </p>
              )}
              <div className="review-step">
                <div>
                  <label htmlFor={`${fieldId}-type`}>Tipo de problema</label>
                  <select
                    id={`${fieldId}-type`}
                    ref={typeField}
                    value={corrected}
                    onChange={(e) => {
                      setCorrected(e.target.value);
                      setDecisionError('');
                    }}
                  >
                    {issues.map((item) => (
                      <option key={item.issue_code} value={item.issue_code}>
                        {item.display_name_pt}
                      </option>
                    ))}
                  </select>
                </div>
                {selectedIssue?.photo_review_block_reason && (
                  <p className="notice" role="status">
                    {plain(selectedIssue.photo_review_block_reason)}
                  </p>
                )}
              </div>
              <fieldset className="review-step">
                <legend>Local</legend>
                <LocationCorrection
                  current={point}
                  latitude={latitude}
                  longitude={longitude}
                  onChange={(nextLatitude, nextLongitude) => {
                    setLatitude(nextLatitude);
                    setLongitude(nextLongitude);
                    setLocationErrors({});
                  }}
                  errors={locationErrors}
                  latitudeField={latitudeField}
                  longitudeField={longitudeField}
                />
              </fieldset>
              <label>
                Notas para a equipe (opcional)
                <textarea
                  rows={2}
                  value={notes}
                  maxLength={1900}
                  onChange={(e) => setNotes(e.target.value)}
                />
              </label>
              <div className="review-actions">
                {decisionError && (
                  <p className="error" role="alert">
                    {decisionError}
                  </p>
                )}
                <div className="actions">
                  {!rejecting && (
                    <button type="button" disabled={busy} onClick={confirmOccurrence}>
                      <span>
                        Confirmar<span className="wide-only"> ocorrência</span>
                      </span>
                    </button>
                  )}
                  <ConfirmButton
                    className="danger"
                    label="Recusar ocorrência"
                    display={
                      <span>
                        Recusar<span className="wide-only"> ocorrência</span>
                      </span>
                    }
                    question="A ocorrência será recusada e sai do mapa público, se estiver nele. Escolha o motivo."
                    confirmLabel="Recusar ocorrência"
                    confirmDisabled={!rejectReason}
                    disabled={busy}
                    onOpenChange={setRejecting}
                    onConfirm={() =>
                      void review(
                        {
                          decision: 'reject',
                          notes: [`[motivo:${rejectReason}]`, notes].filter(Boolean).join(' '),
                        },
                        'Ocorrência recusada.',
                      )
                    }
                  >
                    <label htmlFor={`${fieldId}-reason`}>Motivo</label>
                    <select
                      id={`${fieldId}-reason`}
                      value={rejectReason}
                      onChange={(e) =>
                        setRejectReason(e.target.value as keyof typeof rejectionReasons)
                      }
                    >
                      <option value="">Escolha o motivo</option>
                      {Object.entries(rejectionReasons).map(([value, label]) => (
                        <option key={value} value={value}>
                          {label}
                        </option>
                      ))}
                    </select>
                  </ConfirmButton>
                </div>
                {done && (
                  <p className="success" role="status" tabIndex={-1} ref={eventDone}>
                    {done}
                  </p>
                )}
              </div>
              <p className="muted">
                Cada decisão fica registrada na auditoria, e o dado original não é apagado.
              </p>
            </>
          )}
        </section>
      )}
    </>
  );
}
