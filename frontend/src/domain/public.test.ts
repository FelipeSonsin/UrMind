import { describe, expect, it } from 'vitest';
import { eventDetail, urbanAnalysis } from '../../tests/fixtures';
import {
  filterableClasses,
  exportMapRecords,
  filterMapRecords,
  issueTaxonomySchema,
  labelFor,
  modelSupportLabel,
  registerTaxonomy,
  type IssueDefinition,
  priorityBand,
  publicEventDetailSchema,
  publicEventSchema,
  publicScoutSchema,
  severityOf,
} from './public';

it('exporta somente campos permitidos e neutraliza fórmula CSV', () => {
  const rows = [
    {
      latitude: -23,
      longitude: -46,
      status: '=CMD()',
      urmind_class: 'D40',
      uploaded_by: 'private-owner',
      storage_path: 'private/photo',
      description: 'private text',
    },
  ];
  const exported = exportMapRecords(rows);
  expect(exported.csv).toContain("'=CMD()");
  expect(JSON.stringify(exported)).not.toContain('private');
  expect(exported.geojson.features[0].geometry.coordinates).toEqual([-46, -23]);
  expect(exportMapRecords([{ latitude: null, longitude: null }]).geojson.features).toEqual([]);
});

it('filtros do mapa não inventam período para relato sem data', () => {
  const rows = [
    { status: 'confirmed', urmind_class: 'A', created_at: '2026-09-24T12:00:00Z' },
    { report_status: 'received', urmind_class: null, created_at: null },
  ];
  const filters = { status: '', family: '', issue: '', from: '', to: '' };
  expect(filterMapRecords(rows, filters)).toHaveLength(2);
  expect(filterMapRecords(rows, { ...filters, from: '2026-09-24', to: '2026-09-24' })).toEqual([
    rows[0],
  ]);
  expect(filterMapRecords(rows, { ...filters, status: 'received' })).toEqual([rows[1]]);
  expect(filterMapRecords(rows, { ...filters, issue: 'missing' })).toEqual([]);
});

const event = {
  id: '3f8b9d3a-2f0c-4f1e-9b1a-4f6a0d5e7c11',
  occurred_at: '2026-09-17T12:00:00+00:00',
  urmind_class: 'URMIND_ROAD_D40',
  status: 'triaged',
  evidence_mode: 'photo',
  visual_confidence: 0.61,
  severity: 'high',
  priority_score: 0.42,
  latitude: -23.55,
  longitude: -46.63,
  snapped_latitude: null,
  snapped_longitude: null,
  road_name: 'Rua da Glória',
};

describe('apresentação pública sem inventar dado', () => {
  it('traduz a classe técnica e mantém a original quando não há rótulo', () => {
    expect(labelFor('URMIND_ROAD_D40')).toBe('Buraco');
    expect(labelFor('URMIND_FUTURO')).toBe('URMIND_FUTURO');
  });

  it('nunca representa severidade só por cor: há rótulo e símbolo', () => {
    for (const level of ['critical', 'high', 'medium', 'low']) {
      const presentation = severityOf(level);
      expect(presentation.label.length).toBeGreaterThan(0);
      expect(presentation.shape.length).toBeGreaterThan(0);
    }
  });

  it('severidade ausente ou desconhecida vira "não determinada", não "baixa"', () => {
    expect(severityOf(null).level).toBe('unknown');
    expect(severityOf(undefined).label).toBe('Não determinada');
    expect(severityOf('inexistente').level).toBe('unknown');
  });

  it('prioridade sem número não recebe faixa plausível', () => {
    expect(priorityBand(null)).toBe('não calculada');
    expect(priorityBand(undefined)).toBe('não calculada');
  });

  it.each([
    [0.9, 'muito alta'],
    [0.75, 'muito alta'],
    [0.5, 'alta'],
    [0.3, 'média'],
    [0.1, 'baixa'],
    [0, 'baixa'],
  ])('faixa de prioridade para %s', (score, expected) => {
    expect(priorityBand(score)).toBe(expected);
  });
});

