import { useEffect, useState, type Dispatch, type SetStateAction } from 'react';
import type { Session } from '@supabase/supabase-js';
import type { UrbanEvent } from '../../domain/contracts';
import { api } from '../../services/api';
import { subscribeToEventChanges, type RealtimeStatus } from '../../services/auth';
import type { Page } from '../routes';

interface TeamDeps {
  session: Session | null;
  page: Page;
  privatePage: boolean;
  canReview: boolean;
  showReports: boolean;
  revision: number;
  setRevision: Dispatch<SetStateAction<number>>;
}

/**
 * Dados da área da equipe: ocorrências (páginas de 100), métricas da fila e o aviso
 * de mudança em tempo real (Supabase Realtime), que pede a releitura de tudo.
 */
export function useTeamData(deps: TeamDeps) {
  const { session, page, privatePage, canReview, showReports, revision, setRevision } = deps;
  const [events, setEvents] = useState<UrbanEvent[] | null>(null);
  const [eventCursors, setEventCursors] = useState<Array<string | null>>([null]);
  const [loading, setLoading] = useState(false);
  const [apiError, setApiError] = useState('');
  const [metrics, setMetrics] = useState<Awaited<ReturnType<typeof api.opsMetrics>> | null>(null);
  const [metricsError, setMetricsError] = useState('');
  const [realtime, setRealtime] = useState<RealtimeStatus>('unavailable');
  const [changed, setChanged] = useState<{ eventId: string | null; at: number }>({
    eventId: null,
    at: 0,
  });

  useEffect(() => {
    setEventCursors([null]);
  }, [session?.user.id]);
  useEffect(() => {
    if (!session || (!privatePage && !showReports)) {
      setRealtime('unavailable');
      return;
    }
    let timer: number | undefined;
    const unsubscribe = subscribeToEventChanges(({ eventId }) => {
      // Rajadas (evento + risco + contexto) viram uma única recarga HTTP.
      window.clearTimeout(timer);
      timer = window.setTimeout(() => {
        setRevision((value) => value + 1);
        setChanged({ eventId, at: Date.now() });
      }, 600);
    }, setRealtime);
    return () => {
      window.clearTimeout(timer);
      unsubscribe();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session?.user.id, canReview, privatePage, showReports]);
  useEffect(() => {
    const controller = new AbortController();
    setEvents(null);
    if (!canReview || !privatePage) return () => controller.abort();
    async function load() {
      setLoading(true);
      setApiError('');
      setEvents(null);
      try {
        const result = await api.health(controller.signal);
        if (controller.signal.aborted) return;
        if (result.database === 'not_configured') {
          setApiError('O banco de dados ainda não está configurado no servidor.');
          return;
        }
        const data = await api.events(controller.signal, eventCursors.at(-1) ?? null);
        if (!controller.signal.aborted) setEvents(data);
      } catch (error) {
        if (!controller.signal.aborted) {
          setApiError(error instanceof Error ? error.message : 'Falha ao consultar API.');
        }
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }
    void load();
    return () => controller.abort();
  }, [revision, canReview, privatePage, eventCursors]);
  useEffect(() => {
    const controller = new AbortController();
    setMetrics(null);
    setMetricsError('');
    if (canReview && (page === 'dashboard' || page === 'login')) {
      api
        .opsMetrics(controller.signal)
        .then((data) => {
          if (!controller.signal.aborted) setMetrics(data);
        })
        .catch((error: Error) => {
          if (!controller.signal.aborted) setMetricsError(error.message);
        });
    }
    return () => controller.abort();
  }, [canReview, page, revision]);

  return {
    events,
    loading,
    apiError,
    cursors: eventCursors,
    setCursors: setEventCursors,
    metrics,
    metricsError,
    realtime,
    changed,
  };
}
export type TeamDataState = ReturnType<typeof useTeamData>;
