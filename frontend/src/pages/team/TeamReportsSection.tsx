import { lazy, useState, type ReactNode } from 'react';
import { ChevronRight, ListChecks } from 'lucide-react';
import { useAppState } from '../../app/AppState';
import { AWAITING } from '../../app/hooks/useReports';
import { useWideScreen } from '../../app/hooks/useWideScreen';
import { DateField } from '../../components/DateField';
import { ReportsMap } from '../../components/reports/ReportsMap';
import { ReportsError, ReportsPager, TeamQueueList } from '../../components/reports/ReportLists';
import { reportTitle } from '../../components/reports/format';
import { EmptyState, PageHeader, Skeleton } from '../../components/ui';
import { reportLabels } from '../../domain/contracts';
import { familyFor, familyLabel, labelFor } from '../../domain/public';

const CaptureReviewPanel = lazy(() =>
  import('../../components/EventDetail').then((m) => ({ default: m.CaptureReviewPanel })),
);
const TaxonomyPanel = lazy(() =>
  import('../../components/TaxonomyPanel').then((m) => ({ default: m.TaxonomyPanel })),
);

/** Situações que a fila deixa escolher uma a uma; o resto entra em "Aguardando revisão". */
const QUEUE_STATUSES = [
  'location_required',
  'human_confirmed',
  'published',
  'rejected',
  'duplicate',
];

/**
 * Relatos na área da equipe: a Fila de revisão, a revisão de um relato (em tela
 * própria, com o próximo da fila) e o Mapa interno.
 */
export function TeamReportsSection() {
  const { page, route } = useAppState();
  if (page === 'review' && route.captureId)
    return <ReviewReport key={route.captureId} id={route.captureId} />;
  if (page === 'review') return <ReviewQueue />;
  return <PrivateMap />;
}

/** Fila de revisão: os relatos sem decisão, dos mais urgentes aos mais antigos. */
function ReviewQueue() {
  const { reports, revision } = useAppState();
  const awaiting = reports.queueFilters.status === AWAITING;
  const status = reports.queueFilters.status;
  return (
    <>
      <PageHeader
        title="Fila de revisão"
        description={
          awaiting
            ? `Relatos esperando decisão, ${
                reports.listed.some((row) => row.priority_score != null)
                  ? 'os mais urgentes primeiro'
                  : 'os mais antigos primeiro'
              }. Abra um para conferir a foto, o tipo de problema, o local e a privacidade.`
            : status
              ? `Relatos na situação “${reportLabels[status as keyof typeof reportLabels]}”. Abra um para ver os detalhes.`
              : 'Relatos em qualquer situação. Abra um para ver os detalhes.'
        }
      />
      <section className="panel reports-panel" aria-label="Relatos da fila">
        <QueueFilters />
        <ReportsError />
        {reports.loading && <Skeleton lines={3} />}
        {!reports.loading && !reports.error && reports.listed.length === 0 && (
          <EmptyState
            compact
            icon={<ListChecks size={22} strokeWidth={1.8} />}
            title={awaiting ? 'Nenhum relato esperando revisão' : 'Nenhum relato neste filtro'}
          >
            {awaiting
              ? 'Os relatos novos aparecem aqui assim que chegam.'
              : 'Mude os filtros para ver outros relatos.'}
          </EmptyState>
        )}
        <TeamQueueList reports={reports.listed} />
        <ReportsPager />
      </section>
      {/* Consulta para quem decide o tipo: uma linha recolhida no fim da fila. */}
      <TaxonomyPanel revision={revision} includeDevelopment />
    </>
  );
}

/** Revisão de um relato: voltar à fila e seguir para o próximo, sem filtros por perto. */
function ReviewReport({ id }: { id: string }) {
  const { reports, refresh } = useAppState();
  // Antes de decidir, seguir adiante é pular este relato (link discreto); depois, é o passo
  // seguinte (botão).
  const [decided, setDecided] = useState(false);
  const report = reports.own.find((row) => row.id === id);
  const queue = reports.listed;
  const index = queue.findIndex((row) => row.id === id);
  // Depois da decisão o relato sai da fila: o próximo passa a ser o primeiro dela.
  const next = (index >= 0 ? queue.slice(index + 1) : queue).find((row) => row.id !== id);
  const received = report?.created_at
    ? new Date(report.created_at).toLocaleString('pt-BR', {
        dateStyle: 'short',
        timeStyle: 'short',
      })
    : null;
  return (
    <>
      <PageHeader
        title="Revisar relato"
        description={
          report
            ? [
                report.urmind_class ? reportTitle(report) : null,
                report.protocol_code ? `protocolo ${report.protocol_code}` : null,
                received ? `recebido em ${received}` : null,
              ]
                .filter(Boolean)
                .join(', ')
                .replace(/^./, (first) => first.toUpperCase())
            : undefined
        }
        back={{ href: '#/app/reviews', label: 'Fila de revisão' }}
        actions={
          next &&
          (decided ? (
            <a className="button" href={`#/app/relato/${next.id}`}>
              Próximo relato <ChevronRight size={16} aria-hidden="true" />
            </a>
          ) : (
            <a className="text-button" href={`#/app/relato/${next.id}`}>
              Pular este relato <ChevronRight size={16} aria-hidden="true" />
            </a>
          ))
        }
      />
      <section className="panel">
        <CaptureReviewPanel
          id={id}
          onChanged={() => {
            setDecided(true);
            refresh();
          }}
          level={1}
        />
      </section>
    </>
  );
}

