import { z } from 'zod';
import type { LiveDetection } from './liveDetection';

/**
 * Inferência fatiada do mesmo ONNX: FULL, TILED e HYBRID. Espelho exato de
 * `backend/app/ml/sliced_inference.py` (mesma grade, mesma regra de fusão), para que
 * navegador e Worker não virem dois sistemas científicos diferentes.
 *
 * - `full`: o quadro inteiro por letterbox (comportamento histórico);
 * - `tiled`: só recortes `tile_size`² com sobreposição, na escala nativa (sem ampliar);
 * - `hybrid`: vista global + recortes; a global mantém contexto e objetos grandes.
 *
 * Cada vista passa pelo pós-processamento do perfil (`decodeYoloxOutput`) no próprio
 * sistema de coordenadas; a fusão só decide conflitos **entre vistas diferentes** e
 * nunca entre classes diferentes. Uma única vista sai intacta.
 *
 * Estado (sprint visual de 26/09/2026): TILED/HYBRID foram medidos e REJEITADOS para o
 * ONNX `d429bde8…` (mais recall, muito mais alarmes falsos; ver
 * `docs/ml/VISUAL_SPRINT_2026-09-26.md`). O produto não importa este módulo; ele fica
 * como espelho testado para a bancada (`frontend/bench/`) e para reavaliar com outros pesos.
 */

export const slicingSchema = z.object({
  mode: z.enum(['full', 'tiled', 'hybrid']),
  tile_size: z.number().int().min(64).max(4096),
  overlap: z.number().min(0).max(0.5),
  merge: z.enum(['nms', 'nms_ios', 'nmm', 'wbf']),
  merge_threshold: z.number().gt(0).lte(1),
});
export type SlicingConfig = z.infer<typeof slicingSchema>;
export type MergeMethod = SlicingConfig['merge'];

/** Sem fatiamento: exatamente o perfil histórico. */
export const FULL_FRAME: SlicingConfig = {
  mode: 'full',
  tile_size: 640,
  overlap: 0.2,
  merge: 'nms',
  merge_threshold: 0.5,
};

/** Recorte `[x0, y0, x1, y1]` em pixels do quadro, fim exclusivo. */
export type View = readonly [number, number, number, number];

function starts(length: number, tile: number, stride: number): number[] {
  if (length <= tile) return [0];
  const result: number[] = [];
  for (let s = 0; s < length - tile; s += stride) result.push(s);
  result.push(length - tile); // último recorte encostado na borda
  return result;
}

/** Recortes que cobrem o quadro inteiro; vazio se o quadro cabe num tile. */
export function tileGrid(width: number, height: number, tileSize: number, overlap: number): View[] {
  if (width <= tileSize && height <= tileSize) return [];
  const stride = Math.max(1, Math.floor(tileSize * (1 - overlap) + 0.5));
  const views: View[] = [];
  for (const y of starts(height, tileSize, stride))
    for (const x of starts(width, tileSize, stride))
      views.push([x, y, Math.min(x + tileSize, width), Math.min(y + tileSize, height)]);
  return views;
}

/** Vistas a inferir, a global primeiro. Imagem pequena: só a global, em todo modo. */
export function planViews(width: number, height: number, config: SlicingConfig): View[] {
  const full: View = [0, 0, width, height];
  if (config.mode === 'full') return [full];
  const tiles = tileGrid(width, height, config.tile_size, config.overlap);
  if (!tiles.length) return [full];
  return config.mode === 'tiled' ? tiles : [full, ...tiles];
}

export function offsetDetections(
  detections: readonly LiveDetection[],
  view: View,
): LiveDetection[] {
  return detections.map((d) => ({
    ...d,
    x_min: d.x_min + view[0],
    y_min: d.y_min + view[1],
    x_max: d.x_max + view[0],
    y_max: d.y_max + view[1],
  }));
}

type Rect = [number, number, number, number];
const area = (b: Rect) => Math.max(0, b[2] - b[0]) * Math.max(0, b[3] - b[1]);

function overlap(a: Rect, b: Rect, metric: 'iou' | 'ios'): number {
  const w = Math.min(a[2], b[2]) - Math.max(a[0], b[0]);
  const h = Math.min(a[3], b[3]) - Math.max(a[1], b[1]);
  if (w <= 0 || h <= 0) return 0;
  const inter = w * h;
  if (metric === 'ios') {
    const smaller = Math.min(area(a), area(b));
    return smaller > 0 ? inter / smaller : 0;
  }
  const union = area(a) + area(b) - inter;
  return union > 0 ? inter / union : 0;
}

