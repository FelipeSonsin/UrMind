import { useEffect, useMemo, useRef, useState } from 'react';
import {
  Camera as CameraIcon,
  CircleStop,
  Copy,
  Flashlight,
  Send,
  Smartphone,
  Unplug,
} from 'lucide-react';
import { encode } from 'uqr';
import {
  MAX_ICE_RESTARTS,
  RECONNECT_GRACE_MS,
  ROBOT_PHONE_CONSTRAINTS,
  ROBOT_SEND_MAX_BITRATE,
  ROBOT_SEND_MAX_FPS,
  RobotCameraConfigError,
  SENDER_READY_INTERVAL_MS,
  SENDER_READY_TIMEOUT_MS,
  createPairing,
  formatRemaining,
  newPeerId,
  pairingCode,
  pairingExpired,
  pairingLink,
  parsePairingHash,
  robotIceServers,
  type Pairing,
  type SignalMessage,
} from '../domain/robotCamera';
import { CameraError, frameToJpeg, openCamera, stopStream } from '../services/cameraStream';
import { drafts, validatePhoto, type CaptureDraft } from '../services/drafts';
import { openSignaling, type SignalingLink } from '../services/robotSignaling';

/** STUN só do ambiente; sem ele a câmera do robô não inicia (ver robotIceServers). */
function iceServers(): RTCIceServer[] {
  return robotIceServers(import.meta.env.VITE_ROBOT_CAMERA_STUN_URL as string | undefined);
}

function configMessage(reason: unknown): string {
  return reason instanceof RobotCameraConfigError
    ? reason.message
    : 'Não foi possível preparar a conexão da câmera do robô.';
}

/**
 * Ajuste do envio, quando o navegador aceita: até 60 quadros/s com banda para detalhe.
 * Em rede fraca, `balanced` reduz um pouco a resolução e um pouco o ritmo juntos, em vez
 * de derrubar só os quadros (vídeo travando) para manter a resolução.
 */
async function tuneSender(sender: RTCRtpSender) {
  try {
    const parameters = sender.getParameters() as RTCRtpSendParameters & {
      degradationPreference?: 'maintain-resolution' | 'maintain-framerate' | 'balanced';
    };
    parameters.degradationPreference = 'balanced';
    for (const encoding of parameters.encodings ?? []) {
      encoding.scaleResolutionDownBy = 1;
      encoding.maxFramerate = ROBOT_SEND_MAX_FPS;
      encoding.maxBitrate = ROBOT_SEND_MAX_BITRATE;
    }
    await sender.setParameters(parameters);
  } catch {
    // Sem suporte: o navegador decide; a captura avisa se a imagem ainda estiver pequena.
  }
}

/** Só os campos do candidato ICE que a sinalização carrega. */
function signalCandidate(candidate: RTCIceCandidate) {
  return {
    candidate: candidate.candidate,
    sdpMid: candidate.sdpMid,
    sdpMLineIndex: candidate.sdpMLineIndex,
    usernameFragment: candidate.usernameFragment,
  };
}

/** Candidatos ICE podem chegar antes da descrição remota: guardados até ela existir. */
async function addCandidate(
  pc: RTCPeerConnection,
  pending: RTCIceCandidateInit[],
  candidate: RTCIceCandidateInit,
) {
  if (!pc.remoteDescription) {
    pending.push(candidate);
    return;
  }
  await pc.addIceCandidate(candidate).catch(() => undefined);
}

async function flushCandidates(pc: RTCPeerConnection, pending: RTCIceCandidateInit[]) {
  for (const candidate of pending.splice(0))
    await pc.addIceCandidate(candidate).catch(() => undefined);
}

function QrCode({ text }: { text: string }) {
  const qr = useMemo(() => encode(text, { ecc: 'M', border: 2 }), [text]);
  const path = useMemo(() => {
    let d = '';
    qr.data.forEach((row, y) =>
      row.forEach((on, x) => {
        if (on) d += `M${x} ${y}h1v1h-1z`;
      }),
    );
    return d;
  }, [qr]);
  // Módulos escuros sobre branco: é o contraste que os leitores de QR esperam.
  return (
    <svg
      className="robot-qr"
      viewBox={`0 0 ${qr.size} ${qr.size}`}
      role="img"
      aria-label="QR Code para conectar o celular"
      shapeRendering="crispEdges"
    >
      <rect width={qr.size} height={qr.size} fill="#ffffff" />
      <path d={path} fill="#0c1411" />
    </svg>
  );
}