/** Mapa interno: todos os relatos no mapa; a lista do próprio mapa segue os filtros dele. */
function PrivateMap() {
  return (
    <>
      <PageHeader
        title="Mapa interno"
        description="Todos os relatos recebidos, no ponto informado por quem enviou. Relato recebido ainda não é problema confirmado."
      />
      <section className="panel reports-panel" aria-label="Relatos no mapa">
        <ReportsMap />
        <ReportsError />
        <ReportsPager />
      </section>
    </>
  );
}

/** Filtros da fila: situação, grupo, tipo, período e alertas. */
function QueueFilters() {
  const { reports } = useAppState();
  const { own, listed, queueFilters, setQueueFilters } = reports;
  const wide = useWideScreen();
  const families = [...new Set(own.map((r) => familyFor(r.urmind_class)).filter(Boolean))];
  const issues = [
    ...new Set(own.map((r) => r.urmind_class).filter((v): v is string => Boolean(v))),
  ];
  const count = listed.length === 1 ? '1 relato' : `${listed.length} relatos`;
  // Filtro diferente do padrão (vindo do Painel, por exemplo): dito por extenso, com "Limpar".
  const active = [
    queueFilters.status !== AWAITING &&
      (queueFilters.status
        ? reportLabels[queueFilters.status as keyof typeof reportLabels]
        : 'todas as situações'),
    queueFilters.family && 'grupo de problema',
    queueFilters.issue && 'tipo de problema',
    (queueFilters.from || queueFilters.to) && 'período',
    reports.queueConflict && 'conflito de local',
    reports.queuePending && 'foto a conferir',
  ].filter((item): item is string => Boolean(item));
  const fields: ReactNode = (
    <div className="filters" role="group" aria-label="Filtros da fila de relatos">
      <div>
        <label htmlFor="queue-status">Situação</label>
        <select
          id="queue-status"
          value={queueFilters.status}
          onChange={(e) => setQueueFilters({ ...queueFilters, status: e.target.value })}
        >
          <option value={AWAITING}>Aguardando revisão</option>
          {QUEUE_STATUSES.map((code) => (
            <option key={code} value={code}>
              {reportLabels[code as keyof typeof reportLabels]}
            </option>
          ))}
          <option value="">Todas</option>
        </select>
      </div>
      {families.length > 1 && (
        <div>
          <label htmlFor="queue-family">Grupo de problema</label>
          <select
            id="queue-family"
            value={queueFilters.family}
            onChange={(e) => setQueueFilters({ ...queueFilters, family: e.target.value })}
          >
            <option value="">Todos</option>
            {families.map((family) => (
              <option key={family} value={family}>
                {familyLabel(family)}
              </option>
            ))}
          </select>
        </div>
      )}
      {issues.length > 1 && (
        <div>
          <label htmlFor="queue-issue">Tipo de problema</label>
          <select
            id="queue-issue"
            value={queueFilters.issue}
            onChange={(e) => setQueueFilters({ ...queueFilters, issue: e.target.value })}
          >
            <option value="">Todos</option>
            {issues.map((code) => (
              <option key={code} value={code}>
                {labelFor(code)}
              </option>
            ))}
          </select>
        </div>
      )}
      <DateField
        label="Recebido a partir de"
        value={queueFilters.from}
        onChange={(from) => setQueueFilters({ ...queueFilters, from })}
      />
      <DateField
        label="Recebido até"
        value={queueFilters.to}
        onChange={(to) => setQueueFilters({ ...queueFilters, to })}
      />
      {/* Os dois alertas juntos, numa linha própria: nenhum fica sozinho embaixo. */}
      <div className="filters-checks">
        <label>
          <input
            type="checkbox"
            checked={reports.queueConflict}
            onChange={(e) => reports.setQueueConflict(e.target.checked)}
          />
          Só com conflito de local
        </label>
        <label>
          <input
            type="checkbox"
            checked={reports.queuePending}
            onChange={(e) => reports.setQueuePending(e.target.checked)}
          />
          Só com foto a conferir
        </label>
      </div>
    </div>
  );
  return (
    <>
      {wide ? (
        fields
      ) : (
        <details className="filters-disclosure">
          <summary>{active.length ? `Filtros: ${active.join(', ')}` : 'Filtros'}</summary>
          {fields}
        </details>
      )}
      <div className="queue-count-row">
        <p className="queue-count" role="status">
          {count}
        </p>
        {active.length > 0 && (
          <button type="button" className="text-button" onClick={reports.resetQueue}>
            Limpar filtros
          </button>
        )}
      </div>
    </>
  );
}
