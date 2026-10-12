import { LOCATION_SOURCES } from '../components/reports/format';
import { severityOf } from '../domain/public';
import { plain } from './plainLanguage';

/**
 * "Detalhes técnicos" em linguagem simples. A API descreve o caminho da análise com
 * chaves internas (`location_source`, `fontes_consultadas`), códigos (`pwa_photo`,
 * `high`, `risk-v1`) e números em formato de máquina; aqui cada um vira rótulo e valor
 * que qualquer pessoa lê. Nome próprio (do modelo, da fonte de dados) fica como veio,
 * sempre com um rótulo que diz o que é.
 *
 * Para mudar um texto, edite as listas abaixo (a chave é o código exato da API).
 */

const number = new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 2 });
const percent = new Intl.NumberFormat('pt-BR', { style: 'percent', maximumFractionDigits: 0 });

/** Data e hora curtas, sem segundos: "17/09/2026, 09:00". */
export function shortDateTime(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' });
}

/** "risk-v1", "actions-v1", "urmind-issue-taxonomy-v4" → "versão 1". */
export function versionName(code: string | null | undefined): string | null {
  if (!code) return null;
  const found = /-v(\d+)$/i.exec(code.trim());
  return found ? `versão ${found[1]}` : code;
}

/** Fase do modelo automático no registro de modelos. Fase desconhecida não aparece. */
const MODEL_STAGES: Record<string, string> = {
  Production: 'em uso',
  Staging: 'em avaliação',
  Archived: 'desativado',
  EXPERIMENTAL_SHADOW: 'experimental, ainda não aprovado para uso',
};

export function stageName(stage: string | null | undefined): string | null {
  return stage ? (MODEL_STAGES[stage] ?? null) : null;
}

/** Fatores da prioridade pela chave da API (`risk.explanation[].factor`). */
const FACTOR_NAMES: Record<string, string> = {
  severity: 'gravidade típica deste tipo de problema',
  confidence: 'certeza da análise da foto',
  sensitive_proximity: 'perto de escola, serviço de saúde ou travessia',
  accessibility: 'impacto em quem anda a pé ou tem mobilidade reduzida',
  recurrence: 'outros problemas no mesmo trecho',
  environment: 'chuva recente',
};

export function factorName(factor: string, label: string): string {
  return FACTOR_NAMES[factor] ?? plain(label);
}

/** Por que a prioridade subiu ou desceu, em frase de gente (completa "Aumentou porque…"). */
const FACTOR_REASONS: Record<string, { up: string; down: string }> = {
  severity: {
    up: 'esse tipo de problema costuma ser grave',
    down: 'esse tipo de problema costuma ser leve',
  },
  confidence: {
    up: 'a foto mostra bem o problema',
    down: 'a foto deixa dúvidas sobre o problema',
  },
  sensitive_proximity: {
    up: 'fica perto de escola, serviço de saúde ou travessia',
    down: 'não fica perto de escola, serviço de saúde ou travessia',
  },
  accessibility: {
    up: 'atrapalha quem anda a pé',
    down: 'atrapalha pouco quem anda a pé',
  },
  recurrence: {
    up: 'há outros problemas no mesmo trecho',
    down: 'não há outros problemas no mesmo trecho',
  },
  environment: {
    up: 'choveu nos últimos dias',
    down: 'não choveu nos últimos dias',
  },
};

export function factorReason(factor: string, direction: 'up' | 'down'): string | null {
  return FACTOR_REASONS[factor]?.[direction] ?? null;
}

/** "a", "a e b", "a, b e c". */
export function listJoin(items: string[]): string {
  if (items.length < 2) return items.join('');
  return `${items.slice(0, -1).join(', ')} e ${items.at(-1)}`;
}

