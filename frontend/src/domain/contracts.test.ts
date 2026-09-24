import { describe, expect, it } from 'vitest';
import { eventSchema, parseCoordinate } from './contracts';

describe('localização sem inferência', () => {
  it('lê classe candidata confirmada por humano sem restringir ao detector visual', () => {
    expect(
      eventSchema.safeParse({
        id: '3f8b9d3a-2f0c-4f1e-9b1a-4f6a0d5e7c11',
        event_key: 'human',
        urmind_class: 'URMIND_FALLEN_TREE',
        status: 'confirmed',
        occurred_at: '2026-09-24T12:00:00Z',
        evidence_mode: 'photo',
      }).success,
    ).toBe(true);
  });
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
