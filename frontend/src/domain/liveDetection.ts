import { z } from 'zod';

/**
 * Prévia de detecção no navegador. Reproduz o contrato canônico de serving
 * (backend/app/ml/serving.py: `_preprocess` + `OnnxDetector.postprocess`), sem
 * inventar outro: YOLOX `preproc` (BGR, 0..255, letterbox 114 no canto superior
 * esquerdo), ONNX exportado com `decode_in_inference=True` → `[1, N, 5 + C]` com
 * cx,cy,w,h em pixels da entrada, score = objectness × classe e NMS de
 * `multiclass_nms`: agnóstico de classe (class_agnostic=True) ou por classe
 * (`multiclass_nms_class_aware`, com limiar de confiança próprio por classe,
 * calibrado em VALIDATION). Qualquer outro contrato é recusado.
 *
 * O resultado é uma prévia não confiável para o servidor: nunca vira Detection nem Event.
 */

/** Único endereço aceito. O cliente nunca escolhe a URL do manifesto nem do modelo. */
export const BROWSER_MODEL_MANIFEST_PATH = '/models/live-detection.json';
const MODEL_PATH = /^\/models\/[A-Za-z0-9][A-Za-z0-9._-]*\.onnx$/;

const sha256 = z.string().regex(/^[0-9a-f]{64}$/);

export const browserModelManifestSchema = z.object({
  schema_version: z.literal(1),
  model_id: z.string().min(1),
  model_version: z.string().min(1),
  scientific_status: z.enum(['APPROVED', 'EXPERIMENTAL', 'DEMONSTRATION', 'REJECTED']),
  use_authorized: z.boolean(),
  distribution_authorized: z.boolean(),
  authorization_ref: z.string().min(1),
  onnx: z.object({
    path: z.string().regex(MODEL_PATH),
    sha256,
    size_bytes: z.number().int().positive(),
  }),
  input: z.object({
    name: z.string().min(1),
    size: z.tuple([z.number().int().positive(), z.number().int().positive()]),
    layout: z.literal('NCHW'),
    dtype: z.literal('float32'),
    color: z.literal('BGR'),
    range: z.literal('0..255'),
    normalization: z.literal('none'),
    letterbox: z.object({
      pad_value: z.literal(114),
      anchor: z.literal('top-left'),
      // false: imagem menor que a entrada não é ampliada (fica em escala 1, com padding).
      // Ausente = true, o `preproc` original do YOLOX.
      upscale: z.boolean().optional(),
    }),
  }),
  output: z.object({
    name: z.string().min(1),
    format: z.literal('yolox_decoded_cxcywh_obj_cls'),
  }),
  class_names: z.array(z.string().min(1)).min(1),
  postprocess: z.object({
    score_threshold: z.number().gt(0).lte(1),
    nms_threshold: z.number().gt(0).lte(1),
    nms: z.enum(['class_agnostic', 'per_class']),
    max_detections: z.number().int().positive(),
    // Um limiar por classe, na ordem de `class_names`; ausente = `score_threshold` para todas.
    class_score_thresholds: z.array(z.number().gt(0).lte(1)).optional(),
  }),
  // Produzido por `python -m app.ml.browser_model` a partir do registro de export.
  provenance: z.object({
    registration_manifest_sha256: sha256,
    closure_manifest_sha256: sha256.nullable(),
    contract_sha256: sha256,
  }),
  // Identidade da inferência inteira (ONNX + classes + entrada + saída + pós-processamento).
  // Mesmo ONNX com outro limiar ou NMS é outro perfil.
  inference_profile: z.object({ sha256, calibration_sha256: sha256.nullable() }).optional(),
});
export type BrowserModelManifest = z.infer<typeof browserModelManifestSchema>;

export type ManifestCheck =
  { ok: true; manifest: BrowserModelManifest } | { ok: false; reason: string };

