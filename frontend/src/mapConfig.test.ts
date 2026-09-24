import { describe, expect, it } from 'vitest';
import { resolveMapProvider } from './mapConfig';

describe('resolveMapProvider', () => {
  it('uses OpenFreeMap as the primary provider', () => {
    expect(resolveMapProvider({}).name).toBe('OpenFreeMap');
    expect(resolveMapProvider({}).styleUrl).toBe('https://tiles.openfreemap.org/styles/liberty');
  });

  it('identifies an explicit OpenFreeMap URL from its provider host', () => {
    const provider = resolveMapProvider({
      VITE_MAP_STYLE_URL: 'https://tiles.openfreemap.org/styles/bright',
    });
    expect(provider.name).toBe('OpenFreeMap');
    expect(provider.attribution).toContain('OpenFreeMap');
  });

  it('identifies an explicit CARTO style URL from its provider host', () => {
    const provider = resolveMapProvider({
      VITE_MAP_STYLE_URL: 'https://basemaps.cartocdn.com/gl/positron-gl-style/style.json',
    });
    expect(provider.name).toBe('CARTO');
    expect(provider.attribution).toContain('CARTO');
  });

  it('does not attribute an unknown custom style to OpenFreeMap', () => {
    const provider = resolveMapProvider({
      VITE_MAP_STYLE_URL: 'https://maps.example/style.json',
    });
    expect(provider.name).toBe('Custom');
    expect(provider.styleUrl).toBe('https://maps.example/style.json');
    expect(provider.attribution).not.toContain('OpenFreeMap');
  });

  it('builds CARTO only when the optional public key is configured', () => {
    const fallback = resolveMapProvider({ VITE_CARTO_BASEMAPS_API_KEY: 'public key' }, 'carto');
    expect(fallback.name).toBe('CARTO');
    expect(fallback.styleUrl).toContain('voyager-gl-style/style.json?key=public%20key');
    expect(fallback.attribution).toContain('OpenStreetMap');
  });

  it('falls back to OpenFreeMap when CARTO is not configured', () => {
    expect(resolveMapProvider({}, 'carto').name).toBe('OpenFreeMap');
  });

  it('does not include the CARTO key in attribution or provider name', () => {
    const fallback = resolveMapProvider(
      { VITE_CARTO_BASEMAPS_API_KEY: 'public-secret-looking' },
      'carto',
    );
    expect(`${fallback.name} ${fallback.attribution}`).not.toContain('public-secret-looking');
  });
});
