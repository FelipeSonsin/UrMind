import { describe, expect, it } from 'vitest';
import {
  AdaptiveCadence,
  checkBrowserModelManifest,
  classColor,
  containRect,
  decodeYoloxOutput,
  describeCameraError,
  fillBgrTensor,
  FrameFreshness,
  InferenceGate,
  isSceneChange,
  LatencyStats,
  letterbox,
  measureFrameQuality,
  ModelContractError,
  postprocessOptions,
  QUALITY_GRID,
  qualityHints,
  overlaySize,
  RateMeter,
  sampleLuminance,
  TemporalTracker,
  toDisplayBox,
  toHex,
  verifyModelBytes,
  type LiveDetection,
} from './liveDetection';

// Fixture de contrato apenas: nenhum modelo real, nenhuma detecção real.
const CLASSES = ['URMIND_ROAD_D00', 'URMIND_ROAD_D10', 'URMIND_ROAD_D20', 'URMIND_ROAD_D40'];
function manifest(overrides: Record<string, unknown> = {}) {
  return {
    schema_version: 1,
    model_id: 'yolox-s-model-v2',
    model_version: 'yolox-s-v2-test',
    scientific_status: 'EXPERIMENTAL',
    use_authorized: true,
    distribution_authorized: true,
    authorization_ref: 'docs/test',
    onnx: { path: '/models/yolox-s-v2.onnx', sha256: 'a'.repeat(64), size_bytes: 10 },
    input: {
      name: 'images',
      size: [640, 640],
      layout: 'NCHW',
      dtype: 'float32',
      color: 'BGR',
      range: '0..255',
      normalization: 'none',
      letterbox: { pad_value: 114, anchor: 'top-left' },
    },
    output: { name: 'output', format: 'yolox_decoded_cxcywh_obj_cls' },
    class_names: CLASSES,
    postprocess: {
      score_threshold: 0.25,
      nms_threshold: 0.65,
      nms: 'class_agnostic',
      max_detections: 50,
    },
    provenance: {
      registration_manifest_sha256: 'd'.repeat(64),
      closure_manifest_sha256: null as string | null,
      contract_sha256: 'c'.repeat(64),
    },
    ...overrides,
  };
}

/** Linha YOLOX decodificada: cx, cy, w, h (pixels da entrada), obj, classes… */
function output(rows: number[][]): { data: Float32Array; dims: number[] } {
  return { data: Float32Array.from(rows.flat()), dims: [1, rows.length, rows[0]?.length ?? 9] };
}
const options = {
  ratio: 1,
  frameWidth: 640,
  frameHeight: 640,
  classNames: CLASSES,
  scoreThresholds: CLASSES.map(() => 0.25),
  nmsThreshold: 0.65,
  nms: 'class_agnostic' as const,
};

describe('manifesto do modelo no navegador', () => {
  it('aceita somente o contrato YOLOX canônico autorizado', () => {
    expect(checkBrowserModelManifest(manifest()).ok).toBe(true);
  });
  it('modelo ausente ou malformado fica indisponível', () => {
    expect(checkBrowserModelManifest(null).ok).toBe(false);
    expect(checkBrowserModelManifest({ schema_version: 1 }).ok).toBe(false);
  });
  it('recusa modelo rejeitado, sem autorização de uso ou de distribuição', () => {
    expect(checkBrowserModelManifest(manifest({ scientific_status: 'REJECTED' })).ok).toBe(false);
    expect(checkBrowserModelManifest(manifest({ use_authorized: false })).ok).toBe(false);
    expect(checkBrowserModelManifest(manifest({ distribution_authorized: false })).ok).toBe(false);
  });
  it('experimental não vira aprovado sem fechamento oficial', () => {
    expect(checkBrowserModelManifest(manifest({ scientific_status: 'APPROVED' })).ok).toBe(false);
    const approved = manifest({ scientific_status: 'APPROVED' });
    approved.provenance = { ...approved.provenance, closure_manifest_sha256: 'e'.repeat(64) };
    expect(checkBrowserModelManifest(approved).ok).toBe(true);
  });
  it('não aceita URL arbitrária para os pesos', () => {
    for (const path of [
      'https://example.com/m.onnx',
      '//cdn.example.com/m.onnx',
      '/models/../secret.onnx',
      '/checkpoints/best.pth',
      '/models/best.pth',
    ])
      expect(
        checkBrowserModelManifest(
          manifest({ onnx: { path, sha256: 'a'.repeat(64), size_bytes: 1 } }),
        ).ok,
      ).toBe(false);
  });
  it('recusa pré-processamento diferente do contrato (não assume todo YOLO igual)', () => {
    const base = manifest();
    const rgb = { ...base, input: { ...base.input, color: 'RGB' } };
    const normalized = { ...base, input: { ...base.input, normalization: 'imagenet' } };
    expect(checkBrowserModelManifest(rgb).ok).toBe(false);
    expect(checkBrowserModelManifest(normalized).ok).toBe(false);
  });
  it('limiares por classe precisam cobrir exatamente as classes', () => {
    const base = manifest();
    const withThresholds = (class_score_thresholds: number[]) => ({
      ...base,
      postprocess: { ...base.postprocess, nms: 'per_class', class_score_thresholds },
    });
    expect(checkBrowserModelManifest(withThresholds([0.05, 0.13, 0.35, 0.13])).ok).toBe(true);
    expect(checkBrowserModelManifest(withThresholds([0.05, 0.13])).ok).toBe(false);
    expect(checkBrowserModelManifest(withThresholds([0, 0.13, 0.35, 0.13])).ok).toBe(false);
  });
  it('sem limiares por classe, o limiar global vale para todas', () => {
    const check = checkBrowserModelManifest(manifest());
    if (!check.ok) throw new Error(check.reason);
    expect(postprocessOptions(check.manifest).scoreThresholds).toEqual(
      CLASSES.map(() => check.manifest.postprocess.score_threshold),
    );
  });
});

