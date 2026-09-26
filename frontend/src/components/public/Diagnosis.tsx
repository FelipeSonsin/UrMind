import {
  labelFor,
  priorityBand,
  severityOf,
  type PublicEventDetail,
  type PublicEvent,
} from '../../domain/public';
import { statuses } from '../../domain/contracts';

const NA = 'não disponível';

function percent(value: number | null | undefined): string {
  return value == null ? NA : `${(value * 100).toFixed(1)}%`;
}

function statusLabel(status: string): string {
  return statuses[status as keyof typeof statuses] ?? status;
}

/** Selo obrigatório para resultado de modelo EXPERIMENTAL_SHADOW. */
export function ExperimentalBadge({ stage }: { stage: string | null | undefined }) {
  if (stage !== 'EXPERIMENTAL_SHADOW') return null;
  return (
    <span className="badge experimental-badge" role="note">
      ANÁLISE EXPERIMENTAL
    </span>
  );
}

/** Resumo de 5 segundos: o que é, quanto urge, o que fazer e em que estado está. */
function UrbanAnalysis({ event }: { event: PublicEventDetail }) {
  return event.analysis ? (
    <section aria-label="Análise urbana">
      {event.model_stage !== 'EXPERIMENTAL_SHADOW' && (
        <ExperimentalBadge stage={event.analysis.provenance.model_stage} />
      )}
      <h3>Descrição</h3>
      <p>{event.analysis.description}</p>
      <h3>Diagnóstico</h3>
      <p>{event.analysis.diagnosis}</p>
      {event.analysis.potential_consequences.length > 0 && (
        <>
          <h3>Consequências potenciais</h3>
          <p className="muted">Possibilidades condicionais; não são uma previsão de ocorrência.</p>
          <ul>
            {event.analysis.potential_consequences.map((item, index) => (
              <li key={`${item.domain}-${index}`}>{item.statement}</li>
            ))}
          </ul>
        </>
      )}
      {event.analysis.possible_causes.length > 0 && (
        <>
          <h3>Possíveis causas</h3>
          <ul>
            {event.analysis.possible_causes.map((cause) => (
              <li key={cause}>{cause}</li>
            ))}
          </ul>
        </>
      )}
      {event.analysis.limitations.length > 0 && (
        <>
          <h3>Limitações da análise</h3>
          <ul>
            {event.analysis.limitations.map((limitation) => (
              <li key={limitation}>{limitation}</li>
            ))}
          </ul>
        </>
      )}
      <p className="muted">
        Análise por regras e texto determinístico.{' '}
        {event.analysis.provenance.assessment_source === 'unavailable'
          ? 'Avaliação persistida indisponível.'
          : `Avaliação persistida${event.analysis.provenance.ruleset_version ? ` · ${event.analysis.provenance.ruleset_version}` : ''}.`}
      </p>
    </section>
  ) : null;
}

export function QuickDiagnosis({ event }: { event: PublicEventDetail }) {
  const severity = severityOf(event.risk?.severity);
  return (
    <div className="quick-diagnosis">
      <h3 className="section-label">Problema detectado</h3>
      <ExperimentalBadge stage={event.model_stage} />
      <h2>{labelFor(event.urmind_class)}</h2>
      <UrbanAnalysis event={event} />
      <div className="quick-grid">
        <div>
          <span>Prioridade</span>
          <strong>
            {priorityBand(event.risk?.priority_score).toUpperCase()}
            {event.risk?.priority_score != null && (
              <small> {event.risk.priority_score.toFixed(2)}</small>
            )}
          </strong>
        </div>
        <div>
          <span>Confiança visual</span>
          <strong>{percent(event.visual_confidence)}</strong>
        </div>
        <div>
          <span>Severidade</span>
          <strong className={`risk-${severity.level}`}>
            <i aria-hidden="true">{severity.shape}</i> {severity.label}
          </strong>
        </div>
        <div>
          <span>Situação</span>
          <strong>{statusLabel(event.status)}</strong>
        </div>
      </div>
    </div>
  );
}

