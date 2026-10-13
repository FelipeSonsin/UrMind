import { useEffect, useState, useSyncExternalStore } from 'react';
import { oldestPendingRead, subscribePendingReads } from '../../services/api';

/** A partir daqui a espera merece explicação: com a API acordada, a resposta leva menos de 1 s. */
const SLOW_AFTER_MS = 6000;

/**
 * Há uma leitura sem resposta há mais de alguns segundos? É o que acontece quando a API
 * volta de um tempo parada; a tela avisa uma vez, no topo, em vez de parecer quebrada.
 */
export function useSlowResponse() {
  const oldest = useSyncExternalStore(subscribePendingReads, oldestPendingRead);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (oldest === null) return;
    const timer = window.setTimeout(
      () => setNow(Date.now()),
      Math.max(0, oldest + SLOW_AFTER_MS - Date.now()),
    );
    return () => window.clearTimeout(timer);
  }, [oldest]);
  return oldest !== null && now - oldest >= SLOW_AFTER_MS;
}
