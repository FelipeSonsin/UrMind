import { useEffect, useState } from 'react';
import { ListChecks, RefreshCw } from 'lucide-react';
import { useAppState } from '../../app/AppState';
import { AWAITING, awaitingDecision, byUrgency } from '../../app/hooks/useReports';
import { ReportsError, TeamQueueList } from '../../components/reports/ReportLists';
import { PageHeader, Skeleton } from '../../components/ui';
import { photoReason } from '../../content/photoReasons';
import { readable } from '../../content/technical';
import { automaticClassCodes } from '../../domain/public';
import { api } from '../../services/api';

/*
 * Blocos do Painel da equipe, na ordem em que aparecem (app: pages/team/TeamArea.tsx):
 * os números e os próximos a revisar, fotos recusadas na checagem, serviços externos e,
 * por fim, os dados técnicos da fila de análise automática, recolhidos.
 */

type Totals = Awaited<ReturnType<typeof api.reportTotals>>;

/** Totais dos relatos; `days` vale para as recusas da checagem da foto. */
function useReportTotals(days: number) {
  // A atualização ao vivo (ou "Atualizar") muda a revisão: os números são relidos sem
  // sumir da tela e sem perder o período escolhido.
  const { revision } = useAppState();
  const [totals, setTotals] = useState<Totals | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    setError('');
    void api
      .reportTotals(controller.signal, days)
      .then((data) => {
        if (!controller.signal.aborted) setTotals(data);
      })
      .catch((failure: Error) => {
        if (!controller.signal.aborted) setError(failure.message);
      });
    return () => controller.abort();
  }, [days, revision]);
  return { totals, error };
}

const number = new Intl.NumberFormat('pt-BR');
const seconds = (ms: number) =>
  `${new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 1 }).format(ms / 1000)} s`;
const percent = new Intl.NumberFormat('pt-BR', { style: 'percent', maximumFractionDigits: 1 });
const when = (iso: string) =>
  new Date(iso).toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' });

/** Painel: os números que pedem ação, os próximos a revisar e o que já foi publicado. */
export function TeamDashboard() {
  const { refresh, reports, teamData } = useAppState();
  const { totals, error } = useReportTotals(30);
  const live = teamData.realtime === 'connected';
  /** Abre a fila já filtrada no que o número conta. */
  const openQueue = (status: string, conflict = false) => {
    reports.setQueueFilters({ status, family: '', issue: '', from: '', to: '' });
    reports.setQueueConflict(conflict);
    reports.setQueuePending(false);
  };
  return (
    <section className="dashboard">
      <PageHeader
        title="Painel"
        description="O que precisa de atenção agora e o que já foi publicado."
        actions={
          <>
            <a className="button" href="#/app/reviews" onClick={() => openQueue(AWAITING)}>
              <ListChecks size={17} aria-hidden="true" /> Abrir fila de revisão
            </a>
            {/* Com atualização ao vivo os números se renovam sozinhos: sem botão. */}
            {!live && (
              <button type="button" className="secondary" onClick={refresh}>
                <RefreshCw size={16} aria-hidden="true" /> Atualizar
              </button>
            )}
          </>
        }
      />
      {error && (
        <p className="error" role="alert">
          Não foi possível carregar os números: {error}
        </p>
      )}
      {!totals && !error && (
        <p role="status" className="loading-line">
          Consultando relatos…
        </p>
      )}
      {totals && (
        <>
          <dl className="stat-row">
            <Stat
              label="Aguardando revisão"
              value={totals.awaiting_review}
              href="#/app/reviews"
              onOpen={() => openQueue(AWAITING)}
            />
            <Stat
              label="Falta o local"
              value={totals.without_location}
              href="#/app/reviews"
              onOpen={() => openQueue('location_required')}
              attention
            />
            <Stat
              label="Conflitos de local"
              value={totals.location_conflicts}
              href="#/app/reviews"
              onOpen={() => openQueue('', true)}
              attention
            />
            <Stat
              label="Publicados"
              value={totals.published}
              href="#/app/reviews"
              onOpen={() => openQueue('published')}
            />
          </dl>
          {/* O "hoje" do servidor é o dia UTC (começa às 21h em Brasília): só a semana. */}
          <p className="muted stat-note">
            {number.format(totals.week)} {totals.week === 1 ? 'relato' : 'relatos'} nos últimos 7
            dias.
          </p>
        </>
      )}
      <NextToReview onOpenQueue={() => openQueue(AWAITING)} />
    </section>
  );
}

