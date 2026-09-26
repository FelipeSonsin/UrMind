/// <reference lib="webworker" />
/**
 * Web Worker do navegador (não confundir com o Worker Python do UrMind): uma sessão
 * ONNX por aba, batch 1, buffers reutilizados. Só é criado quando a pessoa inicia a
 * detecção, então a biblioteca e o modelo nunca pesam no carregamento das páginas.
 */
import * as ort from 'onnxruntime-web/webgpu';
// Mesmo pacote e versão do JS acima; servido pela própria origem, sem CDN.
import wasmUrl from 'onnxruntime-web/ort-wasm-simd-threaded.asyncify.wasm?url';
import {
  decodeYoloxOutput,
  fillBgrTensor,
  letterbox,
  measureFrameQuality,
  ModelContractError,
  postprocessOptions,
  QUALITY_GRID,
  sampleLuminance,
  verifyModelBytes,
  type BrowserModelManifest,
  type FrameQuality,
  type LiveDetection,
} from '../domain/liveDetection';

export type InferenceProvider = 'webgpu' | 'wasm';

export type WorkerRequest =
  | { type: 'load'; manifest: BrowserModelManifest }
  | {
      type: 'frame';
      run: number;
      frameId: number;
      timestamp: number;
      width: number;
      height: number;
      bitmap: ImageBitmap;
    }
  | { type: 'dispose' };

export type WorkerResponse =
  | { type: 'ready'; provider: InferenceProvider; fallbackReason: string | null }
  | {
      type: 'result';
      run: number;
      frameId: number;
      timestamp: number;
      width: number;
      height: number;
      detections: LiveDetection[];
      /** Letterbox + tensor + qualidade. */
      preprocessMs: number;
      /** Só `session.run`. */
      inferenceMs: number;
      /** Decode + NMS. */
      postprocessMs: number;
      quality: FrameQuality;
      inferenceProfile: string | null;
    }
  | { type: 'frame-error'; run: number; frameId: number; message: string }
  | { type: 'load-error'; message: string };

const scope = self as unknown as DedicatedWorkerGlobalScope;

ort.env.wasm.wasmPaths = { wasm: wasmUrl };
// Uma thread: dispensa COOP/COEP (SharedArrayBuffer) e não disputa CPU com o resto da máquina.
ort.env.wasm.numThreads = 1;
// Já estamos num worker dedicado; o proxy do ORT seria um segundo worker, e não combina com WebGPU.
ort.env.wasm.proxy = false;

let manifest: BrowserModelManifest | null = null;
let session: ort.InferenceSession | null = null;
let tensorData: Float32Array | null = null;
let canvas: OffscreenCanvas | null = null;
let context: OffscreenCanvasRenderingContext2D | null = null;
// Luminância do quadro anterior da mesma execução (movimento/troca de cena); dois buffers alternados.
let luminance: Float32Array | null = null;
let previousLuminance: Float32Array | null = null;
let previousRun = -1;

async function createSession(
  bytes: Uint8Array,
  provider: InferenceProvider,
): Promise<ort.InferenceSession> {
  const created = await ort.InferenceSession.create(bytes, {
    executionProviders: [provider],
    graphOptimizationLevel: 'all',
  });
  try {
    const current = manifest!;
    if (!created.inputNames.includes(current.input.name))
      throw new ModelContractError(`O modelo não tem a entrada "${current.input.name}".`);
    if (!created.outputNames.includes(current.output.name))
      throw new ModelContractError(`O modelo não tem a saída "${current.output.name}".`);
    // Uma execução real valida o provider e o formato da saída antes de qualquer quadro.
    const [height, width] = current.input.size;
    const probe = new ort.Tensor('float32', new Float32Array(3 * height * width).fill(114), [
      1,
      3,
      height,
      width,
    ]);
    const outputs = await created.run({ [current.input.name]: probe });
    const output = outputs[current.output.name];
    try {
      decodeYoloxOutput(output.data as Float32Array, output.dims, {
        ratio: 1,
        frameWidth: width,
        frameHeight: height,
        ...postprocessOptions(current),
      });
    } finally {
      output.dispose();
      probe.dispose();
    }
    return created;
  } catch (reason) {
    await created.release();
    throw reason;
  }
}