/** Fail-closed: manifesto ausente, malformado ou sem autorização → sem modelo. */
export function checkBrowserModelManifest(raw: unknown): ManifestCheck {
  const parsed = browserModelManifestSchema.safeParse(raw);
  if (!parsed.success)
    return { ok: false, reason: 'O manifesto do modelo não segue o contrato suportado.' };
  const manifest = parsed.data;
  if (manifest.scientific_status === 'REJECTED')
    return { ok: false, reason: 'O modelo publicado foi rejeitado e não pode ser usado.' };
  if (manifest.scientific_status === 'APPROVED' && !manifest.provenance.closure_manifest_sha256)
    return { ok: false, reason: 'Modelo declarado aprovado sem o fechamento oficial.' };
  if (!manifest.use_authorized)
    return { ok: false, reason: 'O uso deste modelo na prévia não foi autorizado.' };
  if (!manifest.distribution_authorized)
    return { ok: false, reason: 'A distribuição deste modelo ao navegador não foi autorizada.' };
  if (new Set(manifest.class_names).size !== manifest.class_names.length)
    return { ok: false, reason: 'O manifesto repete classes.' };
  const perClass = manifest.postprocess.class_score_thresholds;
  if (perClass && perClass.length !== manifest.class_names.length)
    return { ok: false, reason: 'Limiares por classe não correspondem às classes do modelo.' };
  return { ok: true, manifest };
}

export class ModelContractError extends Error {}

export function toHex(buffer: ArrayBuffer): string {
  return Array.from(new Uint8Array(buffer), (byte) => byte.toString(16).padStart(2, '0')).join('');
}

/** Bytes do modelo conferidos por tamanho e SHA-256 antes de criar a sessão. */
export async function verifyModelBytes(
  bytes: ArrayBuffer,
  expected: BrowserModelManifest['onnx'],
): Promise<void> {
  if (bytes.byteLength !== expected.size_bytes)
    throw new ModelContractError(
      `Download do modelo incompleto ou divergente (${bytes.byteLength} de ${expected.size_bytes} bytes).`,
    );
  const digest = toHex(await crypto.subtle.digest('SHA-256', bytes));
  if (digest !== expected.sha256)
    throw new ModelContractError('O modelo baixado não confere com o checksum registrado.');
}

export interface Letterbox {
  ratio: number;
  resizedWidth: number;
  resizedHeight: number;
}

/**
 * Mesmo cálculo do YOLOX `preproc`: `int()` trunca, a imagem fica no canto superior
 * esquerdo. Com `upscale = false` a razão nunca passa de 1: imagem pequena não é esticada.
 */
export function letterbox(
  frameWidth: number,
  frameHeight: number,
  inputSize: readonly [number, number],
  upscale = true,
): Letterbox {
  const [inputHeight, inputWidth] = inputSize;
  const fit = Math.min(inputHeight / frameHeight, inputWidth / frameWidth);
  const ratio = upscale ? fit : Math.min(1, fit);
  return {
    ratio,
    resizedWidth: Math.floor(frameWidth * ratio),
    resizedHeight: Math.floor(frameHeight * ratio),
  };
}

/** RGBA do canvas letterbox → tensor CHW na ordem B, G, R, valores 0..255, sem normalização. */
export function fillBgrTensor(
  rgba: Uint8ClampedArray,
  width: number,
  height: number,
  out: Float32Array,
): Float32Array {
  const plane = width * height;
  if (rgba.length !== plane * 4 || out.length !== plane * 3)
    throw new ModelContractError('Buffer de pré-processamento com tamanho inesperado.');
  for (let i = 0, p = 0; i < plane; i++, p += 4) {
    out[i] = rgba[p + 2];
    out[plane + i] = rgba[p + 1];
    out[2 * plane + i] = rgba[p];
  }
  return out;
}

/** Caixa alinhada aos eixos, em pixels do quadro original. Sem ângulo: o modelo não o fornece. */
export interface LiveDetection {
  classIndex: number;
  className: string;
  score: number;
  x_min: number;
  y_min: number;
  x_max: number;
  y_max: number;
}

