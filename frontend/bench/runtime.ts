import { FULL_FRAME, type SlicingConfig } from '../src/domain/slicedInference';

const params = new URLSearchParams(location.search);
const result = document.getElementById('result')!;
const [frameWidth, frameHeight] = (params.get('frame') ?? '1920x1080').split('x').map(Number);

async function frame(): Promise<ImageBitmap> {
  const file = (document.getElementById('file') as HTMLInputElement).files?.[0];
  if (file) {
    const bitmap = await createImageBitmap(file, { imageOrientation: 'from-image' });
    if (bitmap.width === frameWidth && bitmap.height === frameHeight) return bitmap;
    // Reduz ao tamanho pedido (simula a resolução da câmera), como a sprint fez com INTER_AREA.
    const canvas = new OffscreenCanvas(frameWidth, frameHeight);
    const context = canvas.getContext('2d')!;
    context.imageSmoothingQuality = 'high';
    context.drawImage(bitmap, 0, 0, frameWidth, frameHeight);
    bitmap.close();
    return createImageBitmap(canvas);
  }
  const canvas = new OffscreenCanvas(frameWidth, frameHeight);
  const context = canvas.getContext('2d')!;
  const gradient = context.createLinearGradient(0, 0, frameWidth, frameHeight);
  gradient.addColorStop(0, '#555');
  gradient.addColorStop(1, '#999');
  context.fillStyle = gradient;
  context.fillRect(0, 0, frameWidth, frameHeight);
  context.strokeStyle = '#222';
  context.lineWidth = 3;
  for (let i = 0; i < 40; i++) {
    context.beginPath();
    context.moveTo((i * 97) % frameWidth, (i * 53) % frameHeight);
    context.lineTo(((i * 97) % frameWidth) + 80, ((i * 53) % frameHeight) + 30);
    context.stroke();
  }
  return createImageBitmap(canvas);
}

async function run(): Promise<void> {
  result.textContent = 'medindo…';
  const slicing: SlicingConfig = {
    ...FULL_FRAME,
    mode: (params.get('mode') as SlicingConfig['mode']) ?? 'hybrid',
  };
  const worker = new Worker(new URL('./runtime.worker.ts', import.meta.url), { type: 'module' });
  const bitmap = await frame();
  const started = performance.now();
  const response = await new Promise<unknown>((resolve) => {
    worker.onmessage = (event) => {
      if (event.data && 'progress' in event.data) {
        result.textContent += `\n${Math.round(performance.now() - started)} ms: ${event.data.progress}`;
        return;
      }
      resolve(event.data);
    };
    worker.onerror = (event) => resolve({ ok: false, error: event.message });
    worker.postMessage(
      {
        bitmap,
        configs: (params.get('configs') ?? 'wasm,webgpu,webgpu-io,webgpu-graph').split(','),
        runs: Number(params.get('runs') ?? 15),
        power: params.get('power') ?? '',
        gpuProfile: params.get('gpuProfile') === '1',
        slicing,
      },
      [bitmap],
    );
  });
  worker.terminate();
  const text = JSON.stringify(
    { wallMs: Math.round(performance.now() - started), ...(response as object) },
    null,
    1,
  );
  result.textContent = text;
  (window as unknown as { benchResult: unknown }).benchResult = JSON.parse(text);
  offerDownload(JSON.parse(text));
}

/**
 * Registro exportado pela própria bancada (sem transcrição manual): carimbo do navegador,
 * parâmetros da URL e a resposta do worker. Só vira arquivo se a pessoa clicar no link.
 */
function offerDownload(response: unknown): void {
  const measuredAt = new Date().toISOString();
  const record = {
    schema_version: 1,
    SOURCE: 'BENCH_AUTOMATIC_EXPORT',
    producer: 'frontend/bench/runtime.html',
    measured_at: measuredAt,
    params: Object.fromEntries(params.entries()),
    development_only: true,
    production_evidence: false,
    ...(response as object),
  };
  const blob = new Blob([JSON.stringify(record, null, 2) + '\n'], { type: 'application/json' });
  const link = document.getElementById('download') as HTMLAnchorElement;
  if (link.href.startsWith('blob:')) URL.revokeObjectURL(link.href);
  link.href = URL.createObjectURL(blob);
  link.download = `browser_runtime_${measuredAt.replace(/[:.]/g, '-')}.json`;
  link.hidden = false;
}

document.getElementById('run')!.addEventListener('click', () => void run());
if (params.get('auto') === '1') void run();
