import {
  labelFor,
  priorityBand,
  severityOf,
  situationLabel,
  type PublicEvent,
} from '../../domain/public';

export function EventFeed({
  events,
  selectedId,
  onSelect,
  title = 'Ocorrências recentes',
  emptyHint,
}: {
  events: PublicEvent[];
  selectedId?: string | null;
  onSelect: (event: PublicEvent) => void;
  title?: string;
  emptyHint?: string;
}) {
  return (
    <section className="panel feed" aria-label={title}>
      <div className="section-heading">
        <h2>{title}</h2>
        <span className="muted">{events.length} registros</span>
      </div>
      {events.length === 0 ? (
        <p className="muted">
          {emptyHint ?? 'Nenhuma ocorrência publicada ainda para esta área piloto.'}
        </p>
      ) : (
        <ul className="feed-list">
          {events.map((event) => {
            const severity = severityOf(event.severity);
            return (
              <li key={event.id}>
                <button
                  type="button"
                  className={`feed-item${selectedId === event.id ? ' selected' : ''}`}
                  aria-current={selectedId === event.id ? 'true' : undefined}
                  onClick={() => onSelect(event)}
                >
                  <time dateTime={event.occurred_at}>
                    {new Date(event.occurred_at).toLocaleDateString('pt-BR', {
                      day: '2-digit',
                      month: '2-digit',
                    })}
                  </time>
                  <span className="feed-main">
                    <strong>{labelFor(event.urmind_class)}</strong>
                    <small>{event.road_name ?? 'via não associada'}</small>
                  </span>
                  <span className={`risk-tag risk-${severity.level}`}>
                    <i aria-hidden="true">{severity.shape}</i> {severity.label}
                  </span>
                  <span className="feed-meta">
                    {situationLabel(event.status)}
                    <small>prioridade {priorityBand(event.priority_score)}</small>
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
