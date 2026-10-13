import { Camera, ClipboardList } from 'lucide-react';
import { useAppState } from '../../app/AppState';
import { ReportsMap } from '../../components/reports/ReportsMap';
import {
  CitizenReportList,
  ReportsError,
  ReportsPager,
} from '../../components/reports/ReportLists';
import { EmptyState, PageHeader, Skeleton } from '../../components/ui';

/**
 * Meus relatos: os relatos enviados por este aparelho, com filtro por situação, mapa
 * (só quando há relato) e o convite para registrar quando ainda não há nenhum.
 */
export function MyReportsPage() {
  const { session, sessionReady, reports } = useAppState();
  // Enquanto a sessão é lida, nada de "nenhum relato" piscando na tela.
  if (!sessionReady)
    return (
      <section className="my-reports">
        <PageHeader title="Meus relatos" />
        <Skeleton lines={3} />
      </section>
    );
  if (!session) return <MyReportsWithoutSession />;
  const { own, mine, mineOptions, mineFilter } = reports;
  return (
    <section className="my-reports">
      {/* Registrar fica no menu (e no convite abaixo, quando não há relato): um botão só. */}
      <PageHeader
        title="Meus relatos"
        description="Relatos enviados por este aparelho. Um relato vira ocorrência confirmada depois da revisão da equipe."
      />
      {mineOptions.length > 2 && (
        <div className="segmented" role="radiogroup" aria-label="Situação do relato">
          {mineOptions.map(([value, label]) => (
            <label key={value || 'all'} className={mineFilter === value ? 'active' : undefined}>
              <input
                type="radio"
                name="mine-filter"
                checked={mineFilter === value}
                onChange={() => {
                  reports.setMineFilter(value);
                  reports.select(null);
                }}
              />
              {label}
            </label>
          ))}
        </div>
      )}
      {/* Sem relato algum, a página mostra só o convite, sem mapa vazio. */}
      {own.length > 0 && <ReportsMap showList={false} />}
      <ReportsError />
      <CitizenReportList reports={mine} />
      {own.length > 0 && mine.length === 0 && (
        <p className="muted">Nenhum relato nesta situação.</p>
      )}
      {reports.loading && (
        <div className="skeleton" role="status" aria-label="Carregando relatos">
          <span />
          <span />
          <span />
        </div>
      )}
      {!reports.loading && !reports.error && own.length === 0 && <NoReportsYet />}
      {(reports.cursors.length > 1 || own.length >= 100) && <ReportsPager />}
    </section>
  );
}

/**
 * Nenhum relato ainda, com ou sem sessão: o mesmo cartão, o mesmo título e um convite. A
 * página já diz o que aparece aqui; o aviso só explica onde ficam os relatos de outro lugar.
 */
function NoReportsYet() {
  return (
    <div className="panel">
      <EmptyState
        icon={<ClipboardList size={22} strokeWidth={1.8} />}
        title="Nenhum relato neste aparelho ainda"
        action={
          <a className="button" href="#/registrar">
            <Camera size={17} aria-hidden="true" /> Registrar relato
          </a>
        }
      >
        Relatos enviados de outro navegador ou aparelho aparecem só onde foram enviados.
      </EmptyState>
    </div>
  );
}

/** Sem sessão neste navegador, ainda não há relatos para mostrar. */
function MyReportsWithoutSession() {
  return (
    <section className="my-reports">
      <PageHeader
        title="Meus relatos"
        description="Os relatos enviados por este aparelho aparecem aqui, com a situação de cada um."
      />
      <NoReportsYet />
    </section>
  );
}
