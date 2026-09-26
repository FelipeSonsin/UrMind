/**
 * Aparência do mapa do Brasil — o único lugar para ajustar tamanho do slot,
 * recorte, cores, linhas e nomes de cidades. `UrbanMap` só lê daqui.
 *
 * Dica: altere um valor, salve e o Vite recarrega o mapa na hora (`npm run dev`).
 */

export const MAP_DESIGN = {
  /** Espaço que o mapa ocupa na página. */
  slot: {
    /**
     * Altura do mapa (qualquer valor CSS). O Brasil é quase quadrado: crescer
     * com a tela evita que ele vire miniatura no computador, e `100vw + 60px`
     * evita sobra vazia no celular.
     */
    height: 'clamp(340px, min(62vh, 100vw + 60px), 620px)',
  },

  /** Recorte: quanto o país fica afastado das bordas do slot, em pixels. */
  framing: {
    /** Folga do aviso "Nenhum registro…" (topo) e dos créditos (base). */
    padding: { top: 56, right: 56, bottom: 44, left: 16 },
    /** Zoom ao abrir num ponto conhecido (captura, relato). */
    pointZoom: 15,
    /** Zoom máximo ao enquadrar vários pontos. */
    pointsMaxZoom: 16,
    /** Folga ao enquadrar vários pontos. */
    pointsPadding: 70,
    /**
     * Se enquadrar os pontos aproximaria menos que isso (em níveis de zoom)
     * além da visão do país, o mapa mostra o Brasil inteiro em vez de cortá-lo.
     */
    countryZoomSpan: 1.5,
  },

  /** Cores do entorno e das linhas; `dark` vale no modo escuro do sistema. */
  colors: {
    light: {
      /** Tudo fora do Brasil — igual à superfície do cartão. */
      mask: '#f4f6f2',
      /** Fronteira nacional. */
      border: '#123f36',
      /** Divisas estaduais. */
      states: '#123f36',
      /** Nome e ponto das capitais. */
      capitalText: '#182c25',
      capitalHalo: '#ffffff',
    },
    dark: {
      mask: '#1c302a',
      border: '#d0ed9e',
      states: '#123f36',
      capitalText: '#182c25',
      capitalHalo: '#ffffff',
    },
  },

  /** Espessura das linhas por zoom: [zoom, largura] interpolados. */
  lines: {
    border: [
      [3, 1.2],
      [8, 2.4],
    ],
    states: [
      [3, 0.6],
      [8, 1.4],
    ],
    statesOpacity: 0.4,
  },

  /**
   * Nomes das capitais na visão do país inteiro, onde o mapa base ainda não
   * traz cidades. A partir de `untilZoom` os rótulos do próprio mapa assumem.
   * Os nomes nunca são cortados: quando não cabem, o mapa esconde o nome
   * inteiro (a prioridade abaixo decide quem aparece primeiro).
   */
  capitals: {
    show: true,
    untilZoom: 3,
    textSize: [
      [2, 10],
      [3, 12],
    ],
    haloWidth: 1.4,
    /**
     * Longitudes a partir das quais o nome só cresce para dentro do país
     * (oeste: texto à direita do ponto; leste: à esquerda), sem sair do slot.
     */
    westEdge: -58,
    eastEdge: -37,
    dotRadius: 2.5,
    /** Fonte de reserva caso o estilo do mapa base não declare uma. */
    fallbackFont: ['Noto Sans Regular'],
  },

  /**
   * Marcadores de ocorrência, desenhados como placas de sinalização: a forma
   * diz a severidade (legível sem cor), a cor reforça, o ícone interno diz a
   * família do problema. `glyph` é a cor do ícone, escolhida pelo contraste.
   */
  markers: {
    /** Tamanho do marcador em pixels; o selecionado cresce por `selectedScale`. */
    size: 24,
    selectedScale: 1.3,
    severity: {
      critical: { shape: 'warning', fill: '#b3261e', glyph: '#ffffff' },
      high: { shape: 'triangle', fill: '#d9480f', glyph: '#ffffff' },
      medium: { shape: 'square', fill: '#e0a526', glyph: '#182c25' },
      low: { shape: 'circle', fill: '#2f6f5e', glyph: '#ffffff' },
      unknown: { shape: 'ring', fill: '#66766f', glyph: '#66766f' },
    },
    /** Relatos de cidadãos (sem severidade): círculo na cor do estado. */
    reportStatus: {
      received: '#64748b',
      model_not_available: '#a16207',
      experimental: '#7c3aed',
      human_confirmed: '#047857',
      confirmed: '#047857',
      rejected: '#991b1b',
      duplicate: '#475569',
    },
    /** Grupos de pontos próximos. */
    cluster: { fill: '#123f36', text: '#ffffff', radius: 20 },
  },
} as const;