/** Onde e sobre qual via. O ponto original nunca é substituído pelo ajustado. */
export function LocationPanel({ event }: { event: PublicEventDetail }) {
  return (
    <dl className="data-list">
      <div>
        <dt>Via</dt>
        <dd>{event.road?.name ?? event.road_name ?? NA}</dd>
      </div>
      <div>
        <dt>Coordenada informada</dt>
        <dd>
          {event.latitude != null && event.longitude != null
            ? `${event.latitude.toFixed(6)}, ${event.longitude.toFixed(6)}`
            : NA}
        </dd>
      </div>
      <div>
        <dt>Ponto ajustado à via</dt>
        <dd>
          {event.snapped_latitude != null && event.snapped_longitude != null
            ? `${event.snapped_latitude.toFixed(6)}, ${event.snapped_longitude.toFixed(6)}`
            : NA}
        </dd>
      </div>
      <div>
        <dt>Distância até a via</dt>
        <dd>
          {event.distance_to_road_m != null ? `${event.distance_to_road_m.toFixed(1)} m` : NA}
        </dd>
      </div>
      <div>
        <dt>Precisão declarada</dt>
        <dd>
          {event.location_accuracy_m != null ? `${event.location_accuracy_m.toFixed(0)} m` : NA}
        </dd>
      </div>
    </dl>
  );
}

