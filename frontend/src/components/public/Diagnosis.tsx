import { contextSummary } from '../../content/plainLanguage';
import { SeveritySign } from '../SeveritySign';
import { readable } from '../../content/technical';
import { labelFor, severityOf, type PublicEventDetail } from '../../domain/public';

/*
 * Peças do detalhe de uma ocorrência publicada. A parte principal fala a língua de
 * quem consulta (o que é, quanto urge, o que fazer, onde); números do modelo, regras
 * e fontes ficam em "Detalhes técnicos" (TechnicalDetails.tsx), recolhidos.
 */

const decimal = new Intl.NumberFormat('pt-BR', {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

/** Selo obrigatório para resultado de modelo EXPERIMENTAL_SHADOW. */
export function ExperimentalBadge({ stage }: { stage: string | null | undefined }) {
  if (stage !== 'EXPERIMENTAL_SHADOW') return null;
  return (
    <span className="badge experimental-badge" role="note">
      ANÁLISE EXPERIMENTAL
    </span>
  );
}

/** Rua da ocorrência; sem via associada, `null`. */
export function eventPlace(event: PublicEventDetail): string | null {
  return event.road?.name ?? event.road_name ?? null;
}

/**
 * Análise urbana registrada (texto de modelo de documento, nunca escrito na hora):
 * o que se vê, a avaliação, consequências possíveis e os limites.
 */
export function UrbanAnalysis({ event }: { event: PublicEventDetail }) {
  const analysis = event.analysis;
  if (!analysis) return null;
  return (
    <section className="panel urban-analysis" aria-label="Análise urbana">
      {event.model_stage !== 'EXPERIMENTAL_SHADOW' && (
        <ExperimentalBadge stage={analysis.provenance.model_stage} />
      )}
      <h2>O que se vê</h2>
      <p>{analysis.description}</p>
      <h3>Avaliação</h3>
      <p>{analysis.diagnosis}</p>
      {analysis.potential_consequences.length > 0 && (
        <>
          <h3>Possíveis consequências</h3>
          <p className="muted">São possibilidades; não são uma previsão.</p>
          <ul>
            {analysis.potential_consequences.map((item, index) => (
              <li key={`${item.domain}-${index}`}>{item.statement}</li>
            ))}
          </ul>
        </>
      )}
      {analysis.possible_causes.length > 0 && (
        <>
          <h3>Possíveis causas</h3>
          <ul>
            {analysis.possible_causes.map((cause) => (
              <li key={cause}>{cause}</li>
            ))}
          </ul>
        </>
      )}
      {analysis.limitations.length > 0 && (
        <>
          <h3>Limites desta análise</h3>
          <ul>
            {analysis.limitations.map((limitation) => (
              <li key={limitation}>{limitation}</li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}

/** Cartão do Início: o que é, onde e a gravidade. Prioridade e o resto ficam no detalhe. */
export function QuickDiagnosis({
  event,
  label = 'Ocorrência selecionada',
}: {
  event: PublicEventDetail;
  /** "Mais recente" enquanto ninguém escolheu um ponto no mapa. */
  label?: string;
}) {
  const severity = severityOf(event.risk?.severity);
  const place = eventPlace(event);
  return (
    <div className="quick-diagnosis">
      <p className="section-label">{label}</p>
      <ExperimentalBadge stage={event.model_stage} />
      <h2>{labelFor(event.urmind_class)}</h2>
      {place && <p className="quick-place">{place}</p>}
      <p className={`risk-tag risk-${severity.level}`}>
        <SeveritySign level={severity.level} /> {severity.label}
      </p>
    </div>
  );
}

/** Solução indicada e quem resolve. A ação é sempre uma sugestão da lista de ações. */
export function RecommendedAction({
  event,
  heading: Heading = 'h3',
  channel = false,
}: {
  event: PublicEventDetail;
  heading?: 'h2' | 'h3';
  /** Na página da ocorrência: diz que o UrMind não avisa a prefeitura e onde pedir. */
  channel?: boolean;
}) {
  const responsibility = event.responsibility;
  return (
    <div className="action-panel">
      <Heading>Solução indicada</Heading>
      <p className="action-title">
        {event.action ? event.action.label : 'Ainda sem solução indicada'}
      </p>
      <p className="action-owner">
        Quem costuma resolver:{' '}
        {responsibility.status === 'assigned' ? (
          <strong>{responsibility.responsible}</strong>
        ) : (
          'a equipe ainda vai definir.'
        )}
      </p>
      {channel && (
        <p className="action-note">
          O UrMind não avisa a prefeitura: para pedir o conserto, registre também no{' '}
          <a
            href="https://sp156.prefeitura.sp.gov.br/portal"
            target="_blank"
            rel="noopener noreferrer"
          >
            SP156
          </a>
          , o canal oficial da cidade de São Paulo.
        </p>
      )}
    </div>
  );
}

/** Onde fica: a via e o endereço aproximado, com o caminho no mapa. */
export function LocationSummary({ event }: { event: PublicEventDetail }) {
  const address = event.context.find(
    (item) => item.source === 'nominatim_reverse' && item.status === 'ok',
  );
  const approximate = address ? contextSummary(address.source, address.summary) : null;
  const place = eventPlace(event);
  // O endereço aproximado já traz a rua: a rua só aparece à parte quando é outra.
  const main = approximate ?? place;
  const extra = place && approximate && !approximate.includes(place) ? place : null;
  return (
    <>
      <h2>Local</h2>
      {main ? (
        <p className="location-line">
          <strong>{main}</strong>
          {extra && <span>Via mais próxima: {extra}</span>}
        </p>
      ) : (
        <p className="muted">A via ainda não foi identificada.</p>
      )}
      {event.latitude != null && event.longitude != null && (
        <p className="location-links">
          <a className="text-button" href={`#/mapa?ponto=${event.id}`}>
            Ver no mapa
          </a>
          <a
            className="text-button"
            href={`https://www.google.com/maps/dir/?api=1&destination=${event.latitude.toFixed(4)},${event.longitude.toFixed(4)}`}
            target="_blank"
            rel="noopener noreferrer"
          >
            Como chegar
          </a>
        </p>
      )}
    </>
  );
}

/** Previsão só existe com Prediction real. RiskAssessment não vira previsão. */
export function PredictionPanel({ event }: { event: PublicEventDetail }) {
  const prediction = event.prediction;
  if (!prediction.available) return null;
  return (
    <div className="prediction-panel">
      <h2>Previsão</h2>
      {prediction.task && <p className="action-title">{readable(prediction.task)}</p>}
      <dl className="data-list">
        {[
          ['Valor estimado', prediction.value != null && decimal.format(prediction.value)],
          ['Prazo', prediction.horizon_days != null && `${prediction.horizon_days} dias`],
          ['Incerteza', prediction.uncertainty != null && decimal.format(prediction.uncertainty)],
          ['Modelo', prediction.model_version],
        ]
          // Dado que não existe não vira linha "não disponível".
          .filter(([, value]) => value)
          .map(([term, value]) => (
            <div key={term as string}>
              <dt>{term}</dt>
              <dd>{value}</dd>
            </div>
          ))}
      </dl>
    </div>
  );
}
