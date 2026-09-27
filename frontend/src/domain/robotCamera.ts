import { z } from 'zod';

/**
 * Câmera do robô: um celular transmite vídeo ao notebook por WebRTC. O Supabase
 * Realtime só troca a sinalização (prontidão, oferta, resposta, ICE, desligamento);
 * nenhum quadro passa por ele. Tudo aqui é lógica pura, testável sem navegador.
 */

/** Quanto tempo o QR aceita um celular novo. Depois de conectado, não expira. */
export const PAIRING_TTL_MS = 5 * 60_000;
/** Queda de rede: tempo para a conexão voltar antes de declarar a câmera desconectada. */
export const RECONNECT_GRACE_MS = 10_000;
/** O celular reenvia "pronto" até o notebook responder, por no máximo este tempo. */
export const SENDER_READY_TIMEOUT_MS = 20_000;
export const SENDER_READY_INTERVAL_MS = 1_500;
/** Recuperações automáticas por reinício de ICE que o celular tenta antes de desistir. */
export const MAX_ICE_RESTARTS = 2;

/**
 * Câmera traseira em 720p; microfone sempre desligado. Os quadros por segundo seguem a
 * regra de todas as câmeras do UrMind (pelo menos 60 quando o aparelho tem o modo),
 * aplicada por `openCamera`. A transmissão envia até esse mesmo ritmo.
 */
export const ROBOT_PHONE_CONSTRAINTS = {
  video: {
    facingMode: 'environment',
    width: { ideal: 1280 },
    height: { ideal: 720 },
  },
  audio: false,
} as const satisfies MediaStreamConstraints;
/**
 * Teto de envio do vídeo: até 60 quadros por segundo (vídeo fluido, sem travar) e banda
 * para cada quadro chegar com detalhe ao modelo: ≈167 kbit por quadro a 60 quadros/s,
 * mais que o dobro do teto anterior de 4,5 Mbps, preservando trincas e textura do asfalto.
 */
export const ROBOT_SEND_MAX_FPS = 60;
export const ROBOT_SEND_MAX_BITRATE = 10_000_000;

export interface Pairing {
  sessionId: string;
  token: string;
  createdAt: number;
  expiresAt: number;
}

const TOKEN_BYTES = 24;
const TOKEN_PATTERN = /^[A-Za-z0-9_-]{32}$/;
const SESSION_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

