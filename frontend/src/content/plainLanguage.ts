/**
 * Textos da API em linguagem simples. A API descreve regras, fontes e limites com o
 * vocabulário técnico do backend; aqui cada frase conhecida ganha a versão que qualquer
 * pessoa entende. Frase nova ou alterada na API aparece como veio: nada aqui afirma
 * uma capacidade que a API não declarou.
 *
 * Para mudar um texto, edite a lista abaixo (a chave é o texto exato da API).
 */
const PLAIN_PHRASES: Record<string, string> = {
  // Regras (GET /public/transparency → rules)
  'regras versionadas, sem LLM':
    'Calculadas por regras fixas e públicas. Nenhuma inteligência artificial escreve ou decide.',
  'tabela de competência com fundamento legal; sem regra, requer triagem':
    'O órgão que a lei indica para cada tipo de problema. Sem regra para o local, a equipe define.',
  'catálogo versionado de ações; sempre sugestão':
    'Escolhida de uma lista fixa de providências e sempre apresentada como sugestão.',
  // Fontes de contexto (→ context_sources[].use)
  'malha viária e equipamentos próximos': 'ruas e serviços próximos ao ponto',
  'endereço aproximado (contexto, não é a coordenada)': 'endereço aproximado do ponto',
  'chuva nas 24 h anteriores': 'chuva nas 24 horas anteriores',
  // Limites (→ limitations)
  'Sem detector automático ativo.': 'Ainda não há análise automática das fotos.',
  'A publicação exige revisão humana.': 'Nada é publicado sem a revisão de uma pessoa da equipe.',
  'limiares ainda não calibrados com revisão de campo':
    'Os critérios de gravidade e prioridade ainda não foram ajustados com vistorias no local.',
  // Detalhe da ocorrência (→ image.reason, prediction.reason)
  'imagem não publicada: o frame ainda não passou por sanitização (rostos, placas e dados pessoais), então só o dado estruturado é público':
    'A foto só é publicada depois que a equipe confere que não há rostos, placas de veículos ou dados pessoais à mostra.',
  'evento sem captura associada': 'Esta ocorrência não tem foto associada.',
  'ainda não há histórico validado suficiente para estimar a evolução deste problema':
    'Ainda não há casos revisados suficientes para estimar como este problema evolui.',
  // Fontes de contexto do detalhe (→ context[].label)
  'Endereço aproximado (Nominatim/OpenStreetMap)': 'Endereço aproximado (OpenStreetMap)',
  'Equipamentos próximos (Overpass/OpenStreetMap)': 'Serviços próximos (OpenStreetMap)',
  'Chuva nas 24 h anteriores (Open-Meteo)': 'Chuva nas 24 horas anteriores (Open-Meteo)',
  // Responsável (→ responsibility.source, responsibility.note)
  'jurisdição municipal do trecho': 'a rua é de responsabilidade da prefeitura',
  'é necessária validação da competência administrativa para este trecho':
    'A equipe ainda vai confirmar quem responde por este trecho.',
  // Lista de tipos de problema (→ /public/taxonomy: issues[].limitations)
  'Foto não comprova abandono. Nunca armazenar ou exibir placa; revisão e sanitização necessárias antes de ativação.':
    'A foto não comprova que o veículo foi abandonado. A placa nunca é guardada nem mostrada; antes de ativar este tipo, a equipe precisa revisar e cobrir as placas.',
  'Não inferir obrigação de corte; referências de altura e área devem ser estabelecidas no guia do novo dataset.':
    'A foto não comprova que é preciso cortar. Os limites de altura e de área ainda vão ser definidos no guia das novas fotos de treino.',
  'Categoria de serviço, sem caixa YOLO; definir unidade territorial, período e critério de suficiência antes de rotular.':
    'Depende de dados do serviço de ônibus, não da foto. Antes de usar este tipo, a equipe ainda vai definir a área, o período e o que conta como suficiente.',
  'Não rotular funcionamento por foto isolada; duração de observação deve ser definida no protocolo do local.':
    'Uma foto só não mostra se funciona. O tempo de observação no local ainda vai ser definido.',
  'Exige inspeção do local e referência do ativo; não inferir obrigação de instalação pela foto.':
    'Precisa de vistoria no local e da identificação do equipamento. A foto não mostra se a instalação é obrigatória.',
  // Tipos que a foto não basta para confirmar (→ issues[].photo_review_block_reason)
  'Confirmação por foto bloqueada: exige evidência contextual verificada do local, cobertura e referência do ativo. O fluxo atual não registra nem verifica essa evidência.':
    'Não dá para confirmar só pela foto: é preciso conferir no local o trecho inteiro e o equipamento. O UrMind ainda não registra essa conferência.',
  'Confirmação por foto bloqueada: exige evidência temporal verificada em sequência ou registro operacional. O fluxo atual não registra nem verifica essa evidência.':
    'Não dá para confirmar só pela foto: é preciso observar o local ao longo do tempo ou ter o registro de funcionamento. O UrMind ainda não registra isso.',
  'Confirmação por foto bloqueada: exige evidência de serviço verificada de cobertura, frequência e demanda. O fluxo atual não registra nem verifica essa evidência.':
    'Não dá para confirmar só pela foto: depende de dados do serviço (cobertura, frequência e demanda). O UrMind ainda não registra esses dados.',
  // Limites da avaliação (→ risk.limitations)
  'limiares não calibrados com revisão de campo':
    'Os critérios de gravidade e prioridade ainda não foram ajustados com vistorias no local.',
};

