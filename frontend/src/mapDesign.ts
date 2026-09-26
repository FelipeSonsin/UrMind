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
    /**
     * Sem localização na foto, o mapa de marcação abre numa região útil: a última
     * usada no aparelho ou a área piloto (FECAP, Liberdade, São Paulo), neste zoom.
     */
    pilotArea: { latitude: -23.5573, longitude: -46.6355 },
    areaZoom: 13,
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

  /**
   * Cores do entorno e das linhas, pela base cartográfica: `satellite` para a imagem de
   * satélite (padrão, ver SATELLITE_IMAGERY), `light`/`dark` para uma base só vetorial.
   */
  colors: {
    light: {
      /** Tudo fora do Brasil: cinza-claro neutro, sem competir com o mapa. */
      mask: '#eef1ec',
      /** Fronteira nacional. */
      border: '#1f5f49',
      /** Divisas estaduais. */
      states: '#1f5f49',
      /** Nome e ponto das capitais. */
      capitalText: '#182c25',
      capitalHalo: '#ffffff',
    },
    dark: {
      mask: '#121f1a',
      border: '#2f7d61',
      states: '#68746f',
      capitalText: '#a8b5ae',
      capitalHalo: '#0c1411',
    },
    /**
     * Sobre a imagem de satélite (padrão): entorno no preto do app, fronteira e nomes
     * em branco-gelo com halo escuro, como no mapa Sentinel de referência.
     */
    satellite: {
      mask: '#050507',
      border: '#dce9ff',
      states: '#dce9ff',
      capitalText: '#dce9ff',
      capitalHalo: '#050507',
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
      critical: { shape: 'warning', fill: '#d8645a', glyph: '#0c1411' },
      high: { shape: 'triangle', fill: '#dd874f', glyph: '#0c1411' },
      medium: { shape: 'square', fill: '#e3b341', glyph: '#0c1411' },
      low: { shape: 'circle', fill: '#55b685', glyph: '#0c1411' },
      unknown: { shape: 'ring', fill: '#a8b5ae', glyph: '#a8b5ae' },
    },
    /**
     * Relatos de cidadãos (sem gravidade): círculo na cor da situação pública —
     * recebido, em análise, confirmado, precisa de localização, não confirmado.
     */
    reportStatus: {
      location_required: '#e3b341',
      received: '#a8b5ae',
      processing: '#6fa6a0',
      model_not_available: '#6fa6a0',
      experimental: '#6fa6a0',
      no_supported_detection: '#6fa6a0',
      human_confirmed: '#55b685',
      published: '#55b685',
      confirmed: '#55b685',
      rejected: '#68746f',
      duplicate: '#68746f',
    },
    /** Contorno de todo marcador e ícone interno dos relatos: separa de qualquer base. */
    keyline: '#0c1411',
    reportGlyph: '#0c1411',
    /** Ponto marcado pela pessoa ao escolher o local da foto. */
    picked: '#b7e36b',
    /** Grupos de pontos próximos. */
    cluster: { fill: '#2f7d61', text: '#ffffff', stroke: '#0c1411', radius: 20 },
  },
} as const;

export type MarkerShape = 'warning' | 'triangle' | 'square' | 'circle' | 'ring';

export type MapColors = Record<keyof (typeof MAP_DESIGN.colors)['light'], string>;

/**
 * Capitais das 27 UFs, só para a visão do país inteiro (abaixo do zoom em que o mapa
 * base passa a trazer as cidades). `uf` é o código IBGE da unidade da federação: um
 * teste confere que cada ponto cai dentro da própria UF na malha oficial do IBGE
 * (`public/geo/brasil-uf.geojson`). Coordenadas com duas casas (~1 km), suficientes
 * para um rótulo nacional. `rank` menor aparece primeiro quando falta espaço.
 */
export const BRAZIL_CAPITALS: {
  name: string;
  uf: string;
  lng: number;
  lat: number;
  rank: number;
}[] = [
  { name: 'Brasília', uf: '53', lng: -47.88, lat: -15.79, rank: 0 },
  { name: 'São Paulo', uf: '35', lng: -46.63, lat: -23.55, rank: 1 },
  // -43.17 (Praça XV) cai na água da baía na malha simplificada do IBGE; -43.20 é o Centro.
  { name: 'Rio de Janeiro', uf: '33', lng: -43.2, lat: -22.91, rank: 1 },
  { name: 'Manaus', uf: '13', lng: -60.02, lat: -3.12, rank: 1 },
  { name: 'Salvador', uf: '29', lng: -38.5, lat: -12.97, rank: 1 },
  { name: 'Belém', uf: '15', lng: -48.5, lat: -1.46, rank: 2 },
  { name: 'Fortaleza', uf: '23', lng: -38.54, lat: -3.73, rank: 2 },
  { name: 'Recife', uf: '26', lng: -34.88, lat: -8.05, rank: 2 },
  { name: 'Porto Alegre', uf: '43', lng: -51.23, lat: -30.03, rank: 2 },
  { name: 'Belo Horizonte', uf: '31', lng: -43.94, lat: -19.92, rank: 2 },
  { name: 'Curitiba', uf: '41', lng: -49.27, lat: -25.43, rank: 2 },
  { name: 'Cuiabá', uf: '51', lng: -56.1, lat: -15.6, rank: 2 },
  { name: 'Porto Velho', uf: '11', lng: -63.9, lat: -8.76, rank: 3 },
  { name: 'Rio Branco', uf: '12', lng: -67.81, lat: -9.97, rank: 3 },
  { name: 'Boa Vista', uf: '14', lng: -60.67, lat: 2.82, rank: 3 },
  { name: 'Macapá', uf: '16', lng: -51.07, lat: 0.03, rank: 3 },
  { name: 'Palmas', uf: '17', lng: -48.33, lat: -10.18, rank: 3 },
  { name: 'Goiânia', uf: '52', lng: -49.25, lat: -16.68, rank: 3 },
  { name: 'Campo Grande', uf: '50', lng: -54.62, lat: -20.47, rank: 3 },
  { name: 'São Luís', uf: '21', lng: -44.3, lat: -2.53, rank: 3 },
  { name: 'Teresina', uf: '22', lng: -42.8, lat: -5.09, rank: 3 },
  { name: 'Natal', uf: '24', lng: -35.21, lat: -5.79, rank: 4 },
  { name: 'João Pessoa', uf: '25', lng: -34.86, lat: -7.12, rank: 4 },
  { name: 'Maceió', uf: '27', lng: -35.73, lat: -9.67, rank: 4 },
  { name: 'Aracaju', uf: '28', lng: -37.07, lat: -10.91, rank: 4 },
  { name: 'Vitória', uf: '32', lng: -40.31, lat: -20.32, rank: 4 },
  { name: 'Florianópolis', uf: '42', lng: -48.55, lat: -27.59, rank: 4 },
];

/** Converte pares [zoom, valor] numa expressão de interpolação do MapLibre. */
export function byZoom(stops: readonly (readonly [number, number])[]) {
  return ['interpolate', ['linear'], ['zoom'], ...stops.flat()] as const;
}