/**
 * Um número do Painel, que leva à lista do que ele conta. O destaque de atenção só
 * aparece no que foge do fluxo normal (falta de local, conflito), e só quando há algum.
 */
function Stat({
  label,
  value,
  href,
  onOpen,
  attention = false,
}: {
  label: string;
  value: number;
  href: string;
  onOpen?: () => void;
  attention?: boolean;
}) {
  return (
    <div className={attention && value > 0 ? 'is-attention' : undefined}>
      <dt>
        <a className="stat-link" href={href} onClick={onOpen}>
          {label}
        </a>
      </dt>
      <dd>{number.format(value)}</dd>
    </div>
  );
}

/** Os três relatos mais urgentes, no mesmo critério da Fila de revisão. */
function NextToReview({ onOpenQueue }: { onOpenQueue: () => void }) {
  const { reports } = useAppState();
  const waiting = reports.own.filter(awaitingDecision).sort(byUrgency);
  return (
    <section className="panel" aria-labelledby="next-title">
      <div className="section-heading">
        <h2 id="next-title">Próximos a revisar</h2>
        {waiting.length > 3 && (
          <a href="#/app/reviews" onClick={onOpenQueue}>
            Ver fila completa
          </a>
        )}
      </div>
      <ReportsError />
      {reports.loading ? (
        <Skeleton lines={3} />
      ) : waiting.length ? (
        <TeamQueueList reports={waiting.slice(0, 3)} />
      ) : (
        !reports.error && <p className="muted">Nenhum relato esperando revisão.</p>
      )}
    </section>
  );
}

/** Fotos recusadas pela checagem de qualidade, por motivo, no período escolhido. */
export function PhotoGateStats() {
  const [days, setDays] = useState(30);
  const { totals, error } = useReportTotals(days);
  const reasons = Object.entries(totals?.rejected_by_reason ?? {});
  const gate = totals?.gate_metrics;
  return (
    <section className="panel" aria-labelledby="gate-title">
      <div className="section-heading">
        <h2 id="gate-title">Fotos recusadas na checagem</h2>
        <div className="inline-field">
          <label htmlFor="gate-period">Período</label>
          <select
            id="gate-period"
            value={days}
            onChange={(event) => setDays(Number(event.target.value))}
          >
            <option value={7}>7 dias</option>
            <option value={30}>30 dias</option>
            <option value={90}>90 dias</option>
          </select>
        </div>
      </div>
      {error && <p role="alert">Não foi possível carregar os números: {error}</p>}
      {!totals && !error && <p role="status">Consultando relatos…</p>}
      {totals && (
        <>
          {gate && (
            <p>
              {number.format(gate.accepted)} {gate.accepted === 1 ? 'foto aceita' : 'fotos aceitas'}{' '}
              e {number.format(gate.rejected)} {gate.rejected === 1 ? 'recusada' : 'recusadas'} nos
              últimos {gate.days} dias.
            </p>
          )}
          {reasons.length === 0 ? (
            <p className="muted">Nenhuma foto recusada no período.</p>
          ) : (
            <dl className="key-values">
              {reasons.map(([reason, count]) => (
                <div key={reason}>
                  <dt>{photoReason(reason)}</dt>
                  <dd>
                    {number.format(count)}
                    {gate?.rates_by_reason[reason] != null && (
                      <small> {percent.format(gate.rates_by_reason[reason]!)} dos envios</small>
                    )}
                  </dd>
                </div>
              ))}
            </dl>
          )}
          {gate && (
            <details>
              <summary>Recusas que talvez estivessem erradas</summary>
              <p className="muted">
                {gate.false_rejection_estimate === null
                  ? 'Ainda não há amostra para estimar.'
                  : `Estimativa: ${percent.format(gate.false_rejection_estimate)}.`}{' '}
                Conta quem teve uma foto recusada, reenviou em até 24 horas e teve o relato
                confirmado depois. Não prova que era a mesma foto, e muda conforme novas revisões.
              </p>
            </details>
          )}
        </>
      )}
    </section>
  );
}

