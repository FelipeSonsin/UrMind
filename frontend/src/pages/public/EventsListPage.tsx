import { Camera, MapPin } from 'lucide-react';
import { EventFeed } from '../../components/public/EventFeed';
import { EmptyState, PageHeader, Skeleton } from '../../components/ui';
import { labelFor, type PublicEvent } from '../../domain/public';

/** Ocorrências confirmadas em lista, com o filtro por tipo de problema no cabeçalho. */
export function PublicEventsPage({
  events,
  loaded,
  listFilter,
  error,
  filters,
  onFilters,
}: {
  events: PublicEvent[];
  /** As ocorrências já chegaram uma vez: antes disso, nada de "nenhuma ocorrência". */
  loaded: boolean;
  /** Tipo de problema que gerou `events`; diferente do filtro atual, a lista ainda vai mudar. */
  listFilter: string;
  error: string;
  filters: { urmind_class: string; status: string };
  onFilters: (value: { urmind_class: string; status: string }) => void;
}) {
  // A lista na tela é de outro filtro e a nova ainda está chegando: o seletor fica (com o
  // foco) e a lista dá lugar ao esqueleto, sem contagem nem itens do filtro anterior.
  const switching = loaded && listFilter !== filters.urmind_class;
  // Só tipos presentes; com um tipo escolhido, ele continua na lista para voltar a "Todos".
  const present = new Set(events.map((event) => event.urmind_class));
  if (filters.urmind_class) present.add(filters.urmind_class);
  const classOptions = [...present]
    .map((code): [string, string] => [code, labelFor(code)])
    .sort((a, b) => a[1].localeCompare(b[1], 'pt-BR'));
  const filter =
    classOptions.length > 1 || filters.urmind_class || switching ? (
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
      {/* Esqueleto da página só na primeira carga; nas releituras, a lista (ou o aviso vazio)
          fica à vista. Se a primeira carga falhou, só o erro. */}
      {!loaded ? (
        !error && (
          <>
            <p className="sr-only" role="status">
              Carregando ocorrências…
            </p>
            <Skeleton lines={4} />
          </>
        )
      ) : (
        <EventFeed
          events={switching ? [] : events}
          // Se a troca falhou, só o seletor e o erro (a página tenta de novo sozinha). Um erro
          // anterior também esconde o "carregando" da troca: melhor que piscar a cada releitura
          // de 30 s com o servidor fora do ar.
          loading={switching && !error}
          toolbar={filter}
          emptyStatus={switching ? undefined : 'Nenhuma ocorrência corresponde a este filtro.'}
          empty={
            // Mesmo desenho do vazio de "Meus relatos": o que é e o que fazer.
            <EmptyState
              icon={<MapPin size={22} strokeWidth={1.8} />}
              title="Nenhuma ocorrência publicada ainda"
              action={
                <a className="button" href="#/registrar">
                  <Camera size={17} aria-hidden="true" /> Registrar relato
                </a>
              }
            >
              Depois da revisão da equipe, cada relato confirmado aparece aqui e no mapa.
            </EmptyState>
          }
        />
      )}
    </>
  );
}
