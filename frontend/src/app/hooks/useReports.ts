import { useEffect, useRef, useState } from 'react';
import type { Session } from '@supabase/supabase-js';
import type { CaptureMarker } from '../../domain/contracts';
import {
  filterMapRecords,
  publicSituation,
  publicSituationLabels,
  type MapFilters,
  type PublicSituation,
} from '../../domain/public';
import { api } from '../../services/api';
import type { Page, Route } from '../routes';

/** Filtro essencial de Meus relatos: a situação pública, sem o estado técnico. */
// Rótulos curtos de filtro (categorias): cabem lado a lado no celular.
const MINE_FILTERS: [PublicSituation | '', string][] = [
  ['', 'Todos'],
  ['analyzing', publicSituationLabels.analyzing],
  ['confirmed', 'Confirmados'],
  ['needs_location', 'Falta o local'],
];

/** Situações em que a equipe já decidiu; as outras esperam revisão. */
const DECIDED = new Set(['human_confirmed', 'published', 'rejected', 'duplicate']);

/** Filtro padrão da fila: todo relato ainda sem decisão da equipe (inclui o sem local). */
export const AWAITING = 'awaiting';

export function awaitingDecision(report: CaptureMarker): boolean {
  return !DECIDED.has(report.report_status);
}

/** Ordem da fila: maior prioridade primeiro; empate, o mais antigo. */
export function byUrgency(a: CaptureMarker, b: CaptureMarker): number {
  return (
    (b.priority_score ?? -1) - (a.priority_score ?? -1) ||
    (a.created_at ?? '').localeCompare(b.created_at ?? '')
  );
}

interface ReportsDeps {
  page: Page;
  route: Route;
  session: Session | null;
  canReview: boolean;
  revision: number;
  /** Muda quando o acompanhamento é consultado de novo: os relatos são relidos. */
  processingRetry: number;
  retryProcessing: () => void;
  refresh: () => void;
}

/**
 * Relatos (capturas) com localização: os da própria sessão no site do cidadão; os de
 * todos, só na área da equipe. Guarda também o relato selecionado no mapa, a foto
 * privada dele, o ponto marcado à mão e os filtros da fila de revisão.
 */
