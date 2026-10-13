/**
 * A API dorme depois de um tempo parada (plano gratuito do Render) e leva perto de um minuto
 * para voltar: o tempo limite padrão cobre esse despertar, para a primeira visita (ou o
 * primeiro envio) não virar erro. Durante a espera, a tela avisa que ela é normal.
 */
export const API_TIMEOUT_MS = 70_000;

/** A partir daqui a espera merece explicação: com a API acordada, a resposta leva menos de 1 s. */
export const SLOW_AFTER_MS = 6000;

// Leituras em andamento, só com o horário de início. Envios não entram: o envio da foto tem
// o próprio progresso.
const pendingReads = new Map<number, number>();
let lastReadId = 0;

/** Registra uma leitura que começa agora; a função devolvida marca o fim (chamar sempre). */
export function trackRead(): () => void {
  const id = ++lastReadId;
  pendingReads.set(id, Date.now());
  recheckSlow();
  return () => {
    if (pendingReads.delete(id)) recheckSlow();
  };
}

/** Início (ms) da leitura mais antiga ainda sem resposta; `null` sem leitura pendente. */
export function oldestPendingRead(): number | null {
  let oldest: number | null = null;
  for (const startedAt of pendingReads.values())
    if (oldest === null || startedAt < oldest) oldest = startedAt;
  return oldest;
}

// Espera longa: verdade quando a leitura pendente mais antiga passou de SLOW_AFTER_MS. O
// relógio fica aqui, fora do React, e a tela só lê o resultado (sem piscar entre leituras).
let slow = false;
let slowTimer: ReturnType<typeof setTimeout> | undefined;
const slowListeners = new Set<() => void>();

function setSlow(value: boolean) {
  if (slow === value) return;
  slow = value;
  slowListeners.forEach((listener) => listener());
}

function recheckSlow() {
  clearTimeout(slowTimer);
  const oldest = oldestPendingRead();
  if (oldest === null) return setSlow(false);
  const wait = oldest + SLOW_AFTER_MS - Date.now();
  if (wait <= 0) return setSlow(true);
  setSlow(false);
  slowTimer = setTimeout(recheckSlow, wait);
}

/** Alguma leitura está sem resposta há mais de SLOW_AFTER_MS? */
export function isSlowResponse() {
  return slow;
}

export function subscribeSlowResponse(listener: () => void) {
  slowListeners.add(listener);
  return () => {
    slowListeners.delete(listener);
  };
}