/** Igual a `yolox.utils.demo_utils.nms` (inclusive o +1 de área). */
function nms(boxes: Float64Array[], scores: number[], threshold: number): number[] {
  const order = scores.map((_, i) => i).sort((a, b) => scores[b] - scores[a]);
  const area = boxes.map((b) => (b[2] - b[0] + 1) * (b[3] - b[1] + 1));
  const keep: number[] = [];
  let remaining = order;
  while (remaining.length) {
    const [i, ...rest] = remaining;
    keep.push(i);
    remaining = rest.filter((j) => {
      const w = Math.max(
        0,
        Math.min(boxes[i][2], boxes[j][2]) - Math.max(boxes[i][0], boxes[j][0]) + 1,
      );
      const h = Math.max(
        0,
        Math.min(boxes[i][3], boxes[j][3]) - Math.max(boxes[i][1], boxes[j][1]) + 1,
      );
      const inter = w * h;
      return inter / (area[i] + area[j] - inter) <= threshold;
    });
  }
  return keep;
}

export interface DecodeOptions {
  ratio: number;
  frameWidth: number;
  frameHeight: number;
  classNames: readonly string[];
  /** Um limiar por classe, na ordem de `classNames`. */
  scoreThresholds: readonly number[];
  nmsThreshold: number;
  nms: BrowserModelManifest['postprocess']['nms'];
}

/** Opções de pós-processamento do manifesto; o limiar global vale para classe sem limiar próprio. */
export function postprocessOptions(
  manifest: BrowserModelManifest,
): Pick<DecodeOptions, 'classNames' | 'scoreThresholds' | 'nmsThreshold' | 'nms'> {
  const { postprocess } = manifest;
  return {
    classNames: manifest.class_names,
    scoreThresholds:
      postprocess.class_score_thresholds ??
      manifest.class_names.map(() => postprocess.score_threshold),
    nmsThreshold: postprocess.nms_threshold,
    nms: postprocess.nms,
  };
}

/**
 * Pós-processamento único (o ONNX já decodifica as grades; aqui não há decode de
 * âncoras). Espelha `OnnxDetector.postprocess`: xyxy/ratio → NMS → recorte ao quadro.
 * Agnóstico: cada linha concorre só com a melhor classe. Por classe: cada classe
 * acima do próprio limiar vira candidata e o NMS roda dentro de cada classe.
 */
export function decodeYoloxOutput(
  output: ArrayLike<number>,
  dims: readonly number[],
  options: DecodeOptions,
): LiveDetection[] {
  const classCount = options.classNames.length;
  if (dims.length !== 3 || dims[0] !== 1)
    throw new ModelContractError(`Saída do modelo com formato inesperado [${dims.join(', ')}].`);
  const [, rows, stride] = dims;
  if (stride !== 5 + classCount)
    throw new ModelContractError(
      `O modelo emite ${stride - 5} classes, mas o manifesto declara ${classCount}: classe desconhecida.`,
    );
  if (output.length !== rows * stride)
    throw new ModelContractError('Saída do modelo com tamanho incoerente.');
  if (options.scoreThresholds.length !== classCount)
    throw new ModelContractError('Limiares por classe não correspondem às classes do modelo.');
  const perClass = options.nms === 'per_class';
  const boxes: Float64Array[] = [];
  const scores: number[] = [];
  const classes: number[] = [];
  const push = (o: number, c: number, score: number) => {
    const cx = output[o];
    const cy = output[o + 1];
    const w = output[o + 2];
    const h = output[o + 3];
    boxes.push(
      Float64Array.of(
        (cx - w / 2) / options.ratio,
        (cy - h / 2) / options.ratio,
        (cx + w / 2) / options.ratio,
        (cy + h / 2) / options.ratio,
      ),
    );
    scores.push(score);
    classes.push(c);
  };
  for (let r = 0; r < rows; r++) {
    const o = r * stride;
    const objectness = output[o + 4];
    if (perClass) {
      for (let c = 0; c < classCount; c++) {
        const score = objectness * output[o + 5 + c];
        if (score > options.scoreThresholds[c]) push(o, c, score);
      }
      continue;
    }
    let best = 0;
    let bestScore = -Infinity;
    for (let c = 0; c < classCount; c++) {
      const score = objectness * output[o + 5 + c];
      if (score > bestScore) {
        bestScore = score;
        best = c;
      }
    }
    if (bestScore > options.scoreThresholds[best]) push(o, best, bestScore);
  }
  let kept: number[];
  if (perClass) {
    kept = [];
    for (let c = 0; c < classCount; c++) {
      const members = classes.flatMap((value, i) => (value === c ? [i] : []));
      const local = nms(
        members.map((i) => boxes[i]),
        members.map((i) => scores[i]),
        options.nmsThreshold,
      );
      kept.push(...local.map((k) => members[k]));
    }
  } else {
    kept = nms(boxes, scores, options.nmsThreshold);
  }
  const detections: LiveDetection[] = [];
  for (const i of kept) {
    const x0 = Math.max(0, boxes[i][0]);
    const y0 = Math.max(0, boxes[i][1]);
    const x1 = Math.min(options.frameWidth, boxes[i][2]);
    const y1 = Math.min(options.frameHeight, boxes[i][3]);
    if (x1 <= x0 || y1 <= y0) continue;
    detections.push({
      classIndex: classes[i],
      className: options.classNames[classes[i]],
      score: scores[i],
      x_min: x0,
      y_min: y0,
      x_max: x1,
      y_max: y1,
    });
  }
  if (perClass) detections.sort((a, b) => b.score - a.score);
  return detections;
}

