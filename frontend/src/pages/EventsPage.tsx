import { lazy, Suspense, useMemo, useState } from 'react';
import { classes, statuses, type UrbanEvent } from '../domain/contracts';

const UrbanMap = lazy(() => import('../components/UrbanMap'));
export function EventsPage({
  events,
  loading,
  error,
  onReload,
  map = false,
}: {
  events: UrbanEvent[] | null;
  loading: boolean;
  error: string;
  onReload: () => void;
  map?: boolean;
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
          `${event.event_key} ${classes[event.urmind_class]}`
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
          <UrbanMap events={filtered} />
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
                      <strong>{classes[event.urmind_class]}</strong>
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
                      <button className="secondary compact" onClick={() => setSelected(event)}>
                        Ver registro
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      {selected && (
        <section className="panel detail" aria-label="Detalhes da ocorrência">
          <div className="section-heading">
            <h2>{classes[selected.urmind_class]}</h2>
            <button className="secondary compact" onClick={() => setSelected(null)}>
              Fechar detalhes
            </button>
          </div>
          <dl>
            <dt>ID da ocorrência</dt>
            <dd>{selected.id}</dd>
            <dt>Evidência</dt>
            <dd>{selected.evidence_mode}</dd>
            <dt>Coordenada original</dt>
            <dd>
              {selected.latitude != null && selected.longitude != null
                ? `${selected.latitude}, ${selected.longitude}`
                : 'Não disponível'}
            </dd>
            <dt>Ponto ajustado à via</dt>
            <dd>
              {selected.snapped_latitude != null && selected.snapped_longitude != null
                ? `${selected.snapped_latitude}, ${selected.snapped_longitude}`
                : 'Não disponível'}
            </dd>
            <dt>Precisão informada</dt>
            <dd>
              {selected.location_accuracy_m == null
                ? 'Não disponível'
                : `${selected.location_accuracy_m} m`}
            </dd>
            <dt>Trecho viário</dt>
            <dd>{selected.road_segment_id || 'Não disponível'}</dd>
            <dt>Responsável / ação</dt>
            <dd>Não disponíveis no contrato HTTP atual</dd>
          </dl>
          <details>
            <summary>Fatores estruturados recebidos</summary>
            <pre>{JSON.stringify(selected.factors, null, 2)}</pre>
          </details>
          <p className="notice">
            Revisão humana ainda não conectada. Confirmações e correções dependerão de autenticação
            e auditoria no backend.
          </p>
        </section>
      )}
    </>
  );
}
