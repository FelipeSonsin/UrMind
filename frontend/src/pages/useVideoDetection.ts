import { useEffect, useRef, useState, type RefObject } from 'react';
import {
  AdaptiveCadence,
  BROWSER_MODEL_MANIFEST_PATH,
  checkBrowserModelManifest,
  classColor,
  DEFAULT_TRACKING,
  FrameFreshness,
  InferenceGate,
  isSceneChange,
  LatencyStats,
  overlaySize,
  qualityHints,
  RateMeter,
  TemporalTracker,
  toDisplayBox,
  type BrowserModelManifest,
  type QualityHint,
  type TrackedDetection,
} from '../domain/liveDetection';
import { labelFor } from '../domain/public';
import type {
  InferenceProvider,
  WorkerRequest,
  WorkerResponse,
} from '../workers/liveDetection.worker';

/**
 * Pipeline único de detecção no navegador, usado pela Detecção ao vivo (câmera deste
 * aparelho) e pela Câmera do robô (vídeo remoto do celular): o mesmo worker, o mesmo
 * modelo e perfil do manifesto, uma inferência por vez, sem fila de quadros, e o
 * <video> sempre no ritmo da câmera, independente da inferência.
 */

const INFERENCE_TIMEOUT_MS = 20_000;
/** Texto sobre a etiqueta colorida da caixa: o fundo do tema, legível em todas as cores. */
const OVERLAY_TEXT = '#0c1411';

/** `requestVideoFrameCallback` ainda não está em todos os tipos DOM do TypeScript. */
type FrameCallbackVideo = HTMLVideoElement & {
  requestVideoFrameCallback?: (
    callback: (now: number, metadata: { mediaTime: number }) => void,
  ) => number;
  cancelVideoFrameCallback?: (handle: number) => void;
};

export type ModelState =
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