describe('integridade dos bytes do modelo', () => {
  const bytes = new TextEncoder().encode('modelo-de-teste').buffer as ArrayBuffer;
  it('confere tamanho e SHA-256', async () => {
    const sha256 = toHex(await crypto.subtle.digest('SHA-256', bytes));
    await expect(
      verifyModelBytes(bytes, { path: '/models/m.onnx', sha256, size_bytes: bytes.byteLength }),
    ).resolves.toBeUndefined();
  });
  it('download incompleto e checksum inválido falham fechados', async () => {
    await expect(
      verifyModelBytes(bytes, { path: '/models/m.onnx', sha256: 'a'.repeat(64), size_bytes: 3 }),
    ).rejects.toThrow(/incompleto/);
    await expect(
      verifyModelBytes(bytes, {
        path: '/models/m.onnx',
        sha256: 'a'.repeat(64),
        size_bytes: bytes.byteLength,
      }),
    ).rejects.toThrow(/checksum/);
  });
});

describe('letterbox do YOLOX preproc', () => {
  it('paisagem e retrato mantêm a proporção e truncam como int()', () => {
    expect(letterbox(1920, 1080, [640, 640])).toEqual({
      ratio: 1 / 3,
      resizedWidth: 640,
      resizedHeight: 360,
    });
    expect(letterbox(1080, 1920, [640, 640])).toMatchObject({
      resizedWidth: 360,
      resizedHeight: 640,
    });
    expect(letterbox(1280, 721, [640, 640])).toMatchObject({
      resizedWidth: 640,
      resizedHeight: 360,
    });
  });
  it('sem ampliar: imagem menor fica em escala 1; maior é reduzida igual', () => {
    expect(letterbox(512, 512, [640, 640])).toEqual({
      ratio: 1.25,
      resizedWidth: 640,
      resizedHeight: 640,
    });
    expect(letterbox(512, 512, [640, 640], false)).toEqual({
      ratio: 1,
      resizedWidth: 512,
      resizedHeight: 512,
    });
    expect(letterbox(1920, 1080, [640, 640], false)).toEqual(letterbox(1920, 1080, [640, 640]));
  });
  it('tensor em BGR, 0..255, CHW, sem normalização', () => {
    const rgba = Uint8ClampedArray.from([10, 20, 30, 255, 114, 114, 114, 255]);
    const out = fillBgrTensor(rgba, 2, 1, new Float32Array(6));
    expect(Array.from(out)).toEqual([30, 114, 20, 114, 10, 114]);
    expect(() => fillBgrTensor(rgba, 2, 2, new Float32Array(6))).toThrow(ModelContractError);
  });
});

