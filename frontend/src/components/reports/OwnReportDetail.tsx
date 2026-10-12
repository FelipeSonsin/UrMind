import type { ReactNode } from 'react';
import type { CaptureMarker } from '../../domain/contracts';
import { labelFor, severityOf } from '../../domain/public';
import { SituationTrack } from '../ui';
import { SeveritySign } from '../SeveritySign';
import { LOCATION_SOURCES, reportAddress } from './format';

/**
 * Relato do próprio autor no mapa: situação, tipo e gravidade (depois da revisão),
 * local, data, descrição e foto. O ponto informado e o trecho de via continuam separados.
 */
export function OwnReportDetail({
  report,
  photoUrl,
  team = false,
  children,
}: {
  report: CaptureMarker;
  photoUrl?: string | null;
  /** Na área da equipe o relato é de outra pessoa: título e avisos mudam. */
  team?: boolean;
  children?: ReactNode;
}) {
  const source = report.location_source ? LOCATION_SOURCES[report.location_source] : undefined;
  const severity = severityOf(report.severity);
  // De onde veio o ponto e com que precisão: "GPS do aparelho. Precisão aproximada: 12 m".
  const origin = [
    source,
    report.accuracy_m != null ? `Precisão aproximada: ${Math.round(report.accuracy_m)} m` : null,
  ]
    .filter(Boolean)
    .join('. ');
  return (
    <>
      <h2>
        {report.urmind_class
          ? labelFor(report.urmind_class)
          : team
            ? 'Relato recebido'
            : 'Seu relato'}
      </h2>
      <SituationTrack status={report.report_status} />
      <dl className="data-list report-facts">
        {report.urmind_class && (
          <div>
            <dt>Gravidade</dt>
            <dd>
              {severity.level === 'unknown' ? (
                severity.label
              ) : (
                <span className={`risk-tag risk-${severity.level}`}>
                  <SeveritySign level={severity.level} /> {severity.label}
                </span>
              )}
            </dd>
          </div>
        )}
        <div>
          <dt>Local</dt>
          <dd>
            {reportAddress(report) ?? 'Endereço ainda não identificado'}
            {origin && <small>{origin}</small>}
          </dd>
        </div>
        <div>
          <dt>Enviado em</dt>
          <dd>
            {report.created_at ? (
              <time dateTime={report.created_at}>
                {new Date(report.created_at).toLocaleString('pt-BR', {
                  dateStyle: 'short',
                  timeStyle: 'short',
                })}
              </time>
            ) : (
              'não registrado'
            )}
          </dd>
        </div>
        {report.snapped_latitude != null && report.snapped_longitude != null && (
          <div>
            <dt>Rua associada</dt>
            <dd>
              {report.road_name ?? 'rua sem nome no mapa'}
              {report.distance_to_road_m != null && (
                <small>a {Math.round(report.distance_to_road_m)} m do ponto informado</small>
              )}
            </dd>
          </div>
        )}
      </dl>
      {!report.urmind_class && !team && (
        <p className="muted">A equipe ainda vai identificar o tipo e a gravidade do problema.</p>
      )}
      {(report.reporters_count ?? 1) > 1 && (
        <p>{report.reporters_count} pessoas relataram este ponto</p>
      )}
      {report.photo_gate?.status === 'NEEDS_REVIEW' && (
        <p className="notice">
          A equipe ainda vai conferir a foto (cena e privacidade). Isso não indica que há um
          problema.
        </p>
      )}
      {report.user_description && <p className="report-note">{report.user_description}</p>}
      {photoUrl && (
        <>
          <img src={photoUrl} alt="Foto privada do relato selecionado" />
          <a href={photoUrl} target="_blank" rel="noopener noreferrer">
            Ver foto original
          </a>
        </>
      )}
      {children}
    </>
  );
}
