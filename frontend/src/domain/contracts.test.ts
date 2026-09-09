import { describe, expect, it } from 'vitest';
import { eventSchema, parseCoordinate } from './contracts';

describe('localização sem inferência', () => {
  it('não transforma campos vazios em coordenada zero', () => {
    expect(() => parseCoordinate('', '')).toThrow();
    expect(() => parseCoordinate(' ', '10')).toThrow();
    expect(() => parseCoordinate('10', '')).toThrow();
  });
  it('aceita zero real e separador decimal brasileiro', () => {
    expect(parseCoordinate('0', '0')).toEqual({ latitude: 0, longitude: 0, accuracy_m: null });
    expect(parseCoordinate('-23,5', '-46,6').latitude).toBe(-23.5);
  });
  it.each([
    ['91', '0'],
    ['0', '-181'],
    ['NaN', '20'],
    ['Infinity', '0'],
  ])('recusa coordenadas inválidas %s, %s', (lat, lon) => {
    expect(() => parseCoordinate(lat, lon)).toThrow();
  });
  it('recusa dados de ocorrência que não correspondem ao backend', () => {
    expect(eventSchema.safeParse({ id: 'inventado', visual_confidence: 95 }).success).toBe(false);
  });
});
