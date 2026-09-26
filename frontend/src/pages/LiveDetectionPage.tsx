import { useEffect, useRef, useState } from 'react';
import { Camera as CameraIcon, CircleStop, ImageUp, Pause, Play, Send } from 'lucide-react';
import {
  AdaptiveCadence,
  BROWSER_MODEL_MANIFEST_PATH,
  checkBrowserModelManifest,
  classColor,
  FrameFreshness,
  InferenceGate,
  isSceneChange,
  LatencyStats,
  overlaySize,
  qualityHints,
  qualityHintText,
  RateMeter,
  TemporalTracker,
  toDisplayBox,
  type BrowserModelManifest,
  type QualityHint,
  type TrackedDetection,
} from '../domain/liveDetection';
import { labelFor } from '../domain/public';
import {
  CameraError,
  frameToJpeg,
  listCameras,
  openCamera,
  stopStream,
} from '../services/cameraStream';
import { stopWarmUp, warmUp, warmUpIfAllowed } from '../services/deviceLocation';
import { drafts, validatePhoto, type CaptureDraft } from '../services/drafts';
import type {
  InferenceProvider,
  WorkerRequest,
  WorkerResponse,
} from '../workers/liveDetection.worker';

const INFERENCE_TIMEOUT_MS = 20_000;

/** `requestVideoFrameCallback` ainda não está em todos os tipos DOM do TypeScript. */
type FrameCallbackVideo = HTMLVideoElement & {
  requestVideoFrameCallback?: (
    callback: (now: number, metadata: { mediaTime: number }) => void,
  ) => number;
  cancelVideoFrameCallback?: (handle: number) => void;
};

type CameraState =
  | { status: 'idle' }
  | { status: 'starting' }
  | { status: 'live'; width: number; height: number; mirrored: boolean; declaredFps: number | null }
  | { status: 'suspended'; message: string }
  | { status: 'error'; message: string };

type ModelState =
  | { status: 'checking' }
  | { status: 'unavailable'; reason: string }
  | { status: 'available'; manifest: BrowserModelManifest }
  | { status: 'loading'; manifest: BrowserModelManifest }
  | {
      status: 'ready';
      manifest: BrowserModelManifest;
      provider: InferenceProvider;
      fallbackReason: string | null;
    }
  | { status: 'failed'; manifest: BrowserModelManifest; message: string };

interface AnalyzedFrame {
  frameId: number;
  timestamp: number;
  width: number;
  height: number;
  mirrored: boolean;
  /** Brutas do modelo, anotadas pela camada temporal (que não remove nenhuma). */
  detections: TrackedDetection[];
  /** Quadro de vídeo (há continuidade) ou imagem avulsa (não há). */
  temporal: boolean;
  hints: QualityHint[];
}

interface Metrics {
  cameraFps: number | null;
  inferenceFps: number | null;
  latencyMs: number | null;
  latencyP50: number | null;
  latencyP95: number | null;
  stages: { preprocess: number; inference: number; postprocess: number } | null;
  /** Intervalo atual entre envios ao modelo, decidido pelo ritmo adaptativo. */
  intervalMs: number | null;
}

const emptyMetrics: Metrics = {
  cameraFps: null,
  inferenceFps: null,
  latencyMs: null,
  latencyP50: null,
  latencyP95: null,
  stages: null,
  intervalMs: null,
};

const statusLabels: Record<BrowserModelManifest['scientific_status'], string> = {
  APPROVED: 'Aprovado',
  EXPERIMENTAL: 'Experimental',
  DEMONSTRATION: 'Demonstração',
  REJECTED: 'Rejeitado',
};

const decimal = new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 1 });
const scoreFormat = new Intl.NumberFormat('pt-BR', {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});
async function fetchManifest(signal: AbortSignal): Promise<ModelState> {
  let response: Response;
  try {
    response = await fetch(BROWSER_MODEL_MANIFEST_PATH, { cache: 'no-cache', signal });
  } catch {
    return { status: 'unavailable', reason: 'Não foi possível consultar o modelo publicado.' };
  }
  if (!response.ok || !response.headers.get('content-type')?.includes('json'))
    return {
      status: 'unavailable',
      reason: 'Nenhum modelo autorizado foi publicado para a prévia no navegador.',
    };
  const check = checkBrowserModelManifest(await response.json().catch(() => null));
  return check.ok
    ? { status: 'available', manifest: check.manifest }
    : { status: 'unavailable', reason: check.reason };
}