export interface DisplayBox {
  left: number;
  top: number;
  width: number;
  height: number;
}

/** Onde `object-fit: contain` desenha o quadro dentro da caixa CSS (faixas centralizadas). */
export function containRect(
  frame: { width: number; height: number },
  display: { width: number; height: number },
): DisplayBox & { scale: number } {
  const scale = Math.min(display.width / frame.width, display.height / frame.height);
  const width = frame.width * scale;
  const height = frame.height * scale;
  return {
    scale,
    left: (display.width - width) / 2,
    top: (display.height - height) / 2,
    width,
    height,
  };
}

/**
 * Quadro e overlay ocupam a mesma caixa CSS; a caixa usa a mesma transformação do
 * `object-fit: contain` do quadro. Com espelhamento (câmera frontal) só a posição
 * horizontal inverte; o texto continua legível e a foto registrada não é espelhada.
 */
export function toDisplayBox(
  detection: Pick<LiveDetection, 'x_min' | 'y_min' | 'x_max' | 'y_max'>,
  frame: { width: number; height: number },
  display: { width: number; height: number },
  mirrored: boolean,
): DisplayBox {
  const fit = containRect(frame, display);
  const x0 = mirrored ? frame.width - detection.x_max : detection.x_min;
  return {
    left: fit.left + x0 * fit.scale,
    top: fit.top + detection.y_min * fit.scale,
    width: (detection.x_max - detection.x_min) * fit.scale,
    height: (detection.y_max - detection.y_min) * fit.scale,
  };
}

/** Backing store do overlay em pixels físicos; o desenho continua em pixels CSS. */
export function overlaySize(cssWidth: number, cssHeight: number, devicePixelRatio: number) {
  const dpr = Number.isFinite(devicePixelRatio) && devicePixelRatio > 0 ? devicePixelRatio : 1;
  return { width: Math.round(cssWidth * dpr), height: Math.round(cssHeight * dpr), dpr };
}

const CLASS_COLORS = ['#d0ed9e', '#f5b83d', '#6cc4e0', '#ff8f70', '#c4a7ff', '#e8d8a8'];
/** Cor pela posição da classe no artefato: estável entre quadros e sessões. */
export function classColor(classIndex: number): string {
  return CLASS_COLORS[classIndex % CLASS_COLORS.length];
}

/**
 * No máximo uma inferência em voo; nada fica enfileirado. Respostas de outra
 * execução (pausa, troca de câmera, saída da rota) ou de outro quadro são descartadas.
 */
export class InferenceGate {
  private run = 0;
  private inFlight: { run: number; frameId: number; sentAt: number } | null = null;
  private nextFrameId = 0;

