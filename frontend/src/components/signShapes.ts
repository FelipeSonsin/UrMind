/**
 * Formas das placas do mapa (grade 32×32) e as cores de gravidade: as mesmas no marcador do
 * mapa, na legenda e no selo de gravidade das listas. Arquivo pequeno, sem o mapa, para o selo
 * entrar nas páginas sem carregar o mapa.
 */
export type SignShape = 'warning' | 'triangle' | 'square' | 'circle' | 'ring' | 'report';

export const SHAPE_PATHS: Record<SignShape, string> = {
  warning: 'M16 3 L30 28 L2 28 Z',
  triangle: 'M16 3 L30 28 L2 28 Z',
  square: 'M7 4 H25 A3 3 0 0 1 28 7 V25 A3 3 0 0 1 25 28 H7 A3 3 0 0 1 4 25 V7 A3 3 0 0 1 7 4 Z',
  circle: 'M3 16 A13 13 0 1 0 29 16 A13 13 0 1 0 3 16 Z',
  ring: 'M3 16 A13 13 0 1 0 29 16 A13 13 0 1 0 3 16 Z',
  report: 'M3 16 A13 13 0 1 0 29 16 A13 13 0 1 0 3 16 Z',
};
/** Raio do centro branco do marcador de relato (círculo vazado). */
export const REPORT_HOLE = 5.5;
/** Inner rule that turns a triangle into a warning plate. */
export const WARNING_INSET = 'M16 9.5 L25 25 L7 25 Z';
/** Contorno de todo marcador: separa a placa de qualquer base do mapa. */
export const KEYLINE = '#0c1411';

/** Placa de cada gravidade: a forma diz o nível (legível sem cor), a cor reforça. */
export const SEVERITY_SIGNS: Record<
  'critical' | 'high' | 'medium' | 'low' | 'unknown',
  { shape: SignShape; fill: string }
> = {
  critical: { shape: 'warning', fill: '#d8645a' },
  high: { shape: 'triangle', fill: '#dd874f' },
  medium: { shape: 'square', fill: '#e3b341' },
  low: { shape: 'circle', fill: '#55b685' },
  unknown: { shape: 'ring', fill: '#a8b5ae' },
};
