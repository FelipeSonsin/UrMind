import { describe, expect, it } from 'vitest';
import type { Transparency } from '../domain/public';
import { systemFlow } from './systemFlow';

const transparency = (model: string | null): Transparency => ({
  model_name: model,
  model_version: model ? '2026-10' : null,
  stage: model ? 'Staging' : null,
  stage_note: null,
  classes: [],
  input_size: [],
  score_threshold: null,
  metrics: {
    map50: null,
    map50_95: null,
    precision: null,
    recall: null,
    f1: null,
    per_class: {},
    not_computed: [],
    samples: null,
  },
  latency: {
    mean_ms: null,
    p50_ms: null,
    p95_ms: null,
    fps_approx: null,
    execution_provider: null,
    hardware: null,
  },
  dataset_name: null,
  dataset_version: null,
  dataset_license: null,
  dataset_source: null,
  context_sources: [{ source: 'OpenStreetMap / Overpass', use: 'malha viária' }],
  rules: {},
  limitations: [],
});

const detection = (stages: ReturnType<typeof systemFlow>) =>
  stages.find((stage) => stage.id === 'deteccao')!;

describe('caminho de um relato', () => {
  it('segue a ordem do Master Plan, com a revisão humana depois da detecção', () => {
    const stages = systemFlow({ transparency: null, automaticClasses: [] });
    expect(stages.map((stage) => stage.id)).toEqual([
      'relato',
      'checagem',
      'deteccao',
      'revisao',
      'contexto',
      'risco',
      'mapa',
    ]);
  });

  it('sem modelo, a análise automática é plano e nunca aparece ativa', () => {
    const stage = detection(systemFlow({ transparency: transparency(null), automaticClasses: [] }));
    expect(stage.state).toBe('planned');
    expect(stage.details.join(' ')).toContain('Hoje não há análise automática');
    expect(stage.planned).toContain('YOLO26s');
  });

  it('modelo registrado sem classe liberada continua planejado, citado como em avaliação', () => {
    const stage = detection(
      systemFlow({
        transparency: transparency('detector-teste'),
        automaticClasses: [],
      }),
    );
    expect(stage.state).toBe('planned');
    expect(stage.details.join(' ')).toContain('programa em avaliação (detector-teste)');
  });

  it('só fica ativa com modelo e classes liberadas pela taxonomia', () => {
    const stage = detection(
      systemFlow({
        transparency: transparency('detector-teste'),
        automaticClasses: ['URMIND_ROAD_D40'],
      }),
    );
    expect(stage.state).toBe('active');
    expect(stage.details[0]).toBe('Modelo detector-teste, versão 2026-10');
  });

  it('descreve a checagem da foto em palavras simples e mostra as fontes de contexto', () => {
    const stages = systemFlow({
      transparency: transparency(null),
      automaticClasses: [],
    });
    const check = stages.find((stage) => stage.id === 'checagem')!;
    // Os números (pixels, luz) ficam em “Ver medidas”, no envio; aqui, o que a pessoa entende.
    expect(check.details.join(' ')).toContain('nítida, com luz suficiente e um tamanho mínimo');
    expect(check.details.join(' ')).not.toMatch(/px|JPEG|PNG|WebP/);
    const context = stages.find((stage) => stage.id === 'contexto')!;
    // A fonte aparece pelo nome comum (OpenStreetMap), ainda identificada.
    expect(context.details.join(' ')).toContain('Fontes: OpenStreetMap.');
  });
});