  get busy(): boolean {
    return this.inFlight !== null;
  }
  get currentRun(): number {
    return this.run;
  }
  /** Invalida tudo que estiver pendente. */
  cancel(): void {
    this.run += 1;
    this.inFlight = null;
  }
  begin(now: number): { run: number; frameId: number } | null {
    if (this.inFlight) return null;
    const ticket = { run: this.run, frameId: ++this.nextFrameId, sentAt: now };
    this.inFlight = ticket;
    return { run: ticket.run, frameId: ticket.frameId };
  }
  /** Aceita somente a resposta do quadro em voo da execução atual. */
  accept(run: number, frameId: number): boolean {
    const current = this.inFlight;
    if (!current || current.run !== run || current.frameId !== frameId || run !== this.run)
      return false;
    this.inFlight = null;
    return true;
  }
  /** Libera o quadro em voo após erro, sem aceitar resultado. */
  fail(run: number, frameId: number): void {
    if (this.inFlight?.run === run && this.inFlight.frameId === frameId) this.inFlight = null;
  }
  inFlightSince(): number | null {
    return this.inFlight?.sentAt ?? null;
  }
}

/** Espera até o próximo envio: respeita o teto de análises/s e deixa folga à máquina. */
export function nextInferenceDelay(
  lastLatencyMs: number,
  elapsedSinceSendMs: number,
  minIntervalMs: number,
): number {
  return Math.max(minIntervalMs - elapsedSinceSendMs, lastLatencyMs * 0.25, 0);
}

/** Taxa medida (eventos/s) numa janela móvel; nunca um valor declarado. */
export class RateMeter {
  private stamps: number[] = [];
  constructor(private readonly windowMs = 2000) {}
  tick(now: number): void {
    this.stamps.push(now);
    this.trim(now);
  }
  rate(now: number): number | null {
    this.trim(now);
    if (this.stamps.length < 2) return null;
    const span = now - this.stamps[0];
    return span > 0 ? ((this.stamps.length - 1) * 1000) / span : null;
  }
  reset(): void {
    this.stamps = [];
  }
  get size(): number {
    return this.stamps.length;
  }
  private trim(now: number): void {
    while (this.stamps.length && now - this.stamps[0] > this.windowMs) this.stamps.shift();
  }
}

/** Latência das últimas análises: p50/p95 medidos, não prometidos. */
export class LatencyStats {
  private samples: number[] = [];
  constructor(private readonly size = 60) {}
  add(ms: number): void {
    this.samples.push(ms);
    if (this.samples.length > this.size) this.samples.shift();
  }
  percentile(p: number): number | null {
    if (!this.samples.length) return null;
    const sorted = [...this.samples].sort((a, b) => a - b);
    return sorted[
      Math.min(sorted.length - 1, Math.max(0, Math.ceil((p / 100) * sorted.length) - 1))
    ];
  }
  reset(): void {
    this.samples = [];
  }
}

/**
 * Camada temporal de apresentação. Associa a detecção de um quadro à do quadro anterior
 * (mesma classe, IoU mínimo, dentro de um intervalo de tempo) e marca se ela é
 * momentânea ou persistente. Não filtra, não confirma e não cria nada: persistência
 * não é confirmação (uma sombra também persiste). As detecções brutas continuam intactas.
 */
export type TrackState = 'provisional' | 'persistent';
export interface TrackedDetection extends LiveDetection {
  trackId: number;
  state: TrackState;
  hits: number;
  /** Tempo desde a primeira vez em que esta observação apareceu. */
  ageMs: number;
}
export interface TrackingOptions {
  /** IoU mínimo para considerar a mesma observação entre quadros. */
  minIou: number;
  /** Sem reaparecer nesse intervalo, a observação expira. */
  maxGapMs: number;
  /** Observações para passar de momentânea a persistente. */
  persistentHits: number;
}
export const DEFAULT_TRACKING: TrackingOptions = { minIou: 0.3, maxGapMs: 1500, persistentHits: 2 };

interface Track {
  id: number;
  classIndex: number;
  box: [number, number, number, number];
  hits: number;
  firstSeen: number;
  lastSeen: number;
}

function boxIou(a: readonly number[], b: readonly number[]): number {
  const w = Math.max(0, Math.min(a[2], b[2]) - Math.max(a[0], b[0]));
  const h = Math.max(0, Math.min(a[3], b[3]) - Math.max(a[1], b[1]));
  const inter = w * h;
  const union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter;
  return union > 0 ? inter / union : 0;
}