// ================================================================== notebook (recebe)

type ReceiverPhase =
  | { kind: 'idle' }
  | { kind: 'waiting'; pairing: Pairing }
  | { kind: 'connecting'; pairing: Pairing }
  | { kind: 'connected'; pairing: Pairing }
  | { kind: 'reconnecting'; pairing: Pairing }
  | { kind: 'disconnected'; message: string }
  | { kind: 'expired' }
  | { kind: 'error'; message: string };

export function RobotCameraPage({
  onOpenDraft,
}: {
  onOpenDraft: (draft: CaptureDraft) => void | Promise<void>;
}) {
  const [phase, setPhase] = useState<ReceiverPhase>({ kind: 'idle' });
  const [now, setNow] = useState(() => Date.now());
  const [copied, setCopied] = useState(false);
  const [capturing, setCapturing] = useState(false);
  const [captureError, setCaptureError] = useState('');
  const [remoteSize, setRemoteSize] = useState<{ width: number; height: number } | null>(null);

  const video = useRef<HTMLVideoElement>(null);
  const phaseRef = useRef<ReceiverPhase>(phase);
  const signaling = useRef<SignalingLink | null>(null);
  const pc = useRef<RTCPeerConnection | null>(null);
  const remote = useRef<MediaStream | null>(null);
  const pending = useRef<RTCIceCandidateInit[]>([]);
  const lockedPeer = useRef<string | null>(null);
  const expiryTimer = useRef<number | undefined>(undefined);
  const graceTimer = useRef<number | undefined>(undefined);
  const generation = useRef(0);

  function updatePhase(next: ReceiverPhase) {
    phaseRef.current = next;
    setPhase(next);
  }

  function teardown(next: ReceiverPhase, notifyPhone: boolean) {
    generation.current += 1;
    window.clearTimeout(expiryTimer.current);
    window.clearTimeout(graceTimer.current);
    graceTimer.current = undefined;
    const connection = pc.current;
    pc.current = null;
    if (connection) {
      connection.ontrack = null;
      connection.onicecandidate = null;
      connection.onconnectionstatechange = null;
      connection.close();
    }
    stopStream(remote.current);
    remote.current = null;
    if (video.current) video.current.srcObject = null;
    pending.current = [];
    const link = signaling.current;
    signaling.current = null;
    const peer = lockedPeer.current;
    lockedPeer.current = null;
    const token = 'pairing' in phaseRef.current ? phaseRef.current.pairing.token : '';
    if (link) {
      const close = () => void link.close();
      if (notifyPhone && peer)
        void link
          .send({ kind: 'disconnect', from: 'receiver', token, peer, reason: 'receiver' })
          .finally(close);
      else close();
    }
    setCopied(false);
    updatePhase(next);
  }

  function lost(message = 'Câmera desconectada') {
    teardown({ kind: 'disconnected', message }, false);
  }

  function connectPhone() {
    teardown({ kind: 'idle' }, true);
    setCaptureError('');
    let servers: RTCIceServer[];
    try {
      servers = iceServers();
    } catch (reason) {
      updatePhase({ kind: 'error', message: configMessage(reason) });
      return;
    }
    const pairing = createPairing();
    const run = generation.current;
    updatePhase({ kind: 'waiting', pairing });
    setNow(Date.now());
    signaling.current = openSignaling(
      pairing.sessionId,
      { role: 'receiver', token: pairing.token },
      (message) => {
        if (run === generation.current) void onSignal(message, pairing, servers);
      },
      (status) => {
        if (run !== generation.current) return;
        if (status === 'unavailable' && phaseRef.current.kind === 'waiting')
          teardown(
            {
              kind: 'error',
              message:
                'Não foi possível abrir o canal de conexão. Verifique a internet e tente de novo.',
            },
            false,
          );
      },
      (peer) => {
        if (run === generation.current)
          void signaling.current?.send({
            kind: 'error',
            from: 'receiver',
            token: '',
            peer,
            reason: 'invalid-token',
          });
      },
    );
    expiryTimer.current = window.setTimeout(() => {
      if (run === generation.current && phaseRef.current.kind === 'waiting')
        teardown({ kind: 'expired' }, false);
    }, pairing.expiresAt - Date.now());
  }

  async function onSignal(message: SignalMessage, pairing: Pairing, servers: RTCIceServer[]) {
    const link = signaling.current;
    if (!link) return;
    const current = phaseRef.current;
    if (message.kind === 'sender-ready') {
      const known = lockedPeer.current === message.peer;
      const idleSlot = !lockedPeer.current;
      if (!known && !idleSlot) {
        // Um celular por sessão: outro aparelho com o mesmo link fica de fora.
        void link.send({
          kind: 'error',
          from: 'receiver',
          token: '',
          peer: message.peer,
          reason: 'busy',
        });
        return;
      }
      if (idleSlot && pairingExpired(pairing)) {
        void link.send({
          kind: 'error',
          from: 'receiver',
          token: '',
          peer: message.peer,
          reason: 'expired',
        });
        return;
      }
      lockedPeer.current = message.peer;
      window.clearTimeout(expiryTimer.current);
      if (current.kind === 'waiting') updatePhase({ kind: 'connecting', pairing });
      void link.send({
        kind: 'receiver-ready',
        from: 'receiver',
        token: pairing.token,
        peer: message.peer,
      });
      return;
    }
    if (message.peer !== lockedPeer.current) return;
    if (message.kind === 'disconnect') {
      lost('Câmera desconectada');
      return;
    }
    if (message.kind === 'offer') {
      let connection = pc.current;
      if (!connection || connection.signalingState === 'closed') {
        connection = new RTCPeerConnection({ iceServers: servers });
        pc.current = connection;
        pending.current = [];
        wirePeer(connection, pairing);
      }
      await connection.setRemoteDescription({ type: 'offer', sdp: message.sdp });
      await flushCandidates(connection, pending.current);
      const answer = await connection.createAnswer();
      await connection.setLocalDescription(answer);
      if (pc.current !== connection) return;
      void link.send({
        kind: 'answer',
        from: 'receiver',
        token: pairing.token,
        peer: message.peer,
        sdp: answer.sdp ?? '',
      });
      return;
    }
    if (message.kind === 'ice-candidate' && pc.current)
      await addCandidate(pc.current, pending.current, message.candidate);
  }

  function wirePeer(connection: RTCPeerConnection, pairing: Pairing) {
    connection.onicecandidate = (event) => {
      const peer = lockedPeer.current;
      if (!event.candidate || !peer) return;
      void signaling.current?.send({
        kind: 'ice-candidate',
        from: 'receiver',
        token: pairing.token,
        peer,
        candidate: signalCandidate(event.candidate),
      });
    };
    connection.ontrack = (event) => {
      const stream = event.streams[0] ?? new MediaStream([event.track]);
      remote.current = stream;
      const player = video.current;
      if (player && player.srcObject !== stream) {
        player.srcObject = stream;
        void player.play().catch(() => undefined);
      }
      event.track.addEventListener('ended', () => {
        if (pc.current === connection) lost('Câmera desconectada');
      });
    };
    connection.onconnectionstatechange = () => {
      if (pc.current !== connection) return;
      const state = connection.connectionState;
      if (state === 'connected') {
        window.clearTimeout(graceTimer.current);
        graceTimer.current = undefined;
        updatePhase({ kind: 'connected', pairing });
      } else if (state === 'disconnected' || state === 'failed') {
        // Queda de rede: segura a sessão por pouco tempo enquanto o celular tenta voltar.
        if (phaseRef.current.kind === 'connected') {
          updatePhase({ kind: 'reconnecting', pairing });
        }
        if (graceTimer.current === undefined)
          graceTimer.current = window.setTimeout(() => {
            graceTimer.current = undefined;
            if (pc.current === connection && connection.connectionState !== 'connected')
              lost('Câmera desconectada');
          }, RECONNECT_GRACE_MS);
      } else if (state === 'closed') lost('Câmera desconectada');
    };
  }

  async function captureEvidence() {
    setCaptureError('');
    setCapturing(true);
    try {
      const player = video.current;
      if (!player?.videoWidth) throw new Error('Não há quadro para capturar. Conecte o celular.');
      // O quadro original do vídeo remoto: caixas, rótulos e interface ficam em outra
      // camada e nunca entram na foto registrada.
      const capturedAt = Date.now();
      const file = await frameToJpeg(
        player,
        player.videoWidth,
        player.videoHeight,
        `camera-robo-${capturedAt}.jpg`,
        capturedAt,
      );
      try {
        await validatePhoto(file);
      } catch (reason) {
        // A transmissão começa em resolução menor e sobe em segundos; explica em vez de só recusar.
        throw Math.min(player.videoWidth, player.videoHeight) < 640
          ? new Error(
              `A imagem do celular ainda está em ${player.videoWidth}×${player.videoHeight}. Aguarde alguns segundos e capture de novo.`,
            )
          : reason;
      }
      const draft: CaptureDraft = {
        id: crypto.randomUUID(),
        photo: file,
        filename: file.name,
        source: 'pwa_photo',
        camera_origin: 'robot_remote',
        captured_at: new Date(capturedAt).toISOString(),
        created_at: new Date().toISOString(),
        // O GPS do notebook não é o local do robô: o ponto vem do endereço ou do mapa.
        coordinate: null,
        source_location: 'unknown',
        location_timestamp: null,
        heading_deg: null,
        speed_mps: null,
        note: '',
        status: 'local_draft',
      };
      await drafts.save(draft);
      teardown({ kind: 'idle' }, true);
      await onOpenDraft(draft);
    } catch (reason) {
      setCaptureError((reason as Error).message);
    } finally {
      setCapturing(false);
    }
  }

  // Resolução realmente recebida (muda quando o celular ajusta a transmissão).
  useEffect(() => {
    const player = video.current;
    if (!player) return;
    const update = () =>
      setRemoteSize(
        player.videoWidth ? { width: player.videoWidth, height: player.videoHeight } : null,
      );
    player.addEventListener('resize', update);
    player.addEventListener('loadedmetadata', update);
    player.addEventListener('emptied', update);
    return () => {
      player.removeEventListener('resize', update);
      player.removeEventListener('loadedmetadata', update);
      player.removeEventListener('emptied', update);
    };
  }, []);

  // Contagem regressiva do QR, só enquanto ele está na tela.
  useEffect(() => {
    if (phase.kind !== 'waiting') return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [phase.kind]);

  // Saída da rota: o celular é avisado e nada da sessão continua vivo.
  useEffect(
    () => () => teardown({ kind: 'idle' }, true),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  const showVideo = phase.kind === 'connected' || phase.kind === 'reconnecting';
  const link = 'pairing' in phase ? pairingLink(window.location.origin, phase.pairing) : '';
  return (
    <>
      <div className="page-heading">
        <div>
          <h1>Câmera do robô</h1>
          <p>Use um celular como câmera sem fio.</p>
        </div>
        {showVideo && (
          <span
            className={`robot-link-state${phase.kind === 'connected' ? ' is-connected' : ''}`}
            role="status"
          >
            {phase.kind === 'connected' ? 'Conectado' : 'Reconectando…'}
          </span>
        )}
      </div>

      {(phase.kind === 'idle' ||
        phase.kind === 'expired' ||
        phase.kind === 'error' ||
        phase.kind === 'disconnected') && (
        <section className="panel robot-start" aria-label="Conexão do celular">
          {phase.kind === 'disconnected' ? (
            <h2>{phase.message}</h2>
          ) : phase.kind === 'expired' ? (
            <h2>O código expirou sem conexão</h2>
          ) : (
            <Smartphone size={28} strokeWidth={1.5} aria-hidden="true" />
          )}
          {phase.kind === 'error' && (
            <p className="error" role="alert">
              {phase.message}
            </p>
          )}
          <p>
            O celular mostra a câmera traseira e transmite direto para este notebook, sem gravar
            vídeo. Você pode capturar um quadro para revisão humana.
          </p>
          <button type="button" onClick={connectPhone}>
            <Smartphone size={16} aria-hidden="true" />
            {phase.kind === 'disconnected' ? 'Reconectar celular' : 'Conectar celular'}
          </button>
        </section>
      )}

      {(phase.kind === 'waiting' || phase.kind === 'connecting') && (
        <section className="panel robot-pairing" aria-label="Pareamento do celular">
          <QrCode text={link} />
          <div>
            <h2>{phase.kind === 'waiting' ? 'Aguardando celular' : 'Conectando ao celular…'}</h2>
            <p>Leia o QR Code com a câmera do celular, ou abra o link no navegador dele.</p>
            <p className="robot-code">
              Código de conferência <strong>{pairingCode(phase.pairing.token)}</strong>
            </p>
            <label>
              Link de conexão
              <input readOnly value={link} onFocus={(event) => event.target.select()} />
            </label>
            <div className="actions">
              <button
                type="button"
                className="secondary"
                onClick={() =>
                  void navigator.clipboard?.writeText(link).then(
                    () => setCopied(true),
                    () => setCopied(false),
                  )
                }
              >
                <Copy size={16} aria-hidden="true" /> {copied ? 'Link copiado' : 'Copiar link'}
              </button>
              <button
                type="button"
                className="ghost"
                onClick={() => teardown({ kind: 'idle' }, true)}
              >
                Cancelar
              </button>
            </div>
            {phase.kind === 'waiting' && (
              <p className="muted" role="timer">
                O código expira em {formatRemaining(phase.pairing.expiresAt - now)}.
              </p>
            )}
          </div>
        </section>
      )}

      <div className="live-grid" hidden={!showVideo}>
        <section className="live-stage-panel" aria-label="Vídeo da câmera do robô">
          <div className="live-stage" style={{ aspectRatio: remoteSize ? `${remoteSize.width} / ${remoteSize.height}` : '16 / 9' }}>
            <div className="live-frame">
              <video ref={video} className="live-video" playsInline muted autoPlay aria-label="Vídeo remoto da câmera do robô" />
            </div>
            <span className="live-stage-tag" role="status">
              {phase.kind === 'reconnecting' ? 'Reconectando…' : 'Câmera conectada'}
            </span>
          </div>
          <div className="live-actions" role="toolbar" aria-label="Controles da câmera do robô">
            <button type="button" className="secondary" disabled={phase.kind !== 'connected' || capturing} onClick={() => void captureEvidence()}>
              <Send size={16} aria-hidden="true" />{' '}{capturing ? 'Capturando…' : 'Capturar evidência'}
            </button>
            <button type="button" className="ghost" onClick={() => teardown({ kind: 'disconnected', message: 'Câmera desconectada' }, true)}>
              <Unplug size={16} aria-hidden="true" /> Desconectar celular
            </button>
          </div>
        </section>
        <section className="panel" aria-label="Registro de evidência">
          {captureError && <p className="error" role="alert">{captureError}</p>}
          <p className="muted">A foto abre como rascunho para marcar o local. A equipe revisa o relato.</p>
          {remoteSize && <p>Vídeo recebido: {remoteSize.width}×{remoteSize.height}</p>}
        </section>
      </div>
    </>
  );
}

// ================================================================== celular (transmite)

type SenderPhase =
  | { kind: 'invalid' }
  | { kind: 'idle' }
  | { kind: 'starting' }
  | { kind: 'waiting' }
  | { kind: 'connecting' }
  | { kind: 'connected' }
  | { kind: 'reconnecting' }
  | { kind: 'ended'; message: string }
  | { kind: 'error'; message: string };

export function RobotPhonePage() {
  const pairing = useMemo(() => parsePairingHash(location.hash), []);
  const [phase, setPhase] = useState<SenderPhase>(pairing ? { kind: 'idle' } : { kind: 'invalid' });
  // Lanterna opcional para melhorar a iluminação da captura.
  const [torch, setTorch] = useState({ supported: false, on: false });
  const preview = useRef<HTMLVideoElement>(null);
  const phaseRef = useRef<SenderPhase>(phase);
  const camera = useRef<MediaStream | null>(null);
  const signaling = useRef<SignalingLink | null>(null);
  const pc = useRef<RTCPeerConnection | null>(null);
  const pending = useRef<RTCIceCandidateInit[]>([]);
  const peer = useRef('');
  const readyTimer = useRef<number | undefined>(undefined);
  const readyDeadline = useRef<number | undefined>(undefined);
  const graceTimer = useRef<number | undefined>(undefined);
  const restarts = useRef(0);
  const wakeLock = useRef<WakeLockSentinel | null>(null);
  const generation = useRef(0);

  function updatePhase(next: SenderPhase) {
    phaseRef.current = next;
    setPhase(next);
  }

  /** Para a transmissão: câmera, conexão, canal e temporizadores. Nada é gravado. */
  async function toggleTorch() {
    const track = camera.current?.getVideoTracks()[0];
    if (!track) return;
    const on = !torch.on;
    try {
      await track.applyConstraints({ advanced: [{ torch: on } as MediaTrackConstraintSet] });
      setTorch({ supported: true, on });
    } catch {
      setTorch({ supported: false, on: false });
    }
  }

  function stop(next: SenderPhase, notifyNotebook: boolean) {
    generation.current += 1;
    window.clearInterval(readyTimer.current);
    window.clearTimeout(readyDeadline.current);
    window.clearTimeout(graceTimer.current);
    graceTimer.current = undefined;
    const connection = pc.current;
    pc.current = null;
    if (connection) {
      connection.onicecandidate = null;
      connection.onconnectionstatechange = null;
      connection.close();
    }
    stopStream(camera.current);
    camera.current = null;
    if (preview.current) preview.current.srcObject = null;
    setTorch({ supported: false, on: false });
    pending.current = [];
    void wakeLock.current?.release().catch(() => undefined);
    wakeLock.current = null;
    const link = signaling.current;
    signaling.current = null;
    if (link && pairing) {
      const close = () => void link.close();
      if (notifyNotebook && peer.current)
        void link
          .send({
            kind: 'disconnect',
            from: 'sender',
            token: pairing.token,
            peer: peer.current,
            reason: 'sender',
          })
          .finally(close);
      else close();
    }
    updatePhase(next);
  }

  async function activate() {
    if (!pairing) return;
    let servers: RTCIceServer[];
    try {
      servers = iceServers();
    } catch (reason) {
      updatePhase({ kind: 'error', message: configMessage(reason) });
      return;
    }
    stop({ kind: 'starting' }, false);
    const run = generation.current;
    let stream: MediaStream;
    try {
      // Câmera traseira, 720p a 30 quadros por segundo, microfone desligado.
      stream = await openCamera(undefined, ROBOT_PHONE_CONSTRAINTS.video);
    } catch (reason) {
      if (run === generation.current)
        updatePhase({
          kind: 'error',
          message:
            reason instanceof CameraError ? reason.message : 'Não foi possível abrir a câmera.',
        });
      return;
    }
    if (run !== generation.current) {
      stopStream(stream);
      return;
    }
    camera.current = stream;
    const capabilities = stream.getVideoTracks()[0]?.getCapabilities?.() as
      (MediaTrackCapabilities & { torch?: boolean }) | undefined;
    setTorch({ supported: Boolean(capabilities?.torch), on: false });
    stream.getVideoTracks()[0]?.addEventListener('ended', () => {
      if (run === generation.current)
        stop({ kind: 'ended', message: 'A câmera do celular foi desligada.' }, true);
    });
    if (preview.current) {
      preview.current.srcObject = stream;
      void preview.current.play().catch(() => undefined);
    }
    // Tela acesa enquanto transmite, quando o navegador permite.
    void navigator.wakeLock
      ?.request('screen')
      .then((lock) => {
        if (run === generation.current) wakeLock.current = lock;
        else void lock.release();
      })
      .catch(() => undefined);
    peer.current = newPeerId();
    restarts.current = 0;
    updatePhase({ kind: 'waiting' });
    signaling.current = openSignaling(
      pairing.sessionId,
      { role: 'sender', token: pairing.token },
      (message) => {
        if (run === generation.current) void onSignal(message, servers);
      },
      (status) => {
        if (run !== generation.current) return;
        if (status === 'connected') announce(run);
        else if (status === 'unavailable' && phaseRef.current.kind === 'waiting')
          stop(
            {
              kind: 'error',
              message: 'Sem conexão com o notebook. Verifique a internet do celular.',
            },
            false,
          );
      },
    );
  }

  /** "Pronto" repetido até o notebook responder; sem resposta, desiste com explicação. */
  function announce(run: number) {
    if (!pairing) return;
    const send = () =>
      void signaling.current?.send({
        kind: 'sender-ready',
        from: 'sender',
        token: pairing.token,
        peer: peer.current,
      });
    send();
    window.clearInterval(readyTimer.current);
    readyTimer.current = window.setInterval(send, SENDER_READY_INTERVAL_MS);
    window.clearTimeout(readyDeadline.current);
    readyDeadline.current = window.setTimeout(() => {
      if (run === generation.current && phaseRef.current.kind === 'waiting')
        stop(
          {
            kind: 'error',
            message:
              'O notebook não respondeu. Gere um novo código na Câmera do robô e leia de novo.',
          },
          false,
        );
    }, SENDER_READY_TIMEOUT_MS);
  }

  async function onSignal(message: SignalMessage, servers: RTCIceServer[]) {
    if (!pairing) return;
    if (message.peer !== peer.current) return;
    if (message.kind === 'error') {
      stop(
        {
          kind: 'error',
          message:
            message.reason === 'busy'
              ? 'Outro celular já está conectado a esta sessão.'
              : 'Link inválido ou expirado. Gere um novo código no notebook.',
        },
        false,
      );
      return;
    }
    if (message.kind === 'receiver-ready') {
      window.clearInterval(readyTimer.current);
      window.clearTimeout(readyDeadline.current);
      if (pc.current) return;
      updatePhase({ kind: 'connecting' });
      await offer(servers, false);
      return;
    }
    if (message.kind === 'answer' && pc.current) {
      await pc.current.setRemoteDescription({ type: 'answer', sdp: message.sdp });
      await flushCandidates(pc.current, pending.current);
      return;
    }
    if (message.kind === 'ice-candidate' && pc.current) {
      await addCandidate(pc.current, pending.current, message.candidate);
      return;
    }
    if (message.kind === 'disconnect')
      stop({ kind: 'ended', message: 'O notebook encerrou a conexão.' }, false);
  }

  async function offer(servers: RTCIceServer[], iceRestart: boolean) {
    if (!pairing) return;
    let connection = pc.current;
    if (!connection) {
      connection = new RTCPeerConnection({ iceServers: servers });
      pc.current = connection;
      pending.current = [];
      for (const track of camera.current?.getVideoTracks() ?? []) {
        // Visão computacional precisa de detalhe: o codificador mantém a resolução e,
        // com rede fraca, reduz quadros por segundo em vez de encolher a imagem.
        track.contentHint = 'detail';
        const sender = connection.addTrack(track, camera.current!);
        void tuneSender(sender);
      }
      wirePeer(connection, servers);
    }
    const description = await connection.createOffer(iceRestart ? { iceRestart: true } : {});
    await connection.setLocalDescription(description);
    if (pc.current !== connection) return;
    void signaling.current?.send({
      kind: 'offer',
      from: 'sender',
      token: pairing.token,
      peer: peer.current,
      sdp: description.sdp ?? '',
    });
  }

  function wirePeer(connection: RTCPeerConnection, servers: RTCIceServer[]) {
    connection.onicecandidate = (event) => {
      if (!event.candidate || !pairing) return;
      void signaling.current?.send({
        kind: 'ice-candidate',
        from: 'sender',
        token: pairing.token,
        peer: peer.current,
        candidate: signalCandidate(event.candidate),
      });
    };
    connection.onconnectionstatechange = () => {
      if (pc.current !== connection) return;
      const state = connection.connectionState;
      if (state === 'connected') {
        window.clearTimeout(graceTimer.current);
        graceTimer.current = undefined;
        updatePhase({ kind: 'connected' });
      } else if (state === 'disconnected' || state === 'failed') {
        if (phaseRef.current.kind === 'connected') updatePhase({ kind: 'reconnecting' });
        // Recuperação curta: poucos reinícios de ICE e um prazo; depois, desiste.
        if (restarts.current < MAX_ICE_RESTARTS) {
          restarts.current += 1;
          void offer(servers, true).catch(() => undefined);
        }
        if (graceTimer.current === undefined)
          graceTimer.current = window.setTimeout(() => {
            graceTimer.current = undefined;
            if (pc.current === connection && connection.connectionState !== 'connected')
              stop({ kind: 'ended', message: 'A conexão com o notebook caiu.' }, true);
          }, RECONNECT_GRACE_MS);
      } else if (state === 'closed')
        stop({ kind: 'ended', message: 'A conexão com o notebook foi encerrada.' }, false);
    };
  }

  // Fechar a página ou trocar de app encerra a transmissão e avisa o notebook.
  useEffect(() => {
    const onHide = () => {
      if (camera.current) stop({ kind: 'ended', message: 'Transmissão encerrada.' }, true);
    };
    addEventListener('pagehide', onHide);
    const onVisibility = () => {
      if (document.hidden) onHide();
    };
    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      removeEventListener('pagehide', onHide);
      document.removeEventListener('visibilitychange', onVisibility);
      stop({ kind: 'idle' }, true);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const transmitting = phase.kind === 'connected' || phase.kind === 'reconnecting';
  const cameraOn =
    transmitting ||
    phase.kind === 'waiting' ||
    phase.kind === 'connecting' ||
    phase.kind === 'starting';
  return (
    <section className="robot-phone" aria-label="Celular como câmera do robô">
      <h1>Câmera do robô</h1>
      {pairing && (
        <p className="robot-code">
          Código de conferência <strong>{pairingCode(pairing.token)}</strong>
        </p>
      )}
      <div className="robot-phone-preview" hidden={!cameraOn}>
        <video ref={preview} playsInline muted autoPlay aria-label="Prévia da câmera do celular" />
      </div>
      {phase.kind === 'invalid' && (
        <p className="error" role="alert">
          Link de conexão inválido. Leia o QR Code mostrado no notebook.
        </p>
      )}
      <p className={`robot-phone-state${transmitting ? ' is-connected' : ''}`} role="status">
        {phase.kind === 'connected'
          ? 'Conectado'
          : phase.kind === 'reconnecting'
            ? 'Reconectando…'
            : phase.kind === 'starting'
              ? 'Abrindo a câmera…'
              : phase.kind === 'waiting'
                ? 'Procurando o notebook…'
                : phase.kind === 'connecting'
                  ? 'Conectando ao notebook…'
                  : phase.kind === 'ended'
                    ? phase.message
                    : phase.kind === 'idle'
                      ? 'Pronto para transmitir a câmera traseira.'
                      : ''}
      </p>
      {transmitting && <p className="muted">Transmitindo câmera</p>}
      {phase.kind === 'error' && (
        <p className="error" role="alert">
          {phase.message}
        </p>
      )}
      {pairing && !cameraOn && (
        <button type="button" onClick={() => void activate()}>
          <CameraIcon size={16} aria-hidden="true" /> Ativar câmera
        </button>
      )}
      {cameraOn && torch.supported && (
        <button
          type="button"
          className="secondary"
          aria-pressed={torch.on}
          onClick={() => void toggleTorch()}
        >
          <Flashlight size={16} aria-hidden="true" />
          {torch.on ? 'Desligar lanterna' : 'Ligar lanterna'}
        </button>
      )}
      {cameraOn && (
        <button
          type="button"
          className="danger"
          onClick={() => stop({ kind: 'ended', message: 'Transmissão encerrada.' }, true)}
        >
          <CircleStop size={16} aria-hidden="true" /> Encerrar
        </button>
      )}
      <p className="muted">
        O vídeo vai direto para o notebook e não é gravado. O microfone fica desligado. Mantenha
        esta página aberta enquanto o robô estiver em uso.
      </p>
    </section>
  );
}
