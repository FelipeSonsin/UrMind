import { useEffect, useState } from 'react';
import { api } from '../services/api';
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

  const responsibility = detail?.responsibility;
  const context = Object.fromEntries((detail?.context ?? []).map((item) => [item.source, item]));
  const address = context.nominatim_reverse;
  const rain = context.open_meteo_rain;
  const pois = context.overpass_pois;
  const nearest = (pois?.data.nearest ?? {}) as Record<string, { distance_m: number } | null>;
  return (
    <section className="panel detail" aria-label="Detalhes da ocorrência">
      <div className="section-heading">
        <h2>{detail ? classes[detail.urmind_class] : 'Carregando ocorrência…'}</h2>
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
                    title={`${d.urmind_class} ${(d.confidence * 100).toFixed(1)}%`}
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
                    .map((d) => `${d.urmind_class} · ${(d.confidence * 100).toFixed(1)}%`)
                    .join('; ')
                : na}
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
            <dd>{detail.risk ? severities[detail.risk.severity] || detail.risk.severity : na}</dd>
            <dt>Prioridade</dt>
            <dd>
              {detail.risk?.priority_score == null ? na : detail.risk.priority_score.toFixed(2)}
              {detail.risk?.uncertainty != null &&
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
          {detail.report && (
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
              <button
                className="text-button danger"
                disabled={busy}
                onClick={() => void review({ decision: 'reject' })}
              >
                Rejeitar
              </button>
            </div>
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
