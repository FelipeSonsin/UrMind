import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import {
  BRAZIL_MAP_BOUNDS,
  BRAZIL_OUTLINE_BOUNDS,
  isInBrazilMapViewport,
  isInsidePolygons,
  LOCAL_LABEL_FIELD,
  localizedLabelField,
  outlinePolygons,
  outsideMask,
  resolveMapProvider,
  type Polygon,
} from './mapConfig';
import { BRAZIL_CAPITALS } from './mapDesign';

describe('basemap labels', () => {
  // Campo de texto real das camadas de rótulo do estilo "liberty" do OpenFreeMap.
  const liberty = [
    'case',
    ['has', 'name:nonlatin'],
    ['concat', ['get', 'name:latin'], '\n', ['get', 'name:nonlatin']],
    ['coalesce', ['get', 'name_en'], ['get', 'name']],
  ];

  it('shows the local OpenStreetMap name instead of the English one', () => {
    expect(localizedLabelField(liberty)).toEqual(['coalesce', ['get', 'name'], ['get', 'name_en']]);
    expect(LOCAL_LABEL_FIELD[1]).toEqual(['get', 'name']);
  });

  it('leaves labels that do not use names untouched (road shields)', () => {
    expect(localizedLabelField(['to-string', ['get', 'ref']])).toBeNull();
    expect(localizedLabelField(undefined)).toBeNull();
  });
});

describe('Brazil operational map viewport', () => {
  it('frames Brazil instead of the whole world', () => {
    expect(BRAZIL_MAP_BOUNDS).toEqual([-75, -35, -28, 6]);
    expect(isInBrazilMapViewport(-23.55, -46.63)).toBe(true);
    expect(isInBrazilMapViewport(38.72, -9.14)).toBe(false);
  });
});

describe('resolveMapProvider', () => {
  it('uses OpenFreeMap as the primary provider', () => {
    expect(resolveMapProvider({}).name).toBe('OpenFreeMap');
    expect(resolveMapProvider({}).styleUrl).toBe('https://tiles.openfreemap.org/styles/dark');
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
    expect(fallback.styleUrl).toContain('dark-matter-gl-style/style.json?key=public%20key');
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

describe('Brazil outline geometry', () => {
  const outline = JSON.parse(
    readFileSync(resolve(__dirname, '../public/geo/brasil.geojson'), 'utf-8'),
  ) as Parameters<typeof outlinePolygons>[0];
  const polygons = outlinePolygons(outline);

  it('accepts points inside the IBGE outline, including Fernando de Noronha', () => {
    expect(isInsidePolygons(polygons, -23.55, -46.63)).toBe(true); // São Paulo
    expect(isInsidePolygons(polygons, -3.12, -60.02)).toBe(true); // Manaus
    expect(isInsidePolygons(polygons, -30.03, -51.23)).toBe(true); // Porto Alegre
    expect(isInsidePolygons(polygons, -3.856, -32.429)).toBe(true); // Noronha
  });

  it('rejects neighbours and ocean that the viewport rectangle lets through', () => {
    const asuncion = [-25.28, -57.63] as const;
    const santaCruz = [-17.78, -63.18] as const;
    const atlantic = [-20, -35] as const;
    for (const [lat, lng] of [asuncion, santaCruz, atlantic]) {
      expect(isInBrazilMapViewport(lat, lng)).toBe(true);
      expect(isInsidePolygons(polygons, lat, lng)).toBe(false);
    }
  });

  it('frames every outline vertex', () => {
    const [west, south, east, north] = BRAZIL_OUTLINE_BOUNDS;
    for (const [shell] of polygons)
      for (const [lng, lat] of shell) {
        expect(lng).toBeGreaterThanOrEqual(west);
        expect(lng).toBeLessThanOrEqual(east);
        expect(lat).toBeGreaterThanOrEqual(south);
        expect(lat).toBeLessThanOrEqual(north);
      }
  });

  it('places every capital label inside its own state on the IBGE state grid', () => {
    const states = JSON.parse(
      readFileSync(resolve(__dirname, '../public/geo/brasil-uf.geojson'), 'utf-8'),
    ) as { features: { properties: { codarea: string }; geometry: { type: string } }[] };
    const byCode = new Map(
      states.features.map((feature) => [
        feature.properties.codarea,
        outlinePolygons({ features: [feature as never] }),
      ]),
    );
    expect(BRAZIL_CAPITALS).toHaveLength(27);
    expect(new Set(BRAZIL_CAPITALS.map((capital) => capital.uf)).size).toBe(27);
    for (const capital of BRAZIL_CAPITALS) {
      const state = byCode.get(capital.uf);
      expect(state, capital.name).toBeDefined();
      expect(isInsidePolygons(state!, capital.lat, capital.lng), capital.name).toBe(true);
      const elsewhere = [...byCode].filter(
        ([code, polygons]) =>
          code !== capital.uf && isInsidePolygons(polygons, capital.lat, capital.lng),
      );
      expect(elsewhere, capital.name).toEqual([]);
    }
  });

  it('honours holes and builds a mask that cuts every shell out of the world', () => {
    const square: Polygon = [
      [
        [0, 0],
        [10, 0],
        [10, 10],
        [0, 10],
        [0, 0],
      ],
      [
        [4, 4],
        [6, 4],
        [6, 6],
        [4, 6],
        [4, 4],
      ],
    ];
    expect(isInsidePolygons([square], 2, 2)).toBe(true);
    expect(isInsidePolygons([square], 5, 5)).toBe(false);
    const mask = outsideMask([square]);
    expect(mask.coordinates[0]).toHaveLength(2);
    expect(mask.coordinates[1]).toEqual([square[1]]);
  });
});
