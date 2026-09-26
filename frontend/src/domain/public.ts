import { z } from 'zod';
import { automaticClasses } from './capabilities';

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

export const publicEventSchema = z.object({
  id: z.string().min(12),
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

const publicEventDetailBase = publicEventSchema.extend({
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
      impact: z.array(z.string()).default([]),
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
export const urbanAnalysisSchema = z.object({
  schema_version: z.literal('urmind-urban-analysis-v1').default('urmind-urban-analysis-v1'),
  identification: z.object({
    issue_code: z.string(),
    display_name: z.string(),
    family: z.string().nullable(),
    model_support_status: z.string().nullable(),
    visual_confidence: z.number().nullable(),
    reviewed: z.boolean(),
  }),
  description: z.string(),
  diagnosis: z.string(),
  potential_consequences: z
    .array(
      z.object({
        domain: z.string(),
        statement: z.string(),
        conditional: z.literal(true).default(true),
        source: z.literal('persisted_phase5').default('persisted_phase5'),
      }),
    )
    .default([]),
  possible_causes: z.array(z.string()).default([]),
  severity: z.string().nullable(),
  risk_level: z.string().nullable(),
  priority_lane: z.string().nullable(),
  action: publicEventDetailBase.shape.action,
  responsibility: publicEventDetailBase.shape.responsibility,
  responsibility_domain: z
    .enum([
      'ROAD_MAINTENANCE',
      'URBAN_FORESTRY',
      'DRAINAGE',
      'URBAN_CLEANING',
      'PEDESTRIAN_INFRASTRUCTURE',
      'TRAFFIC_AUTHORITY',
      'PUBLIC_LIGHTING',
      'CIVIL_DEFENSE',
      'GENERAL_INSPECTION',
    ])
    .nullable(),
  context: publicEventDetailBase.shape.context.default([]),
  limitations: z.array(z.string()).default([]),
  provenance: z.object({
    taxonomy_version: z.string(),
    model_version: z.string().nullable(),
    model_stage: z.string().nullable(),
    dataset_version: z.string().nullable(),
    ruleset_version: z.string().nullable(),
    assessed_at: z.string().nullable(),
    assessment_source: z.enum(['persisted_phase5', 'unavailable']),
    method: z.literal('deterministic_template').default('deterministic_template'),
  }),
});
export const publicEventDetailSchema = publicEventDetailBase.extend({
  analysis: urbanAnalysisSchema.nullable().optional(),
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

/**
 * Gravidade em palavras do público, sempre vinda da avaliação registrada. Cada nível
 * tem rótulo e símbolo próprios (WCAG); sem avaliação, nunca vira "leve".
 */
export const severityPresentation: Record<string, { label: string; shape: string; level: string }> =
  {
    critical: { label: 'Crítica', shape: '▲▲', level: 'critical' },
    high: { label: 'Grave', shape: '▲', level: 'high' },
    medium: { label: 'Moderada', shape: '■', level: 'medium' },
    low: { label: 'Leve', shape: '●', level: 'low' },
    unknown: { label: 'Ainda não avaliada', shape: '?', level: 'unknown' },
  };

/** Níveis avaliados, do mais grave ao mais leve (ordem dos filtros e da legenda). */
export const SEVERITY_ORDER = ['critical', 'high', 'medium', 'low'] as const;

/** "Buraco · Grave": tipo e gravidade separados; sem avaliação, só o tipo. */
export function issueHeadline(code: string | null | undefined, severity?: string | null): string {
  const type = code ? labelFor(code) : 'Problema ainda não identificado';
  const assessed = severity && severity !== 'unknown' ? severityPresentation[severity] : undefined;
  return assessed ? `${type} · ${assessed.label}` : type;
}

/**
 * Situação pública agrupada. O estado técnico continua no dado (e na área da equipe);
 * o público vê só o caminho do relato: recebido, em análise, confirmado ou pendente de local.
 * Recusado ou duplicado aparece como "Não confirmado", nunca como confirmado.
 */
export type PublicSituation =
  'received' | 'analyzing' | 'confirmed' | 'needs_location' | 'declined';
export const publicSituationLabels: Record<PublicSituation, string> = {
  received: 'Recebido',
  analyzing: 'Em análise',
  confirmed: 'Confirmado',
  needs_location: 'Precisa de localização',
  declined: 'Não confirmado',
};
const SITUATION_BY_STATUS: Record<string, PublicSituation> = {
  received: 'received',
  // Etapas internas do processamento (Worker): para o público, só "Em análise".
  queued: 'received',
  processing_detection: 'analyzing',
  detection_completed: 'analyzing',
  building_event: 'analyzing',
  enriching_context: 'analyzing',
  building_features: 'analyzing',
  assessing: 'analyzing',
  completed: 'analyzing',
  no_event: 'analyzing',
  needs_review: 'analyzing',
  failed: 'analyzing',
  processing: 'analyzing',
  model_not_available: 'analyzing',
  experimental: 'analyzing',
  no_supported_detection: 'analyzing',
  human_confirmed: 'confirmed',
  published: 'confirmed',
  location_required: 'needs_location',
  rejected: 'declined',
  duplicate: 'declined',
  // Ocorrências (Event)
  detected: 'analyzing',
  review: 'analyzing',
  triage_required: 'analyzing',
  confirmed: 'confirmed',
};
export function publicSituation(status: string | null | undefined): PublicSituation {
  return SITUATION_BY_STATUS[status ?? ''] ?? 'received';
}
export function situationLabel(status: string | null | undefined): string {
  return publicSituationLabels[publicSituation(status)];
}

/** Taxonomia canônica servida por `/public/taxonomy` (fonte única de classes). */
export const issueTaxonomySchema = z.object({
  taxonomy_version: z.string(),
  issues: z.array(
    z.object({
      issue_code: z.string(),
      taxonomy_version: z.string(),
      family: z.string(),
      display_name_pt: z.string(),
      display_name_en: z.string(),
      description: z.string(),
      visual_definition: z.string(),
      included_examples: z.array(z.string()),
      excluded_examples: z.array(z.string()),
      model_support_status: z.enum([
        'ACTIVE_MODEL',
        'EXPERIMENTAL_MODEL',
        'DATA_REQUIRED',
        'REVIEW_ONLY',
        'DISABLED',
      ]),
      dataset_status: z.string(),
      review_status: z.string(),
      responsibility_domain: z.string(),
      version: z.number().int(),
      related_legacy_codes: z.array(z.string()),
      model_may_emit: z.boolean(),
      risk_groups: z.array(z.string()).optional(),
      photo_detectable: z.union([z.boolean(), z.literal('limited')]).optional(),
      limitations: z.array(z.string()).optional(),
      triage_priority_hint: z.string().nullable().optional(),
    }),
  ),
});
export type IssueTaxonomy = z.infer<typeof issueTaxonomySchema>;
export type IssueDefinition = IssueTaxonomy['issues'][number];

const taxonomyLabels = new Map<string, string>();
const taxonomyFamilies = new Map<string, string>();
let emittableCodes: string[] = [];

/** Registra os rótulos canônicos; o mapa estático fica só como fallback offline. */
export function registerTaxonomy(taxonomy: IssueTaxonomy): void {
  taxonomyLabels.clear();
  taxonomyFamilies.clear();
  for (const issue of taxonomy.issues) {
    taxonomyLabels.set(issue.issue_code, issue.display_name_pt);
    taxonomyFamilies.set(issue.issue_code, issue.family);
  }
  emittableCodes = taxonomy.issues.filter((i) => i.model_may_emit).map((i) => i.issue_code);
}

export function familyFor(code?: string | null): string {
  return code ? (taxonomyFamilies.get(code) ?? '') : '';
}

/** Nome público das famílias da taxonomia; código desconhecido aparece como está. */
export const familyLabels: Record<string, string> = {
  ROAD_SURFACE: 'Pavimento',
  DRAINAGE: 'Drenagem',
  VEGETATION_OBSTRUCTION: 'Vegetação',
  PEDESTRIAN_INFRASTRUCTURE: 'Calçadas e pedestres',
  TRAFFIC_INFRASTRUCTURE: 'Sinalização e trânsito',
  URBAN_INFRASTRUCTURE: 'Infraestrutura urbana',
  WASTE_OBSTRUCTION: 'Resíduos e entulho',
};
export function familyLabel(family: string): string {
  return familyLabels[family] ?? family;
}

export type MapFilters = {
  status: string;
  family: string;
  issue: string;
  from: string;
  to: string;
};

/** Início do dia `yyyy-mm-dd` no fuso de quem usa o mapa (o dia que a pessoa escolheu). */
export function localDayStart(isoDate: string): number {
  const [year, month, day] = isoDate.split('-').map(Number);
  return new Date(year, month - 1, day).getTime();
}

/** `dd/mm/aaaa` → `aaaa-mm-dd`; data inexistente ou incompleta → null. */
export function parseBrDate(text: string): string | null {
  const match = /^(\d{2})\/(\d{2})\/(\d{4})$/.exec(text.trim());
  if (!match) return null;
  const [, day, month, year] = match.map(Number);
  const date = new Date(year, month - 1, day);
  if (date.getFullYear() !== year || date.getMonth() !== month - 1 || date.getDate() !== day)
    return null;
  return `${match[3]}-${match[2]}-${match[1]}`;
}

/** Enquanto a pessoa digita: só dígitos, com as barras de `dd/mm/aaaa` no lugar. */
export function maskBrDate(text: string): string {
  const digits = text.replace(/\D/g, '').slice(0, 8);
  return [digits.slice(0, 2), digits.slice(2, 4), digits.slice(4)].filter(Boolean).join('/');
}

/** `aaaa-mm-dd` → `dd/mm/aaaa`. */
export function formatBrDate(isoDate: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(isoDate);
  return match ? `${match[3]}/${match[2]}/${match[1]}` : '';
}

export function filterMapRecords<
  T extends {
    urmind_class?: string | null;
    report_status?: string;
    status?: string;
    created_at?: string | null;
    occurred_at?: string | null;
  },
>(records: T[], filters: MapFilters): T[] {
  return records.filter((row) => {
    const stamp = row.created_at ?? row.occurred_at;
    const timestamp = stamp ? Date.parse(stamp) : NaN;
    return (
      (!filters.status || (row.report_status ?? row.status) === filters.status) &&
      (!filters.family || familyFor(row.urmind_class) === filters.family) &&
      (!filters.issue || row.urmind_class === filters.issue) &&
      (!filters.from || timestamp >= localDayStart(filters.from)) &&
      (!filters.to || timestamp < localDayStart(filters.to) + 86_400_000)
    );
  });
}

/**
 * Opções reais dos filtros do mapa: o vocabulário completo do tipo de ponto exibido
 * (relato ou ocorrência) e as famílias/classes que existem nos pontos ou que o modelo
 * pode emitir. Com uma família escolhida, só as classes dela.
 */
export function mapFilterOptions(
  records: Array<{ urmind_class?: string | null; report_status?: string; status?: string }>,
  family: string,
  vocabulary: { reports: Record<string, string>; events: Record<string, string> },
): {
  statuses: [string, string][];
  families: [string, string][];
  issues: [string, string][];
} {
  const hasReports = records.some((row) => row.report_status);
  const eventStatuses = new Set(
    records.filter((row) => !row.report_status && row.status).map((row) => row.status!),
  );
  const statuses: [string, string][] = [
    ...(hasReports ? Object.entries(vocabulary.reports) : []),
    ...Object.entries(vocabulary.events).filter(([code]) => eventStatuses.has(code)),
  ];
  const codes = new Set(emittableCodes);
  for (const row of records) if (row.urmind_class) codes.add(row.urmind_class);
  const families = [...new Set([...codes].map((code) => familyFor(code)).filter(Boolean))]
    .sort()
    .map((code): [string, string] => [code, familyLabel(code)]);
  const issues = [...codes]
    .filter((code) => !family || familyFor(code) === family)
    .map((code): [string, string] => [code, labelFor(code)])
    .sort((a, b) => a[1].localeCompare(b[1], 'pt-BR'));
  return { statuses, families, issues };
}

/** Filtros do mapa público: tipo de problema, gravidade e período. */
export type PublicMapFilters = { issue: string; severity: string; period: string };
export const EMPTY_PUBLIC_FILTERS: PublicMapFilters = { issue: '', severity: '', period: '' };
export const PERIOD_OPTIONS: [string, string][] = [
  ['7', 'Últimos 7 dias'],
  ['30', 'Últimos 30 dias'],
  ['90', 'Últimos 90 dias'],
];

type PublicMapRecord = {
  urmind_class?: string | null;
  severity?: string | null;
  created_at?: string | null;
  occurred_at?: string | null;
};

export function filterPublicMap<T extends PublicMapRecord>(
  records: T[],
  filters: PublicMapFilters,
  now = Date.now(),
): T[] {
  const days = Number(filters.period);
  const since = days > 0 ? now - days * 86_400_000 : null;
  return records.filter((row) => {
    const stamp = row.created_at ?? row.occurred_at;
    return (
      (!filters.issue || row.urmind_class === filters.issue) &&
      (!filters.severity || (row.severity ?? 'unknown') === filters.severity) &&
      (since == null || (stamp != null && Date.parse(stamp) >= since))
    );
  });
}

/**
 * Opções reais: só os tipos e as gravidades que existem nos pontos carregados. Nada
 * de listar a taxonomia inteira nem categoria que ninguém registrou.
 */
export function publicMapFilterOptions(
  records: PublicMapRecord[],
  now = Date.now(),
): {
  issues: [string, string][];
  severities: [string, string][];
  periods: [string, string][];
} {
  const codes = new Set(records.map((row) => row.urmind_class).filter((c): c is string => !!c));
  const levels = new Set(records.map((row) => row.severity ?? 'unknown'));
  // Período só aparece quando separa pontos: nem vazio, nem igual a "qualquer data",
  // nem igual ao período mais curto já oferecido.
  let previous = -1;
  const periods = PERIOD_OPTIONS.filter(([days]) => {
    const inside = filterPublicMap(records, { issue: '', severity: '', period: days }, now).length;
    const useful = inside > 0 && inside < records.length && inside !== previous;
    if (useful) previous = inside;
    return useful;
  });
  return {
    periods,
    issues: [...codes]
      .map((code): [string, string] => [code, labelFor(code)])
      .sort((a, b) => a[1].localeCompare(b[1], 'pt-BR')),
    severities: [...SEVERITY_ORDER, 'unknown']
      .filter((level) => levels.has(level))
      .map((level): [string, string] => [level, severityOf(level).label]),
  };
}

/** Classes que podem aparecer como detecção de modelo (filtros de ocorrência). */
export function filterableClasses(): [string, string][] {
  return automaticClassCodes().map((code) => [code, labelFor(code)]);
}

/** Classes da detecção automática: taxonomia servida ou, offline, o registro de capacidades. */
export function automaticClassCodes(): readonly string[] {
  return automaticClasses(emittableCodes);
}

/**
 * O que o público pode ler sobre o suporte de modelo. DATA_REQUIRED nunca vira
 * "IA já reconhece": a classe está em desenvolvimento e não é detectada.
 */
export function modelSupportLabel(issue: Pick<IssueDefinition, 'model_support_status'>): string {
  switch (issue.model_support_status) {
    case 'ACTIVE_MODEL':
      return 'Reconhecida por modelo aprovado';
    case 'EXPERIMENTAL_MODEL':
      return 'Análise experimental';
    case 'REVIEW_ONLY':
      return 'Somente revisão humana';
    case 'DISABLED':
      return 'Desativada';
    default:
      return 'Em desenvolvimento';
  }
}

export function labelFor(urmindClass: string): string {
  return taxonomyLabels.get(urmindClass) ?? publicClassLabels[urmindClass] ?? urmindClass;
}

export function severityOf(severity: string | null | undefined) {
  return severityPresentation[severity ?? 'unknown'] ?? severityPresentation.unknown;
}

/** Explicit export allowlist: never spread a private Capture/Event into a download. */
export function exportMapRecords(
  records: Array<{
    latitude?: number | null;
    longitude?: number | null;
    urmind_class?: string | null;
    report_status?: string;
    status?: string;
    created_at?: string | null;
    occurred_at?: string | null;
  }>,
) {
  const rows = records
    .filter((row) => row.latitude != null && row.longitude != null)
    .map((row) => ({
      latitude: row.latitude!,
      longitude: row.longitude!,
      issue_code: row.urmind_class ?? '',
      status: row.report_status ?? row.status ?? '',
      date: row.created_at ?? row.occurred_at ?? '',
    }));
  const cell = (value: unknown) => {
    const text = String(value);
    return `"${(/^[=+@\-\t\r]/.test(text) ? `'${text}` : text).replaceAll('"', '""')}"`;
  };
  return {
    csv: [
      'latitude,longitude,issue_code,status,date',
      ...rows.map((row) => Object.values(row).map(cell).join(',')),
    ].join('\r\n'),
    geojson: {
      type: 'FeatureCollection',
      features: rows.map(({ latitude, longitude, ...properties }) => ({
        type: 'Feature',
        geometry: { type: 'Point', coordinates: [longitude, latitude] },
        properties,
      })),
    },
  };
}

/** Prioridade 0–1 vira faixa legível. Mantém o número ao lado, nunca no lugar. */
export function priorityBand(score: number | null | undefined): string {
  if (score == null) return 'não calculada';
  if (score >= 0.75) return 'muito alta';
  if (score >= 0.5) return 'alta';
  if (score >= 0.25) return 'média';
  return 'baixa';
}