async function load(next: BrowserModelManifest): Promise<WorkerResponse> {
  manifest = next;
  const response = await fetch(next.onnx.path, { credentials: 'same-origin', cache: 'no-cache' });
  if (!response.ok)
    throw new ModelContractError(`Download do modelo falhou (HTTP ${response.status}).`);
  const bytes = await response.arrayBuffer();
  await verifyModelBytes(bytes, next.onnx);
  const model = new Uint8Array(bytes);
  let fallbackReason: string | null = null;
  const gpu = (navigator as Navigator & { gpu?: { requestAdapter(): Promise<unknown> } }).gpu;
  if (gpu && (await gpu.requestAdapter().catch(() => null))) {
    try {
      session = await createSession(model, 'webgpu');
      return { type: 'ready', provider: 'webgpu', fallbackReason: null };
    } catch (reason) {
      if (reason instanceof ModelContractError) throw reason;
      fallbackReason = `WebGPU recusou o modelo: ${(reason as Error).message}`;
    }
  } else {
    fallbackReason = 'WebGPU indisponível neste navegador ou dispositivo.';
  }
  session = await createSession(model, 'wasm');
  return { type: 'ready', provider: 'wasm', fallbackReason };
}

async function infer(request: Extract<WorkerRequest, { type: 'frame' }>): Promise<WorkerResponse> {
  const { bitmap } = request;
  try {
    if (!session || !manifest) throw new Error('Modelo não carregado.');
    const [inputHeight, inputWidth] = manifest.input.size;
    if (!canvas || !context) {
      canvas = new OffscreenCanvas(inputWidth, inputHeight);
      context = canvas.getContext('2d', { willReadFrequently: true });
      if (!context) throw new Error('Canvas indisponível no worker.');
      tensorData = new Float32Array(3 * inputWidth * inputHeight);
    }
    const preprocessStarted = performance.now();
    const geometry = letterbox(
      request.width,
      request.height,
      manifest.input.size,
      manifest.input.letterbox.upscale ?? true,
    );
    context.fillStyle = 'rgb(114, 114, 114)';
    context.fillRect(0, 0, inputWidth, inputHeight);
    context.imageSmoothingEnabled = true;
    context.imageSmoothingQuality = 'low';
    context.drawImage(bitmap, 0, 0, geometry.resizedWidth, geometry.resizedHeight);
    const rgba = context.getImageData(0, 0, inputWidth, inputHeight).data;
    fillBgrTensor(rgba, inputWidth, inputHeight, tensorData!);
    if (request.run !== previousRun) previousLuminance = null;
    const sampled = sampleLuminance(
      rgba,
      inputWidth,
      geometry.resizedWidth,
      geometry.resizedHeight,
      luminance ?? new Float32Array(QUALITY_GRID * QUALITY_GRID),
    );
    const quality = measureFrameQuality(sampled, previousLuminance);
    luminance = previousLuminance;
    previousLuminance = sampled;
    previousRun = request.run;
    const started = performance.now();
    const input = new ort.Tensor('float32', tensorData!, [1, 3, inputHeight, inputWidth]);
    const outputs = await session.run({ [manifest.input.name]: input });
    const ran = performance.now();
    const output = outputs[manifest.output.name];
    try {
      const detections = decodeYoloxOutput(output.data as Float32Array, output.dims, {
        ratio: geometry.ratio,
        frameWidth: request.width,
        frameHeight: request.height,
        ...postprocessOptions(manifest),
      });
      return {
        type: 'result',
        run: request.run,
        frameId: request.frameId,
        timestamp: request.timestamp,
        width: request.width,
        height: request.height,
        detections,
        preprocessMs: started - preprocessStarted,
        inferenceMs: ran - started,
        postprocessMs: performance.now() - ran,
        quality,
        inferenceProfile: manifest.inference_profile?.sha256 ?? null,
      };
    } finally {
      output.dispose();
    }
  } catch (reason) {
    return {
      type: 'frame-error',
      run: request.run,
      frameId: request.frameId,
      message: (reason as Error).message,
    };
  } finally {
    bitmap.close();
  }
}

async function dispose(): Promise<void> {
  const current = session;
  session = null;
  manifest = null;
  tensorData = null;
  canvas = null;
  context = null;
  luminance = null;
  previousLuminance = null;
  previousRun = -1;
  await current?.release().catch(() => undefined);
}

scope.onmessage = (event: MessageEvent<WorkerRequest>) => {
  const request = event.data;
  if (request.type === 'load')
    void load(request.manifest).then(
      (response) => scope.postMessage(response),
      (reason: Error) => scope.postMessage({ type: 'load-error', message: reason.message }),
    );
  else if (request.type === 'frame') void infer(request).then((r) => scope.postMessage(r));
  else void dispose().then(() => scope.close());
};
