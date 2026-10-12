import type { ReactNode } from 'react';
import { contextSummary, plain } from '../../content/plainLanguage';
import {
  factorName,
  factorReason,
  listJoin,
  readable,
  shortDateTime,
  stageName,
  traceStep,
  versionName,
} from '../../content/technical';
import { statuses } from '../../domain/contracts';
import { labelFor, priorityBand, type PublicEventDetail } from '../../domain/public';

/** Motivos registrados numa direção, em frase: "esse tipo… e a foto…". */
function reasons(items: Array<{ factor: string }>, direction: 'up' | 'down'): string {
  return listJoin(
    items
      .map((item) => factorReason(item.factor, direction))
      .filter((text): text is string => Boolean(text)),
  );
}

/*
 * Detalhes técnicos de uma ocorrência, recolhidos: coordenadas, modelo, regras com
 * versão, fontes e o caminho da análise etapa por etapa. Tudo em palavras simples
 * (traduções em content/technical.ts); dado que não existe não vira linha vazia.
 */

const decimal = new Intl.NumberFormat('pt-BR', {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});
const oneDecimal = new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 1 });
const percent = (value: number) => `${oneDecimal.format(value * 100)}%`;
const point = (latitude?: number | null, longitude?: number | null) =>
  latitude != null && longitude != null ? `${latitude.toFixed(6)}, ${longitude.toFixed(6)}` : null;
const CALIBRATION = plain('limiares ainda não calibrados com revisão de campo');

/** Um grupo dentro dos detalhes técnicos: título e pares rótulo/valor. */
function TechGroup({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="tech-group" aria-label={title}>
      <h3>{title}</h3>
      {children}
    </section>
  );
}

/** Um par rótulo/valor; sem valor, a linha não aparece. */
function Row({ term, children }: { term: string; children: ReactNode }) {
  if (children == null || children === false || children === '') return null;
  return (
    <div>
      <dt>{term}</dt>
      <dd>{children}</dd>
    </div>
  );
}

