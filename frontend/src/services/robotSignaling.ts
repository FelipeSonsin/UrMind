import {
  channelName,
  readSignal,
  signalSchema,
  type SignalMessage,
  type SignalRole,
} from '../domain/robotCamera';
import { openBroadcastChannel, type BroadcastLink, type RealtimeStatus } from './auth';

/**
 * Sinalização da câmera do robô: canal `robot-camera:<sessão>` no Supabase Realtime.
 * Só passam prontidão, oferta/resposta SDP, candidatos ICE, desligamento e erro;
 * o vídeo vai direto entre os aparelhos pelo WebRTC.
 */
export interface SignalingLink {
  send(message: SignalMessage): Promise<boolean>;
  close(): Promise<void>;
}

type Transport = (
  name: string,
  onPayload: (payload: unknown) => void,
  onStatus: (status: RealtimeStatus) => void,
) => BroadcastLink;

/**
 * Os testes de navegador trocam o transporte por um canal local entre as abas
 * (sem internet); a aplicação usa sempre o Supabase Realtime.
 */
declare global {
  interface Window {
    __urmindRobotSignalingTransport?: Transport;
  }
}

const supabaseTransport: Transport = (name, onPayload, onStatus) =>
  openBroadcastChannel(name, 'signal', onPayload, onStatus);

export function openSignaling(
  sessionId: string,
  expect: { role: SignalRole; token: string },
  onMessage: (message: SignalMessage) => void,
  onStatus: (status: RealtimeStatus) => void,
  /** Celular que se apresentou com token errado: o notebook recusa sem revelar o certo. */
  onInvalidSender?: (peer: string) => void,
): SignalingLink {
  const transport = window.__urmindRobotSignalingTransport ?? supabaseTransport;
  const link = transport(
    channelName(sessionId),
    (payload) => {
      const message = readSignal(payload, expect);
      if (message) {
        onMessage(message);
        return;
      }
      const parsed = signalSchema.safeParse(payload);
      if (
        onInvalidSender &&
        parsed.success &&
        parsed.data.kind === 'sender-ready' &&
        expect.role === 'receiver'
      )
        onInvalidSender(parsed.data.peer);
    },
    onStatus,
  );
  let closed = false;
  return {
    send: (message) => (closed ? Promise.resolve(false) : link.send({ ...message })),
    close: async () => {
      if (closed) return;
      closed = true;
      await link.close();
    },
  };
}
