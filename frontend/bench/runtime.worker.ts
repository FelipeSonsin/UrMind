/// <reference lib="webworker" />
/**
 * Bancada de runtime da detecção ao vivo (somente desenvolvimento; fora do build).
 * Mesmo ONNX, mesmo pré/pós-processamento do worker do produto, medido por etapa em
 * quatro configurações: WASM (1 thread), WebGPU padrão, WebGPU com entrada/saída na GPU
 * (IO binding) e WebGPU com graph capture. Mede também o custo de uma passada HYBRID.
 */
import * as ort from 'onnxruntime-web/webgpu';
import wasmUrl from 'onnxruntime-web/ort-wasm-simd-threaded.asyncify.wasm?url';
import {
  checkBrowserModelManifest,
  decodeYoloxOutput,
  fillBgrTensor,
  letterbox,
  postprocessOptions,
  verifyModelBytes,
  type BrowserModelManifest,
  type LiveDetection,
} from '../src/domain/liveDetection';
import {
  finalizeDetections,
  mergeViewDetections,
  offsetDetections,
  planViews,
  type SlicingConfig,
} from '../src/domain/slicedInference';

ort.env.wasm.wasmPaths = { wasm: wasmUrl };
ort.env.wasm.numThreads = 1;
ort.env.wasm.proxy = false;

type Config = 'wasm' | 'webgpu' | 'webgpu-io' | 'webgpu-graph';
interface Request {
  power: 'low-power' | 'high-performance' | '';
  gpuProfile: boolean;
  bitmap: ImageBitmap;
  configs: Config[];
  runs: number;
  slicing: SlicingConfig;
  /** Manifesto a medir (só `/models/*.json`); padrão = o publicado. */
  manifest: string;
}

const MANIFEST_PATH = /^\/models\/[A-Za-z0-9][A-Za-z0-9._-]*\.json$/;

const percentile = (values: number[], q: number) => {
  const sorted = [...values].sort((a, b) => a - b);
  return sorted.length ? sorted[Math.min(sorted.length - 1, Math.floor(q * sorted.length))] : NaN;
};
const summary = (values: number[]) => ({
  p50: +percentile(values, 0.5).toFixed(2),
  p95: +percentile(values, 0.95).toFixed(2),
  n: values.length,
});

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Gpu = any;

async function createSession(model: Uint8Array, config: Config, manifest: BrowserModelManifest) {
  const options: ort.InferenceSession.SessionOptions = {
    executionProviders: [config === 'wasm' ? 'wasm' : 'webgpu'],
    graphOptimizationLevel: 'all',
  };
  if (config === 'webgpu-io' || config === 'webgpu-graph')
    options.preferredOutputLocation = 'gpu-buffer';
  if (config === 'webgpu-graph') options.enableGraphCapture = true;
  const started = performance.now();
  const session = await ort.InferenceSession.create(model, options);
  return { session, createMs: performance.now() - started, inputName: manifest.input.name };
}

const progress = (text: string) => self.postMessage({ progress: text });

