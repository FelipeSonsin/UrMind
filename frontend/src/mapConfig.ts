export interface MapEnvironment {
  VITE_MAP_STYLE_URL?: string;
  VITE_CARTO_BASEMAPS_API_KEY?: string;
}

export interface MapProvider {
  name: 'OpenFreeMap' | 'CARTO' | 'Custom';
  styleUrl: string;
  attribution: string;
}

// Presentation viewport only. Admission of a new report must use the
// authoritative country geometry in the backend, not this rectangle.
export const BRAZIL_MAP_BOUNDS = [-75, -35, -28, 6] as const;

// Bounding box of the IBGE country outline (public/geo/brasil.geojson, malha
// "máxima"), including Fernando de Noronha; frames the whole country inside
// the map slot at the minimum zoom.
export const BRAZIL_OUTLINE_BOUNDS = [-74, -33.75, -32.4, 5.28] as const;
export const BRAZIL_OUTLINE_URL = '/geo/brasil.geojson';
export const BRAZIL_STATES_URL = '/geo/brasil-uf.geojson';

export function isInBrazilMapViewport(latitude: number, longitude: number): boolean {
  const [west, south, east, north] = BRAZIL_MAP_BOUNDS;
  return latitude >= south && latitude <= north && longitude >= west && longitude <= east;
}

/** [lng, lat] ring; the first ring of a polygon is its shell, the rest are holes. */
export type Ring = [number, number][];
export type Polygon = Ring[];

interface OutlineGeometry {
  type: string;
  coordinates?: unknown;
}

/** Flattens a GeoJSON FeatureCollection of (Multi)Polygons into polygons. */
export function outlinePolygons(collection: {
  features?: { geometry?: OutlineGeometry | null }[];
}): Polygon[] {
  return (collection.features ?? []).flatMap(({ geometry }): Polygon[] => {
    if (geometry?.type === 'Polygon') return [geometry.coordinates as Polygon];
    if (geometry?.type === 'MultiPolygon') return geometry.coordinates as Polygon[];
    return [];
  });
}

function ringContains(ring: Ring, longitude: number, latitude: number): boolean {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i];
    const [xj, yj] = ring[j];
    if (
      yi > latitude !== yj > latitude &&
      longitude < ((xj - xi) * (latitude - yi)) / (yj - yi) + xi
    )
      inside = !inside;
  }
  return inside;
}

/** Point-in-territory test that honours holes. */
export function isInsidePolygons(
  polygons: Polygon[],
  latitude: number,
  longitude: number,
): boolean {
  return polygons.some(
    ([shell, ...holes]) =>
      ringContains(shell, longitude, latitude) &&
      !holes.some((hole) => ringContains(hole, longitude, latitude)),
  );
}

/**
 * Geometry covering everything outside the territory: the world with every
 * shell cut out, plus each interior hole filled back in.
 */
export function outsideMask(polygons: Polygon[]) {
  const world: Ring = [
    [-180, -85],
    [180, -85],
    [180, 85],
    [-180, 85],
    [-180, -85],
  ];
  return {
    type: 'MultiPolygon' as const,
    coordinates: [
      [world, ...polygons.map(([shell]) => shell)],
      ...polygons.flatMap(([, ...holes]) => holes.map((hole) => [hole])),
    ],
  };
}

/**
 * O estilo do OpenFreeMap rotula com `name_en` antes de `name`: aparecia "New Fribourg"
 * em vez de Nova Friburgo e "Federal District" em vez de Distrito Federal. No Brasil o
 * `name` do OpenStreetMap já é o nome local em português; o `name:pt` dos blocos não
 * serve, pois traz rótulos da Wikidata ("Liberdade (bairro de São Paulo)", "Estação
 * Santana"). O rótulo usa `name` e só recorre a `name_en` quando falta o nome local.
 */
export const LOCAL_LABEL_FIELD = ['coalesce', ['get', 'name'], ['get', 'name_en']];

/** Campo de texto a aplicar numa camada do mapa base, ou null se ela não usa `name_en`. */
export function localizedLabelField(textField: unknown): unknown[] | null {
  return JSON.stringify(textField ?? null).includes('"name_en"') ? LOCAL_LABEL_FIELD : null;
}

/**
 * Imagem de satélite sob o mapa: mosaico Sentinel-2 sem nuvens da EOX (cobertura
 * mundial, inclusive o Brasil; o SentinelMap de referência cobre só a Europa).
 * Licença CC BY-NC-SA 4.0: uso não comercial com o crédito abaixo, que aparece no
 * canto do mapa. Acima do zoom máximo o MapLibre amplia o último nível (10 m/pixel).
 */
export const SATELLITE_IMAGERY = {
  tiles: ['https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless-2024_3857/default/g/{z}/{y}/{x}.jpg'],
  tileSize: 256,
  maxzoom: 15,
  attribution:
    '<a href="https://s2maps.eu" target="_blank" rel="noopener noreferrer">Sentinel-2 cloudless – s2maps.eu</a> by EOX IT Services GmbH (Contains modified Copernicus Sentinel data 2024)',
} as const;

const OPENFREE_STYLE = 'https://tiles.openfreemap.org/styles/liberty';
const CARTO_STYLE = 'https://basemaps.cartocdn.com/gl/voyager-gl-style/style.json';
const OPENFREE_ATTRIBUTION = 'OpenFreeMap © OpenMapTiles Data from OpenStreetMap';
const CARTO_ATTRIBUTION = '© OpenStreetMap contributors, © CARTO';

function providerHost(styleUrl: string): string | null {
  try {
    return new URL(styleUrl).hostname.toLowerCase();
  } catch {
    return null;
  }
}

function hostMatches(host: string | null, domain: string): boolean {
  return host === domain || host?.endsWith(`.${domain}`) === true;
}

export function resolveMapProvider(
  env: MapEnvironment,
  requested: 'openfreemap' | 'carto' = 'openfreemap',
): MapProvider {
  const cartoKey = env.VITE_CARTO_BASEMAPS_API_KEY?.trim();
  if (requested === 'carto' && cartoKey) {
    return {
      name: 'CARTO',
      styleUrl: `${CARTO_STYLE}?key=${encodeURIComponent(cartoKey)}`,
      attribution: CARTO_ATTRIBUTION,
    };
  }
  const styleUrl = env.VITE_MAP_STYLE_URL?.trim() || OPENFREE_STYLE;
  const host = providerHost(styleUrl);
  if (hostMatches(host, 'openfreemap.org')) {
    return { name: 'OpenFreeMap', styleUrl, attribution: OPENFREE_ATTRIBUTION };
  }
  if (hostMatches(host, 'cartocdn.com') || hostMatches(host, 'carto.com')) {
    return { name: 'CARTO', styleUrl, attribution: CARTO_ATTRIBUTION };
  }
  return {
    name: 'Custom',
    styleUrl,
    attribution: 'Attribution declared by the configured MapLibre style',
  };
}

/** Base cartográfica escura (padrão) ou clara, para as cores do contorno e das capitais. */
export function isDarkBasemap(styleUrl: string): boolean {
  return /\/styles\/(dark|fiord)|dark-matter/i.test(styleUrl);
}