export interface AnalyzedFrame {
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

export interface Metrics {
  cameraFps: number | null;
  inferenceFps: number | null;
  latencyMs: number | null;
  latencyP50: number | null;
  latencyP95: number | null;
  stages: { preprocess: number; inference: number; postprocess: number } | null;
  /** Intervalo atual entre envios ao modelo, decidido pelo ritmo adaptativo. */
  intervalMs: number | null;
}

export const emptyMetrics: Metrics = {
  cameraFps: null,
  inferenceFps: null,
  latencyMs: null,
  latencyP50: null,
  latencyP95: null,
  stages: null,
  intervalMs: null,
};

/** O que está no palco agora: vídeo ao vivo (com continuidade) e se está espelhado. */
export interface DetectionSource {
  live: boolean;
  mirrored: boolean;
}

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

export function useVideoDetection({
  video,
  stillCanvas,
  source,
  measureCamera,
}: {
  video: RefObject<HTMLVideoElement | null>;
  /** Onde a imagem avulsa analisada é desenhada (só a Detecção ao vivo usa). */
  stillCanvas?: RefObject<HTMLCanvasElement | null>;
  /** Lido no momento de cada quadro e de cada resultado. */
  source: () => DetectionSource;
  /** Vídeo no palco: mede os quadros apresentados para os detalhes técnicos. */
  measureCamera: boolean;
}) {
  const [model, setModel] = useState<ModelState>({ status: 'checking' });
  const [detecting, setDetecting] = useState(false);
  const [analyzed, setAnalyzed] = useState<AnalyzedFrame | null>(null);
  const [metrics, setMetrics] = useState<Metrics>(emptyMetrics);
  const [inferenceError, setInferenceError] = useState('');
  // Caixas de vídeo expiram na mesma janela do rastreador temporal: sem resultado novo,
  // não ficam paradas sobre uma cena que já mudou.
  const [stale, setStale] = useState(false);
  const staleTimer = useRef<number | undefined>(undefined);

  const worker = useRef<Worker | null>(null);
  const snapshot = useRef<HTMLCanvasElement | null>(null);
  const stillPending = useRef(false);
  const gate = useRef(new InferenceGate());
  const detectingRef = useRef(false);
  const modelRef = useRef<ModelState>(model);
  const sourceRef = useRef(source);
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
  sourceRef.current = source;

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
    window.clearTimeout(staleTimer.current);
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

  /** Esquece quadros e resultados: nada analisado fica na tela nem na memória. */
  function clear() {
    for (const canvas of [stillCanvas?.current, snapshot.current]) {
      if (!canvas) continue;
      canvas.width = 0;
      canvas.height = 0;
    }
    stillPending.current = false;
    window.clearTimeout(staleTimer.current);
    setStale(false);
    setAnalyzed(null);
  }

  function resetMetrics() {
    cameraMeter.current.reset();
    setMetrics(emptyMetrics);
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

  /** Imagem avulsa (sem vídeo): uma análise só, sem continuidade temporal. */
  function analyzeStill(bitmap: ImageBitmap) {
    const current = modelRef.current;
    if (!('manifest' in current)) return;
    const canvas = (snapshot.current ??= document.createElement('canvas'));
    canvas.width = bitmap.width;
    canvas.height = bitmap.height;
    canvas.getContext('2d')?.drawImage(bitmap, 0, 0);
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
      const live = sourceRef.current();
      const temporal = live.live;
      // Cena nova (ou imagem avulsa): nenhuma associação anterior vale para este quadro.
      if (!temporal || isSceneChange(message.quality)) tracker.current.reset();
      const tracked = tracker.current.update(message.detections, message.timestamp);
      // Imagem avulsa: o palco mostra a própria imagem analisada. Vídeo: o palco segue
      // mostrando o vídeo ao vivo e só as caixas mudam quando chega um resultado novo.
      const frame = stillCanvas?.current;
      const still = snapshot.current;
      if (!temporal && frame && still) {
        frame.width = message.width;
        frame.height = message.height;
        frame.getContext('2d')?.drawImage(still, 0, 0);
      }
      setAnalyzed({
        frameId: message.frameId,
        timestamp: message.timestamp,
        width: message.width,
        height: message.height,
        mirrored: live.live && live.mirrored,
        detections: tracked.sort((a, b) => b.score - a.score),
        temporal,
        hints: qualityHints(message.quality, message.width, message.height),
      });
      window.clearTimeout(staleTimer.current);
      setStale(false);
      if (temporal)
        staleTimer.current = window.setTimeout(() => setStale(true), DEFAULT_TRACKING.maxGapMs);
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

  /**
   * Baixa, confere e inicia o modelo no worker sem começar a analisar. Quem sabe que o
   * vídeo vai chegar (celular pareando) chama antes; a análise começa sem esperar o modelo.
   */
  function prepareModel() {
    const current = modelRef.current;
    if (current.status === 'available') ensureWorker(current.manifest);
  }

  /**
   * Começa a analisar o vídeo do palco; sem vídeo ao vivo ou sem modelo, não faz nada.
   * `retryFailed: false` (início automático) deixa um modelo recusado como está: nova
   * tentativa, com outro download, só por pedido explícito da pessoa.
   */
  function startDetection({ retryFailed = true }: { retryFailed?: boolean } = {}) {
    const current = modelRef.current;
    if (!sourceRef.current().live || !('manifest' in current)) return;
    if (current.status === 'failed') {
      if (!retryFailed) return;
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

  // Manifesto: único endereço fixo, conferido antes de qualquer download de pesos.
  useEffect(() => {
    const controller = new AbortController();
    void fetchManifest(controller.signal).then((next) => {
      if (!controller.signal.aborted) updateModel(next);
    });
    return () => controller.abort();
  }, []);

  // Saída da rota: nenhum temporizador, pedido de quadro ou worker continua vivo.
  useEffect(
    () => () => {
      detectingRef.current = false;
      gate.current.cancel();
      window.clearTimeout(loopTimer.current);
      window.clearTimeout(watchdog.current);
      window.clearTimeout(staleTimer.current);
      cancelFrameRequest();
      disposeWorker();
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  // FPS do vídeo medido por quadro apresentado, e as taxas publicadas 2× por segundo.
  useEffect(() => {
    if (!measureCamera) return;
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
  }, [measureCamera, video]);

  return {
    model,
    detecting,
    analyzed,
    /** Resultado de vídeo mais velho que a janela do rastreador: não desenhar caixas. */
    stale,
    metrics,
    inferenceError,
    setInferenceError,
    prepareModel,
    startDetection,
    stopDetection,
    analyzeStill,
    clear,
    resetMetrics,
  };
}

/** Tamanho do palco em pixels CSS, acompanhado por ResizeObserver. */
export function useElementSize(ref: RefObject<HTMLElement | null>) {
  const [size, setSize] = useState({ width: 0, height: 0 });
  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) =>
      setSize({ width: entry.contentRect.width, height: entry.contentRect.height }),
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, [ref]);
  return size;
}

/**
 * Caixas desenhadas numa camada própria, por cima do vídeo e na mesma transformação
 * visual. A camada nunca entra na foto registrada: a captura lê o vídeo, não o overlay.
 */
export function useDetectionOverlay(
  overlay: RefObject<HTMLCanvasElement | null>,
  stageSize: { width: number; height: number },
  analyzed: AnalyzedFrame | null,
  model: ModelState,
  stabilize: boolean,
) {
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
      // Só o nome simples sobre o vídeo; a confiança fica nos detalhes técnicos.
      const text = labelFor(detection.className);
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
      context.fillStyle = OVERLAY_TEXT;
      context.fillText(text, x + 5, y + 4);
    }
    context.globalAlpha = 1;
  }, [overlay, analyzed, stageSize, model, stabilize]);
}
