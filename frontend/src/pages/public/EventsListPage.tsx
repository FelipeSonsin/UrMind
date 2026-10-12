import { EventFeed } from '../../components/public/EventFeed';
import { PageHeader, Skeleton } from '../../components/ui';
import { labelFor, type PublicEvent } from '../../domain/public';

/** Ocorrências confirmadas em lista, com o filtro por tipo de problema no cabeçalho. */
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
  const filter =
    classOptions.length > 1 || filters.urmind_class ? (
      <label>
        Tipo de problema
        <select
          value={filters.urmind_class}
          onChange={(event) =>
            onFilters({ ...filters, urmind_class: event.target.value, status: '' })
          }
        >
          <option value="">Todos os tipos</option>
          {classOptions.map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </label>
    ) : null;
  return (
    <>
      <PageHeader
        title="Ocorrências confirmadas"
        description="Problemas que a equipe confirmou, do mais recente ao mais antigo."
        actions={
          <a className="button secondary" href="#/mapa">
            Ver no mapa
          </a>
        }
      />
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {/* Ao trocar o filtro, a lista anterior fica à vista até a nova chegar. */}
      {loading && !events.length ? (
        <Skeleton lines={4} />
      ) : (
        <EventFeed
          events={events}
          toolbar={filter}
          emptyHint={
            filters.urmind_class
              ? 'Nenhuma ocorrência corresponde a este filtro.'
              : 'Nenhuma ocorrência publicada ainda.'
          }
        />
      )}
    </>
  );
}
