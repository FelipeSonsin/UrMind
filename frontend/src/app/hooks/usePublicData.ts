import { useEffect, useState } from 'react';
import type { PublicEvent, PublicEventDetail, PublicStatus } from '../../domain/public';
import { publicApi } from '../../services/publicApi';

/** Carrega um recurso público com cancelamento e estados de carga/erro reais. */
export function usePublicData<T>(
  loader: (signal: AbortSignal) => Promise<T>,
  deps: unknown[],
): { data: T | null; error: string; loading: boolean; notFound: boolean } {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState('');
  // Resposta 404: o recurso não existe (ou não está publicado), não é falha a repetir.
  const [notFound, setNotFound] = useState(false);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    loader(controller.signal)
      .then((value) => {
        if (controller.signal.aborted) return;
        setData(value);
        setError('');
        setNotFound(false);
      })
      .catch((reason: Error & { status?: number }) => {
        if (controller.signal.aborted) return;
        setError(reason.message);
        setNotFound(reason.status === 404);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return { data, error, loading, notFound };
}

/** Estado de cada componente do sistema (API, banco, detector). */
export function useSystemStatus(revision: number, enabled = true) {
  return usePublicData<PublicStatus | null>(
    (signal) => (enabled ? publicApi.status(signal) : Promise.resolve(null)),
    [revision, enabled],
  );
}

/** Ocorrências publicadas (até 50), com o filtro de tipo da página Ocorrências. */
export function usePublicEvents(
  revision: number,
  filters: { urmind_class?: string; status?: string },
) {
  return usePublicData<PublicEvent[]>(
    (signal) => publicApi.events({ limit: 50, ...filters }, signal),
    [revision, filters.urmind_class, filters.status],
  );
}

/** Detalhe público de uma ocorrência; `null` sem ocorrência escolhida. */
export function useEventDetail(id: string | null, revision: number) {
  return usePublicData<PublicEventDetail | null>(
    (signal) => (id ? publicApi.event(id, signal) : Promise.resolve(null)),
    [id, revision],
  );
}