export function TechnicalDetails({ event }: { event: PublicEventDetail }) {
  const risk = event.risk;
  const responsibility = event.responsibility;
  const factors = risk
    ? [...risk.explanation.increased, ...risk.explanation.decreased].filter(
        (item) => item.value != null,
      )
    : [];
  // Limites que a leitura principal ainda não mostrou (a calibração já aparece lá).
  const limits = risk ? risk.limitations.map(plain).filter((text) => text !== CALIBRATION) : [];
  const automatic = Boolean(event.model_version) || event.detections.length > 0;
  const stage = stageName(event.model_stage);
  return (
    <section className="panel technical" aria-labelledby="technical-title">
      <details>
        <summary id="technical-title">
          Detalhes técnicos
          <small>
            {automatic
              ? 'coordenadas, análise automática, regras e fontes'
              : 'coordenadas, regras e fontes'}
          </small>
        </summary>
        <div className="tech-grid">
          <TechGroup title="Localização">
            <dl className="data-list">
              <Row term="Coordenada informada">{point(event.latitude, event.longitude)}</Row>
              <Row term="Ponto ajustado à rua">
                {point(event.snapped_latitude, event.snapped_longitude)}
              </Row>
              <Row term="Distância até a rua">
                {event.distance_to_road_m != null &&
                  `${oneDecimal.format(event.distance_to_road_m)} m`}
              </Row>
              <Row term="Precisão informada">
                {event.location_accuracy_m != null && `${event.location_accuracy_m.toFixed(0)} m`}
              </Row>
            </dl>
          </TechGroup>
          <TechGroup title="Análise da foto">
            <dl className="data-list">
              {automatic ? (
                <>
                  <Row term="Modelo">
                    {event.model_version ?? 'não informado'}
                    {stage && <small> {stage}</small>}
                  </Row>
                  <Row term="Certeza da análise">
                    {event.visual_confidence != null && percent(event.visual_confidence)}
                  </Row>
                  <Row term="Tipos detectados">
                    {event.detections.length
                      ? event.detections
                          .map(
                            (item) => `${labelFor(item.urmind_class)} ${percent(item.confidence)}`,
                          )
                          .join(', ')
                      : 'nenhum'}
                  </Row>
                </>
              ) : (
                <Row term="Análise automática">nenhuma; quem avaliou a foto foi a equipe</Row>
              )}
              <Row term="Estado interno">
                {statuses[event.status as keyof typeof statuses] ?? readable(event.status)}
              </Row>
              <Row term="Revisão da equipe">{event.reviewed ? 'feita' : 'ainda não feita'}</Row>
              <Row term="Registrada em">{shortDateTime(event.occurred_at)}</Row>
            </dl>
          </TechGroup>
          <TechGroup title="Cálculo da prioridade">
            {/* A prioridade em frases, antes dos números que a compõem. */}
            {risk && (
              <p className="tech-summary">
                Prioridade {priorityBand(risk.priority_score)}.
                {reasons(risk.explanation.increased, 'up') &&
                  ` Aumentou porque ${reasons(risk.explanation.increased, 'up')}.`}
                {reasons(risk.explanation.decreased, 'down') &&
                  ` Diminuiu porque ${reasons(risk.explanation.decreased, 'down')}.`}
              </p>
            )}
            <dl className="data-list">
              <Row term="Pontuação">
                {risk?.priority_score != null
                  ? `${decimal.format(risk.priority_score)} de 1,00`
                  : 'ainda não calculada'}
              </Row>
              {risk && (
                <>
                  <Row term="Incerteza">
                    {risk.uncertainty != null
                      ? `${decimal.format(risk.uncertainty)} (${risk.uncertainty_band})`
                      : risk.uncertainty_band}
                  </Row>
                  {factors.length > 0 && (
                    <Row term="Fatores">
                      {factors
                        .map(
                          (item) =>
                            `${factorName(item.factor, item.label)}: ${decimal.format(item.value!)}`,
                        )
                        .join('; ')}
                    </Row>
                  )}
                  {risk.explanation.unavailable.length > 0 && (
                    <Row term="Sem informação">
                      {risk.explanation.unavailable
                        .map((item) => factorName(item.factor, item.label))
                        .join('; ')}
                    </Row>
                  )}
                  <Row term="Regras">
                    {versionName(risk.ruleset_version)}
                    {!risk.thresholds_are_calibrated && (
                      <small> critérios ainda não ajustados com vistorias</small>
                    )}
                  </Row>
                  {limits.length > 0 && <Row term="Outros limites">{limits.join(' ')}</Row>}
                </>
              )}
            </dl>
          </TechGroup>
          <TechGroup title="Responsável e solução">
            <dl className="data-list">
              <Row term="Fundamento">
                {responsibility.source
                  ? plain(responsibility.source)
                  : 'ainda não há regra de competência para este trecho'}
              </Row>
              <Row term="Regras de competência">{versionName(responsibility.version)}</Row>
              <Row term="Observação">{responsibility.note && plain(responsibility.note)}</Row>
              {event.action && (
                <Row term="Lista de ações">
                  {versionName(event.action.version)}
                  <small> A decisão final é do órgão responsável.</small>
                </Row>
              )}
            </dl>
          </TechGroup>
          <TechGroup title="Fontes de contexto">
            {event.context.length ? (
              <ul className="context-list">
                {event.context.map((item) => (
                  <li key={item.source}>
                    <strong>{plain(item.label)}</strong>
                    <span>
                      {item.status === 'ok'
                        ? (contextSummary(item.source, item.summary) ?? 'consultada')
                        : 'sem resposta nesta consulta'}
                    </span>
                    {item.attribution && <small>{item.attribution}</small>}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="muted">Nenhuma fonte de contexto foi consultada.</p>
            )}
          </TechGroup>
          {!event.prediction.available && (
            <TechGroup title="Dados da previsão">
              <p>
                {event.prediction.reason
                  ? plain(event.prediction.reason)
                  : 'Ainda não há previsão para esta ocorrência.'}
              </p>
            </TechGroup>
          )}
          {event.analysis && (
            <TechGroup title="Origem do texto">
              <dl className="data-list">
                <Row term="Método">texto de modelo de documento, sem inteligência artificial</Row>
                <Row term="Regras">
                  {event.analysis.provenance.assessment_source === 'unavailable'
                    ? 'sem avaliação registrada'
                    : versionName(event.analysis.provenance.ruleset_version)}
                </Row>
              </dl>
            </TechGroup>
          )}
        </div>
        <TechGroup title="Caminho da análise">
          <ol className="decision-trace">
            {event.trace.map((raw) => {
              const step = traceStep(raw);
              return (
                <li key={raw.step} className={step.done ? 'done' : 'unavailable'}>
                  {step.done && step.rows.length > 0 ? (
                    <details>
                      <summary>
                        <span className="trace-title">{step.name}</span>
                        <span className="trace-state">concluída</span>
                      </summary>
                      <dl className="data-list">
                        {step.rows.map(([term, value]) => (
                          <Row key={term} term={term}>
                            {value}
                          </Row>
                        ))}
                      </dl>
                    </details>
                  ) : (
                    <p className="trace-line">
                      <span className="trace-title">{step.name}</span>
                      <span className="trace-state">
                        {step.done ? 'concluída' : 'sem registro'}
                      </span>
                    </p>
                  )}
                </li>
              );
            })}
          </ol>
        </TechGroup>
      </details>
    </section>
  );
}
