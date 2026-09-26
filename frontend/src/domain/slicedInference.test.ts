import { describe, expect, it } from 'vitest';
import type { LiveDetection } from './liveDetection';
import golden from './slicedInference.golden.json';
import {
  finalizeDetections,
  FULL_FRAME,
  mergeViewDetections,
  offsetDetections,
  planViews,
  slicingSchema,
  tileGrid,
  type MergeMethod,
} from './slicedInference';

const det = (
  x0: number,
  y0: number,
  x1: number,
  y1: number,
  score: number,
  classIndex: number,
): LiveDetection => ({
  classIndex,
  className: `C${classIndex}`,
  score,
  x_min: x0,
  y_min: y0,
  x_max: x1,
  y_max: y1,
});
const rows = (list: readonly LiveDetection[]) =>
  list.map((d) => [d.x_min, d.y_min, d.x_max, d.y_max, d.score, d.classIndex]);

describe('grade de tiles', () => {
  it('não fatia imagem pequena e cobre 1080p com bordas encostadas', () => {
    expect(tileGrid(512, 512, 640, 0.2)).toEqual([]);
    expect(tileGrid(640, 640, 640, 0.2)).toEqual([]);
    const tiles = tileGrid(1920, 1080, 640, 0.2);
    expect(tiles).toHaveLength(8);
    expect(new Set(tiles.map((t) => t[0]))).toEqual(new Set([0, 512, 1024, 1280]));
    expect(new Set(tiles.map((t) => t[1]))).toEqual(new Set([0, 440]));
    expect(tileGrid(1000, 600, 640, 0.2)).toEqual([
      [0, 0, 640, 600],
      [360, 0, 1000, 600],
    ]);
  });

  it('é idêntica à grade do backend', () => {
    for (const [key, expected] of Object.entries(golden.grids)) {
      const [size, overlap] = key.split('@');
      const [w, h] = size.split('x').map(Number);
      expect(tileGrid(w, h, 640, Number(overlap)), key).toEqual(expected);
    }
  });

  it('planeja FULL, TILED e HYBRID', () => {
    expect(planViews(1920, 1080, FULL_FRAME)).toEqual([[0, 0, 1920, 1080]]);
    expect(planViews(1920, 1080, { ...FULL_FRAME, mode: 'tiled' })).toHaveLength(8);
    const hybrid = planViews(1920, 1080, { ...FULL_FRAME, mode: 'hybrid' });
    expect(hybrid[0]).toEqual([0, 0, 1920, 1080]);
    expect(hybrid).toHaveLength(9);
    expect(planViews(512, 512, { ...FULL_FRAME, mode: 'hybrid' })).toEqual([[0, 0, 512, 512]]);
  });

  it('recusa configuração fora do contrato', () => {
    expect(slicingSchema.safeParse({ ...FULL_FRAME, mode: 'mosaic' }).success).toBe(false);
    expect(slicingSchema.safeParse({ ...FULL_FRAME, overlap: 0.9 }).success).toBe(false);
    expect(slicingSchema.safeParse({ ...FULL_FRAME, merge_threshold: 0 }).success).toBe(false);
    expect(slicingSchema.safeParse(FULL_FRAME).success).toBe(true);
  });
});

describe('fusão entre vistas', () => {
  it('leva a vista ao quadro', () => {
    expect(rows(offsetDetections([det(10, 20, 30, 40, 0.5, 1)], [512, 440, 1152, 1080]))).toEqual([
      [522, 460, 542, 480, 0.5, 1],
    ]);
  });

  it('uma vista sai intacta e a mesma vista nunca é resolvida de novo', () => {
    const same = [det(100, 100, 300, 200, 0.8, 0), det(120, 110, 180, 190, 0.5, 0)];
    for (const method of ['nms', 'nms_ios', 'nmm', 'wbf'] as MergeMethod[]) {
      expect(rows(mergeViewDetections([same], method, 0.3))).toEqual(rows(same));
      expect(rows(mergeViewDetections([same, []], method, 0.3))).toEqual(rows(same));
    }
  });

  it('IOS remove a metade cortada que o IoU mantém', () => {
    const full = [det(100, 100, 300, 200, 0.8, 0)];
    const cut = [det(100, 100, 190, 200, 0.5, 0)];
    expect(mergeViewDetections([full, cut], 'nms', 0.5)).toHaveLength(2);
    expect(rows(mergeViewDetections([full, cut], 'nms_ios', 0.5))).toEqual(rows(full));
  });

  it('NMM reconstrói objeto dividido entre tiles e não funde classes diferentes', () => {
    const merged = mergeViewDetections(
      [
        [det(110, 100, 290, 200, 0.4, 1)],
        [det(100, 100, 220, 200, 0.7, 1)],
        [det(180, 100, 300, 200, 0.6, 1)],
      ],
      'nmm',
      0.5,
    );
    expect(rows(merged)).toEqual([[100, 100, 300, 200, 0.7, 1]]);
    expect(
      mergeViewDetections(
        [[det(100, 100, 220, 200, 0.7, 1)], [det(180, 100, 300, 200, 0.6, 3)]],
        'nmm',
        0.5,
      ),
    ).toHaveLength(2);
  });

  it('WBF faz média ponderada pelo score', () => {
    const merged = mergeViewDetections(
      [[det(100, 100, 200, 200, 0.75, 0)], [det(110, 100, 210, 200, 0.25, 0)]],
      'wbf',
      0.5,
    );
    expect(merged).toHaveLength(1);
    expect(merged[0].x_min).toBeCloseTo(102.5, 9);
    expect(merged[0].x_max).toBeCloseTo(202.5, 9);
    expect(merged[0].score).toBe(0.75);
  });

  it('reproduz o backend nos quatro métodos (caso golden gerado pelo Python)', () => {
    const views = golden.views.map((view) =>
      view.map(([x0, y0, x1, y1, s, c]) => det(x0, y0, x1, y1, s, c)),
    );
    for (const [key, expected] of Object.entries(golden.expected)) {
      const [method, threshold] = key.split('@');
      const merged = rows(mergeViewDetections(views, method as MergeMethod, Number(threshold)));
      expect(merged.length, key).toBe(expected.length);
      merged.forEach((row, i) =>
        row.forEach((value, k) =>
          expect(value, `${key}[${i}][${k}]`).toBeCloseTo(expected[i][k], 6),
        ),
      );
    }
  });

  it('ordena por score e aplica o teto', () => {
    const list = [1, 2, 3, 4, 5].map((i) => det(0, 0, 10 + i, 10, i / 10, 0));
    expect(finalizeDetections(list, 3).map((d) => d.score)).toEqual([0.5, 0.4, 0.3]);
  });
});