describe('pós-processamento YOLOX', () => {
  it('score = objectness × classe e caixa restaurada ao quadro original', () => {
    // Quadro 1920×1080 → ratio 1/3; caixa na entrada (cx 100, cy 50, 40×20).
    const { data, dims } = output([[100, 50, 40, 20, 0.9, 0.1, 0.8, 0, 0]]);
    const [det] = decodeYoloxOutput(data, dims, {
      ...options,
      ratio: 1 / 3,
      frameWidth: 1920,
      frameHeight: 1080,
    });
    expect(det.className).toBe('URMIND_ROAD_D10');
    expect(det.score).toBeCloseTo(0.72, 6);
    expect(det.x_min).toBeCloseTo(240);
    expect(det.y_min).toBeCloseTo(120);
    expect(det.x_max).toBeCloseTo(360);
    expect(det.y_max).toBeCloseTo(180);
  });
  it('a faixa de padding não desloca coordenadas (letterbox no canto superior esquerdo)', () => {
    // Retrato 1080×1920: imagem ocupa x 0..360 da entrada; o resto é padding.
    const { data, dims } = output([[180, 320, 20, 20, 1, 0, 0, 0.9, 0]]);
    const [det] = decodeYoloxOutput(data, dims, {
      ...options,
      ratio: 1 / 3,
      frameWidth: 1080,
      frameHeight: 1920,
    });
    expect(det.x_min).toBeCloseTo(510);
    expect(det.y_max).toBeCloseTo(990);
  });
  it('NMS agnóstico de classe suprime a caixa sobreposta de menor score', () => {
    const { data, dims } = output([
      [100, 100, 50, 50, 1, 0.9, 0, 0, 0],
      [102, 101, 50, 50, 1, 0, 0.6, 0, 0],
      [400, 400, 30, 30, 1, 0, 0, 0, 0.5],
    ]);
    const detections = decodeYoloxOutput(data, dims, options);
    expect(detections.map((d) => d.className)).toEqual(['URMIND_ROAD_D00', 'URMIND_ROAD_D40']);
  });
  it('limiar é estrito e nada detectado devolve lista vazia', () => {
    const { data, dims } = output([[100, 100, 10, 10, 0.5, 0.5, 0, 0, 0]]);
    expect(decodeYoloxOutput(data, dims, options)).toEqual([]);
  });
  it('recorta ao quadro e descarta caixa degenerada', () => {
    const { data, dims } = output([
      [5, 5, 40, 40, 1, 0.9, 0, 0, 0],
      [-50, 100, 20, 20, 1, 0.9, 0, 0, 0],
    ]);
    const detections = decodeYoloxOutput(data, dims, options);
    expect(detections).toHaveLength(1);
    expect(detections[0]).toMatchObject({ x_min: 0, y_min: 0 });
  });
  it('limiar por classe: cada classe usa o próprio corte', () => {
    const { data, dims } = output([
      [100, 100, 20, 20, 1, 0.1, 0, 0, 0],
      [300, 300, 20, 20, 1, 0, 0, 0.3, 0],
    ]);
    const detections = decodeYoloxOutput(data, dims, {
      ...options,
      scoreThresholds: [0.05, 0.13, 0.35, 0.13],
      nms: 'per_class',
    });
    // D00 0,1 > 0,05 passa; D20 0,3 < 0,35 cai.
    expect(detections.map((d) => d.className)).toEqual(['URMIND_ROAD_D00']);
  });
  it('NMS por classe só suprime dentro da mesma classe e ordena por score', () => {
    const { data, dims } = output([
      [100, 100, 50, 50, 1, 0.9, 0, 0, 0],
      [102, 101, 50, 50, 1, 0, 0.6, 0, 0],
      [101, 100, 50, 50, 1, 0.7, 0, 0, 0],
    ]);
    const detections = decodeYoloxOutput(data, dims, {
      ...options,
      nmsThreshold: 0.45,
      nms: 'per_class',
    });
    expect(detections.map((d) => [d.className, d.score])).toEqual([
      ['URMIND_ROAD_D00', expect.closeTo(0.9, 6)],
      ['URMIND_ROAD_D10', expect.closeTo(0.6, 6)],
    ]);
  });
  it('lista de limiares incompatível com as classes falha fechada', () => {
    const { data, dims } = output([[100, 100, 20, 20, 1, 0.9, 0, 0, 0]]);
    expect(() => decodeYoloxOutput(data, dims, { ...options, scoreThresholds: [0.1] })).toThrow(
      ModelContractError,
    );
  });
  it('classe desconhecida ou formato diferente falham, sem caixas', () => {
    const extra = output([[1, 1, 1, 1, 1, 0, 0, 0, 0, 0.9]]);
    expect(() => decodeYoloxOutput(extra.data, extra.dims, options)).toThrow(/classe desconhecida/);
    expect(() => decodeYoloxOutput(new Float32Array(9), [9], options)).toThrow(ModelContractError);
    expect(() => decodeYoloxOutput(new Float32Array(8), [1, 1, 9], options)).toThrow(
      ModelContractError,
    );
  });
});