/** Código solto (`algum_codigo`) em palavras, para nada em snake_case chegar à tela. */
export function readable(text: string): string {
  if (!/^[a-z0-9]+(?:_[a-z0-9]+)+$/i.test(text)) return text;
  const words = text.replace(/_/g, ' ').toLowerCase();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** Etapas do caminho da análise, com os nomes usados em "Como o UrMind funciona". */
const STEP_NAMES: Record<string, string> = {
  capture: 'Relato',
  detection: 'Análise automática',
  context: 'Contexto do local',
  risk: 'Gravidade e prioridade',
  decision: 'Responsável',
  action: 'Solução indicada',
};

const CAPTURE_SOURCES: Record<string, string> = {
  pwa_photo: 'foto tirada pelo site',
  exif_upload: 'foto escolhida da galeria',
  scout: 'câmera do robô',
  import: 'foto importada pela equipe',
};

const CONTEXT_SOURCES: Record<string, string> = {
  nominatim_reverse: 'OpenStreetMap (endereço)',
  overpass_pois: 'OpenStreetMap (arredores)',
  open_meteo_rain: 'Open-Meteo (chuva)',
};

const PRIORITY_LANES: Record<string, string> = {
  routine: 'normal',
  elevated: 'alta',
  expedited: 'urgente',
};

/** Rótulo de cada dado do caminho da análise, pela chave da API. */
const DETAIL_NAMES: Record<string, string> = {
  location_source: 'Origem do local',
  classe: 'Tipo de problema',
  detecções: 'Detecções',
  confiança: 'Certeza',
  fontes_consultadas: 'Fontes consultadas',
  fontes_disponíveis: 'Fontes que responderam',
  severidade: 'Gravidade',
  prioridade: 'Prioridade',
  incerteza: 'Incerteza',
  responsável: 'Responsável',
  estado: 'Situação',
  ação: 'Ação',
};

function detailValue(key: string, value: unknown): string | null {
  if (value == null || value === '') return null;
  const numeric = typeof value === 'number' ? value : Number.NaN;
  if (key === 'confiança') {
    const share = typeof value === 'string' ? Number(value) : numeric;
    return Number.isFinite(share) ? percent.format(share) : null;
  }
  if (key === 'location_source') return LOCATION_SOURCES[String(value)] ?? readable(String(value));
  if (key === 'severidade') return severityOf(String(value)).label;
  if (key === 'prioridade') {
    if (Number.isFinite(numeric)) return `${number.format(numeric)} de 1`;
    return PRIORITY_LANES[String(value)] ?? readable(String(value));
  }
  if (typeof value === 'boolean') return value ? 'sim' : 'não';
  if (Number.isFinite(numeric)) return number.format(numeric);
  return readable(plain(String(value)));
}

function stepSource(step: TraceStep): string | null {
  const source = step.source?.trim();
  if (!source) return null;
  if (step.step === 'capture') return CAPTURE_SOURCES[source] ?? plain(source);
  if (step.step === 'context')
    return source
      .split(/,\s*/)
      .map((item) => CONTEXT_SOURCES[item] ?? item)
      .join(', ');
  if (step.step === 'risk') return `regras de prioridade, ${versionName(source)}`;
  const catalog = /^actions_catalog\s+(\S+)$/.exec(source);
  if (catalog) return `lista de ações, ${versionName(catalog[1])}`;
  return plain(source);
}

export interface TraceStep {
  step: string;
  status: string;
  title: string;
  source: string | null;
  detail: Record<string, unknown>;
  at: string | null;
}

/** Uma etapa do caminho da análise: nome, se tem registro e os dados que existem. */
export function traceStep(step: TraceStep): {
  name: string;
  done: boolean;
  rows: Array<[string, string]>;
} {
  const rows: Array<[string, string]> = [];
  const source = stepSource(step);
  if (source) rows.push(['Fonte', source]);
  for (const [key, value] of Object.entries(step.detail)) {
    const shown = detailValue(key, value);
    if (shown != null) rows.push([DETAIL_NAMES[key] ?? readable(key), shown]);
  }
  const when = shortDateTime(step.at);
  if (when) rows.push(['Quando', when]);
  return {
    name: STEP_NAMES[step.step] ?? plain(step.title),
    done: step.status === 'done',
    rows,
  };
}
