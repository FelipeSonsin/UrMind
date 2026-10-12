import { useMemo, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import { PageHeader } from '../../components/ui';
import { useWideScreen } from '../../app/hooks/useWideScreen';
import { statuses, statusTones, type UrbanEvent } from '../../domain/contracts';
import { labelFor } from '../../domain/public';

// Estados ainda sem decisão humana (EventStatus no backend).
const awaitingDecision = new Set<string>(['detected', 'review', 'triage_required']);
const percent = new Intl.NumberFormat('pt-BR', { style: 'percent', maximumFractionDigits: 1 });

/**
 * Ocorrências da equipe: busca, filtros por tipo e situação, e o link para cada uma.
 * No celular a tabela vira cartões, com o nome de cada dado ao lado do valor.
 */
export function EventsPage({
  events,
  loading,
  error,
  onReload,
}: {
  events: UrbanEvent[] | null;
  loading: boolean;
  error: string;
  /** Sem atualização ao vivo, a página oferece "Atualizar"; com ela, não precisa. */
  onReload?: () => void;
}) {
  const [status, setStatus] = useState('');
  const [category, setCategory] = useState('');
  const filtered = useMemo(
    () =>
      (events || []).filter(
        (event) =>
          (!status || event.status === status) && (!category || event.urmind_class === category),
      ),
    [events, status, category],
  );
  // Só os tipos que aparecem nas ocorrências carregadas, com o nome da lista oficial.
  const types = [...new Set((events ?? []).map((event) => event.urmind_class))];
  const wide = useWideScreen();
  const filters = (
    <div className="filters" role="group" aria-label="Filtros das ocorrências">
      <div>
        <label htmlFor="team-event-class">Tipo de problema</label>
        <select
          id="team-event-class"
          value={category}
          onChange={(e) => setCategory(e.target.value)}
        >
          <option value="">Todos</option>
          {types.map((code) => (
            <option key={code} value={code}>
              {labelFor(code)}
            </option>
          ))}
        </select>
      </div>
      <div>
        <label htmlFor="team-event-status">Situação</label>
        <select id="team-event-status" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">Todas</option>
          {Object.entries(statuses).map(([id, label]) => (
            <option key={id} value={id}>
              {label}
            </option>
          ))}
        </select>
      </div>
    </div>
  );
  // Sem análise automática não há confiança a mostrar: a coluna só aparece com dado.
  const showConfidence = filtered.some((event) => event.visual_confidence != null);
  return (
    <>
      <PageHeader
        title="Ocorrências"
        description="Problemas registrados a partir dos relatos. Abra uma ocorrência para ver os detalhes e decidir."
        actions={
          onReload && (
            <button type="button" className="secondary" disabled={loading} onClick={onReload}>
              <RefreshCw size={16} aria-hidden="true" /> {loading ? 'Consultando…' : 'Atualizar'}
            </button>
          )
        }
      />
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <section className="panel" aria-label="Lista de ocorrências">
        {wide ? (
          filters
        ) : (
          <details className="filters-disclosure">
            <summary>Filtros</summary>
            {filters}
          </details>
        )}
        {!events ? (
          !error && (
            <p role="status" className="loading-line">
              Carregando ocorrências…
            </p>
          )
        ) : !filtered.length ? (
          <div className="empty">
            <h3>Nenhuma ocorrência neste filtro</h3>
            <p>Ajuste os filtros ou atualize depois de revisar novos relatos.</p>
          </div>
        ) : (
          <>
            <p className="muted">
              {filtered.length === 1 ? '1 ocorrência' : `${filtered.length} ocorrências`}
            </p>
            <div className="table-scroll">
              <table className="cards-on-phone">
                <thead>
                  <tr>
                    <th>Tipo de problema</th>
                    <th>Situação</th>
                    <th>Data</th>
                    {showConfidence && <th>Confiança da análise</th>}
                    <th>
                      <span className="sr-only">Abrir</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((event) => (
                    <tr key={event.id}>
                      <td>
                        {/* O código interno (event_key) continua valendo na busca. */}
                        <strong>{labelFor(event.urmind_class)}</strong>
                      </td>
                      <td data-label="Situação">
                        <span className={`badge ${statusTones[event.status] ?? ''}`}>
                          {statuses[event.status]}
                        </span>
                      </td>
                      <td data-label="Data">
                        {new Date(event.occurred_at).toLocaleString('pt-BR', {
                          dateStyle: 'short',
                          timeStyle: 'short',
                        })}
                      </td>
                      {showConfidence && (
                        <td data-label="Confiança da análise">
                          {/* Sem score do modelo antes da decisão humana. */}
                          {awaitingDecision.has(event.status)
                            ? 'Oculto até a revisão'
                            : event.visual_confidence == null
                              ? 'sem análise automática'
                              : percent.format(event.visual_confidence)}
                        </td>
                      )}
                      <td>
                        <a className="report-action" href={`#/app/eventos/${event.id}`}>
                          Ver ocorrência
                        </a>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </section>
    </>
  );
}