describe('caixa, imagem e texto na mesma transformação', () => {
  const det = { x_min: 480, y_min: 270, x_max: 960, y_max: 540 };
  const frame = { width: 1920, height: 1080 };
  it('escala uniforme quando a caixa CSS tem a proporção do quadro', () => {
    expect(toDisplayBox(det, frame, { width: 960, height: 540 }, false)).toEqual({
      left: 240,
      top: 135,
      width: 240,
      height: 135,
    });
  });
  it('compensa as faixas do object-fit: contain após resize', () => {
    const fit = containRect(frame, { width: 960, height: 740 });
    expect(fit).toMatchObject({ left: 0, top: 100, width: 960, height: 540 });
    expect(toDisplayBox(det, frame, { width: 960, height: 740 }, false).top).toBe(235);
    // Retrato num palco largo: faixas laterais.
    const portrait = containRect({ width: 1080, height: 1920 }, { width: 960, height: 540 });
    expect(portrait.left).toBeCloseTo(328.125);
  });
  it('espelha só a posição horizontal na câmera frontal', () => {
    const box = toDisplayBox(det, frame, { width: 960, height: 540 }, true);
    expect(box.left).toBe(480);
    expect(box.width).toBe(240);
  });
  it('overlay em pixels físicos para devicePixelRatio', () => {
    expect(overlaySize(960, 540, 2)).toEqual({ width: 1920, height: 1080, dpr: 2 });
    expect(overlaySize(333.3, 100, 1.5)).toEqual({ width: 500, height: 150, dpr: 1.5 });
    expect(overlaySize(10, 10, Number.NaN).dpr).toBe(1);
  });
  it('cor por classe é estável', () => {
    expect(classColor(1)).toBe(classColor(1));
    expect(classColor(0)).not.toBe(classColor(1));
  });
});

describe('sincronização quadro ↔ resultado', () => {
  it('no máximo uma inferência em voo, sem fila', () => {
    const gate = new InferenceGate();
    const first = gate.begin(0)!;
    for (let i = 0; i < 100; i++) expect(gate.begin(i)).toBeNull();
    expect(gate.accept(first.run, first.frameId)).toBe(true);
    expect(gate.busy).toBe(false);
  });
  it('resultado atrasado de execução cancelada (pausa, câmera trocada, rota fechada) é descartado', () => {
    const gate = new InferenceGate();
    const stale = gate.begin(0)!;
    gate.cancel();
    expect(gate.accept(stale.run, stale.frameId)).toBe(false);
    const fresh = gate.begin(1)!;
    expect(gate.accept(stale.run, stale.frameId)).toBe(false);
    expect(gate.accept(fresh.run, fresh.frameId + 1)).toBe(false);
    expect(gate.accept(fresh.run, fresh.frameId)).toBe(true);
    expect(gate.accept(fresh.run, fresh.frameId)).toBe(false);
  });
  it('erro libera o quadro sem aceitar resultado', () => {
    const gate = new InferenceGate();
    const ticket = gate.begin(5)!;
    expect(gate.inFlightSince()).toBe(5);
    gate.fail(ticket.run, ticket.frameId);
    expect(gate.busy).toBe(false);
    expect(gate.accept(ticket.run, ticket.frameId)).toBe(false);
  });
  it('ritmo respeita o teto e deixa folga quando a inferência é lenta', () => {
    expect(new AdaptiveCadence().next(50)).toBe(150);
    // 800 ms com no máximo 75% de ocupação: período de ~1067 ms, espera de ~267 ms.
    expect(new AdaptiveCadence().next(800)).toBeCloseTo(266.67, 1);
  });
  it('latência maior recua na hora; latência menor acelera aos poucos até o teto', () => {
    const cadence = new AdaptiveCadence();
    for (let i = 0; i < 20; i++) cadence.next(60);
    expect(cadence.periodMs).toBe(200);
    // Aparelho esquentou: a primeira resposta lenta já espaça os envios.
    cadence.next(900);
    expect(cadence.periodMs).toBeCloseTo(1200);
    // Voltou a ficar rápido: o período encurta no máximo 15% por resultado.
    const periods: number[] = [];
    for (let i = 0; i < 30; i++) {
      cadence.next(60);
      periods.push(cadence.periodMs!);
    }
    for (let i = 1; i < periods.length; i++) {
      expect(periods[i]).toBeLessThanOrEqual(periods[i - 1]);
      expect(periods[i]).toBeGreaterThanOrEqual(periods[i - 1] * 0.85 - 1e-9);
    }
    expect(periods[0]).toBeGreaterThan(900);
    expect(periods.at(-1)).toBe(200);
  });
  it('nunca envia duas vezes o mesmo quadro de vídeo', () => {
    const freshness = new FrameFreshness();
    expect(freshness.accept(1.0)).toBe(true);
    expect(freshness.accept(1.0)).toBe(false);
    expect(freshness.accept(1.033)).toBe(true);
    freshness.reset();
    expect(freshness.accept(1.033)).toBe(true);
  });
  it('taxa é medida numa janela móvel, sem crescer sem limite', () => {
    const meter = new RateMeter(1000);
    expect(meter.rate(0)).toBeNull();
    for (let t = 0; t <= 10_000; t += 200) meter.tick(t);
    expect(meter.rate(10_000)).toBeCloseTo(5);
    expect(meter.size).toBeLessThanOrEqual(6);
    meter.reset();
    expect(meter.rate(10_000)).toBeNull();
  });
});

