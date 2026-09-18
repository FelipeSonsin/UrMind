import { z } from 'zod';

// Espelha app/schemas/public.py. Campo ausente chega como null com motivo: a tela
// mostra "não disponível" e nunca preenche com número plausível.

const component = z.object({
  name: z.string(),
  status: z.enum(['ok', 'degraded', 'unavailable']),
  detail: z.string().nullable(),
});

export const publicStatusSchema = z.object({
  api: component,
  database: component,
  detector: component,
  scout: component,
  last_event_at: z.string().nullable(),
  events_total: z.number(),
  road_segments_total: z.number(),
  pilot_area: z.string().nullable(),
  checked_at: z.string(),
});
export type PublicStatus = z.infer<typeof publicStatusSchema>;

export const scoutCameraSchema = z.object({
  mode: z.enum(['live_video', 'live_snapshots', 'unavailable']),
  reason: z.string().nullable(),
  stream_url: z.string().nullable(),
  frame_url: z.string().nullable(),
  latency_ms: z.number().nullable(),
});
export const publicScoutSchema = z.object({
  status: z.enum(['live', 'degraded', 'offline', 'no_device']),
  device_code: z.string().nullable(),
  last_seen: z.string().nullable(),
  camera: scoutCameraSchema,
  telemetry: z.record(z.string(), z.unknown()),
  mission: z.string().nullable(),
});
export type PublicScout = z.infer<typeof publicScoutSchema>;
export type ScoutCamera = z.infer<typeof scoutCameraSchema>;

export const publicEventSchema = z.object({
  id: z.string().uuid(),
  occurred_at: z.string(),
  urmind_class: z.string(),
  status: z.string(),
  evidence_mode: z.string(),
  visual_confidence: z.number().nullable(),
  severity: z.string().nullable(),
  priority_score: z.number().nullable(),
  latitude: z.number().nullable(),
  longitude: z.number().nullable(),
  snapped_latitude: z.number().nullable(),
  snapped_longitude: z.number().nullable(),
  road_name: z.string().nullable(),
});
export type PublicEvent = z.infer<typeof publicEventSchema>;

const riskFactor = z.object({
  factor: z.string(),
  label: z.string(),
  value: z.number().nullable().optional(),
  weight: z.number().nullable().optional(),
  delta: z.number().nullable().optional(),
  reason: z.string().nullable().optional(),
});

export const publicEventDetailSchema = publicEventSchema.extend({
  distance_to_road_m: z.number().nullable(),
  location_accuracy_m: z.number().nullable(),
  road: z
    .object({
      name: z.string().nullable(),
      highway: z.string().nullable(),
      distance_m: z.number().nullable(),
      jurisdiction: z.string().nullable(),
    })
    .nullable(),
  detections: z.array(
    z.object({
      urmind_class: z.string(),
      confidence: z.number(),
      bbox: z
        .object({ x: z.number(), y: z.number(), width: z.number(), height: z.number() })
        .nullable(),
    }),
  ),
  image: z.object({
    available: z.boolean(),
    privacy_redacted: z.boolean(),
    reason: z.string().nullable(),
    url: z.string().nullable(),
  }),
  risk: z
    .object({
      severity: z.string(),
      priority_score: z.number().nullable(),
      uncertainty: z.number().nullable(),
      uncertainty_band: z.string(),
      coverage: z.number().nullable(),
      ruleset_version: z.string().nullable(),
      thresholds_are_calibrated: z.boolean(),
      explanation: z.object({
        increased: z.array(riskFactor),
        decreased: z.array(riskFactor),
        unavailable: z.array(riskFactor),
        baseline: z.number().nullable(),
      }),
      limitations: z.array(z.string()),
      assessed_at: z.string().nullable(),
    })
    .nullable(),
  action: z.object({ code: z.string(), label: z.string(), version: z.string() }).nullable(),
  responsibility: z.object({
    status: z.enum(['assigned', 'requires_triage']),
    responsible: z.string().nullable(),
    source: z.string().nullable(),
    version: z.string().nullable(),
    note: z.string().nullable(),
  }),
  context: z.array(
    z.object({
      source: z.string(),
      label: z.string(),
      status: z.string(),
      fetched_at: z.string().nullable(),
      attribution: z.string().nullable(),
      summary: z.record(z.string(), z.unknown()),
    }),
  ),
  prediction: z.object({
    available: z.boolean(),
    reason: z.string().nullable(),
    task: z.string().nullable(),
    horizon_days: z.number().nullable(),
    value: z.number().nullable(),
    uncertainty: z.number().nullable(),
    model_version: z.string().nullable(),
    generated_at: z.string().nullable(),
  }),
  trace: z.array(
    z.object({
      step: z.enum(['capture', 'detection', 'context', 'risk', 'decision', 'action']),
      status: z.enum(['done', 'unavailable']),
      title: z.string(),
      source: z.string().nullable(),
      detail: z.record(z.string(), z.unknown()),
      at: z.string().nullable(),
    }),
  ),
  model_version: z.string().nullable(),
  model_stage: z.string().nullable(),
  dataset_version: z.string().nullable(),
  reviewed: z.boolean(),
});
export type PublicEventDetail = z.infer<typeof publicEventDetailSchema>;

