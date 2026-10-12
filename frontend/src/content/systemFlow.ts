import { activeCapabilities } from '../domain/capabilities';
import type { Transparency } from '../domain/public';
import { sourceName } from './plainLanguage';
import { stageName } from './technical';

/**
 * O caminho de um relato no UrMind, como o Master Plan descreve
 * (docs/Planinng/MASTER_PLAN.md, §2 e §6): relato → checagem da foto → [análise
 * automática, planejada] → revisão humana → contexto → gravidade e ação → mapa.
 *
 * Para mudar um texto, edite esta lista. O estado "ativo" ou "planejado" de cada etapa
 * vem dos dados reais da API sempre que eles existem; nada aqui liga um modelo.
 */
export type FlowState = 'active' | 'planned';

export interface FlowStage {
  id: string;
  /** Nome curto na trilha. */
  title: string;
  /** Uma linha sob o nome. */
  summary: string;
  state: FlowState;
  /** Quem faz ou decide nesta etapa. */
  who: string;
  /** O que acontece, em frases curtas. */
  details: string[];
  /** Parte desta etapa que ainda é plano (sem dizer que existe). */
  planned?: string;
}

/** "A, B e C". */
function list(items: string[]): string {
  return items.length < 2 ? items.join('') : `${items.slice(0, -1).join(', ')} e ${items.at(-1)}`;
}

export function systemFlow({
  transparency,
  automaticClasses,
}: {
  transparency: Transparency | null;
  /** Classes que a taxonomia oficial libera para a detecção (`model_may_emit`). */
  automaticClasses: readonly string[];
}): FlowStage[] {
  // Um modelo só conta como ativo quando a taxonomia libera classes para ele: a mera
  // presença de um modelo registrado não liga a etapa (Master Plan, §7).
  const model = transparency?.model_name ?? null;
  const detecting = Boolean(model) && automaticClasses.length > 0;
  const sources = [
    ...new Set(transparency?.context_sources.map((source) => sourceName(source.source)) ?? []),
  ];
  return [
    {
      id: 'relato',
      title: 'Relato',
      summary: 'Foto e local',
      state: 'active',
      who: activeCapabilities.robotCamera
        ? 'A pessoa que viu o problema, pelo celular ou pela câmera do robô.'
        : 'A pessoa que viu o problema, pelo celular.',
      details: [
        activeCapabilities.robotCamera
          ? 'A foto é tirada na hora, escolhida da galeria ou capturada pela câmera do robô.'
          : 'A foto é tirada na hora ou escolhida da galeria.',
        'O local vem do GPS do aparelho, da posição gravada na foto ou de um ponto marcado no mapa.',
      ],
    },
    {
      id: 'checagem',
      title: 'Checagem da foto',
      summary: 'Nitidez, luz e tamanho',
      state: 'active',
      who: 'O seu aparelho e, de novo, o UrMind ao receber a foto.',
      details: [
        'Vale foto comum de celular, com até 10 MB.',
        'Ela precisa estar nítida, com luz suficiente e um tamanho mínimo.',
        'A foto original fica guardada como evidência privada.',
      ],
    },
    detecting
      ? {
          id: 'deteccao',
          title: 'Análise automática',
          summary: 'Sugestão a partir da foto',
          state: 'active',
          who: 'Um programa que reconhece problemas em fotos, sempre como sugestão.',
          details: [
            [`Modelo ${model}`, transparency?.model_version].filter(Boolean).join(', versão '),
            ...(transparency?.stage_note ? [transparency.stage_note] : []),
            'O programa aponta onde pode estar o problema. A decisão continua com a equipe.',
          ],
        }
      : {
          id: 'deteccao',
          title: 'Análise automática',
          summary: 'Sugestão a partir da foto',
          state: 'planned',
          who: 'Um programa que reconhece problemas em fotos, depois de aprovado.',
          details: [
            'Hoje não há análise automática: toda foto vai direto para a revisão de uma pessoa, e nada é marcado automaticamente na foto.',
            'Quando existir, o programa vai apontar onde está o problema na foto, só como sugestão para a equipe.',
            ...(model
              ? [
                  `Há um programa ${stageName(transparency?.stage) ?? 'registrado'} (${model}). Nenhum resultado dele é publicado.`,
                ]
              : []),
          ],
          planned:
            'Previsto: um programa que reconhece problemas em fotos, chamado YOLO26s, treinado só com imagens autorizadas e testado por tipo de problema e por região. Ele só entra em uso com a aprovação da equipe.',
        },
    {
      id: 'revisao',
      title: 'Revisão da equipe',
      summary: 'Decisão da equipe',
      state: 'active',
      who: 'A equipe do UrMind.',
      details: [
        'Uma pessoa confere a foto, o local e a privacidade e define o tipo de problema.',
        'A equipe confirma, corrige ou recusa o relato. Problemas que dependem de horário ou de dados de serviço, como poucas linhas de ônibus, não são confirmados só pela foto.',
      ],
    },
    {
      id: 'contexto',
      title: 'Contexto do local',
      summary: 'Rua e arredores',
      state: 'active',
      who: 'Fontes públicas de dados, sempre identificadas.',
      details: [
        'O ponto é ligado à rua mais próxima e ao que existe em volta, como escolas e travessias.',
        sources.length
          ? `Fontes: ${list(sources)}.`
          : 'As fontes usadas aparecem em “Dados e regras”, mais abaixo.',
      ],
    },
    {
      id: 'risco',
      title: 'Gravidade e solução',
      summary: 'Regras públicas',
      state: 'active',
      who: 'Regras fixas e públicas; a solução é sempre uma sugestão.',
      details: [
        'A gravidade e a prioridade seguem regras fixas e públicas, sem inteligência artificial.',
        'O responsável sugerido sai de uma tabela de competências. Sem regra para o local, a equipe define.',
      ],
      planned:
        'Planejado: uma estimativa de risco aprendida com casos já revisados, sempre como recomendação que a equipe pode contestar.',
    },
    {
      id: 'mapa',
      title: 'Mapa público',
      summary: 'Ocorrência confirmada',
      state: 'active',
      who: 'Qualquer pessoa pode consultar.',
      details: [
        'A ocorrência confirmada aparece no mapa com tipo, gravidade e local.',
        'A foto só é publicada depois de uma revisão de privacidade. Quem enviou acompanha pelo protocolo.',
      ],
    },
  ];
}
