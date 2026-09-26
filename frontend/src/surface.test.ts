import { describe, expect, it } from 'vitest';
import { currentSurface } from './surface';

describe('superfície pelo endereço', () => {
  it('só /admin é a área da equipe; o resto é o site do cidadão', () => {
    expect(currentSurface('/')).toBe('public');
    expect(currentSurface('/admin')).toBe('admin');
    expect(currentSurface('/admin/')).toBe('admin');
    expect(currentSurface('/admin/index.html')).toBe('admin');
    // Parecidos não contam: nada vira área da equipe por acidente.
    expect(currentSurface('/administracao')).toBe('public');
    expect(currentSurface('/x/admin/')).toBe('public');
  });
});