/**
 * Estado de cada verificação em palavras. O que não foi medido (configuração, registro
 * sem teste) fica como "não verificado", nunca como fora do ar.
 */
type Health = { label: string; tone: 'ok' | 'degraded' | 'unavailable' | 'off' };
const HEALTH: Partial<Record<string, Health>> = {
  OK: { label: 'Funcionando', tone: 'ok' },
  OK_PROVIDER: { label: 'Funcionando', tone: 'ok' },
  OK_PUBLIC: { label: 'Funcionando', tone: 'ok' },
  AVAILABLE: { label: 'Funcionando', tone: 'ok' },
  DEGRADED: { label: 'Instável', tone: 'degraded' },
  UNAVAILABLE: { label: 'Fora do ar', tone: 'unavailable' },
};
const health = (status: string): Health =>
  HEALTH[status] ?? { label: 'Não verificado', tone: 'off' };

/** Nome de cada serviço externo e o que acontece com os relatos quando ele falha. */
const SERVICES: Record<string, { name: string; effect: string }> = {
  Supabase: {
    name: 'Login e armazenamento das fotos',
    effect: 'o envio e a consulta de relatos podem falhar',
  },
  Nominatim: {
    name: 'Busca de endereços',
    effect: 'os relatos continuam chegando, sem o endereço aproximado',
  },
  'Open-Meteo': {
    name: 'Dados de chuva',
    effect: 'os relatos continuam chegando, sem a chuva das últimas 24 horas',
  },
  Overpass: {
    name: 'Serviços próximos',
    effect: 'os relatos continuam chegando, sem as escolas, a saúde e as travessias por perto',
  },
  OpenFreeMap: { name: 'Mapa', effect: 'o mapa pode não aparecer; os relatos continuam chegando' },
  CARTO: { name: 'Mapa reserva', effect: 'o mapa reserva pode não aparecer' },
  Geofabrik: { name: 'Base de ruas', effect: 'a base de ruas não é atualizada' },
  'IBGE SIDRA': { name: 'Dados do IBGE', effect: 'os relatos continuam chegando' },
  'IBGE CNEFE': { name: 'Endereços do IBGE', effect: 'os relatos continuam chegando' },
  Webots: { name: 'Simulador do robô', effect: 'os relatos não são afetados' },
};
const serviceName = (name: string) => SERVICES[name]?.name ?? name;

/** Serviços externos: uma frase sobre o que está com problema; a lista inteira, recolhida. */
export function IntegrationHealth() {
  const [rows, setRows] = useState<Awaited<ReturnType<typeof api.integrationHealth>> | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    void api
      .integrationHealth(controller.signal)
      .then((data) => {
        if (!controller.signal.aborted) setRows(data);
      })
      .catch((failure: Error) => {
        if (!controller.signal.aborted) setError(failure.message);
      });
    return () => controller.abort();
  }, []);
  const problems = (rows ?? []).filter((row) =>
    ['degraded', 'unavailable'].includes(health(row.status).tone),
  );
  const latest = (rows ?? [])
    .filter((row) => health(row.status).tone !== 'off')
    .map((row) => row.checked_at)
    .sort()
    .at(-1);
  return (
    <section className="panel" aria-labelledby="health-title">
      <h2 id="health-title">Serviços externos</h2>
      {error && <p role="alert">Não foi possível consultar a verificação: {error}</p>}
      {!rows && !error && <p role="status">Consultando verificações…</p>}
      {rows?.length === 0 && <p className="muted">Nenhuma verificação registrada ainda.</p>}
      {rows && rows.length > 0 && (
        <>
          {problems.length === 0 ? (
            <p className="health-summary is-ok">
              <span className="service-dot" aria-hidden="true" />
              Nenhum problema na última verificação{latest ? `, em ${when(latest)}` : ''}.
            </p>
          ) : (
            <ul className="health-summary">
              {problems.map((row) => (
                <li key={row.name} className={`is-${health(row.status).tone}`}>
                  <span className="service-dot" aria-hidden="true" />
                  <span>
                    <strong>{serviceName(row.name)}</strong>{' '}
                    {health(row.status).tone === 'unavailable'
                      ? 'está fora do ar'
                      : 'está instável'}
                    : {SERVICES[row.name]?.effect ?? 'os relatos continuam chegando'}.
                  </span>
                </li>
              ))}
            </ul>
          )}
          <details>
            <summary>Todos os serviços</summary>
            <ul className="service-list">
              {rows.map((row) => {
                const state = health(row.status);
                return (
                  <li key={row.name} className={`is-${state.tone}`}>
                    <span className="service-dot" aria-hidden="true" />
                    <span className="service-name">
                      {serviceName(row.name)}
                      <small>
                        {state.tone === 'off'
                          ? 'Sem teste automático.'
                          : `Verificado em ${when(row.checked_at)}${
                              row.latency_ms != null
                                ? `, resposta em ${number.format(row.latency_ms)} ms`
                                : ''
                            }. Último sucesso: ${
                              row.last_success ? when(row.last_success) : 'nunca registrado'
                            }.`}
                      </small>
                    </span>
                    <span className="service-state">{state.label}</span>
                  </li>
                );
              })}
            </ul>
          </details>
        </>
      )}
    </section>
  );
}