describe('erros de câmera', () => {
  const ok = { secure: true, supported: true };
  it('explica cada falha sem pedir microfone ou servidor', () => {
    expect(describeCameraError(null, { secure: false, supported: true }).code).toBe(
      'insecure_context',
    );
    expect(describeCameraError(null, { secure: true, supported: false }).code).toBe('unsupported');
    expect(describeCameraError({ name: 'NotAllowedError' }, ok).code).toBe('permission_denied');
    expect(describeCameraError({ name: 'NotFoundError' }, ok).code).toBe('not_found');
    expect(describeCameraError({ name: 'NotReadableError' }, ok).code).toBe('busy');
    expect(describeCameraError({ name: 'OverconstrainedError' }, ok).code).toBe('constraints');
    expect(describeCameraError(new Error('x'), ok).code).toBe('unknown');
  });
});

function detection(classIndex: number, x: number, score = 0.5): LiveDetection {
  return {
    classIndex,
    className: CLASSES[classIndex],
    score,
    x_min: x,
    y_min: 100,
    x_max: x + 50,
    y_max: 150,
  };
}

describe('camada temporal (apresentação)', () => {
  it('reaparição compatível vira persistente; primeira aparição é momentânea', () => {
    const tracker = new TemporalTracker();
    const [first] = tracker.update([detection(0, 100)], 1000);
    expect(first.state).toBe('provisional');
    const [second] = tracker.update([detection(0, 104)], 1300);
    expect(second).toMatchObject({ state: 'persistent', hits: 2, trackId: first.trackId });
    expect(second.ageMs).toBe(300);
  });
  it('não associa classes diferentes nem caixas distantes', () => {
    const tracker = new TemporalTracker();
    const [a] = tracker.update([detection(0, 100)], 0);
    const [otherClass] = tracker.update([detection(1, 100)], 100);
    const [far] = tracker.update([detection(0, 400)], 200);
    expect(otherClass.trackId).not.toBe(a.trackId);
    expect(far.trackId).not.toBe(a.trackId);
    expect(far.state).toBe('provisional');
  });
  it('duas trincas vizinhas não se fundem numa só observação', () => {
    const tracker = new TemporalTracker();
    const first = tracker.update([detection(0, 100, 0.9), detection(0, 160, 0.8)], 0);
    const next = tracker.update([detection(0, 102, 0.9), detection(0, 162, 0.8)], 200);
    expect(new Set(next.map((d) => d.trackId))).toEqual(new Set(first.map((d) => d.trackId)));
    expect(next.every((d) => d.state === 'persistent')).toBe(true);
  });
  it('expira após o intervalo, com relógio voltando, e no reset', () => {
    const tracker = new TemporalTracker();
    tracker.update([detection(0, 100)], 0);
    expect(tracker.update([detection(0, 100)], 5000)[0].state).toBe('provisional');
    expect(tracker.update([detection(0, 100)], 4000)[0].state).toBe('provisional');
    tracker.reset();
    expect(tracker.size).toBe(0);
    expect(tracker.update([detection(0, 100)], 4100)[0].state).toBe('provisional');
  });
  it('não remove nem altera as detecções brutas', () => {
    const raw = [detection(0, 100, 0.9), detection(2, 300, 0.2)];
    const tracked = new TemporalTracker().update(raw, 0);
    expect(tracked).toHaveLength(2);
    expect(
      tracked.map(({ trackId, state, hits, ageMs, recentScores, meanScore, ...rest }) => rest),
    ).toEqual(raw);
  });
  it('guarda um histórico curto de confiança por observação, sem filtrar nem alterar o score', () => {
    const tracker = new TemporalTracker();
    let last = tracker.update([detection(0, 100, 0.2)], 0)[0];
    expect(last.recentScores).toEqual([0.2]);
    [0.4, 0.6, 0.8, 0.9, 0.7].forEach((score, i) => {
      [last] = tracker.update([detection(0, 101 + i, score)], (i + 1) * 100);
    });
    expect(last.recentScores).toEqual([0.4, 0.6, 0.8, 0.9, 0.7]);
    expect(last.meanScore).toBeCloseTo(0.68, 9);
    expect(last.score).toBe(0.7);
    // Nova cena: o histórico recomeça com a observação.
    tracker.reset();
    expect(tracker.update([detection(0, 100, 0.3)], 700)[0].recentScores).toEqual([0.3]);
  });
});

