import { useAppState } from '../../app/AppState';
import { reportLabels, type CaptureMarker } from '../../domain/contracts';
import { publicSituation } from '../../domain/public';
import { SituationTrack } from '../ui';
import { awaitingDecision } from '../../app/hooks/useReports';
import { reportPlace, reportTitle } from './format';

/**
 * Relatos do cidadão: título, local e data, a trilha da situação e a próxima ação. O
 * título abre o relato no mapa da página; sem mapa ao lado (`linkTitles`), abre a
 * página do relato. `showFollow` some no acompanhamento, que já é a página do relato.
 */
export function CitizenReportList({
  reports,
  showFollow = true,
  linkTitles = false,
}: {
  reports: CaptureMarker[];
  showFollow?: boolean;
  linkTitles?: boolean;
}) {
  const { reports: state } = useAppState();
  return (
    <ul className="report-list" aria-label="Lista de relatos">
      {reports.map((report) => (
        <li key={report.id} className={state.selected === report.id ? 'is-selected' : undefined}>
          <div className="report-main">
            {linkTitles ? (
              <a className="report-title" href={`#/processando/${report.id}`}>
                {reportTitle(report)}
              </a>
            ) : (
              <button
                type="button"
                className="report-title"
                onClick={() => state.select(report.id)}
                aria-label={`Ver no mapa: ${reportTitle(report)}`}
              >
                {reportTitle(report)}
              </button>
            )}
            <ReportMeta report={report} />
          </div>
          <SituationTrack status={report.report_status} />
          {report.report_status === 'location_required' ? (
            <a className="report-action" href={`#/processando/${report.id}`}>
              Informar o local
            </a>
          ) : report.event_public_id && publicSituation(report.report_status) === 'confirmed' ? (
            <a className="report-action" href={`#/resultado/${report.event_public_id}`}>
              Ver ocorrência
            </a>
          ) : (
            showFollow && (
              <a className="report-action" href={`#/processando/${report.id}`}>
                Acompanhar
              </a>
            )
          )}
        </li>
      ))}
    </ul>
  );
}

/** Local (rua e bairro) e data do relato; sem endereço confirmado, só a data. */
function ReportMeta({ report }: { report: CaptureMarker }) {
  const place = reportPlace(report);
  if (!place && !report.created_at) return null;
  return (
    <span className="report-meta">
      {place && <span className="report-place">{place}</span>}
      {report.created_at && (
        <time dateTime={report.created_at}>
          {new Date(report.created_at).toLocaleDateString('pt-BR')}
        </time>
      )}
    </span>
  );
}

/**
 * Fila da equipe: o relato, a situação, o protocolo, a data e os alertas. Na fila e no
 * Painel o título abre a revisão; no Mapa interno (`onMap`), mostra o relato no mapa.
 * Os links ficam na área da equipe: endereços do site do cidadão abririam o Painel.
 */
export function TeamQueueList({
  reports,
  onMap = false,
}: {
  reports: CaptureMarker[];
  onMap?: boolean;
}) {
  const { reports: state } = useAppState();
  return (
    <ul className="queue-list" aria-label="Fila de relatos">
      {reports.map((report) => {
        const reviewHref = `#/app/relato/${report.id}`;
        const missingLocation = report.report_status === 'location_required';
        return (
          <li
            key={report.id}
            className={onMap && state.selected === report.id ? 'is-selected' : undefined}
          >
            <div className="queue-main">
              {onMap ? (
                <button
                  type="button"
                  className="report-title"
                  onClick={() => state.select(report.id)}
                  aria-label={`Ver no mapa: ${reportTitle(report)}`}
                >
                  {reportTitle(report)}
                </button>
              ) : (
                <a className="report-title" href={reviewHref}>
                  {reportTitle(report)}
                </a>
              )}
              <span className="report-meta">
                {report.protocol_code && (
                  <span className="record-id" translate="no">
                    {report.protocol_code}
                  </span>
                )}
                {report.created_at && (
                  <time dateTime={report.created_at}>
                    {new Date(report.created_at).toLocaleString('pt-BR', {
                      dateStyle: 'short',
                      timeStyle: 'short',
                    })}
                  </time>
                )}
              </span>
              {report.location_conflict && (
                <p className="queue-alert">
                  O GPS do aparelho e o local gravado na foto não coincidem. Confira o ponto.
                </p>
              )}
            </div>
            {/* Selo só no que já foi decidido: "Aguardando revisão" é o normal da fila e
                "Falta o local" já está na ação. A célula fica, para alinhar as colunas. */}
            <span className="badge-slot">
              {!awaitingDecision(report) && (
                <span className="badge">{reportLabels[report.report_status]}</span>
              )}
            </span>
            {report.event_id && !missingLocation ? (
              <a className="report-action" href={`#/app/eventos/${report.event_id}`}>
                Ver ocorrência
              </a>
            ) : (
              <a className="report-action" href={reviewHref}>
                {missingLocation
                  ? 'Informar o local'
                  : awaitingDecision(report)
                    ? 'Revisar'
                    : 'Ver relato'}
              </a>
            )}
          </li>
        );
      })}
    </ul>
  );
}

/** Erro ao consultar relatos, com nova tentativa. */
export function ReportsError() {
  const { reports, refresh } = useAppState();
  if (!reports.error) return null;
  return (
    <p className="error" role="alert">
      {reports.error}{' '}
      <button type="button" className="secondary compact" onClick={refresh}>
        Tentar de novo
      </button>
    </p>
  );
}

/** Páginas de 100 relatos (cursor por data e id); com uma página só, nada aparece. */
export function ReportsPager() {
  const { privatePage, reports } = useAppState();
  const { own, cursors, setCursors } = reports;
  if (cursors.length === 1 && own.length < 100) return null;
  return (
    <nav className="actions pager" aria-label="Páginas de relatos">
      <button
        className="secondary"
        disabled={cursors.length === 1}
        onClick={() => setCursors(cursors.slice(0, -1))}
      >
        Anterior
      </button>
      <button
        className="secondary"
        disabled={own.length < 100 || !own.at(-1)?.created_at}
        onClick={() => {
          const last = own.at(-1)!;
          setCursors([...cursors, `${last.created_at}|${last.id}`]);
        }}
      >
        Próxima
      </button>
      <span className="muted">
        Página {cursors.length}
        {privatePage && '. Os filtros valem para os relatos desta página.'}
      </span>
    </nav>
  );
}