export function useReports(deps: ReportsDeps) {
  const { page, route, session, canReview, revision, processingRetry } = deps;
  const [reports, setReports] = useState<{
    owner: string;
    onlyMine: boolean;
    data: CaptureMarker[];
  } | null>(null);
  const [reportError, setReportError] = useState('');
  const [reportCursors, setReportCursors] = useState<Array<string | null>>([null]);
  const [selectedReport, setSelectedReport] = useState<string | null>(null);
  const [reportPhoto, setReportPhoto] = useState<{ owner: string; id: string; url: string } | null>(
    null,
  );
  const [manualPoint, setManualPoint] = useState<{ latitude: number; longitude: number } | null>(
    null,
  );
  const locationOperation = useRef<AbortController | null>(null);
  const [savingLocation, setSavingLocation] = useState(false);
  const [queueFilters, setQueueFilters] = useState<MapFilters>({
    status: AWAITING,
    family: '',
    issue: '',
    from: '',
    to: '',
  });
  const [queueConflict, setQueueConflict] = useState(false);
  const [queuePending, setQueuePending] = useState(false);
  /** Fila no padrão: todo relato ainda sem decisão, sem outro filtro. */
  function resetQueue() {
    setQueueFilters({ status: AWAITING, family: '', issue: '', from: '', to: '' });
    setQueueConflict(false);
    setQueuePending(false);
  }
  const [mineFilter, setMineFilter] = useState<PublicSituation | ''>('');

  // Páginas do cidadão mostram só os relatos da própria sessão (propriedade conferida
  // no servidor); a equipe vê os de todos apenas na área interna.
  const onlyMine = ['my-reports', 'map', 'overview', 'processing'].includes(page);
  const ownReports =
    session && reports?.owner === session.user.id && reports.onlyMine === onlyMine
      ? reports.data
      : [];
  const showReports =
    page === 'processing' ||
    page === 'private-map' ||
    page === 'map' ||
    page === 'overview' ||
    page === 'my-reports' ||
    (canReview && (page === 'review' || page === 'dashboard'));
  // Relatos ainda não chegaram desta sessão: mostra carregamento, não o estado vazio.
  const reportsLoading =
    Boolean(session) &&
    showReports &&
    !reportError &&
    !(reports?.owner === session?.user.id && reports?.onlyMine === onlyMine);
  const listedReports =
    page === 'review'
      ? filterMapRecords(
          ownReports,
          queueFilters.status === AWAITING ? { ...queueFilters, status: '' } : queueFilters,
        )
          .filter(
            (row) =>
              (queueFilters.status !== AWAITING || awaitingDecision(row)) &&
              (!queueConflict || row.location_conflict) &&
              (!queuePending || row.photo_gate?.status === 'NEEDS_REVIEW'),
          )
          .sort(byUrgency)
      : ownReports;
  const presentSituations = new Set(ownReports.map((row) => publicSituation(row.report_status)));
  const mineOptions = MINE_FILTERS.filter(
    ([value]) => value === '' || presentSituations.has(value),
  );
  const mineReports =
    page === 'my-reports' && mineFilter
      ? ownReports.filter((row) => publicSituation(row.report_status) === mineFilter)
      : ownReports;

  useEffect(() => {
    if (page === 'review' && canReview && route.captureId) setSelectedReport(route.captureId);
    // O relato que acabou de ser enviado (ou reaberto pelo link) fica em foco no mapa.
    if (page === 'processing' && route.captureId) setSelectedReport(route.captureId);
  }, [page, canReview, route.captureId]);
  useEffect(() => {
    locationOperation.current?.abort();
    setManualPoint(null);
    setSelectedReport(null);
    setReportPhoto(null);
    setReports(null);
    setReportCursors([null]);
  }, [session?.user.id]);
  useEffect(() => {
    const controller = new AbortController();
    if (!session || !showReports) return () => controller.abort();
    const owner = session.user.id;
    api
      .captureMarkers(controller.signal, onlyMine, reportCursors.at(-1) ?? null)
      .then((data) => {
        if (!controller.signal.aborted) {
          setReports({ owner, onlyMine, data });
          setReportError('');
        }
      })
      .catch(() => {
        if (!controller.signal.aborted) setReportError('Não foi possível consultar os relatos.');
      });
    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session?.user.id, showReports, onlyMine, page, revision, processingRetry, reportCursors]);
  useEffect(() => {
    const controller = new AbortController();
    setReportPhoto(null);
    if (!session || !selectedReport) return () => controller.abort();
    const owner = session.user.id;
    api
      .captureImage(selectedReport, controller.signal)
      .then(({ image_url }) => {
        if (!controller.signal.aborted)
          setReportPhoto({ owner, id: selectedReport, url: image_url });
      })
      .catch(() => undefined);
    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session?.user.id, selectedReport]);

  /** Relato sem localização: grava o ponto marcado no mapa e reconsulta o andamento. */
  async function confirmMissingLocation() {
    if (!manualPoint || !route.captureId || !session) return;
    const controller = new AbortController();
    locationOperation.current?.abort();
    locationOperation.current = controller;
    setSavingLocation(true);
    try {
      await api.captureLocation(
        route.captureId,
        manualPoint.latitude,
        manualPoint.longitude,
        controller.signal,
      );
      if (!controller.signal.aborted) {
        setManualPoint(null);
        deps.retryProcessing();
        deps.refresh();
      }
    } catch (error) {
      if (!controller.signal.aborted) setReportError((error as Error).message);
    } finally {
      if (!controller.signal.aborted) setSavingLocation(false);
    }
  }

  /** Foto privada do relato selecionado, só da sessão que a pediu. */
  function photoFor(id: string): string | null {
    return reportPhoto?.owner === session?.user.id && reportPhoto?.id === id
      ? reportPhoto.url
      : null;
  }

  return {
    own: ownReports,
    listed: listedReports,
    mine: mineReports,
    mineOptions,
    mineFilter,
    setMineFilter,
    showReports,
    loading: reportsLoading,
    error: reportError,
    cursors: reportCursors,
    setCursors: setReportCursors,
    selected: selectedReport,
    select: setSelectedReport,
    photoFor,
    manualPoint,
    setManualPoint,
    confirmMissingLocation,
    savingLocation,
    queueFilters,
    setQueueFilters,
    queueConflict,
    setQueueConflict,
    queuePending,
    setQueuePending,
    resetQueue,
  };
}
export type ReportsState = ReturnType<typeof useReports>;
