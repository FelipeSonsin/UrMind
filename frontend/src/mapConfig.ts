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

export function isInBrazilMapViewport(latitude: number, longitude: number): boolean {
  const [west, south, east, north] = BRAZIL_MAP_BOUNDS;
  return latitude >= south && latitude <= north && longitude >= west && longitude <= east;
}

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
