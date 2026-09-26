import { useEffect, useState } from 'react';
import { api } from '../services/api';
import { publicApi } from '../services/publicApi';
import { type IssueDefinition, modelSupportLabel, labelFor } from '../domain/public';
import { ExperimentalBadge } from './public/Diagnosis';
import {
  classes,
  parseCoordinate,
  severities,
  statuses,
  type EventDetail as Detail,
  type ReviewPayload,
} from '../domain/contracts';

const na = 'Não disponível';
// Mesmos códigos de backend/app/ml/tabular.py (REJECTION_REASONS): só erro visual
// vira rótulo negativo de review_confirmed.
const rejectionReasons = {
  erro_visual: 'Erro visual: a imagem não mostra o dano proposto',
  duplicidade: 'Duplicidade (administrativo)',
  localizacao: 'Localização ausente ou incorreta',
  imagem_inconclusiva: 'Imagem insuficiente para decidir',
} as const;

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
  const labels: Record<string, string> = {
    received: 'Recebido',
    photo_gate: 'Porteiro',
    location: 'Localização',
    analysis: 'Análise',
    review: 'Revisão humana',
    publication: 'Publicação',
    ACCEPTED: 'Foto aceita para análise',
    NEEDS_REVIEW: 'Verificação pendente',
    REJECTED: 'Foto recusada',
    confirmed: 'Confirmado',
    rejected: 'Não é problema',
    duplicate: 'Duplicado',
    attached: 'Evidência anexada',
    detached: 'Evidência desanexada',
    published: 'Publicado',
    withdrawn: 'Despublicado',
    adjusted: 'Localização ajustada',
    declared: 'Localização declarada',
    model_not_available: 'Análise indisponível — sem modelo autorizado',
    experimental: 'Análise experimental',
    queued: 'Na fila',
    processing_detection: 'Em análise',
    location_required: 'Localização necessária',
    unknown: 'Não registrado',
    blur: 'Nitidez insuficiente',
    resolution: 'Resolução insuficiente',
    underexposed: 'Muito escura',
    overexposed: 'Muito clara',
    scene: 'Cena não compatível',
    face: 'Atenção à privacidade',
    face_large: 'Rosto em primeiro plano',
  };
  return (
    <details onToggle={(event) => setExpanded(event.currentTarget.open)}>
      <summary>Histórico do relato</summary>
      {error && <p role="alert">{error}</p>}
      {!rows && !error && expanded && <p role="status">Consultando histórico…</p>}
      <ol>
        {rows?.map((row, index) => (
          <li key={index}>
            <strong>
              {labels[row.stage] ?? row.stage}: {labels[row.status] ?? row.status}
            </strong>
            {row.at ? (
              <time dateTime={row.at}> — {new Date(row.at).toLocaleString('pt-BR')}</time>
            ) : (
              <span> — Data não registrada</span>
            )}
            {row.source && <span> · Origem: {row.source}</span>}
            {row.reasons?.length ? (
              <p>{row.reasons.map((reason) => labels[reason] ?? reason).join(', ')}</p>
            ) : null}
          </li>
        ))}
      </ol>
    </details>
  );
}