export type MarkerShape = 'warning' | 'triangle' | 'square' | 'circle' | 'ring';

export type MapColors = Record<keyof (typeof MAP_DESIGN.colors)['light'], string>;

/** Capitais das 27 UFs; `rank` menor aparece primeiro quando falta espaço. */
export const BRAZIL_CAPITALS: { name: string; lng: number; lat: number; rank: number }[] = [
  { name: 'Brasília', lng: -47.88, lat: -15.79, rank: 0 },
  { name: 'São Paulo', lng: -46.63, lat: -23.55, rank: 1 },
  { name: 'Rio de Janeiro', lng: -43.17, lat: -22.91, rank: 1 },
  { name: 'Manaus', lng: -60.02, lat: -3.12, rank: 1 },
  { name: 'Salvador', lng: -38.5, lat: -12.97, rank: 1 },
  { name: 'Belém', lng: -48.5, lat: -1.46, rank: 2 },
  { name: 'Fortaleza', lng: -38.54, lat: -3.73, rank: 2 },
  { name: 'Recife', lng: -34.88, lat: -8.05, rank: 2 },
  { name: 'Porto Alegre', lng: -51.23, lat: -30.03, rank: 2 },
  { name: 'Belo Horizonte', lng: -43.94, lat: -19.92, rank: 2 },
  { name: 'Curitiba', lng: -49.27, lat: -25.43, rank: 2 },
  { name: 'Cuiabá', lng: -56.1, lat: -15.6, rank: 2 },
  { name: 'Porto Velho', lng: -63.9, lat: -8.76, rank: 3 },
  { name: 'Rio Branco', lng: -67.81, lat: -9.97, rank: 3 },
  { name: 'Boa Vista', lng: -60.67, lat: 2.82, rank: 3 },
  { name: 'Macapá', lng: -51.07, lat: 0.03, rank: 3 },
  { name: 'Palmas', lng: -48.33, lat: -10.18, rank: 3 },
  { name: 'Goiânia', lng: -49.25, lat: -16.68, rank: 3 },
  { name: 'Campo Grande', lng: -54.62, lat: -20.47, rank: 3 },
  { name: 'São Luís', lng: -44.3, lat: -2.53, rank: 3 },
  { name: 'Teresina', lng: -42.8, lat: -5.09, rank: 3 },
  { name: 'Natal', lng: -35.21, lat: -5.79, rank: 4 },
  { name: 'João Pessoa', lng: -34.86, lat: -7.12, rank: 4 },
  { name: 'Maceió', lng: -35.73, lat: -9.67, rank: 4 },
  { name: 'Aracaju', lng: -37.07, lat: -10.91, rank: 4 },
  { name: 'Vitória', lng: -40.31, lat: -20.32, rank: 4 },
  { name: 'Florianópolis', lng: -48.55, lat: -27.59, rank: 4 },
];

/** Converte pares [zoom, valor] numa expressão de interpolação do MapLibre. */
export function byZoom(stops: readonly (readonly [number, number])[]) {
  return ['interpolate', ['linear'], ['zoom'], ...stops.flat()] as const;
}