interface Candidate {
  box: Rect;
  score: number;
  view: number;
  source: LiveDetection;
}

function weighted(members: readonly Candidate[]): Rect {
  const total = members.reduce((sum, m) => sum + m.score, 0);
  const box: Rect = [0, 0, 0, 0];
  for (const m of members) for (let k = 0; k < 4; k++) box[k] += (m.box[k] * m.score) / total;
  return box;
}

function mergeClass(
  items: Candidate[],
  method: MergeMethod,
  threshold: number,
): [Rect, number, LiveDetection][] {
  // Ordem estável: score decrescente, depois a posição (vista na ordem do plano).
  const order = items.map((_, i) => i).sort((a, b) => items[b].score - items[a].score || a - b);
  const used = new Array<boolean>(items.length).fill(false);
  const result: [Rect, number, LiveDetection][] = [];
  order.forEach((i, position) => {
    if (used[i]) return;
    used[i] = true;
    const rest = order.slice(position + 1);
    if (method === 'nms' || method === 'nms_ios') {
      const metric = method === 'nms_ios' ? 'ios' : 'iou';
      for (const j of rest)
        if (
          !used[j] &&
          items[j].view !== items[i].view &&
          overlap(items[i].box, items[j].box, metric) > threshold
        )
          used[j] = true;
      result.push([[...items[i].box] as Rect, items[i].score, items[i].source]);
      return;
    }
    const members = [items[i]];
    const views = new Set([items[i].view]);
    let current: Rect = [...items[i].box] as Rect;
    let grew = true;
    while (grew) {
      grew = false;
      for (const j of rest) {
        if (used[j] || views.has(items[j].view)) continue;
        const reference = method === 'nmm' ? current : weighted(members);
        if (overlap(reference, items[j].box, method === 'nmm' ? 'ios' : 'iou') > threshold) {
          used[j] = true;
          members.push(items[j]);
          views.add(items[j].view);
          if (method === 'nmm')
            current = [
              Math.min(current[0], items[j].box[0]),
              Math.min(current[1], items[j].box[1]),
              Math.max(current[2], items[j].box[2]),
              Math.max(current[3], items[j].box[3]),
            ];
          grew = true;
        }
      }
    }
    const fused = method === 'nmm' ? current : weighted(members);
    result.push([fused, Math.max(...members.map((m) => m.score)), items[i].source]);
  });
  return result;
}

/**
 * Funde detecções já no quadro, vindas de vistas diferentes. Com no máximo uma vista
 * não vazia devolve essa vista intacta (FULL, imagem pequena, tiles sem contribuição).
 */
export function mergeViewDetections(
  perView: readonly (readonly LiveDetection[])[],
  method: MergeMethod,
  threshold: number,
): LiveDetection[] {
  const nonEmpty = perView.filter((d) => d.length);
  if (nonEmpty.length <= 1) return nonEmpty.length ? [...nonEmpty[0]] : [];
  const candidates: Candidate[] = [];
  perView.forEach((detections, view) =>
    detections.forEach((d) =>
      candidates.push({
        box: [d.x_min, d.y_min, d.x_max, d.y_max],
        score: d.score,
        view,
        source: d,
      }),
    ),
  );
  const classes = [...new Set(candidates.map((c) => c.source.classIndex))].sort((a, b) => a - b);
  const merged: LiveDetection[] = [];
  for (const classIndex of classes)
    for (const [box, score, source] of mergeClass(
      candidates.filter((c) => c.source.classIndex === classIndex),
      method,
      threshold,
    ))
      merged.push({ ...source, score, x_min: box[0], y_min: box[1], x_max: box[2], y_max: box[3] });
  return merged;
}

/** Ordena por score (estável) e aplica o teto de detecções do perfil. */
export function finalizeDetections(
  detections: readonly LiveDetection[],
  maxDetections: number,
): LiveDetection[] {
  return detections
    .map((d, i) => [d, i] as const)
    .sort((a, b) => b[0].score - a[0].score || a[1] - b[1])
    .slice(0, maxDetections)
    .map(([d]) => d);
}