export function CaptureReviewPanel({ id, onChanged }: { id: string; onChanged: () => void }) {
  const [detail, setDetail] = useState<Awaited<ReturnType<typeof api.captureReview>> | null>(null);
  const [issues, setIssues] = useState<IssueDefinition[]>([]);
  const [issue, setIssue] = useState('');
  const [notes, setNotes] = useState('');
  const [duplicate, setDuplicate] = useState('');
  const [latitude, setLatitude] = useState('');
  const [longitude, setLongitude] = useState('');
  const [privacy, setPrivacy] = useState(false);
  const [adjudicate, setAdjudicate] = useState(false);
  const [admin, setAdmin] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  const [originalUrl, setOriginalUrl] = useState<string | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    setDetail(null);
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
        }
      })
      .catch((reason: Error) => {
        if (!controller.signal.aborted) setError(reason.message);
      });
    return () => controller.abort();
  }, [id, revision]);
  const run = async (operation: () => Promise<unknown>) => {
    setBusy(true);
    setError('');
    try {
      await operation();
      setRevision((value) => value + 1);
      onChanged();
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const review = (decision: 'correct' | 'reject') => {
    const coordinate = latitude || longitude ? parseCoordinate(latitude, longitude) : null;
    if ((latitude || longitude) && !coordinate) {
      setError('Informe latitude e longitude válidas.');
      return;
    }
    void run(() =>
      api.reviewCapture(id, {
        decision,
        notes,
        adjudicate,
        ...(decision === 'correct' && issue ? { corrected_class: issue } : {}),
        ...(decision === 'correct' && coordinate ? { corrected_location: coordinate } : {}),
        ...(decision === 'reject' && duplicate ? { duplicate_of_protocol: duplicate } : {}),
      }),
    );
  };
  const event = detail?.events[0];
  const lastReview = detail?.reviews.at(-1);
  const routing = issues.find((item) => item.issue_code === (issue || lastReview?.corrected_class));
  return (
    <section aria-label="Revisão do relato" className="review-panel">
      <h3>Revisão humana</h3>
      {error && <p role="alert">{error}</p>}
      {!detail ? (
        <p>Carregando relato…</p>
      ) : (
        <>
          <p>
            {detail.protocol_code} — {detail.location_source}
          </p>
          {originalUrl ? (
            <figure>
              <img
                src={originalUrl}
                alt="Foto original privada do relato"
                style={{ maxWidth: '100%' }}
              />
              <figcaption>
                <a href={originalUrl} target="_blank" rel="noopener noreferrer">
                  Ver original privado
                </a>
              </figcaption>
            </figure>
          ) : (
            <p>Imagem privada indisponível.</p>
          )}
          <p>{detail.user_description}</p>
          <details>
            <summary>Resultado do porteiro</summary>
            <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>
              {JSON.stringify(detail.photo_gate, null, 2)}
            </pre>
          </details>
          {detail.location && (
            <p>
              Original: {detail.location.latitude}, {detail.location.longitude}. Precisão declarada:{' '}
              {detail.location.accuracy_m ?? na} m.
            </p>
          )}
          {detail.location_conflict && <p>Conflito GPS × EXIF: conferir localização.</p>}
          {detail.additional_evidence && (
            <aside className="notice">
              <p>Foto anexada a outro relato; não cria ponto independente.</p>
              <button disabled={busy} onClick={() => void run(() => api.detachEvidence(id))}>
                Desanexar evidência
              </button>
            </aside>
          )}
          <label>
            Classe humana
            <select value={issue} onChange={(e) => setIssue(e.target.value)}>
              <option value="">Selecione após revisar a foto</option>
              {issues.map((item) => (
                <option key={item.issue_code} value={item.issue_code}>
                  {item.display_name_pt} — {modelSupportLabel(item)}
                </option>
              ))}
            </select>
          </label>
          <label>
            Notas
            <textarea value={notes} maxLength={2000} onChange={(e) => setNotes(e.target.value)} />
          </label>
          {routing && (
            <aside className="notice">
              <strong>Sugestão, não encaminhamento</strong>
              <p>
                Para {routing.display_name_pt}, consulte a equipe responsável pelo domínio{' '}
                {routing.responsibility_domain}. A competência depende da jurisdição e deve ser
                conferida pela equipe; nenhum pedido é enviado automaticamente.
              </p>
              <p>
                Se o local estiver no município de São Paulo, consulte o{' '}
                <a
                  href="https://sp156.prefeitura.sp.gov.br/portal"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  canal oficial SP156
                </a>
                . Em outra cidade, use o canal oficial local.
              </p>
            </aside>
          )}
          <label>
            Latitude corrigida
            <input value={latitude} onChange={(e) => setLatitude(e.target.value)} />
          </label>
          <label>
            Longitude corrigida
            <input value={longitude} onChange={(e) => setLongitude(e.target.value)} />
          </label>
          {admin && (
            <label>
              <input
                type="checkbox"
                checked={adjudicate}
                onChange={(e) => setAdjudicate(e.target.checked)}
              />
              Adjudicar como admin
            </label>
          )}
          <button disabled={busy || (!issue && !latitude)} onClick={() => review('correct')}>
            Confirmar rótulo humano
          </button>
          <button disabled={busy} onClick={() => review('reject')}>
            Não é problema
          </button>
          <label>
            Protocolo duplicado
            <input value={duplicate} onChange={(e) => setDuplicate(e.target.value.toUpperCase())} />
          </label>
          <button disabled={busy || !duplicate} onClick={() => review('reject')}>
            Marcar duplicado
          </button>
          <p>
            Consenso entre revisores distintos ou adjudicação continuam obrigatórios. Isto não é
            inferência da IA.
          </p>
          <label>
            <input
              type="checkbox"
              checked={privacy}
              onChange={(e) => setPrivacy(e.target.checked)}
            />
            Atesto que o conteúdo visual pode ser publicado, sem rostos identificáveis, placas ou
            dados pessoais.
          </label>
          <button
            disabled={busy || !privacy || event?.status !== 'confirmed' || !lastReview}
            onClick={() =>
              void run(() =>
                api.publishEvent(event!.id, {
                  publish: true,
                  review_id: lastReview!.id,
                  visible_content_reviewed: privacy,
                  reason: notes || 'Publicação após revisão humana',
                }),
              )
            }
          >
            Publicar relato
          </button>
          <button
            disabled={busy || !event}
            onClick={() =>
              void run(() =>
                api.publishEvent(event!.id, {
                  publish: false,
                  reason: notes || 'Retirada pela equipe revisora',
                }),
              )
            }
          >
            Despublicar relato
          </button>
          <h4>Histórico</h4>
          <ul>
            {detail.reviews.map((item) => (
              <li key={item.id}>
                {item.created_at}: {item.decision} {item.corrected_class} {item.notes}
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}

export function EventDetail({
  id,
  onClose,
  onChanged,
  refreshAt = 0,
}: {
  id: string;
  onClose: () => void;
  onChanged: () => void;
  refreshAt?: number;
}) {
  const [detail, setDetail] = useState<Detail | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [corrected, setCorrected] = useState<keyof typeof classes>('URMIND_ROAD_D00');
  const [revision, setRevision] = useState(0);
  const [canReview, setCanReview] = useState(false);
  const [latitude, setLatitude] = useState('');
  const [longitude, setLongitude] = useState('');
  const [rejectReason, setRejectReason] = useState<keyof typeof rejectionReasons | ''>('');
  const [rejectNotes, setRejectNotes] = useState('');

  useEffect(() => {
    // Papel decidido no servidor (JWT app_metadata); a tela só respeita.
    const controller = new AbortController();
    api
      .me(controller.signal)
      .then((me) => !controller.signal.aborted && setCanReview(me.can_review))
      .catch(() => !controller.signal.aborted && setCanReview(false));
    return () => controller.abort();
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    setError('');
    api
      .event(id, controller.signal)
      .then((data) => !controller.signal.aborted && setDetail(data))
      .catch((reason: Error) => !controller.signal.aborted && setError(reason.message));
    return () => controller.abort();
  }, [id, revision, refreshAt]);

  async function review(payload: ReviewPayload) {
    setBusy(true);
    setError('');
    try {
      await api.review(id, payload);
      setRevision((value) => value + 1);
      onChanged();
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function correctLocation() {
    try {
      const coordinate = parseCoordinate(latitude, longitude);
      void review({ decision: 'correct', corrected_location: coordinate });
    } catch (reason) {
      setError((reason as Error).message);
    }
  }

  // Protocolo de revisão: antes da primeira decisão humana, quem revisa não vê
  // score do modelo, severidade, prioridade nem relatório gerado a partir deles.
  const blind = canReview && (detail?.reviews.length ?? 0) === 0;
  const hidden = 'Oculto até a decisão humana';
  const responsibility = detail?.responsibility;
  const context = Object.fromEntries((detail?.context ?? []).map((item) => [item.source, item]));
  const address = context.nominatim_reverse;
  const rain = context.open_meteo_rain;
  const pois = context.overpass_pois;
  const nearest = (pois?.data.nearest ?? {}) as Record<string, { distance_m: number } | null>;
  return (
    <section className="panel detail" aria-label="Detalhes da ocorrência">
      <div className="section-heading">
        <h2>{detail ? labelFor(detail.urmind_class) : 'Carregando ocorrência…'}</h2>
        <button className="secondary compact" onClick={onClose}>
          Fechar detalhes
        </button>
      </div>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {detail && (
        <>
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
                        : `${d.urmind_class} ${(d.confidence * 100).toFixed(1)}%`
                    }
                  />
                ))}
              </div>
              <figcaption>Caixas: detecções que sustentam esta ocorrência.</figcaption>
            </figure>
          ) : (
            <p className="notice">Imagem da evidência indisponível.</p>
          )}
          <dl>
            <dt>Estado</dt>
            <dd>{statuses[detail.status]}</dd>
            <dt>Detecções</dt>
            <dd>
              {detail.detections.length
                ? detail.detections
                    .map((d) =>
                      blind
                        ? labelFor(d.urmind_class)
                        : `${d.urmind_class} · ${(d.confidence * 100).toFixed(1)}%`,
                    )
                    .join('; ')
                : na}
              {blind && detail.detections.length > 0 && ` (score: ${hidden.toLowerCase()})`}
            </dd>
            <dt>Coordenada original</dt>
            <dd>
              {detail.latitude != null && detail.longitude != null
                ? `${detail.latitude}, ${detail.longitude}`
                : na}
            </dd>
            <dt>Ponto ajustado à via</dt>
            <dd>
              {detail.snapped_latitude != null && detail.snapped_longitude != null
                ? `${detail.snapped_latitude}, ${detail.snapped_longitude} (${detail.distance_to_road_m?.toFixed(1)} m)`
                : na}
            </dd>
            <dt>Severidade</dt>
            <dd>
              {blind
                ? hidden
                : detail.risk
                  ? severities[detail.risk.severity] || detail.risk.severity
                  : na}
            </dd>
            <dt>Prioridade</dt>
            <dd>
              {blind
                ? hidden
                : detail.risk?.priority_score == null
                  ? na
                  : detail.risk.priority_score.toFixed(2)}
              {!blind &&
                detail.risk?.uncertainty != null &&
                ` · incerteza ${detail.risk.uncertainty.toFixed(2)}`}
            </dd>
            <dt>Responsável sugerido</dt>
            <dd>
              {responsibility == null
                ? na
                : responsibility === 'requires_triage'
                  ? 'Requer triagem: sem regra de competência confiável para este trecho'
                  : `${responsibility.responsible} — ${responsibility.source}`}
            </dd>
            <dt>Ação sugerida</dt>
            <dd>{detail.action ? detail.action.label : na}</dd>
            <dt>Endereço aproximado (contexto)</dt>
            <dd>
              {address?.status === 'ok'
                ? `${String(address.data.display_name)} · ${String(address.data.attribution)}`
                : address
                  ? 'Contexto indisponível'
                  : na}
            </dd>
            <dt>Chuva nas 24 h anteriores</dt>
            <dd>
              {rain?.status === 'ok'
                ? `${String(rain.data.rain_mm_24h)} mm · ${String(rain.data.attribution)}`
                : rain
                  ? 'Contexto indisponível'
                  : na}
            </dd>
            <dt>Escola / saúde / travessia próximas</dt>
            <dd>
              {pois?.status === 'ok'
                ? (['school', 'health', 'crossing'] as const)
                    .map((kind) =>
                      nearest[kind]
                        ? `${nearest[kind]!.distance_m} m`
                        : `> ${String(pois.data.radius_m)} m`,
                    )
                    .join(' / ')
                : pois
                  ? 'Contexto indisponível'
                  : na}
            </dd>
          </dl>
          {detail.report && !blind && (
            <details open>
              <summary>Relatório</summary>
              <pre className="report">{detail.report}</pre>
            </details>
          )}
          <div className="review-box">
            <h3>Revisão humana</h3>
            {detail.reviews.map((r) => (
              <p key={r.created_at} className="muted">
                {new Date(r.created_at).toLocaleString('pt-BR')}: {r.decision}
                {r.corrected_class ? ` → ${r.corrected_class}` : ''}
              </p>
            ))}
            {!canReview && (
              <p className="notice">Sua conta não tem papel de revisor definido no servidor.</p>
            )}
            <div className="actions" hidden={!canReview}>
              <button disabled={busy} onClick={() => void review({ decision: 'confirm' })}>
                Confirmar
              </button>
              <select
                aria-label="Classe corrigida"
                value={corrected}
                onChange={(e) => setCorrected(e.target.value as keyof typeof classes)}
              >
                {Object.entries(classes).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
              <button
                className="secondary"
                disabled={busy || corrected === detail.urmind_class}
                onClick={() => void review({ decision: 'correct', corrected_class: corrected })}
              >
                Corrigir classe
              </button>
            </div>
            <div className="actions" hidden={!canReview}>
              <select
                aria-label="Motivo da rejeição"
                value={rejectReason}
                onChange={(e) => setRejectReason(e.target.value as keyof typeof rejectionReasons)}
              >
                <option value="">Motivo da rejeição…</option>
                {Object.entries(rejectionReasons).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
              <input
                aria-label="Observação da rejeição"
                placeholder="Observação (opcional)"
                maxLength={1900}
                value={rejectNotes}
                onChange={(e) => setRejectNotes(e.target.value)}
              />
              <button
                className="text-button danger"
                disabled={busy || !rejectReason}
                onClick={() =>
                  void review({
                    decision: 'reject',
                    notes: `[motivo:${rejectReason}] ${rejectNotes}`.trim(),
                  })
                }
              >
                Rejeitar
              </button>
            </div>
            {blind && (
              <p className="muted">
                Decida pela evidência. Na dúvida, não decida: o caso fica pendente e não vira
                rótulo.
              </p>
            )}
            <div className="actions" hidden={!canReview}>
              <input
                aria-label="Latitude corrigida"
                placeholder="Latitude"
                inputMode="decimal"
                value={latitude}
                onChange={(e) => setLatitude(e.target.value)}
              />
              <input
                aria-label="Longitude corrigida"
                placeholder="Longitude"
                inputMode="decimal"
                value={longitude}
                onChange={(e) => setLongitude(e.target.value)}
              />
              <button className="secondary" disabled={busy} onClick={correctLocation}>
                Corrigir localização
              </button>
            </div>
            <p className="muted">
              A inferência original permanece registrada; a revisão gera auditoria.
            </p>
          </div>
        </>
      )}
    </section>
  );
}
