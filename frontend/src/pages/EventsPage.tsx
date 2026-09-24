import { lazy, Suspense, useMemo, useState } from 'react';
import { EventDetail } from '../components/EventDetail';
import { classes, statuses, type UrbanEvent } from '../domain/contracts';
import { labelFor } from '../domain/public';

const UrbanMap = lazy(() => import('../components/UrbanMap'));
export function EventsPage({
  events,
  loading,
  error,
  onReload,
  map = false,
  changed,
  linkDetails = false,
}: {
  events: UrbanEvent[] | null;
  loading: boolean;
  error: string;
  onReload: () => void;
  map?: boolean;
  changed?: { eventId: string | null; at: number };
  linkDetails?: boolean;
}) {
  const [query, setQuery] = useState('');
  const [status, setStatus] = useState('');
  const [category, setCategory] = useState('');
  const [selected, setSelected] = useState<UrbanEvent | null>(null);
  const filtered = useMemo(
    () =>
      (events || []).filter(
        (event) =>
          (!status || event.status === status) &&
          (!category || event.urmind_class === category) &&
          `${event.event_key} ${labelFor(event.urmind_class)}`
            .toLocaleLowerCase('pt-BR')
            .includes(query.toLocaleLowerCase('pt-BR')),
      ),
    [events, query, status, category],
  );
  return (
    <>
      <div className="page-heading">
        <div>
          <p className="eyebrow">OBSERVATÓRIO / {map ? 'TERRITÓRIO' : 'OCORRÊNCIAS'}</p>
          <h1>{map ? 'Gêmeo digital 2D' : 'Ocorrências urbanas'}</h1>
          <p>
            {map
              ? 'Evidências no território, com sua localização original preservada.'
              : 'Acompanhe as ocorrências consolidadas pelo backend.'}
          </p>
        </div>
        <button className="secondary" disabled={loading} onClick={onReload}>
          {loading ? 'Consultando…' : 'Atualizar'}
        </button>
      </div>
      <div className="filters panel">
        <label>
          Buscar
          <input
            type="search"
            placeholder="Classe ou identificador"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>
        <div>
          <label htmlFor="class-filter">Classe</label>
          <select id="class-filter" value={category} onChange={(e) => setCategory(e.target.value)}>
            <option value="">Todas as classes</option>
            {Object.entries(classes).map(([id, label]) => (
              <option key={id} value={id}>
                {label}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor="status-filter">Estado</label>
          <select id="status-filter" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">Todos os estados</option>
            {Object.entries(statuses).map(([id, label]) => (
              <option key={id} value={id}>
                {label}
              </option>
            ))}
          </select>
        </div>
      </div>
      {error && (
        <p className="error" role="alert">
          {error} Nenhuma contagem foi estimada.
        </p>
      )}
      {loading && <p role="status">Carregando ocorrências…</p>}
      {map && (
        <Suspense fallback={<p>Carregando mapa…</p>}>
          <UrbanMap events={filtered} allowExport />
        </Suspense>
      )}
      <section className="panel">
        <div className="section-heading">
          <h2>Registros {events && `(${filtered.length})`}</h2>
          <span className="muted">Até 500 registros mais recentes</span>
        </div>
        {!filtered.length ? (
          <div className="empty">
            <h3>{events ? 'Nenhuma ocorrência neste recorte' : 'Aguardando dados do backend'}</h3>
            <p>
              {events
                ? 'Ajuste os filtros ou atualize após o processamento de novas evidências.'
                : 'Conecte o backend e o banco para consultar ocorrências reais.'}
            </p>
          </div>
        ) : (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Ocorrência</th>
                  <th>Estado</th>
                  <th>Data</th>
                  <th>Confiança visual</th>
                  <th>Detalhes</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((event) => (
                  <tr key={event.id}>
                    <td>
                      <strong>{labelFor(event.urmind_class)}</strong>
                      <small className="record-id">{event.event_key}</small>
                    </td>
                    <td>
                      <span className="badge">{statuses[event.status]}</span>
                    </td>
                    <td>{new Date(event.occurred_at).toLocaleString('pt-BR')}</td>
                    <td>
                      {event.visual_confidence == null
                        ? 'Não disponível'
                        : `${(event.visual_confidence * 100).toFixed(1)}%`}
                    </td>
                    <td>
                      {linkDetails ? (
                        <a href={`#/app/eventos/${event.id}`}>Ver registro</a>
                      ) : (
                        <button className="secondary compact" onClick={() => setSelected(event)}>
                          Ver registro
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      {selected && (
        <EventDetail
          key={selected.id}
          id={selected.id}
          onClose={() => setSelected(null)}
          onChanged={onReload}
          refreshAt={
            changed && (!changed.eventId || changed.eventId === selected.id) ? changed.at : 0
          }
        />
      )}
    </>
  );
}