async function measure(
  request: Request,
  manifest: BrowserModelManifest,
  model: Uint8Array,
  config: Config,
) {
  progress(`${config}: criando sessão`);
  const [inputHeight, inputWidth] = manifest.input.size;
  const canvas = new OffscreenCanvas(inputWidth, inputHeight);
  const context = canvas.getContext('2d', { willReadFrequently: true })!;
  const tensorData = new Float32Array(3 * inputWidth * inputHeight);
  const { session, createMs } = await createSession(model, config, manifest);
  const gpuIo = config === 'webgpu-io' || config === 'webgpu-graph';
  let device: Gpu = null;
  let inputBuffer: Gpu = null;
  let outputBuffer: Gpu = null;
  let staging: Gpu = null;
  const outputDims = [1, 8400, 5 + manifest.class_names.length];
  const outputBytes = outputDims.reduce((a, b) => a * b, 1) * 4;
  if (gpuIo) {
    device = await ort.env.webgpu.device;
    const G = (self as Gpu).GPUBufferUsage;
    inputBuffer = device.createBuffer({
      size: tensorData.byteLength,
      usage: G.STORAGE | G.COPY_DST | G.COPY_SRC,
    });
    outputBuffer = device.createBuffer({
      size: outputBytes,
      usage: G.STORAGE | G.COPY_SRC | G.COPY_DST,
    });
    staging = device.createBuffer({ size: outputBytes, usage: G.MAP_READ | G.COPY_DST });
  }
  const inputTensor = gpuIo
    ? ort.Tensor.fromGpuBuffer(inputBuffer, {
        dataType: 'float32',
        dims: [1, 3, inputHeight, inputWidth],
      })
    : null;
  const outputTensor = gpuIo
    ? ort.Tensor.fromGpuBuffer(outputBuffer, { dataType: 'float32', dims: outputDims })
    : null;
  const options = postprocessOptions(manifest);
  const upscale = manifest.input.letterbox.upscale ?? true;

  async function view(bitmap: ImageBitmap, v: readonly [number, number, number, number]) {
    const t0 = performance.now();
    const w = v[2] - v[0];
    const h = v[3] - v[1];
    const geometry = letterbox(w, h, manifest.input.size, upscale);
    context.fillStyle = 'rgb(114, 114, 114)';
    context.fillRect(0, 0, inputWidth, inputHeight);
    context.imageSmoothingEnabled = true;
    context.imageSmoothingQuality = 'low';
    context.drawImage(
      bitmap,
      v[0],
      v[1],
      w,
      h,
      0,
      0,
      geometry.resizedWidth,
      geometry.resizedHeight,
    );
    fillBgrTensor(
      context.getImageData(0, 0, inputWidth, inputHeight).data,
      inputWidth,
      inputHeight,
      tensorData,
    );
    if (gpuIo) device.queue.writeBuffer(inputBuffer, 0, tensorData);
    const t1 = performance.now();
    let data: Float32Array;
    let dims: readonly number[];
    let dispose = () => {};
    if (gpuIo) {
      await session.run(
        { [manifest.input.name]: inputTensor! },
        { [manifest.output.name]: outputTensor! },
      );
      const t2run = performance.now();
      const encoder = device.createCommandEncoder();
      encoder.copyBufferToBuffer(outputBuffer, 0, staging, 0, outputBytes);
      device.queue.submit([encoder.finish()]);
      await staging.mapAsync((self as Gpu).GPUMapMode.READ);
      data = new Float32Array(staging.getMappedRange().slice(0));
      staging.unmap();
      dims = outputDims;
      const t3 = performance.now();
      const detections = offsetDetections(
        decodeYoloxOutput(data, dims, {
          ratio: geometry.ratio,
          frameWidth: w,
          frameHeight: h,
          ...options,
        }),
        v,
      );
      return {
        detections,
        preprocess: t1 - t0,
        run: t2run - t1,
        download: t3 - t2run,
        decode: performance.now() - t3,
      };
    }
    const input = new ort.Tensor('float32', tensorData, [1, 3, inputHeight, inputWidth]);
    const outputs = await session.run({ [manifest.input.name]: input });
    const t2 = performance.now();
    const output = outputs[manifest.output.name];
    data = output.data as Float32Array;
    dims = output.dims;
    dispose = () => output.dispose();
    const detections = offsetDetections(
      decodeYoloxOutput(data, dims, {
        ratio: geometry.ratio,
        frameWidth: w,
        frameHeight: h,
        ...options,
      }),
      v,
    );
    dispose();
    return {
      detections,
      preprocess: t1 - t0,
      run: t2 - t1,
      download: 0,
      decode: performance.now() - t2,
    };
  }

  const full: [number, number, number, number] = [
    0,
    0,
    request.bitmap.width,
    request.bitmap.height,
  ];
  progress(`${config}: sessão criada em ${createMs.toFixed(0)} ms; aquecendo`);
  for (let i = 0; i < 3; i++) await view(request.bitmap, full); // aquecimento (e captura do grafo)
  progress(`${config}: medindo quadro inteiro`);
  const stages = {
    preprocess: [] as number[],
    run: [] as number[],
    download: [] as number[],
    decode: [] as number[],
    total: [] as number[],
  };
  let fullDetections: LiveDetection[] = [];
  for (let i = 0; i < request.runs; i++) {
    const t = performance.now();
    const r = await view(request.bitmap, full);
    stages.total.push(performance.now() - t);
    stages.preprocess.push(r.preprocess);
    stages.run.push(r.run);
    stages.download.push(r.download);
    stages.decode.push(r.decode);
    fullDetections = r.detections;
  }
  const views = planViews(request.bitmap.width, request.bitmap.height, request.slicing);
  progress(`${config}: medindo passada ${request.slicing.mode} (${views.length} vistas)`);
  const passes: number[] = [];
  let hybrid: LiveDetection[] = [];
  for (let i = 0; i < Math.max(2, Math.ceil(request.runs / 5)); i++) {
    const t = performance.now();
    const perView: LiveDetection[][] = [];
    for (const v of views)
      perView.push(
        finalizeDetections(
          (await view(request.bitmap, v)).detections,
          manifest.postprocess.max_detections,
        ),
      );
    hybrid = finalizeDetections(
      mergeViewDetections(perView, request.slicing.merge, request.slicing.merge_threshold),
      manifest.postprocess.max_detections,
    );
    passes.push(performance.now() - t);
  }
  inputBuffer?.destroy?.();
  outputBuffer?.destroy?.();
  staging?.destroy?.();
  await session.release();
  return {
    config,
    createMs: +createMs.toFixed(1),
    frame: [request.bitmap.width, request.bitmap.height],
    stagesMs: Object.fromEntries(Object.entries(stages).map(([k, v]) => [k, summary(v)])),
    fullDetections: fullDetections.length,
    fullSample: fullDetections
      .slice(0, 5)
      .map((d) => [
        d.className,
        +d.score.toFixed(4),
        Math.round(d.x_min),
        Math.round(d.y_min),
        Math.round(d.x_max),
        Math.round(d.y_max),
      ]),
    slicedPass: {
      mode: request.slicing.mode,
      views: views.length,
      ms: summary(passes),
      detections: hybrid.length,
    },
  };
}