export class TemporalTracker {
  private tracks: Track[] = [];
  private nextId = 1;
  constructor(private readonly options: TrackingOptions = DEFAULT_TRACKING) {}

  /** Encerra tudo: troca de câmera, de cena, pausa, saída da rota, logout. */
  reset(): void {
    this.tracks = [];
  }

  get size(): number {
    return this.tracks.length;
  }

  update(detections: readonly LiveDetection[], timestamp: number): TrackedDetection[] {
    const { minIou, maxGapMs, persistentHits } = this.options;
    // Relógio voltando ou intervalo longo: nada do passado vale para este quadro.
    this.tracks = this.tracks.filter(
      (track) => timestamp >= track.lastSeen && timestamp - track.lastSeen <= maxGapMs,
    );
    const used = new Set<number>();
    const order = detections
      .map((_, i) => i)
      .sort((a, b) => detections[b].score - detections[a].score);
    const result: TrackedDetection[] = new Array(detections.length);
    for (const index of order) {
      const detection = detections[index];
      const box: [number, number, number, number] = [
        detection.x_min,
        detection.y_min,
        detection.x_max,
        detection.y_max,
      ];
      let best: Track | null = null;
      let bestIou = minIou;
      for (const track of this.tracks) {
        if (used.has(track.id) || track.classIndex !== detection.classIndex) continue;
        const overlap = boxIou(track.box, box);
        if (overlap >= bestIou) {
          best = track;
          bestIou = overlap;
        }
      }
      if (best) {
        best.box = box;
        best.hits += 1;
        best.lastSeen = timestamp;
      } else {
        best = {
          id: this.nextId++,
          classIndex: detection.classIndex,
          box,
          hits: 1,
          firstSeen: timestamp,
          lastSeen: timestamp,
        };
        this.tracks.push(best);
      }
      used.add(best.id);
      result[index] = {
        ...detection,
        trackId: best.id,
        state: best.hits >= persistentHits ? 'persistent' : 'provisional',
        hits: best.hits,
        ageMs: timestamp - best.firstSeen,
      };
    }
    return result;
  }
}

/**
 * Indicadores heurísticos da captura, medidos numa grade reduzida da imagem já
 * redimensionada para o modelo. Não são rótulos científicos nem descartam o quadro:
 * só orientam a pessoa. Limites conferidos em imagens de VALIDATION (originais × versões
 * borradas, escurecidas e deslocadas; ver docs/LIVE_DETECTION.md).
 */
export interface FrameQuality {
  /** Luminância média 0..255. */
  brightness: number;
  /** Variância do Laplaciano: baixa = pouco detalhe (desfoque). */
  sharpness: number;
  /** Diferença média para o quadro analisado anterior; null no primeiro quadro. */
  motion: number | null;
}
export const QUALITY_GRID = 160;
export const QUALITY_LIMITS = {
  dark: 60,
  bright: 225,
  blurry: 150,
  motion: 30,
  sceneChange: 45,
};

/** Luminância numa grade QUALITY_GRID² (vizinho mais próximo) da região útil do letterbox. */
export function sampleLuminance(
  rgba: Uint8ClampedArray,
  stride: number,
  regionWidth: number,
  regionHeight: number,
  out: Float32Array = new Float32Array(QUALITY_GRID * QUALITY_GRID),
): Float32Array {
  const grid = QUALITY_GRID;
  for (let gy = 0; gy < grid; gy++) {
    const y = Math.floor((gy * regionHeight) / grid);
    for (let gx = 0; gx < grid; gx++) {
      const p = (y * stride + Math.floor((gx * regionWidth) / grid)) * 4;
      out[gy * grid + gx] = 0.299 * rgba[p] + 0.587 * rgba[p + 1] + 0.114 * rgba[p + 2];
    }
  }
  return out;
}

