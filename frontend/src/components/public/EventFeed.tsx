import type { ReactNode } from 'react';
import { ChevronRight } from 'lucide-react';
import { labelFor, severityOf, type PublicEvent } from '../../domain/public';
import { SeveritySign } from '../SeveritySign';

/**
 * Lista de ocorrências publicadas (todas já confirmadas pela equipe): o tipo, a rua e a
 * data, a gravidade e uma seta. Cada item é um link para o detalhe, então abre em nova
 * aba e pode ser copiado. A prioridade fica no detalhe.
 */
export function EventFeed({
  events,
  label = 'Ocorrências',
  toolbar,
  emptyHint,
}: {
  events: PublicEvent[];
  /** Nome acessível da lista. */
  label?: string;
  /** Filtros no cabeçalho da lista, ao lado da contagem. */
  toolbar?: ReactNode;
  emptyHint?: string;
}) {
  const count = events.length === 1 ? '1 ocorrência' : `${events.length} ocorrências`;
  return (
    <section className="panel feed" aria-label={label}>
      <div className="feed-head">
        <p className="feed-count" role="status">
          {count}
        </p>
        {toolbar}
      </div>
      {events.length === 0 ? (
        <p className="muted feed-empty">
          {emptyHint ?? 'Nenhuma ocorrência publicada ainda para esta área piloto.'}
        </p>
      ) : (
        <ul className="feed-list">
          {events.map((event) => {
            const severity = severityOf(event.severity);
            const date = new Date(event.occurred_at).toLocaleDateString('pt-BR');
            return (
              <li key={event.id}>
                <a className="feed-item" href={`#/events/${event.id}`}>
                  <span className="feed-main">
                    <strong>{labelFor(event.urmind_class)}</strong>
                    <small>
                      {event.road_name && <span>{event.road_name}</span>}
                      <time dateTime={event.occurred_at}>{date}</time>
                    </small>
                  </span>
                  <span className={`risk-tag risk-${severity.level}`}>
                    <SeveritySign level={severity.level} /> {severity.label}
                  </span>
                  <ChevronRight className="feed-chevron" size={18} aria-hidden="true" />
                </a>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