/**
 * Nome do tipo de problema no site do cidadão. A lista oficial (da API) usa termos de
 * engenharia em alguns tipos; para o cidadão eles ganham o nome do dia a dia. A área
 * da equipe continua com o nome oficial. A chave é o código do tipo.
 */
export const PLAIN_ISSUE_NAMES: Record<string, string> = {
  URMIND_ROAD_D00: 'Rachadura ao longo da rua',
  URMIND_ROAD_D10: 'Rachadura atravessando a rua',
  URMIND_ROAD_D20: 'Asfalto com muitas rachaduras',
  URMIND_DAMAGED_BARRIER: 'Grade ou mureta de proteção danificada',
  URMIND_FADED_ROAD_MARKING: 'Faixa apagada',
  URMIND_VEGETATION_OBSTRUCTION: 'Vegetação bloqueando a passagem',
  URMIND_VEGETATION_ON_POWER_LINES: 'Galho na fiação',
  URMIND_ILLEGAL_DUMPING: 'Lixo ou entulho descartado',
  URMIND_ROAD_DEBRIS: 'Objeto ou entulho na rua',
};

/** A versão simples de um texto da API, ou o próprio texto quando não há uma. */
export function plain(text: string): string {
  return PLAIN_PHRASES[text.trim()] ?? text;
}

/** Nome das regras de `transparency.rules`, pela chave da API. */
const RULE_NAMES: Record<string, string> = {
  severidade_e_prioridade: 'Gravidade e prioridade',
  responsável: 'Responsável',
  ação: 'Solução indicada',
};

export function ruleName(key: string): string {
  if (RULE_NAMES[key]) return RULE_NAMES[key];
  const text = key.replace(/_/g, ' ');
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** Nome comum das fontes externas; a fonte continua identificada pelo nome próprio. */
const SOURCE_NAMES: Record<string, string> = {
  'OpenStreetMap / Overpass': 'OpenStreetMap',
  Nominatim: 'OpenStreetMap',
};

export function sourceName(source: string): string {
  return SOURCE_NAMES[source] ?? source;
}

/** Área piloto pelo identificador da API (`/public/status → pilot_area`). */
const PILOT_AREAS: Record<string, string> = {
  'fecap-liberdade-3km': 'Bairro da Liberdade, em São Paulo, até 3\u00a0km da faculdade FECAP',
};

export function pilotAreaName(id: string | null | undefined): string {
  if (!id) return 'não definida';
  return PILOT_AREAS[id] ?? id;
}

const number = new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 1 });
const POI_NAMES: Record<string, string> = {
  school: 'escola',
  health: 'serviço de saúde',
  crossing: 'travessia de pedestres',
};

/** Resumo de uma fonte de contexto (endereço, serviços próximos, chuva) em uma frase. */
export function contextSummary(source: string, summary: Record<string, unknown>): string | null {
  if (source === 'nominatim_reverse') {
    // O mesmo formato das listas: rua e bairro; a cidade só quando falta o bairro.
    const parts = [summary.road, summary.suburb || summary.city].filter(Boolean);
    return parts.length ? parts.join(', ') : null;
  }
  if (source === 'overpass_pois') {
    const radius = typeof summary.radius_m === 'number' ? summary.radius_m : null;
    const found = Object.entries(POI_NAMES)
      .filter(([key]) => typeof summary[key] === 'number')
      .map(([key, name]) => `${name} a ${number.format(summary[key] as number)} m`);
    if (!found.length) return radius ? `Nada encontrado num raio de ${radius} m.` : null;
    return `${found.join(', ')}${radius ? ` (busca num raio de ${radius} m)` : ''}.`;
  }
  if (source === 'open_meteo_rain') {
    const rain = summary.rain_mm_24h;
    if (typeof rain !== 'number') return null;
    return rain === 0
      ? 'Sem chuva nas 24 horas anteriores.'
      : `${number.format(rain)} mm de chuva.`;
  }
  const entries = Object.entries(summary).filter(([, value]) => value != null);
  return entries.length
    ? entries.map(([key, value]) => `${key}: ${String(value)}`).join(', ')
    : null;
}