/** Por que esta prioridade: fatores reais do RiskAssessment, sem texto gerado. */
export function RiskExplanation({ event }: { event: PublicEventDetail }) {
  const risk = event.risk;
  if (!risk)
    return (
      <>
        <UrbanAnalysis event={event} />
        <p className="notice">Ainda não há avaliação de risco publicada para esta ocorrência.</p>
      </>
    );
  const { increased, decreased, unavailable } = risk.explanation;
  return (
    <div className="explanation">
      <UrbanAnalysis event={event} />
      <h3 className="section-label">Por que esta prioridade</h3>
      <p className="explanation-head">
        Prioridade <strong>{priorityBand(risk.priority_score)}</strong>
        {risk.priority_score != null && <> ({risk.priority_score.toFixed(2)} de 1,00)</>} ·
        incerteza <strong>{risk.uncertainty_band}</strong>
      </p>
      <div className="explanation-columns">
        <section>
          <h4>Aumentaram a prioridade</h4>
          {increased.length ? (
            <ul>
              {increased.map((item) => (
                <li key={item.factor}>
                  <span aria-hidden="true">↑</span> {item.label}
                  {item.value != null && <small> {item.value.toFixed(2)}</small>}
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted">Nenhum fator acima da média.</p>
          )}
        </section>
        <section>
          <h4>Reduziram a prioridade</h4>
          {decreased.length ? (
            <ul>
              {decreased.map((item) => (
                <li key={item.factor}>
                  <span aria-hidden="true">↓</span> {item.label}
                  {item.value != null && <small> {item.value.toFixed(2)}</small>}
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted">Nenhum fator abaixo da média.</p>
          )}
        </section>
        <section>
          <h4>Sem dado disponível</h4>
          {unavailable.length ? (
            <ul>
              {unavailable.map((item) => (
                <li key={item.factor}>
                  <span aria-hidden="true">–</span> {item.label}
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted">Todos os fatores tinham dado.</p>
          )}
        </section>
      </div>
      {!risk.thresholds_are_calibrated && (
        <p className="muted">
          Limiares ainda não calibrados com eventos revisados em campo; regra {risk.ruleset_version}
          .
        </p>
      )}
      {risk.limitations.length > 0 && (
        <details>
          <summary>Limitações declaradas ({risk.limitations.length})</summary>
          <ul>
            {risk.limitations.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

export function RecommendedAction({ event }: { event: PublicEventDetail }) {
  const responsibility = event.responsibility;
  return (
    <div className="action-panel">
      <h3 className="section-label">O que fazer agora</h3>
      <h3>{event.action ? event.action.label : 'Ação ainda não sugerida'}</h3>
      <dl className="data-list">
        <div>
          <dt>Responsável sugerido</dt>
          <dd>
            {responsibility.status === 'assigned' ? (
              responsibility.responsible
            ) : (
              <>
                Em triagem
                <small> {responsibility.note}</small>
              </>
            )}
          </dd>
        </div>
        <div>
          <dt>Fundamento</dt>
          <dd>{responsibility.source ?? 'sem regra de competência validada para este trecho'}</dd>
        </div>
      </dl>
      {event.action && (
        <p className="muted">
          Sugestão do catálogo {event.action.version}. A decisão técnica e administrativa continua
          sendo do órgão responsável.
        </p>
      )}
    </div>
  );
}

/** Previsão só existe com Prediction real. RiskAssessment não vira previsão. */
export function PredictionPanel({ event }: { event: PublicEventDetail }) {
  const prediction = event.prediction;
  return (
    <div className="prediction-panel">
      <h3 className="section-label">Previsão</h3>
      {prediction.available ? (
        <>
          <h3>{prediction.task}</h3>
          <dl className="data-list">
            <div>
              <dt>Valor estimado</dt>
              <dd>{prediction.value != null ? prediction.value.toFixed(2) : NA}</dd>
            </div>
            <div>
              <dt>Horizonte</dt>
              <dd>{prediction.horizon_days != null ? `${prediction.horizon_days} dias` : NA}</dd>
            </div>
            <div>
              <dt>Incerteza</dt>
              <dd>{prediction.uncertainty != null ? prediction.uncertainty.toFixed(2) : NA}</dd>
            </div>
            <div>
              <dt>Modelo</dt>
              <dd>{prediction.model_version ?? NA}</dd>
            </div>
          </dl>
        </>
      ) : (
        <>
          <h3>Ainda não disponível</h3>
          <p>{prediction.reason}</p>
          <p className="muted">
            Avaliação de risco não é previsão: o UrMind só projeta evolução quando houver histórico
            validado e um modelo próprio para isso.
          </p>
        </>
      )}
    </div>
  );
}

export function ContextPanel({ event }: { event: PublicEventDetail }) {
  if (!event.context.length)
    return <p className="muted">Nenhuma fonte de contexto foi consultada para esta ocorrência.</p>;
  return (
    <ul className="context-list">
      {event.context.map((item) => (
        <li key={item.source}>
          <strong>{item.label}</strong>
          <small>{item.source}</small>
          {item.status === 'ok' ? (
            <span>
              {Object.entries(item.summary)
                .filter(([, value]) => value != null)
                .map(([key, value]) => `${key}: ${String(value)}`)
                .join(' · ') || 'sem detalhes'}
            </span>
          ) : (
            <span className="muted">contexto indisponível</span>
          )}
          {item.attribution && <small>{item.attribution}</small>}
        </li>
      ))}
    </ul>
  );
}

/** Caminho da decisão, etapa a etapa, com fonte e instante de cada uma. */
export function DecisionTrace({ event }: { event: PublicEventDetail }) {
  return (
    <ol className="decision-trace">
      {event.trace.map((step) => (
        <li key={step.step} className={step.status}>
          <details>
            <summary>
              <span className="trace-title">{step.title}</span>
              <span className="trace-state">
                {step.status === 'done' ? 'concluída' : 'sem dado'}
              </span>
            </summary>
            <dl className="data-list">
              <div>
                <dt>Fonte</dt>
                <dd>{step.source ?? NA}</dd>
              </div>
              {Object.entries(step.detail).map(([key, value]) => (
                <div key={key}>
                  <dt>{key}</dt>
                  <dd>{value == null ? NA : String(value)}</dd>
                </div>
              ))}
              <div>
                <dt>Quando</dt>
                <dd>{step.at ? new Date(step.at).toLocaleString('pt-BR') : NA}</dd>
              </div>
            </dl>
          </details>
        </li>
      ))}
    </ol>
  );
}

export function EventTraceability({ event }: { event: PublicEventDetail }) {
  return (
    <dl className="data-list">
      <div>
        <dt>Modelo</dt>
        <dd>
          {event.model_version ?? NA}
          {event.model_stage && (
            <small>
              {event.model_stage === 'EXPERIMENTAL_SHADOW'
                ? ' — Análise experimental (não aprovada para produção)'
                : ` estágio ${event.model_stage}`}
            </small>
          )}
        </dd>
      </div>
      <div>
        <dt>Revisão humana</dt>
        <dd>{event.reviewed ? 'houve revisão' : 'ainda não revisada'}</dd>
      </div>
      <div>
        <dt>Registrada em</dt>
        <dd>{new Date(event.occurred_at).toLocaleString('pt-BR')}</dd>
      </div>
    </dl>
  );
}

export function eventSubtitle(event: PublicEvent): string {
  const severity = severityOf(event.severity);
  return `${severity.label} · ${priorityBand(event.priority_score)}`;
}
