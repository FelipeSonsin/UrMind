import { useSyncExternalStore } from 'react';
import { isSlowResponse, subscribeSlowResponse } from '../../services/network';

/**
 * Há uma leitura sem resposta há mais de alguns segundos? É o que acontece quando a API
 * volta de um tempo parada; a tela avisa uma vez, em vez de parecer quebrada. O cálculo
 * (com o relógio) fica em `services/network.ts`.
 */
export function useSlowResponse() {
  return useSyncExternalStore(subscribeSlowResponse, isSlowResponse, () => false);
}
