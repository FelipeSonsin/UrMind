import type { ReactNode } from 'react';
import { ChevronRight } from 'lucide-react';
import { labelFor, severityOf, type PublicEvent } from '../../domain/public';
import { SeveritySign } from '../SeveritySign';
import { Skeleton } from '../ui';

/**
 * Lista de ocorrências publicadas (todas já confirmadas pela equipe): o tipo, a rua e a
 * data, a gravidade e uma seta. Cada item é um link para o detalhe, então abre em nova
 * aba e pode ser copiado. A prioridade fica no detalhe.
 */
export function EventFeed({
  events,
  label = 'Ocorrências',
  toolbar,
  loading = false,
  emptyStatus,
  empty,
}: {
  events: PublicEvent[];
  /** Nome acessível da lista. */
  label?: string;
  /** Filtros no cabeçalho da lista, ao lado da contagem. */
  toolbar?: ReactNode;
  /** Troca de filtro em andamento: o cabeçalho fica (com o filtro) e a lista vira esqueleto. */
  loading?: boolean;
  /**
   * Filtro sem resultado: a frase fica no lugar da contagem, que continua montada para o
   * leitor de tela anunciar a troca (de "2 ocorrências" para a frase e de volta).
   */
  emptyStatus?: string;
  /** Lista vazia sem filtro: só este aviso, sem uma contagem "0 ocorrências" dizendo o mesmo. */
  empty: ReactNode;
}) {
  const count = events.length === 1 ? '1 ocorrência' : `${events.length} ocorrências`;
  return (
    <section className="panel feed" aria-label={label}>
      {(events.length > 0 || toolbar) && (
        <div className="feed-head">
          <p className="feed-count" role="status">
            {loading ? 'Carregando ocorrências…' : events.length > 0 ? count : emptyStatus}
          </p>
          {toolbar}
        </div>
      )}
      {loading ? (
        <Skeleton lines={4} />
      ) : events.length === 0 ? (
        !toolbar && empty
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
