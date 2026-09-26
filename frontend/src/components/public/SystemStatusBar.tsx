import type { PublicStatus } from '../../domain/public';

const DOT: Record<string, string> = { ok: 'ok', degraded: 'degraded', unavailable: 'down' };

function since(iso: string | null): string {
  if (!iso) return 'nenhuma ainda';
  const seconds = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  if (seconds < 60) return `há ${seconds} s`;
  if (seconds < 3600) return `há ${Math.round(seconds / 60)} min`;
  if (seconds < 86400) return `há ${Math.round(seconds / 3600)} h`;
  return new Date(iso).toLocaleDateString('pt-BR');
}

/** Estado por componente: nunca reduz tudo a um único ONLINE/OFFLINE. */
export function SystemStatusBar({
  status,
  error,
}: {
  status: PublicStatus | null;
  error?: string;
}) {
  if (error) return <p className="status-bar error">{error}</p>;
  if (!status) return <p className="status-bar muted">Consultando o estado do sistema…</p>;
  // O Scout (hardware) está fora do escopo mobile/foto; seu estado não aparece mais.
  const items = [
    { key: 'detector', label: 'IA', component: status.detector },
    { key: 'api', label: 'API', component: status.api },
    { key: 'database', label: 'Banco', component: status.database },
  ];
  return (
    <div className="status-bar" role="status">
      {items.map((item) => (
        <span key={item.key} className="status-chip" title={item.component.detail ?? undefined}>
          <i aria-hidden="true" className={DOT[item.component.status]} />
          <b>{item.label}</b>
          <span>
            {item.component.status === 'ok'
              ? 'operacional'
              : item.component.status === 'degraded'
                ? 'parcial'
                : 'indisponível'}
          </span>
        </span>
      ))}
      <span className="status-chip">
        <b>Última ocorrência</b>
        <span>{since(status.last_event_at)}</span>
      </span>
      <span className="status-chip">
        <b>Área piloto</b>
        <span>
          {status.pilot_area ?? 'não definida'} · {status.road_segments_total} trechos
        </span>
      </span>
    </div>
  );
}