self.onmessage = async (event: MessageEvent<Request>) => {
  const request = event.data;
  const results: unknown[] = [];
  const kernels = new Map<string, { ms: number; n: number }>();
  if (request.power) ort.env.webgpu.powerPreference = request.power;
  if (request.gpuProfile) {
    ort.env.webgpu.profiling = {
      mode: 'default',
      ondata: (d: Gpu) => {
        const key = d.kernelType as string;
        const item = kernels.get(key) ?? { ms: 0, n: 0 };
        item.ms += (Number(d.endTime) - Number(d.startTime)) / 1e6;
        item.n += 1;
        kernels.set(key, item);
      },
    };
  }
  try {
    if (!MANIFEST_PATH.test(request.manifest)) throw new Error('manifesto fora de /models/');
    const raw = await (await fetch(request.manifest, { cache: 'no-cache' })).json();
    const checked = checkBrowserModelManifest(raw);
    if (!checked.ok) throw new Error(checked.reason);
    const manifest = checked.manifest;
    const bytes = await (await fetch(manifest.onnx.path, { cache: 'no-cache' })).arrayBuffer();
    await verifyModelBytes(bytes, manifest.onnx);
    const model = new Uint8Array(bytes);
    const adapter = await (navigator as Gpu).gpu
      ?.requestAdapter?.(request.power ? { powerPreference: request.power } : undefined)
      .catch(() => null);
    const info = adapter
      ? (adapter.info ?? (await adapter.requestAdapterInfo?.().catch(() => null)))
      : null;
    for (const config of request.configs) {
      try {
        results.push(await measure(request, manifest, model, config));
      } catch (reason) {
        results.push({ config, error: (reason as Error).message });
      }
    }
    self.postMessage({
      ok: true,
      userAgent: navigator.userAgent,
      hardwareConcurrency: navigator.hardwareConcurrency,
      webgpuAdapter: info
        ? { vendor: info.vendor, architecture: info.architecture, description: info.description }
        : null,
      ortVersion: ort.env.versions,
      power: request.power || 'default',
      kernelsTopMs: [...kernels.entries()]
        .sort((a, b) => b[1].ms - a[1].ms)
        .slice(0, 12)
        .map(([k, v]) => [k, +v.ms.toFixed(1), v.n]),
      results,
    });
  } catch (reason) {
    self.postMessage({ ok: false, error: (reason as Error).message, results });
  } finally {
    request.bitmap.close();
  }
};