export function LiveDetectionPage({
  onOpenDraft,
}: {
  onOpenDraft: (draft: CaptureDraft) => void | Promise<void>;
}) {
  const [camera, setCamera] = useState<CameraState>({ status: 'idle' });
  const [model, setModel] = useState<ModelState>({ status: 'checking' });
  const [cameras, setCameras] = useState<MediaDeviceInfo[]>([]);
  const [selected, setSelected] = useState('');
  const [detecting, setDetecting] = useState(false);
  const [analyzed, setAnalyzed] = useState<AnalyzedFrame | null>(null);
  const [metrics, setMetrics] = useState<Metrics>(emptyMetrics);
  // Camada temporal só de apresentação, reversível: desligada, tudo é desenhado igual.
  const [stabilize, setStabilize] = useState(true);
  const [inferenceError, setInferenceError] = useState('');
  const [captureError, setCaptureError] = useState('');
  const [capturing, setCapturing] = useState(false);
  const [stageSize, setStageSize] = useState({ width: 0, height: 0 });
  // Imagem do aparelho: só prévia visual, sem localização, sem Capture.
  const [still, setStill] = useState<{ name: string } | null>(null);

  const video = useRef<HTMLVideoElement>(null);
  const stillInput = useRef<HTMLInputElement>(null);
  const stillPending = useRef(false);
  const frameCanvas = useRef<HTMLCanvasElement>(null);
  const overlay = useRef<HTMLCanvasElement>(null);
  const stage = useRef<HTMLDivElement>(null);
  const stream = useRef<MediaStream | null>(null);
  const worker = useRef<Worker | null>(null);
  const snapshot = useRef<HTMLCanvasElement | null>(null);
  const gate = useRef(new InferenceGate());
  const cameraToken = useRef(0);
  const detectingRef = useRef(false);
  const modelRef = useRef<ModelState>(model);
  const cameraRef = useRef<CameraState>(camera);
  const loopTimer = useRef<number | undefined>(undefined);
  const frameRequest = useRef(0);
  const watchdog = useRef<number | undefined>(undefined);
  const cadence = useRef(new AdaptiveCadence());
  const freshness = useRef(new FrameFreshness());
  const inferenceMeter = useRef(new RateMeter());
  const cameraMeter = useRef(new RateMeter());
  const latencyStats = useRef(new LatencyStats());
  const tracker = useRef(new TemporalTracker());
  modelRef.current = model;
  cameraRef.current = camera;

  function updateModel(next: ModelState) {
    modelRef.current = next;
    setModel(next);
  }

  function cancelFrameRequest() {
    if (frameRequest.current)
      (video.current as FrameCallbackVideo | null)?.cancelVideoFrameCallback?.(
        frameRequest.current,
      );
    frameRequest.current = 0;
  }

  function stopDetection() {
    gate.current.cancel();
    window.clearTimeout(loopTimer.current);
    window.clearTimeout(watchdog.current);
    cancelFrameRequest();
    detectingRef.current = false;
    inferenceMeter.current.reset();
    latencyStats.current.reset();
    cadence.current.reset();
    freshness.current.reset();
    // Pausa, troca de câmera, saída da rota e logout encerram as observações.
    tracker.current.reset();
    // Caixas antigas sobre um vídeo que continua andando enganariam: saem na pausa.
    // O resultado de uma imagem avulsa (sem vídeo) continua na tela.
    setAnalyzed((value) => (value?.temporal ? null : value));
    setDetecting(false);
  }

  function clearFrames() {
    for (const canvas of [frameCanvas.current, overlay.current, snapshot.current]) {
      if (!canvas) continue;
      canvas.width = 0;
      canvas.height = 0;
    }
    stillPending.current = false;
    setStill(null);
    setAnalyzed(null);
  }

  function stopCamera(next: CameraState = { status: 'idle' }) {
    cameraToken.current += 1;
    stopDetection();
    stopStream(stream.current);
    stream.current = null;
    if (video.current) video.current.srcObject = null;
    cameraMeter.current.reset();
    clearFrames();
    setMetrics(emptyMetrics);
    setCamera(next);
  }

  function disposeWorker() {
    const current = worker.current;
    worker.current = null;
    if (!current) return;
    current.onmessage = null;
    current.onerror = null;
    current.postMessage({ type: 'dispose' } satisfies WorkerRequest);
    // A liberação da sessão é assíncrona; terminate garante que nada fique vivo.
    window.setTimeout(() => current.terminate(), 2000);
  }

  function failInference(message: string) {
    stopDetection();
    setInferenceError(message);
  }

  function schedule(delay: number) {
    window.clearTimeout(loopTimer.current);
    loopTimer.current = window.setTimeout(tick, delay);
  }

  /**
   * Pede ao vídeo o próximo quadro apresentado (requestVideoFrameCallback) e só então o
   * envia. O <video> nunca espera pela inferência: ele segue no ritmo da câmera, e o
   * modelo recebe sempre o quadro mais novo, um por vez, sem fila.
   */
  function tick() {
    const player = video.current as FrameCallbackVideo | null;
    if (!detectingRef.current || document.hidden || !worker.current || !player) return;
    if (modelRef.current.status !== 'ready' || gate.current.busy) return;
    if (!player.videoWidth || !player.videoHeight) return schedule(100);
    if (player.requestVideoFrameCallback) {
      cancelFrameRequest();
      frameRequest.current = player.requestVideoFrameCallback((_now, metadata) => {
        frameRequest.current = 0;
        sendVideoFrame(metadata.mediaTime);
      });
    } else sendVideoFrame(player.currentTime);
  }

  function sendVideoFrame(mediaTime: number) {
    const player = video.current;
    const current = worker.current;
    if (!detectingRef.current || document.hidden || !current || !player) return;
    if (modelRef.current.status !== 'ready' || gate.current.busy) return;
    // O mesmo quadro já foi analisado (vídeo parado ou câmera mais lenta): espera o próximo.
    if (!freshness.current.accept(mediaTime)) return schedule(30);
    const ticket = gate.current.begin(performance.now());
    if (!ticket) return;
    const width = player.videoWidth;
    const height = player.videoHeight;
    const canvas = (snapshot.current ??= document.createElement('canvas'));
    canvas.width = width;
    canvas.height = height;
    canvas.getContext('2d')?.drawImage(player, 0, 0, width, height);
    postFrame(current, canvas, ticket, 'Não foi possível ler o quadro da câmera.');
  }

  /** Envia o conteúdo de `snapshot` ao worker; a resposta volta por onWorkerMessage. */
  function postFrame(
    current: Worker,
    canvas: HTMLCanvasElement,
    ticket: { run: number; frameId: number },
    readError: string,
  ) {
    const { width, height } = canvas;
    const timestamp = Date.now();
    window.clearTimeout(watchdog.current);
    watchdog.current = window.setTimeout(() => {
      if (gate.current.currentRun !== ticket.run || !gate.current.busy) return;
      disposeWorker();
      updateModel({
        status: 'failed',
        manifest: (modelRef.current as Extract<ModelState, { manifest: unknown }>).manifest,
        message: 'A inferência não respondeu a tempo e foi encerrada para não travar a página.',
      });
      failInference('Sem resposta do modelo em 20 s. A análise foi interrompida.');
    }, INFERENCE_TIMEOUT_MS);
    createImageBitmap(canvas).then(
      (bitmap) => {
        if (gate.current.currentRun !== ticket.run || worker.current !== current) {
          bitmap.close();
          return;
        }
        current.postMessage(
          {
            type: 'frame',
            run: ticket.run,
            frameId: ticket.frameId,
            timestamp,
            width,
            height,
            bitmap,
          } satisfies WorkerRequest,
          [bitmap],
        );
      },
      () => {
        gate.current.fail(ticket.run, ticket.frameId);
        failInference(readError);
      },
    );
  }

  function sendStill() {
    const current = worker.current;
    const canvas = snapshot.current;
    if (!stillPending.current || !current || !canvas?.width) return;
    if (modelRef.current.status !== 'ready') return;
    const ticket = gate.current.begin(performance.now());
    if (!ticket) return;
    stillPending.current = false;
    postFrame(current, canvas, ticket, 'Não foi possível ler a imagem escolhida.');
  }

  async function analyzeStill(file: File) {
    const current = modelRef.current;
    if (!('manifest' in current)) return;
    stopCamera();
    setInferenceError('');
    setCaptureError('');
    let bitmap: ImageBitmap;
    try {
      // Orientação EXIF aplicada; os metadados não são usados como localização aqui.
      bitmap = await createImageBitmap(file, { imageOrientation: 'from-image' });
    } catch {
      setInferenceError('Não foi possível ler esta imagem. Use JPEG, PNG ou WebP.');
      return;
    }
    const canvas = (snapshot.current ??= document.createElement('canvas'));
    canvas.width = bitmap.width;
    canvas.height = bitmap.height;
    canvas.getContext('2d')?.drawImage(bitmap, 0, 0);
    bitmap.close();
    setStill({ name: file.name });
    stillPending.current = true;
    if (current.status === 'failed')
      updateModel({ status: 'available', manifest: current.manifest });
    if (modelRef.current.status === 'ready') sendStill();
    else ensureWorker(current.manifest);
  }

  function onWorkerMessage(event: MessageEvent<WorkerResponse>) {
    const message = event.data;
    const current = modelRef.current;
    if (!('manifest' in current)) return;
    if (message.type === 'ready') {
      updateModel({
        status: 'ready',
        manifest: current.manifest,
        provider: message.provider,
        fallbackReason: message.fallbackReason,
      });
      if (detectingRef.current) schedule(0);
      else sendStill();
    } else if (message.type === 'load-error') {
      disposeWorker();
      updateModel({ status: 'failed', manifest: current.manifest, message: message.message });
      failInference('');
    } else if (message.type === 'frame-error') {
      gate.current.fail(message.run, message.frameId);
      if (message.run === gate.current.currentRun)
        failInference(`A análise do quadro falhou: ${message.message}`);
    } else {
      const sentAt = gate.current.inFlightSince();
      if (!gate.current.accept(message.run, message.frameId)) return;
      window.clearTimeout(watchdog.current);
      const now = performance.now();
      const latency = sentAt === null ? message.inferenceMs : now - sentAt;
      latencyStats.current.add(latency);
      inferenceMeter.current.tick(now);
      const live = cameraRef.current;
      const temporal = live.status === 'live';
      // Cena nova (ou imagem avulsa): nenhuma associação anterior vale para este quadro.
      if (!temporal || isSceneChange(message.quality)) tracker.current.reset();
      const tracked = tracker.current.update(message.detections, message.timestamp);
      // Imagem avulsa: o palco mostra a própria imagem analisada. Câmera: o palco segue
      // mostrando o vídeo ao vivo e só as caixas mudam quando chega um resultado novo.
      const frame = frameCanvas.current;
      const source = snapshot.current;
      if (!temporal && frame && source) {
        frame.width = message.width;
        frame.height = message.height;
        frame.getContext('2d')?.drawImage(source, 0, 0);
      }
      setAnalyzed({
        frameId: message.frameId,
        timestamp: message.timestamp,
        width: message.width,
        height: message.height,
        mirrored: live.status === 'live' && live.mirrored,
        detections: tracked.sort((a, b) => b.score - a.score),
        temporal,
        hints: qualityHints(message.quality, message.width, message.height),
      });
      const delay = detectingRef.current ? cadence.current.next(latency) : null;
      setMetrics((value) => ({
        ...value,
        intervalMs: cadence.current.periodMs,
        latencyMs: latency,
        latencyP50: latencyStats.current.percentile(50),
        latencyP95: latencyStats.current.percentile(95),
        stages: {
          preprocess: message.preprocessMs,
          inference: message.inferenceMs,
          postprocess: message.postprocessMs,
        },
      }));
      if (delay !== null) schedule(delay);
    }
  }

  function ensureWorker(manifest: BrowserModelManifest) {
    if (worker.current) return;
    const created = new Worker(new URL('../workers/liveDetection.worker.ts', import.meta.url), {
      type: 'module',
    });
    created.onmessage = onWorkerMessage;
    created.onerror = () => {
      disposeWorker();
      updateModel({
        status: 'failed',
        manifest,
        message: 'O ONNX Runtime não pôde ser iniciado neste navegador.',
      });
      failInference('');
    };
    worker.current = created;
    updateModel({ status: 'loading', manifest });
    created.postMessage({ type: 'load', manifest } satisfies WorkerRequest);
  }

  async function refreshCameras() {
    try {
      setCameras(await listCameras());
    } catch {
      setCameras([]);
    }
  }

  async function startCamera(deviceId = selected) {
    stopCamera({ status: 'starting' });
    const token = cameraToken.current;
    setInferenceError('');
    try {
      const opened = await openCamera(deviceId || undefined);
      if (token !== cameraToken.current) {
        stopStream(opened);
        return;
      }
      stream.current = opened;
      const track = opened.getVideoTracks()[0];
      const settings = track?.getSettings() ?? {};
      track?.addEventListener('ended', () => {
        if (token === cameraToken.current)
          stopCamera({
            status: 'error',
            message: 'A câmera foi desconectada. Reconecte-a e toque em Iniciar câmera.',
          });
      });
      const player = video.current;
      if (!player) throw new Error('Área de vídeo indisponível.');
      player.srcObject = opened;
      await player.play();
      if (token !== cameraToken.current) return;
      setCamera({
        status: 'live',
        width: player.videoWidth || settings.width || 0,
        height: player.videoHeight || settings.height || 0,
        mirrored: settings.facingMode === 'user',
        declaredFps: settings.frameRate ?? null,
      });
      void refreshCameras();
    } catch (reason) {
      if (token !== cameraToken.current) return;
      stopStream(stream.current);
      stream.current = null;
      if (video.current) video.current.srcObject = null;
      setCamera({
        status: 'error',
        message:
          reason instanceof CameraError
            ? reason.message
            : reason instanceof Error && reason.name === 'NotAllowedError'
              ? 'O navegador bloqueou a reprodução da câmera.'
              : 'Não foi possível abrir a câmera.',
      });
    }
  }

  function startDetection() {
    const current = modelRef.current;
    if (cameraRef.current.status !== 'live' || !('manifest' in current)) return;
    if (current.status === 'failed') {
      // Nova tentativa explícita: outro worker, outra verificação de checksum.
      updateModel({ status: 'available', manifest: current.manifest });
    }
    setInferenceError('');
    gate.current.cancel();
    detectingRef.current = true;
    setDetecting(true);
    if (modelRef.current.status === 'ready') schedule(0);
    else ensureWorker(current.manifest);
  }

  async function captureAndRegister() {
    setCaptureError('');
    setCapturing(true);
    // O registro pede o GPS do aparelho logo em seguida; começa a procurar já.
    warmUp();
    try {
      const player = video.current;
      if (!player?.videoWidth) throw new Error('Não há quadro para capturar. Inicie a câmera.');
      // O quadro que está na tela agora, direto do vídeo: as caixas ficam no overlay
      // e nunca entram na foto registrada.
      const capturedAt = Date.now();
      const file = await frameToJpeg(
        player,
        player.videoWidth,
        player.videoHeight,
        `deteccao-ao-vivo-${capturedAt}.jpg`,
        capturedAt,
      );
      await validatePhoto(file);
      const draft: CaptureDraft = {
        id: crypto.randomUUID(),
        photo: file,
        filename: file.name,
        source: 'pwa_photo',
        captured_at: new Date(capturedAt).toISOString(),
        created_at: new Date().toISOString(),
        // O registro obtém o GPS deste instante; nada é inventado aqui.
        coordinate: null,
        source_location: 'unknown',
        location_timestamp: null,
        heading_deg: null,
        speed_mps: null,
        note: '',
        status: 'local_draft',
      };
      await drafts.save(draft);
      stopCamera();
      await onOpenDraft(draft);
    } catch (reason) {
      setCaptureError((reason as Error).message);
    } finally {
      setCapturing(false);
    }
  }

  // Manifesto: único endereço fixo, conferido antes de qualquer download de pesos.
  useEffect(() => {
    const controller = new AbortController();
    void fetchManifest(controller.signal).then((next) => {
      if (!controller.signal.aborted) updateModel(next);
    });
    void refreshCameras();
    return () => controller.abort();
  }, []);

  // Saída da rota ou logout (a página é remontada por sessão): nada continua vivo.
  useEffect(
    () => () => {
      stopCamera();
      disposeWorker();
    },
    [],
  );

  // Com permissão já concedida, o GPS se estabiliza enquanto a câmera está aberta, e
  // "Capturar e registrar" encontra uma posição recente. Sem permissão, nada é pedido aqui.
  useEffect(() => {
    void warmUpIfAllowed();
    return stopWarmUp;
  }, []);

  // Página oculta: a inferência para e a câmera é desligada; a retomada é sempre explícita.
  useEffect(() => {
    const onVisibility = () => {
      if (document.hidden && stream.current)
        stopCamera({
          status: 'suspended',
          message:
            'A câmera foi desligada porque a página ficou oculta. Inicie de novo para continuar.',
        });
    };
    document.addEventListener('visibilitychange', onVisibility);
    return () => document.removeEventListener('visibilitychange', onVisibility);
  }, []);

  // FPS da câmera medido por quadro apresentado, e as taxas publicadas 2× por segundo.
  useEffect(() => {
    if (camera.status !== 'live') return;
    const player = video.current as
      | (HTMLVideoElement & {
          requestVideoFrameCallback?: (cb: () => void) => number;
          cancelVideoFrameCallback?: (id: number) => void;
        })
      | null;
    let handle = 0;
    const onFrame = () => {
      cameraMeter.current.tick(performance.now());
      handle = player?.requestVideoFrameCallback?.(onFrame) ?? 0;
    };
    handle = player?.requestVideoFrameCallback?.(onFrame) ?? 0;
    const interval = window.setInterval(() => {
      const now = performance.now();
      setMetrics((value) => ({
        ...value,
        cameraFps: cameraMeter.current.rate(now),
        inferenceFps: inferenceMeter.current.rate(now),
      }));
    }, 500);
    return () => {
      window.clearInterval(interval);
      if (handle) player?.cancelVideoFrameCallback?.(handle);
    };
  }, [camera.status]);

  useEffect(() => {
    const element = stage.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) =>
      setStageSize({ width: entry.contentRect.width, height: entry.contentRect.height }),
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  // Caixas desenhadas sobre o próprio quadro analisado, na mesma transformação visual.
  useEffect(() => {
    const canvas = overlay.current;
    if (!canvas) return;
    if (!analyzed || !stageSize.width || !stageSize.height || !('manifest' in model)) {
      canvas.width = 0;
      canvas.height = 0;
      return;
    }
    const size = overlaySize(stageSize.width, stageSize.height, window.devicePixelRatio);
    canvas.width = size.width;
    canvas.height = size.height;
    const context = canvas.getContext('2d');
    if (!context) return;
    context.setTransform(size.dpr, 0, 0, size.dpr, 0, 0);
    context.clearRect(0, 0, stageSize.width, stageSize.height);
    context.font = '600 12px "Public Sans Variable", "Segoe UI", sans-serif';
    context.textBaseline = 'top';
    const limit = model.manifest.postprocess.max_detections;
    // Maior score escolhe o lugar do rótulo primeiro; os seguintes descem se colidirem.
    const placed: Array<{ x: number; y: number; w: number }> = [];
    const drawn = analyzed.detections.slice(0, limit).map((detection) => {
      const box = toDisplayBox(detection, analyzed, stageSize, analyzed.mirrored);
      const text = `${labelFor(detection.className)} ${scoreFormat.format(detection.score)}`;
      const w = context.measureText(text).width + 10;
      const x = Math.min(Math.max(0, box.left), Math.max(0, stageSize.width - w));
      let y = box.top >= 20 ? box.top - 20 : box.top;
      while (
        y + 20 <= stageSize.height &&
        placed.some((chip) => x < chip.x + chip.w && chip.x < x + w && Math.abs(chip.y - y) < 20)
      )
        y += 21;
      placed.push({ x, y, w });
      return { detection, box, text, x, y, w };
    });
    // Desenho do menor para o maior score: o mais confiável fica por cima.
    for (const { detection, box, text, x, y, w } of drawn.reverse()) {
      const color = classColor(detection.classIndex);
      // Momentânea: tracejada e mais leve. Continua visível: persistência não é confirmação.
      const provisional = stabilize && analyzed.temporal && detection.state === 'provisional';
      context.globalAlpha = provisional ? 0.7 : 1;
      context.setLineDash(provisional ? [6, 4] : []);
      context.strokeStyle = color;
      context.lineWidth = 2;
      context.strokeRect(box.left, box.top, box.width, box.height);
      context.setLineDash([]);
      context.fillStyle = color;
      context.fillRect(x, y, w, 20);
      context.fillStyle = '#0d2a24';
      context.fillText(text, x + 5, y + 4);
    }
    context.globalAlpha = 1;
  }, [analyzed, stageSize, model, stabilize]);

  const live = camera.status === 'live';
  const hasManifest = 'manifest' in model;
  const manifest = hasManifest ? model.manifest : null;
  // Só uma imagem avulsa ocupa o palco no lugar do vídeo; a câmera nunca é substituída.
  const showStill = still !== null && analyzed !== null;
  const limit = manifest?.postprocess.max_detections ?? 0;
  const aspect = showStill
    ? `${analyzed.width} / ${analyzed.height}`
    : live && camera.width && camera.height
      ? `${camera.width} / ${camera.height}`
      : '16 / 9';

  const startDetectionHint = !live
    ? 'Inicie a câmera para analisar quadros.'
    : !hasManifest
      ? 'Detecção indisponível.'
      : model.status === 'loading'
        ? 'Preparando a detecção…'
        : detecting
          ? 'A detecção já está em andamento.'
          : '';
  const hints = [
    camera.status === 'starting' && 'Aguardando a câmera…',
    startDetectionHint && `Iniciar detecção: ${startDetectionHint}`,
    !detecting && live && hasManifest && 'Pausar detecção: a detecção não está em andamento.',
    !live && !still && 'Capturar e registrar: inicie a câmera para ter um quadro.',
    still &&
      'Capturar e registrar: vale só para a câmera. Para registrar esta imagem, use Registrar evidência.',
  ].filter(Boolean) as string[];

  // Para quem usa: pronta ou indisponível. O provedor (WebGPU/WASM) fica nos detalhes.
  const detectionStatus =
    model.status === 'checking'
      ? 'Verificando a detecção…'
      : model.status === 'unavailable'
        ? `Detecção indisponível. ${model.reason}`
        : model.status === 'loading'
          ? 'Preparando a detecção…'
          : model.status === 'failed'
            ? `Detecção indisponível. ${model.message}`
            : model.status === 'ready'
              ? 'Detecção pronta'
              : 'Detecção disponível: começa quando você tocar em Iniciar detecção.';

  return (
    <>
      <div className="page-heading">
        <div>
          <h1>Detecção ao vivo</h1>
          <p>
            Aponte a câmera para a via. A análise acontece neste aparelho e nada é enviado até você
            registrar.
          </p>
        </div>
        {manifest && manifest.scientific_status !== 'APPROVED' && (
          <span className="badge experimental-badge">Prévia experimental</span>
        )}
      </div>

      <div className="live-grid">
        <section className="live-stage-panel" aria-label="Imagem da câmera">
          <div
            className={`live-stage${showStill ? ' is-still' : ''}`}
            style={{ aspectRatio: aspect }}
          >
            <div className="live-frame" ref={stage}>
              <video
                ref={video}
                className={`live-video${live && camera.mirrored ? ' mirrored' : ''}`}
                playsInline
                muted
                aria-label="Imagem ao vivo da câmera"
              />
              <canvas
                ref={frameCanvas}
                className="live-analyzed"
                aria-label="Imagem analisada"
                hidden={!showStill}
              />
              <canvas ref={overlay} className="live-overlay" aria-hidden="true" />
              {!live && !showStill && (
                <div className="live-placeholder">
                  <CameraIcon size={30} strokeWidth={1.4} />
                  <p>
                    {camera.status === 'starting'
                      ? 'Abrindo a câmera…'
                      : camera.status === 'error' || camera.status === 'suspended'
                        ? camera.message
                        : 'Toque em Iniciar câmera.'}
                  </p>
                </div>
              )}
            </div>
            {showStill && (
              <span className="live-stage-tag">Imagem do aparelho · prévia sem localização</span>
            )}
            {live && detecting && <span className="live-stage-tag">Detectando</span>}
          </div>
          {analyzed && analyzed.hints.length > 0 && (
            <ul className="live-quality" aria-label="Qualidade da captura">
              {analyzed.hints.map((hint) => (
                <li key={hint}>{qualityHintText[hint]}</li>
              ))}
            </ul>
          )}
        </section>

        <div className="live-side">
          <section className="panel" aria-label="Controle da câmera">
            <p
              role="status"
              className={`live-status${model.status === 'ready' ? ' is-ready' : ''}${
                model.status === 'failed' || model.status === 'unavailable' ? ' is-error' : ''
              }`}
            >
              {detectionStatus}
            </p>
            {cameras.length > 1 && (
              <label>
                Câmera do aparelho
                <select
                  value={selected}
                  disabled={camera.status === 'starting' || capturing}
                  onChange={(event) => {
                    setSelected(event.target.value);
                    if (live) void startCamera(event.target.value);
                  }}
                >
                  <option value="">Automática (traseira quando houver)</option>
                  {cameras.map((device, index) => (
                    <option key={device.deviceId || index} value={device.deviceId}>
                      {device.label || `Câmera ${index + 1}`}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <div className="actions">
              <button
                type="button"
                disabled={camera.status === 'starting' || live || capturing}
                onClick={() => void startCamera()}
              >
                <CameraIcon size={17} /> Iniciar câmera
              </button>
              <button
                type="button"
                className="secondary"
                disabled={!live && camera.status !== 'starting'}
                onClick={() => stopCamera()}
              >
                <CircleStop size={17} /> Encerrar câmera
              </button>
            </div>
            <div className="actions live-actions">
              <button
                type="button"
                disabled={Boolean(startDetectionHint)}
                onClick={startDetection}
                aria-describedby="live-hints"
              >
                <Play size={17} /> Iniciar detecção
              </button>
              <button
                type="button"
                className="secondary"
                disabled={!detecting}
                onClick={stopDetection}
                aria-describedby="live-hints"
              >
                <Pause size={17} /> Pausar detecção
              </button>
              <button
                type="button"
                className="secondary"
                disabled={capturing || still !== null || !live}
                onClick={() => void captureAndRegister()}
                aria-describedby="live-hints"
              >
                <Send size={17} /> {capturing ? 'Capturando…' : 'Capturar e registrar'}
              </button>
            </div>
            {hints.length > 0 && (
              <ul id="live-hints" className="live-hints">
                {hints.map((hint) => (
                  <li key={hint}>{hint}</li>
                ))}
              </ul>
            )}
            {(camera.status === 'error' || camera.status === 'suspended') && (
              <p className="error" role="alert">
                {camera.message}
              </p>
            )}
            {inferenceError && (
              <p className="error" role="alert">
                {inferenceError}
              </p>
            )}
            {captureError && (
              <p className="error" role="alert">
                {captureError}
              </p>
            )}
            <p className="muted">
              Capturar grava a imagem sem as caixas e abre o registro com a localização do aparelho.
              A análise oficial é feita depois do envio.
            </p>
          </section>

          <section className="panel" aria-label="Detecções do quadro analisado" aria-live="polite">
            <h2>O que a prévia encontrou</h2>
            {!analyzed ? (
              <p className="muted">As detecções aparecem aqui quando a análise começar.</p>
            ) : analyzed.detections.length === 0 ? (
              <p>Nenhum dos problemas reconhecidos apareceu nesta imagem.</p>
            ) : (
              <>
                <ol className="live-detections">
                  {analyzed.detections.slice(0, limit).map((detection, index) => (
                    <li key={index}>
                      <i
                        style={{ background: classColor(detection.classIndex) }}
                        aria-hidden="true"
                      />
                      <span>
                        <strong>{labelFor(detection.className)}</strong>
                      </span>
                      <b title="Confiança do modelo nesta prévia, de 0 a 1">
                        {scoreFormat.format(detection.score)}
                      </b>
                    </li>
                  ))}
                </ol>
                {analyzed.detections.length > limit && (
                  <p className="muted">
                    Exibindo {limit} de {analyzed.detections.length} detecções.
                  </p>
                )}
              </>
            )}
            <p className="muted">
              O número é a confiança do modelo nesta prévia (0 a 1). Não é gravidade nem confirmação
              do problema.
            </p>
          </section>

          <section className="panel" aria-label="Imagem do aparelho">
            <h2>Analisar uma imagem</h2>
            <input
              ref={stillInput}
              type="file"
              accept="image/jpeg,image/png,image/webp"
              hidden
              aria-label="Escolher imagem para prévia"
              onChange={(event) => {
                const file = event.target.files?.[0];
                event.target.value = '';
                if (file) void analyzeStill(file);
              }}
            />
            <div className="actions">
              <button
                type="button"
                className="secondary"
                disabled={!hasManifest || capturing || model.status === 'loading'}
                onClick={() => stillInput.current?.click()}
              >
                <ImageUp size={17} /> Analisar imagem
              </button>
            </div>
            {still && <p className="muted">Arquivo: {still.name}</p>}
            <p className="muted">
              Só uma prévia neste aparelho: a imagem não é enviada e não vira relato.
            </p>
          </section>

          <details className="panel live-technical">
            <summary>Detalhes técnicos</summary>
            <dl className="live-readout" aria-label="Desempenho medido">
              <div>
                {/* Sem requestVideoFrameCallback só existe o valor declarado pelo dispositivo. */}
                <dt>
                  {metrics.cameraFps == null && live && camera.declaredFps
                    ? 'Câmera (declarado)'
                    : 'Câmera'}
                </dt>
                <dd>
                  {metrics.cameraFps != null
                    ? `${decimal.format(metrics.cameraFps)} fps`
                    : live && camera.declaredFps
                      ? `${decimal.format(camera.declaredFps)} fps`
                      : '—'}
                </dd>
              </div>
              <div>
                <dt>Análises</dt>
                <dd>
                  {metrics.inferenceFps != null
                    ? `${decimal.format(metrics.inferenceFps)} /s`
                    : '—'}
                </dd>
              </div>
              <div>
                <dt>Intervalo</dt>
                <dd>{metrics.intervalMs != null ? `${Math.round(metrics.intervalMs)} ms` : '—'}</dd>
              </div>
              <div>
                <dt>Latência</dt>
                <dd>{metrics.latencyMs != null ? `${Math.round(metrics.latencyMs)} ms` : '—'}</dd>
              </div>
              <div>
                <dt>Latência p50 / p95</dt>
                <dd>
                  {metrics.latencyP50 != null && metrics.latencyP95 != null
                    ? `${Math.round(metrics.latencyP50)} / ${Math.round(metrics.latencyP95)} ms`
                    : '—'}
                </dd>
              </div>
              <div>
                <dt>Pré · modelo · pós</dt>
                <dd>
                  {metrics.stages
                    ? `${Math.round(metrics.stages.preprocess)} · ${Math.round(metrics.stages.inference)} · ${Math.round(metrics.stages.postprocess)} ms`
                    : '—'}
                </dd>
              </div>
              <div>
                <dt>Resolução</dt>
                <dd>{live ? `${camera.width}×${camera.height}` : '—'}</dd>
              </div>
              <div>
                <dt>Quadro</dt>
                <dd>{analyzed ? `nº ${analyzed.frameId}` : '—'}</dd>
              </div>
            </dl>
            {manifest && (
              <dl className="live-model">
                <div>
                  <dt>Versão</dt>
                  <dd>{manifest.model_version}</dd>
                </div>
                <div>
                  <dt>Status científico</dt>
                  <dd>{statusLabels[manifest.scientific_status]}</dd>
                </div>
                <div>
                  <dt>Classes do artefato</dt>
                  <dd>{manifest.class_names.map(labelFor).join(', ')}</dd>
                </div>
                <div>
                  <dt>Perfil de inferência</dt>
                  <dd title={manifest.inference_profile?.sha256}>
                    {manifest.inference_profile
                      ? manifest.inference_profile.sha256.slice(0, 12)
                      : 'não versionado'}
                  </dd>
                </div>
                <div>
                  <dt>Execução</dt>
                  <dd>
                    {model.status === 'ready'
                      ? model.provider === 'webgpu'
                        ? 'WebGPU'
                        : 'WASM (1 thread)'
                      : '—'}
                  </dd>
                </div>
              </dl>
            )}
            {model.status === 'ready' && model.fallbackReason && (
              <p className="muted">{model.fallbackReason}</p>
            )}
            <label className="live-toggle">
              <input
                type="checkbox"
                checked={stabilize}
                onChange={(event) => setStabilize(event.target.checked)}
              />
              Tracejar detecções que ainda não se repetiram em quadros seguidos
            </label>
          </details>
        </div>
      </div>
    </>
  );
}