export function measureFrameQuality(
  luminance: Float32Array,
  previous: Float32Array | null,
): FrameQuality {
  const grid = QUALITY_GRID;
  let sum = 0;
  let motion = 0;
  for (let i = 0; i < luminance.length; i++) {
    sum += luminance[i];
    if (previous) motion += Math.abs(luminance[i] - previous[i]);
  }
  let lapSum = 0;
  let lapSq = 0;
  let count = 0;
  for (let y = 1; y < grid - 1; y++)
    for (let x = 1; x < grid - 1; x++) {
      const i = y * grid + x;
      const lap =
        4 * luminance[i] -
        luminance[i - grid] -
        luminance[i + grid] -
        luminance[i - 1] -
        luminance[i + 1];
      lapSum += lap;
      lapSq += lap * lap;
      count++;
    }
  const mean = lapSum / count;
  return {
    brightness: sum / luminance.length,
    sharpness: lapSq / count - mean * mean,
    motion: previous ? motion / luminance.length : null,
  };
}

export type QualityHint = 'dark' | 'bright' | 'blurry' | 'motion' | 'low_resolution';
const MIN_USEFUL_SIDE = 360;

export const qualityHintText: Record<QualityHint, string> = {
  dark: 'Pouca luz: aproxime-se de uma área iluminada.',
  bright: 'Imagem clara demais: evite sol direto na lente.',
  blurry: 'Imagem possivelmente desfocada: segure firme e toque para focar.',
  motion: 'Movimento excessivo: pare a câmera por um instante.',
  low_resolution: 'Resolução baixa para o modelo: escolha outra câmera, se houver.',
};

export function qualityHints(
  quality: FrameQuality,
  frameWidth: number,
  frameHeight: number,
): QualityHint[] {
  const hints: QualityHint[] = [];
  if (quality.brightness < QUALITY_LIMITS.dark) hints.push('dark');
  else if (quality.brightness > QUALITY_LIMITS.bright) hints.push('bright');
  // Em imagem escura o Laplaciano cai por falta de luz, não por foco: só avisa desfoque com luz útil.
  else if (quality.sharpness < QUALITY_LIMITS.blurry) hints.push('blurry');
  if (quality.motion != null && quality.motion > QUALITY_LIMITS.motion) hints.push('motion');
  if (Math.min(frameWidth, frameHeight) < MIN_USEFUL_SIDE) hints.push('low_resolution');
  return hints;
}

/** Mudança brusca de cena: associações anteriores não valem para o quadro novo. */
export function isSceneChange(quality: FrameQuality): boolean {
  return quality.motion != null && quality.motion > QUALITY_LIMITS.sceneChange;
}

export type CameraErrorCode =
  | 'insecure_context'
  | 'unsupported'
  | 'permission_denied'
  | 'not_found'
  | 'busy'
  | 'constraints'
  | 'disconnected'
  | 'unknown';

export function describeCameraError(
  reason: unknown,
  environment: { secure: boolean; supported: boolean },
): { code: CameraErrorCode; message: string } {
  if (!environment.secure)
    return {
      code: 'insecure_context',
      message: 'A câmera só abre em HTTPS ou em localhost. Abra o UrMind por um endereço seguro.',
    };
  if (!environment.supported)
    return {
      code: 'unsupported',
      message:
        'Este navegador não oferece acesso à câmera. Use Chrome, Edge, Firefox ou Safari atuais.',
    };
  const name = (reason as { name?: string } | null)?.name ?? '';
  switch (name) {
    case 'NotAllowedError':
    case 'PermissionDeniedError':
      return {
        code: 'permission_denied',
        message:
          'O acesso à câmera foi negado. Libere a câmera nas permissões do site e tente de novo.',
      };
    case 'NotFoundError':
    case 'DevicesNotFoundError':
      return { code: 'not_found', message: 'Nenhuma câmera foi encontrada neste dispositivo.' };
    case 'NotReadableError':
    case 'TrackStartError':
    case 'AbortError':
      return {
        code: 'busy',
        message: 'A câmera está em uso por outro aplicativo ou aba. Feche-o e tente de novo.',
      };
    case 'OverconstrainedError':
    case 'ConstraintNotSatisfiedError':
      return {
        code: 'constraints',
        message: 'A câmera escolhida não atende a esta configuração. Selecione outra câmera.',
      };
    case 'SecurityError':
      return {
        code: 'insecure_context',
        message: 'O navegador bloqueou a câmera nesta página por política de segurança.',
      };
    default:
      return { code: 'unknown', message: 'Não foi possível abrir a câmera.' };
  }
}
