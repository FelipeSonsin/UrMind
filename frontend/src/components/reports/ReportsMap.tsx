import { lazy } from 'react';
import { useAppState } from '../../app/AppState';
import { OwnReportDetail } from './OwnReportDetail';

const UrbanMap = lazy(() => import('../UrbanMap'));
const CaptureReviewPanel = lazy(() =>
  import('../EventDetail').then((m) => ({ default: m.CaptureReviewPanel })),
);

/**
 * Mapa dos relatos carregados (os da sessão no site do cidadão; os de todos na área da
 * equipe), com o detalhe do relato selecionado. No acompanhamento de um relato sem
 * localização, um toque no mapa marca o ponto (o mapa abre na área atendida).
 */
export function ReportsMap({
  onlyId,
  showList = true,
  pick = false,
  pickedPoint = null,
}: {
  /** Só este relato (página "Seu relato"): o mapa não mostra os outros. */
  onlyId?: string;
  /** "Ver em lista": desligado onde a página já lista os relatos. */
  showList?: boolean;
  /** Marcar o local com um toque (relato sem local, na página dele). */
  pick?: boolean;
  /** Endereço escolhido na busca: o mapa leva o alvo até ele. */
  pickedPoint?: { latitude: number; longitude: number } | null;
} = {}) {
  const { page, privatePage, team, reports, refresh } = useAppState();
  const teamView = privatePage && team.canReview;
  const selected = reports.own.find((row) => row.id === reports.selected);
  const events = onlyId ? reports.mine.filter((row) => row.id === onlyId) : reports.mine;
  return (
    <UrbanMap
      events={events}
      showList={showList}
      showLegend={!onlyId}
      audience={teamView ? 'team' : 'public'}
      showFilters={teamView}
      emptyMessage={
        onlyId
          ? 'O local deste relato aparece aqui.'
          : reports.mineFilter
            ? 'Nenhum relato nesta situação.'
            : teamView
              ? 'Nenhum relato com localização ainda.'
              : 'Seus relatos com localização aparecem aqui.'
      }
      allowExport={team.canReview}
      selectedId={reports.selected}
      onSelect={reports.select}
      onCloseDetail={() => reports.select(null)}
      detail={
        selected ? (
          <OwnReportDetail
            report={selected}
            photoUrl={reports.photoFor(selected.id)}
            team={teamView}
          >
            {team.canReview && privatePage && (
              <CaptureReviewPanel key={selected.id} id={selected.id} onChanged={refresh} />
            )}
            {!team.canReview && page !== 'processing' && (
              <a href={`#/processando/${selected.id}`}>Acompanhar relato</a>
            )}
          </OwnReportDetail>
        ) : null
      }
      pickedPoint={pickedPoint}
      onPickLocation={
        pick ? (latitude, longitude) => reports.setManualPoint({ latitude, longitude }) : undefined
      }
    />
  );
}