/** Situação final de cada foto na fila de análise automática. */
const OUTCOMES: Record<string, string> = {
  not_processed: 'Não processadas',
  analysis_completed: 'Análise concluída',
  needs_review: 'Enviadas à revisão',
  model_not_available: 'Sem modelo disponível',
  no_supported_detection: 'Nada reconhecido',
  failed: 'Falharam',
};

/** Fila da análise automática: dados técnicos, recolhidos no fim do Painel. */
export function AutomaticQueue() {
  const { teamData } = useAppState();
  const { metrics, metricsError } = teamData;
  return (
    <section className="panel technical" aria-labelledby="queue-title">
      <details>
        <summary>Detalhes técnicos</summary>
        <h2 id="queue-title">Fila da análise automática</h2>
        <p className="muted">
          {automaticClassCodes().length
            ? 'Fotos à espera da análise automática e quanto ela demora.'
            : 'A análise automática está desligada: as fotos novas vão direto para a revisão. Os números abaixo são da fila anterior.'}
        </p>
        {metricsError && (
          <p className="error" role="alert">
            Não foi possível carregar os números: {metricsError}
          </p>
        )}
        {!metrics && !metricsError && (
          <p role="status" className="loading-line">
            Consultando a fila…
          </p>
        )}
        {metrics && (
          <>
            <dl className="key-values">
              <div>
                <dt>Pendentes</dt>
                <dd>{number.format(metrics.queue.pending)}</dd>
              </div>
              <div>
                <dt>Em processamento</dt>
                <dd>{number.format(metrics.queue.processing)}</dd>
              </div>
              <div>
                <dt>Concluídas</dt>
                <dd>{number.format(metrics.queue.archived)}</dd>
              </div>
              <div>
                <dt>Concluídas após nova tentativa</dt>
                <dd>{number.format(metrics.queue.retried)}</dd>
              </div>
              {/* Sem medição, uma linha só; nada de "não disponível" repetido. */}
              {metrics.latency_ms.samples > 0 && metrics.latency_ms.p50_ms != null ? (
                <>
                  <div>
                    <dt>Tempo típico de análise</dt>
                    <dd>{seconds(metrics.latency_ms.p50_ms)}</dd>
                  </div>
                  {metrics.latency_ms.p95_ms != null && (
                    <div>
                      <dt>Tempo em 95% dos casos</dt>
                      <dd>{seconds(metrics.latency_ms.p95_ms)}</dd>
                    </div>
                  )}
                  <div>
                    <dt>Análises medidas</dt>
                    <dd>{number.format(metrics.latency_ms.samples)}</dd>
                  </div>
                </>
              ) : (
                <div>
                  <dt>Tempo de análise</dt>
                  <dd>ainda sem medições</dd>
                </div>
              )}
            </dl>
            <h3>Resultado por foto</h3>
            {Object.keys(metrics.outcomes).length ? (
              <dl className="key-values">
                {Object.entries(metrics.outcomes).map(([status, total]) => (
                  <div key={status}>
                    <dt>{OUTCOMES[status] ?? readable(status)}</dt>
                    <dd>{number.format(total)}</dd>
                  </div>
                ))}
              </dl>
            ) : (
              <p className="muted">Nenhum resultado registrado.</p>
            )}
          </>
        )}
      </details>
    </section>
  );
}