describe('contrato público espelha o backend', () => {
  it('preserva análise determinística opcional e payload anterior', () => {
    expect(publicEventDetailSchema.parse(eventDetail).analysis).toBeUndefined();
    expect(publicEventDetailSchema.parse({ ...eventDetail, analysis: null }).analysis).toBeNull();
    expect(
      publicEventDetailSchema.parse({ ...eventDetail, analysis: urbanAnalysis }).analysis
        ?.description,
    ).toBe(urbanAnalysis.description);
  });

  it('recusa consequência incondicional e origem não persistida', () => {
    for (const consequence of [
      { ...urbanAnalysis.potential_consequences[0], conditional: false },
      { ...urbanAnalysis.potential_consequences[0], source: 'generated' },
    ]) {
      expect(
        publicEventDetailSchema.safeParse({
          ...eventDetail,
          analysis: { ...urbanAnalysis, potential_consequences: [consequence] },
        }).success,
      ).toBe(false);
    }
  });
  it('aceita a ocorrência pública real', () => {
    expect(publicEventSchema.safeParse(event).success).toBe(true);
  });

  it('recusa confiança em escala de porcentagem ou id fabricado', () => {
    expect(publicEventSchema.safeParse({ ...event, visual_confidence: '61%' }).success).toBe(false);
    expect(publicEventSchema.safeParse({ ...event, id: 'evento-1' }).success).toBe(false);
  });

  it('exige severidade e prioridade nulas quando não há avaliação', () => {
    const parsed = publicEventSchema.parse({ ...event, severity: null, priority_score: null });
    expect(parsed.severity).toBeNull();
    expect(severityOf(parsed.severity).level).toBe('unknown');
  });

  it('detalhe sem risco, ação ou previsão continua válido e explicitamente vazio', () => {
    const detail = publicEventDetailSchema.parse({
      ...event,
      severity: null,
      priority_score: null,
      distance_to_road_m: null,
      location_accuracy_m: null,
      road: null,
      detections: [],
      image: {
        available: false,
        privacy_redacted: false,
        reason: 'imagem não publicada',
        url: null,
      },
      risk: null,
      action: null,
      responsibility: {
        status: 'requires_triage',
        responsible: null,
        source: null,
        version: null,
        note: 'sem regra aplicável',
      },
      context: [],
      prediction: {
        available: false,
        reason: 'ainda não há histórico validado suficiente',
        task: null,
        horizon_days: null,
        value: null,
        uncertainty: null,
        model_version: null,
        generated_at: null,
      },
      trace: [
        {
          step: 'risk',
          status: 'unavailable',
          title: 'Risco',
          source: null,
          detail: {},
          at: null,
        },
      ],
      model_version: null,
      model_stage: null,
      dataset_version: null,
      reviewed: false,
    });
    expect(detail.prediction.available).toBe(false);
    expect(detail.prediction.value).toBeNull();
    expect(detail.responsibility.status).toBe('requires_triage');
    expect(detail.trace[0].status).toBe('unavailable');
  });

  it('estado do Scout fora do enum real é rejeitado', () => {
    const scout = publicScoutSchema.safeParse({
      status: 'transmitindo',
      device_code: null,
      last_seen: null,
      camera: {
        mode: 'unavailable',
        reason: 'sem câmera',
        stream_url: null,
        frame_url: null,
        latency_ms: null,
      },
      telemetry: {},
      mission: null,
    });
    expect(scout.success).toBe(false);
  });

  it('modo de câmera fora dos estados reais é rejeitado', () => {
    const scout = publicScoutSchema.safeParse({
      status: 'no_device',
      device_code: null,
      last_seen: null,
      camera: { mode: 'demo', reason: null, stream_url: null, frame_url: null, latency_ms: null },
      telemetry: {},
      mission: null,
    });
    expect(scout.success).toBe(false);
  });
});

describe('taxonomia canônica', () => {
  const issue = (code: string, status: IssueDefinition['model_support_status'], may: boolean) => ({
    issue_code: code,
    taxonomy_version: 'urmind-issue-taxonomy-v2',
    family: 'ROAD_SURFACE',
    display_name_pt: `rótulo ${code}`,
    display_name_en: code,
    description: 'd',
    visual_definition: 'v',
    included_examples: ['a'],
    excluded_examples: ['b'],
    model_support_status: status,
    dataset_status: 'NEEDS_MORE_DATA',
    review_status: 'PENDING_HUMAN_REVIEW',
    responsibility_domain: 'x',
    version: 1,
    related_legacy_codes: [],
    model_may_emit: may,
  });

  it('usa rótulos da API e filtra só classes que o modelo pode emitir', () => {
    registerTaxonomy(
      issueTaxonomySchema.parse({
        taxonomy_version: 'urmind-issue-taxonomy-v2',
        issues: [
          issue('URMIND_ROAD_D40', 'EXPERIMENTAL_MODEL', true),
          issue('URMIND_FALLEN_TREE', 'DATA_REQUIRED', false),
        ],
      }),
    );
    expect(labelFor('URMIND_FALLEN_TREE')).toBe('rótulo URMIND_FALLEN_TREE');
    expect(filterableClasses().map(([code]) => code)).toEqual(['URMIND_ROAD_D40']);
  });

  it('classe sem dados nunca aparece como reconhecida pela IA', () => {
    const label = modelSupportLabel({ model_support_status: 'DATA_REQUIRED' });
    expect(label).toBe('Em desenvolvimento');
    expect(label.toLowerCase()).not.toContain('reconhec');
    expect(modelSupportLabel({ model_support_status: 'EXPERIMENTAL_MODEL' })).toBe(
      'Análise experimental',
    );
  });
});