export const transparencySchema = z.object({
  model_name: z.string().nullable(),
  model_version: z.string().nullable(),
  stage: z.string().nullable(),
  stage_note: z.string().nullable(),
  classes: z.array(z.string()),
  input_size: z.array(z.number()),
  score_threshold: z.number().nullable(),
  metrics: z.object({
    map50: z.number().nullable(),
    map50_95: z.number().nullable(),
    precision: z.number().nullable(),
    recall: z.number().nullable(),
    f1: z.number().nullable(),
    per_class: z.record(z.string(), z.record(z.string(), z.number().nullable())),
    not_computed: z.array(z.string()),
    samples: z.number().nullable(),
  }),
  latency: z.object({
    mean_ms: z.number().nullable(),
    p50_ms: z.number().nullable(),
    p95_ms: z.number().nullable(),
    fps_approx: z.number().nullable(),
    execution_provider: z.string().nullable(),
    hardware: z.string().nullable(),
  }),
  dataset_name: z.string().nullable(),
  dataset_version: z.string().nullable(),
  dataset_license: z.string().nullable(),
  dataset_source: z.string().nullable(),
  context_sources: z.array(z.record(z.string(), z.string())),
  rules: z.record(z.string(), z.unknown()),
  limitations: z.array(z.string()),
});
export type Transparency = z.infer<typeof transparencySchema>;

/** Rótulo público das classes técnicas. Sem tradução, mostra a própria classe. */
export const publicClassLabels: Record<string, string> = {
  URMIND_ROAD_D00: 'Trinca longitudinal',
  URMIND_ROAD_D10: 'Trinca transversal',
  URMIND_ROAD_D20: 'Trinca em malha',
  URMIND_ROAD_D40: 'Buraco',
  URMIND_MANHOLE: 'Bueiro',
  URMIND_SIDEWALK: 'Calçada',
  URMIND_SIGNAGE: 'Sinalização',
  URMIND_UNKNOWN: 'Não classificado',
};

/** Risco nunca depende só de cor: cada nível tem rótulo e símbolo próprios (WCAG). */
export const severityPresentation: Record<string, { label: string; shape: string; level: string }> =
  {
    critical: { label: 'Crítica', shape: '▲▲', level: 'critical' },
    high: { label: 'Alta', shape: '▲', level: 'high' },
    medium: { label: 'Média', shape: '■', level: 'medium' },
    low: { label: 'Baixa', shape: '●', level: 'low' },
    unknown: { label: 'Não determinada', shape: '?', level: 'unknown' },
  };

export function labelFor(urmindClass: string): string {
  return publicClassLabels[urmindClass] ?? urmindClass;
}

export function severityOf(severity: string | null | undefined) {
  return severityPresentation[severity ?? 'unknown'] ?? severityPresentation.unknown;
}

/** Prioridade 0–1 vira faixa legível. Mantém o número ao lado, nunca no lugar. */
export function priorityBand(score: number | null | undefined): string {
  if (score == null) return 'não calculada';
  if (score >= 0.75) return 'muito alta';
  if (score >= 0.5) return 'alta';
  if (score >= 0.25) return 'média';
  return 'baixa';
}
