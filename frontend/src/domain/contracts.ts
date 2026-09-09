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