function base64url(bytes: Uint8Array): string {
  let binary = '';
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

/** Sessão e token aleatórios (Web Crypto), válidos por cinco minutos. */
export function createPairing(
  now = Date.now(),
  random: Pick<Crypto, 'randomUUID' | 'getRandomValues'> = crypto,
): Pairing {
  return {
    sessionId: random.randomUUID(),
    token: base64url(random.getRandomValues(new Uint8Array(TOKEN_BYTES))),
    createdAt: now,
    expiresAt: now + PAIRING_TTL_MS,
  };
}

export function pairingExpired(pairing: Pick<Pairing, 'expiresAt'>, now = Date.now()): boolean {
  return now >= pairing.expiresAt;
}

/** Nome do canal temporário da sessão no Realtime. */
export function channelName(sessionId: string): string {
  return `robot-camera:${sessionId}`;
}

/** Endereço aberto pelo QR: a origem vem do próprio navegador, nunca de um domínio fixo. */
export function pairingLink(origin: string, pairing: Pick<Pairing, 'sessionId' | 'token'>): string {
  const query = new URLSearchParams({ session: pairing.sessionId, token: pairing.token });
  return `${origin.replace(/\/$/, '')}/#/camera-robo/celular?${query}`;
}

/** Lê sessão e token do endereço do celular; qualquer coisa fora do formato é recusada. */
export function parsePairingHash(hash: string): { sessionId: string; token: string } | null {
  const [route, search = ''] = hash.replace(/^#/, '').split('?');
  if (route.replace(/\/$/, '') !== '/camera-robo/celular') return null;
  const query = new URLSearchParams(search);
  const sessionId = query.get('session') ?? '';
  const token = query.get('token') ?? '';
  return SESSION_PATTERN.test(sessionId) && TOKEN_PATTERN.test(token) ? { sessionId, token } : null;
}

/**
 * Código curto mostrado nos dois aparelhos para a pessoa conferir que pareou o
 * celular certo. Derivado do token; não é segredo e não substitui o token.
 */
export function pairingCode(token: string): string {
  const alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';
  let code = '';
  for (let index = 0; index < 4; index += 1)
    code += alphabet[token.charCodeAt(index * 7) % alphabet.length];
  return code;
}

/** "4:05" para o tempo que falta até o QR expirar. */
export function formatRemaining(ms: number): string {
  const total = Math.max(0, Math.ceil(ms / 1000));
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`;
}

export class RobotCameraConfigError extends Error {}

/**
 * STUN vindo só de `VITE_ROBOT_CAMERA_STUN_URL`. Sem a variável (ou com credencial,
 * TURN ou outro esquema), a câmera do robô não inicia e diz o que falta.
 */
export function robotIceServers(stunUrl: string | undefined): RTCIceServer[] {
  const value = stunUrl?.trim();
  if (!value)
    throw new RobotCameraConfigError(
      'Câmera do robô não configurada: defina VITE_ROBOT_CAMERA_STUN_URL no ambiente do frontend.',
    );
  if (!/^stuns?:[A-Za-z0-9.-]+(:\d{1,5})?$/.test(value))
    throw new RobotCameraConfigError(
      'VITE_ROBOT_CAMERA_STUN_URL inválida: use um endereço stun: sem usuário nem senha.',
    );
  return [{ urls: value }];
}

// ---------------------------------------------------------------------------- sinalização

export type SignalRole = 'sender' | 'receiver';

const base = { token: z.string(), peer: z.string().min(8).max(64) };
export const signalSchema = z.discriminatedUnion('kind', [
  z.object({ kind: z.literal('sender-ready'), from: z.literal('sender'), ...base }),
  z.object({ kind: z.literal('receiver-ready'), from: z.literal('receiver'), ...base }),
  z.object({
    kind: z.literal('offer'),
    from: z.literal('sender'),
    ...base,
    sdp: z.string().min(1).max(100_000),
  }),
  z.object({
    kind: z.literal('answer'),
    from: z.literal('receiver'),
    ...base,
    sdp: z.string().min(1).max(100_000),
  }),
  z.object({
    kind: z.literal('ice-candidate'),
    from: z.enum(['sender', 'receiver']),
    ...base,
    candidate: z.object({
      candidate: z.string().max(2_000),
      sdpMid: z.string().nullable().optional(),
      sdpMLineIndex: z.number().int().nullable().optional(),
      usernameFragment: z.string().nullable().optional(),
    }),
  }),
  z.object({
    kind: z.literal('disconnect'),
    from: z.enum(['sender', 'receiver']),
    ...base,
    reason: z.string().max(200).optional(),
  }),
  z.object({
    kind: z.literal('error'),
    from: z.enum(['sender', 'receiver']),
    token: z.string(),
    peer: z.string().max(64),
    reason: z.enum(['invalid-token', 'expired', 'busy', 'camera']),
  }),
]);
export type SignalMessage = z.infer<typeof signalSchema>;

/**
 * Aceita só mensagens do outro lado, com o token do pareamento. `error` é a única
 * exceção: o notebook não devolve o token a quem apresentou um token errado.
 */
export function readSignal(
  payload: unknown,
  expect: { role: SignalRole; token: string },
): SignalMessage | null {
  const parsed = signalSchema.safeParse(payload);
  if (!parsed.success) return null;
  const message = parsed.data;
  const other: SignalRole = expect.role === 'sender' ? 'receiver' : 'sender';
  if (message.from !== other) return null;
  if (message.kind === 'error') return message;
  return message.token === expect.token ? message : null;
}

/** Identificador aleatório de cada tentativa de conexão (um celular por vez). */
export function newPeerId(random: { randomUUID(): string } = crypto): string {
  return random.randomUUID();
}

// ---------------------------------------------------------------------------- conexão

export type LinkVerdict = 'connected' | 'connecting' | 'reconnecting' | 'lost';

/**
 * O que a tela deve mostrar para um estado do RTCPeerConnection. Queda temporária
 * vira "reconectando" por RECONNECT_GRACE_MS; depois disso (ou fechada) é perda.
 */
export function linkVerdict(
  state: RTCPeerConnectionState,
  disconnectedForMs: number | null,
): LinkVerdict {
  if (state === 'connected') return 'connected';
  if (state === 'new' || state === 'connecting') return 'connecting';
  if (state === 'closed') return 'lost';
  return disconnectedForMs != null && disconnectedForMs >= RECONNECT_GRACE_MS
    ? 'lost'
    : 'reconnecting';
}
