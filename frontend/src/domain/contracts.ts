import { z } from 'zod';

export const classes = {
  URMIND_ROAD_D00: 'Trinca longitudinal',
  URMIND_ROAD_D10: 'Trinca transversal',
  URMIND_ROAD_D20: 'Trinca em malha',
  URMIND_ROAD_D40: 'Buraco',
  URMIND_MANHOLE: 'Bueiro (escopo futuro)',
  URMIND_SIDEWALK: 'Calçada (escopo futuro)',
  URMIND_SIGNAGE: 'Sinalização (escopo futuro)',
  URMIND_UNKNOWN: 'Não classificado',
} as const;
export const statuses = {
  detected: 'Detectada',
  review: 'Em revisão',
  confirmed: 'Confirmada',
  rejected: 'Rejeitada',
  triage_required: 'Requer triagem',
} as const;
const optionalNumber = z.number().finite().nullable().optional();
export const eventSchema = z.object({
  id: z.string().uuid(),
  event_key: z.string(),
  urmind_class: z.enum(
    Object.keys(classes) as [keyof typeof classes, ...Array<keyof typeof classes>],
  ),
  status: z.enum(Object.keys(statuses) as [keyof typeof statuses, ...Array<keyof typeof statuses>]),
  occurred_at: z.string().datetime({ offset: true }),
  evidence_mode: z.string(),
  model_status: z.string().nullable().optional(),
  visual_confidence: z.number().min(0).max(1).nullable().optional(),
  fused_confidence: z.number().min(0).max(1).nullable().optional(),
  latitude: z.number().min(-90).max(90).nullable().optional(),
  longitude: z.number().min(-180).max(180).nullable().optional(),
  snapped_latitude: optionalNumber,
  snapped_longitude: optionalNumber,
  location_accuracy_m: optionalNumber,
  distance_to_road_m: optionalNumber,
  road_segment_id: z.string().uuid().nullable().optional(),
  factors: z.record(z.string(), z.unknown()).default({}),
});
export type UrbanEvent = z.infer<typeof eventSchema>;
export const coordinateSchema = z.object({
  latitude: z.number().finite().min(-90).max(90),
  longitude: z.number().finite().min(-180).max(180),
  accuracy_m: z.number().finite().nonnegative().nullable(),
});
export type Coordinate = z.infer<typeof coordinateSchema>;

export function parseCoordinate(latitude: string, longitude: string): Coordinate {
  if (!latitude.trim() || !longitude.trim()) throw new Error('Informe latitude e longitude.');
  const result = coordinateSchema.safeParse({
    latitude: Number(latitude.replace(',', '.')),
    longitude: Number(longitude.replace(',', '.')),
    accuracy_m: null,
  });
  if (!result.success)
    throw new Error('Coordenadas inválidas: latitude de −90 a 90 e longitude de −180 a 180.');
  return result.data;
}

export const uploadResultSchema = z.object({
  id: z.string().uuid(),
  capture_key: z.string(),
  created: z.boolean(),
  requires_manual_location: z.boolean(),
  location_source: z.string().optional(),
  exif_status: z.string().optional(),
});
export type UploadResult = z.infer<typeof uploadResultSchema>;

export const captureProcessingSchema = z.object({
  capture_id: z.string().uuid(),
  status: z.enum([
    'received',
    'queued',
    'processing_detection',
    'detection_completed',
    'building_event',
    'enriching_context',
    'building_features',
    'assessing',
    'completed',
    'no_supported_detection',
    'no_event',
    'needs_review',
    'failed',
    'model_not_available',
    'location_required',
  ]),
  requires_manual_location: z.boolean(),
  event_ids: z.array(z.string().uuid()),
  model_version_id: z.string().uuid().nullable(),
  model_status: z.string().nullable().optional(),
  updated_at: z.string().nullable(),
});
export type CaptureProcessing = z.infer<typeof captureProcessingSchema>;

export const captureMarkerSchema = z.object({
  id: z.string().uuid(),
  latitude: z.number().finite().min(-90).max(90),
  longitude: z.number().finite().min(-180).max(180),
  report_status: z.enum([
    'received',
    'model_not_available',
    'experimental',
    'human_confirmed',
    'no_supported_detection',
  ]),
  event_id: z.string().uuid().nullable().optional(),
  user_description: z.string().nullable().optional(),
  location_source: z.string().optional(),
  location_conflict: z.boolean().nullable().optional(),
  accuracy_m: optionalNumber,
  urmind_class: z.string().nullable().optional(),
  severity: z.string().nullable().optional(),
  priority_score: optionalNumber,
});
export type CaptureMarker = z.infer<typeof captureMarkerSchema>;
export const reportLabels: Record<CaptureMarker['report_status'], string> = {
  received: 'Relato recebido — aguardando análise',
  model_not_available: 'Análise indisponível — sem modelo autorizado',
  experimental: 'Análise experimental',
  human_confirmed: 'Confirmado por revisão humana',
  no_supported_detection: 'Relato recebido — nenhum problema das classes suportadas identificado',
};

const detectionSchema = z.object({
  id: z.string().uuid(),
  urmind_class: z.string(),
  confidence: z.number().min(0).max(1),
  bbox: z.object({ x: z.number(), y: z.number(), width: z.number(), height: z.number() }),
  model_version_id: z.string().uuid().nullable(),
});
export const eventDetailSchema = eventSchema.extend({
  image_url: z.string().url().nullable(),
  capture: z
    .object({
      id: z.string().uuid(),
      capture_key: z.string(),
      source: z.string(),
      source_location: z.string(),
      captured_at: z.string(),
      storage_path: z.string().nullable(),
    })
    .nullable(),
  detections: z.array(detectionSchema),
  risk: z
    .object({
      severity: z.string(),
      priority_score: z.number().nullable(),
      uncertainty: z.number().nullable(),
      factors: z.record(z.string(), z.unknown()),
      created_at: z.string(),
    })
    .nullable(),
  responsibility: z
    .union([
      z.object({ responsible: z.string(), source: z.string(), version: z.string() }),
      z.literal('requires_triage'),
    ])
    .nullable(),
  action: z.object({ code: z.string(), label: z.string(), version: z.string() }).nullable(),
  report: z.string().nullable(),
  context: z
    .array(
      z.object({
        source: z.string(),
        status: z.string(),
        fetched_at: z.string(),
        data: z.record(z.string(), z.unknown()),
        error: z.string().nullable().optional(),
      }),
    )
    .default([]),
  reviews: z.array(
    z.object({
      decision: z.enum(['confirm', 'correct', 'reject']),
      corrected_class: z.string().nullable(),
      notes: z.string().nullable(),
      reviewer: z.string(),
      created_at: z.string(),
    }),
  ),
});
export type EventDetail = z.infer<typeof eventDetailSchema>;

export type ReviewPayload =
  | { decision: 'confirm' | 'reject'; notes?: string }
  | {
      decision: 'correct';
      corrected_class?: keyof typeof classes;
      corrected_location?: Coordinate;
      notes?: string;
    };

export const severities: Record<string, string> = {
  unknown: 'Não determinada',
  low: 'Baixa',
  medium: 'Média',
  high: 'Alta',
  critical: 'Crítica',
};
