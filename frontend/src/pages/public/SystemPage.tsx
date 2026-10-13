import { RefreshCw } from 'lucide-react';
import { PageHeader } from '../../components/ui';
import { pilotAreaName } from '../../content/plainLanguage';
import { automaticClassCodes, type PublicStatus } from '../../domain/public';

type Component = PublicStatus['api'];

const STATE: Record<string, string> = {
  ok: 'Funcionando',
  degraded: 'Instável',
  unavailable: 'Fora do ar',
};

const number = new Intl.NumberFormat('pt-BR');
const relative = new Intl.RelativeTimeFormat('pt-BR', { numeric: 'auto' });

function since(iso: string | null): string {
  if (!iso) return 'nenhuma ainda';
  const minutes = Math.round((new Date(iso).getTime() - Date.now()) / 60_000);
  if (minutes > -60) return relative.format(Math.min(-1, minutes), 'minute');
  if (minutes > -1440) return relative.format(Math.round(minutes / 60), 'hour');
  return new Date(iso).toLocaleDateString('pt-BR');
}

/** "17/09/2026 às 09:05", sem segundos. */
function at(iso: string): string {
  const date = new Date(iso);
  return `${date.toLocaleDateString('pt-BR')} às ${date.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })}`;
}

/**
 * Situação do serviço, fora da navegação do cidadão: cada parte com o próprio estado
 * (nunca um único "online"), o que já foi publicado e a região do projeto piloto.
 */
export function SystemPage({
  status,
  error,
  onReload,
}: {
  status: PublicStatus | null;
  error: string;
  onReload: () => void;
}) {
  // A análise automática desligada é o desenho atual, não uma falha: aparece neutra.
  // Sem tipo de problema liberado para ela, não está ativa, mesmo com um modelo em teste.
  const analysisOff =
    !status || status.detector.status === 'unavailable' || automaticClassCodes().length === 0;
  const parts: Array<{ key: string; label: string; component: Component; note?: string }> = status
    ? [
        { key: 'api', label: 'Envio e consulta de relatos', component: status.api },
        { key: 'database', label: 'Armazenamento dos relatos', component: status.database },
        {
          key: 'detector',
          label: 'Análise automática das fotos',
          component: status.detector,
          note: analysisOff ? 'As fotos são revisadas pela equipe.' : undefined,
        },
      ]
    : [];
  return (
    <>
      <PageHeader
        title="Situação do serviço"
        description="Veja se o UrMind está funcionando normalmente. Se algo parar, aparece aqui."
        actions={
          <button className="secondary" onClick={onReload}>
            <RefreshCw size={16} aria-hidden="true" /> Atualizar
          </button>
        }
      />
      {error ? (
        <p className="error" role="alert">
          {error}
        </p>
      ) : !status ? (
        <p className="muted" role="status">
          Consultando o serviço…
        </p>
      ) : (
        <>
          <section className="panel" aria-labelledby="service-title">
            <h2 id="service-title">Partes do serviço</h2>
            <ul className="service-list" aria-live="polite">
              {parts.map(({ key, label, component, note }) => {
                const off = key === 'detector' && analysisOff;
                return (
                  <li key={key} className={`is-${off ? 'off' : component.status}`}>
                    <span className="service-dot" aria-hidden="true" />
                    <span className="service-name">{label}</span>
                    <span className="service-state">
                      {off ? 'Não ativa' : (STATE[component.status] ?? component.status)}
                      {note && <small>{note}</small>}
                    </span>
                  </li>
                );
              })}
            </ul>
            <p className="muted service-checked">Verificado em {at(status.checked_at)}.</p>
          </section>
          {/* Os números contam tudo o que foi publicado; a região do projeto piloto é um dado
              ao lado, não um limite (relatos de qualquer lugar do Brasil são recebidos). */}
          <section className="panel" aria-labelledby="coverage-title">
            <h2 id="coverage-title">Mapa público</h2>
            <dl className="data-list coverage-list">
              <div>
                <dt>Ocorrências confirmadas</dt>
                <dd>{number.format(status.events_total)}</dd>
              </div>
              <div>
                <dt>Última publicação</dt>
                <dd>{since(status.last_event_at)}</dd>
              </div>
              <div>
                <dt>Projeto piloto</dt>
                <dd>{pilotAreaName(status.pilot_area)}</dd>
              </div>
            </dl>
          </section>
        </>
      )}
    </>
  );
}