describe('latência medida', () => {
  it('p50/p95 da janela recente e reset', () => {
    const stats = new LatencyStats(10);
    expect(stats.percentile(50)).toBeNull();
    for (let i = 1; i <= 20; i++) stats.add(i * 10);
    expect(stats.percentile(50)).toBe(150);
    expect(stats.percentile(95)).toBe(200);
    stats.reset();
    expect(stats.percentile(95)).toBeNull();
  });
});

describe('qualidade da captura (heurística)', () => {
  const grid = QUALITY_GRID;
  function checker(level: number, contrast: number): Float32Array {
    const out = new Float32Array(grid * grid);
    for (let y = 0; y < grid; y++)
      for (let x = 0; x < grid; x++)
        out[y * grid + x] = level + ((x + y) % 2 ? contrast : -contrast);
    return out;
  }
  it('imagem nítida e iluminada não gera aviso', () => {
    const quality = measureFrameQuality(checker(140, 40), null);
    expect(quality.motion).toBeNull();
    expect(qualityHints(quality, 1920, 1080)).toEqual([]);
  });
  it('escura, estourada, sem detalhe e baixa resolução', () => {
    expect(qualityHints(measureFrameQuality(checker(30, 10), null), 1920, 1080)).toEqual(['dark']);
    expect(qualityHints(measureFrameQuality(checker(240, 5), null), 1920, 1080)).toEqual([
      'bright',
    ]);
    expect(qualityHints(measureFrameQuality(checker(140, 0), null), 1920, 1080)).toEqual([
      'blurry',
    ]);
    expect(qualityHints(measureFrameQuality(checker(140, 40), null), 320, 240)).toEqual([
      'low_resolution',
    ]);
  });
  it('movimento e troca de cena pela diferença ao quadro anterior', () => {
    const base = checker(140, 40);
    const moved = measureFrameQuality(checker(175, 40), base);
    expect(qualityHints(moved, 1920, 1080)).toContain('motion');
    expect(isSceneChange(moved)).toBe(false);
    expect(isSceneChange(measureFrameQuality(checker(200, 40), base))).toBe(true);
    expect(isSceneChange(measureFrameQuality(base, base))).toBe(false);
  });
  it('amostra só a região útil do letterbox, sem o padding', () => {
    const stride = 8;
    const rgba = new Uint8ClampedArray(stride * stride * 4).fill(114);
    for (let y = 0; y < 4; y++)
      for (let x = 0; x < 4; x++) rgba.set([200, 200, 200, 255], (y * stride + x) * 4);
    const luminance = sampleLuminance(rgba, stride, 4, 4);
    expect(Math.min(...luminance)).toBeCloseTo(200, 3);
  });
});
