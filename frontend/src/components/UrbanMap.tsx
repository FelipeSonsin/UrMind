import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import * as maplibregl from 'maplibre-gl';
import mapWorkerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url';
import 'maplibre-gl/dist/maplibre-gl.css';
import { convertFilter, expression } from '@maplibre/maplibre-gl-style-spec';
import {
  EMPTY_PUBLIC_FILTERS,
  familyFor,
  filterMapRecords,
  filterPublicMap,
  issueHeadline,
  labelFor,
  mapFilterOptions,
  publicMapFilterOptions,
  publicSituation,
  publicSituationLabels,
  severityOf,
  situationLabel,
  type MapFilters,
  type PublicMapFilters,
  type PublicSituation,
} from '../domain/public';
import {
  BRAZIL_OUTLINE_BOUNDS,
  BRAZIL_OUTLINE_URL,
  BRAZIL_STATES_URL,
  isDarkBasemap,
  isInBrazilMapViewport,
  isInsidePolygons,
  localizedLabelField,
  outlinePolygons,
  outsideMask,
  resolveMapProvider,
  SATELLITE_IMAGERY,
  type Polygon,
} from '../mapConfig';
import { MAP_LOCALE } from '../content/mapLocale';
import { DateField } from './DateField';
import { useWideScreen } from '../app/hooks/useWideScreen';
import { reportLabels, statuses, type CaptureMarker } from '../domain/contracts';
import { BRAZIL_CAPITALS, MAP_DESIGN, byZoom, type MapColors } from '../mapDesign';
import {
  REPORT_HOLE,
  SHAPE_PATHS,
  WARNING_INSET,
  markerImage,
  markerImageId,
  markerStyle,
  type MarkerStyle,
} from './mapMarkers';
import { api } from '../services/api';

// Vite must emit the actual worker; MapLibre's sibling default URL does not
// survive bundling the library into a hashed application chunk.
maplibregl.setWorkerUrl(mapWorkerUrl);

const BRAZIL_FIT: maplibregl.LngLatBoundsLike = [
  [BRAZIL_OUTLINE_BOUNDS[0], BRAZIL_OUTLINE_BOUNDS[1]],
  [BRAZIL_OUTLINE_BOUNDS[2], BRAZIL_OUTLINE_BOUNDS[3]],
];
const BRAZIL_PADDING = MAP_DESIGN.framing.padding;

let brazilOutline: Promise<Polygon[]> | undefined;
let brazilPolygons: Polygon[] | undefined;
function loadBrazilOutline(): Promise<Polygon[]> {
  brazilOutline ??= fetch(BRAZIL_OUTLINE_URL)
    .then((response) => {
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return response.json();
    })
    .then((collection) => (brazilPolygons = outlinePolygons(collection)))
    .catch((error) => {
      brazilOutline = undefined;
      throw error;
    });
  return brazilOutline;
}

/**
 * Presentation check against the IBGE outline once it is loaded, the viewport
 * rectangle before that. The backend remains the authority on admission.
 */
function isInsideBrazil(latitude: number, longitude: number): boolean {
  if (!brazilPolygons) return isInBrazilMapViewport(latitude, longitude);
  return isInsidePolygons(brazilPolygons, latitude, longitude);
}

/** Resolves to true once the outline is available, so markers can be re-filtered. */
function useBrazilOutline(): boolean {
  const [ready, setReady] = useState(Boolean(brazilPolygons));
  useEffect(() => {
    if (ready) return;
    let active = true;
    loadBrazilOutline()
      .then(() => active && setReady(true))
      .catch(() => {
        // Sem o contorno, o retângulo de apresentação continua valendo.
      });
    return () => {
      active = false;
    };
  }, [ready]);
  return ready;
}

/** Cores do contorno e das capitais conforme a base carregada (escura por padrão). */
let basemapIsDark = true;
let basemapIsSatellite = false;
function outlineTheme(): MapColors {
  if (basemapIsSatellite) return MAP_DESIGN.colors.satellite;
  return basemapIsDark ? MAP_DESIGN.colors.dark : MAP_DESIGN.colors.light;
}

/**
 * Põe a imagem de satélite como chão do mapa, logo acima do fundo do estilo. Solo,
 * água, parques e prédios pintados da base vetorial saem (a foto mostra isso); ruas,
 * divisas e nomes continuam, com texto claro e halo escuro para ler sobre a imagem.
 */
function addSatellite(map: maplibregl.Map) {
  const layers = map.getStyle().layers ?? [];
  for (const layer of layers) {
    if (['fill', 'fill-extrusion', 'hillshade', 'raster'].includes(layer.type))
      map.setLayoutProperty(layer.id, 'visibility', 'none');
    // Ruas e divisas meio transparentes: guiam sem esconder a imagem.
    if (layer.type === 'line') map.setPaintProperty(layer.id, 'line-opacity', 0.5);
    if (layer.type === 'symbol' && map.getLayoutProperty(layer.id, 'text-field') != null) {
      map.setPaintProperty(layer.id, 'text-color', MAP_DESIGN.colors.satellite.capitalText);
      map.setPaintProperty(layer.id, 'text-halo-color', MAP_DESIGN.colors.satellite.capitalHalo);
    }
  }
  const { attribution, maxzoom, tileSize, tiles } = SATELLITE_IMAGERY;
  map.addSource('satellite', { type: 'raster', tiles: [...tiles], tileSize, maxzoom, attribution });
  map.addLayer(
    { id: 'satellite', type: 'raster', source: 'satellite' },
    layers.find((layer) => layer.type !== 'background')?.id,
  );
  basemapIsSatellite = true;
}

/** Reuses the basemap's own label font so the glyphs exist on its server. */
function basemapFont(map: maplibregl.Map, layers: maplibregl.LayerSpecification[]) {
  for (const layer of layers) {
    if (layer.type !== 'symbol' || !/city|place|town/i.test(layer.id)) continue;
    const font = map.getLayoutProperty(layer.id, 'text-font');
    if (Array.isArray(font) && font.every((name) => typeof name === 'string'))
      return font as string[];
  }
  return [...MAP_DESIGN.capitals.fallbackFont];
}

function addCapitals(map: maplibregl.Map, layers: maplibregl.LayerSpecification[]) {
  const { capitals } = MAP_DESIGN;
  if (!capitals.show) return;
  const theme = outlineTheme();
  map.addSource('brazil-capitals', {
    type: 'geojson',
    data: {
      type: 'FeatureCollection',
      features: BRAZIL_CAPITALS.map(({ name, lng, lat, rank }) => ({
        type: 'Feature',
        properties: { name, rank, lng },
        geometry: { type: 'Point', coordinates: [lng, lat] },
      })),
    },
  });
  map.addLayer(
    {
      id: 'brazil-capitals-dot',
      type: 'circle',
      source: 'brazil-capitals',
      maxzoom: capitals.untilZoom,
      paint: {
        'circle-radius': capitals.dotRadius,
        'circle-color': theme.capitalText,
        'circle-stroke-color': theme.capitalHalo,
        'circle-stroke-width': 1,
      },
    },
    map.getLayer('report-clusters') ? 'report-clusters' : undefined,
  );
  map.addLayer(
    {
      id: 'brazil-capitals',
      type: 'symbol',
      source: 'brazil-capitals',
      maxzoom: capitals.untilZoom,
      layout: {
        'text-field': ['get', 'name'],
        'text-font': basemapFont(map, layers),
        'text-size': byZoom(capitals.textSize) as unknown as maplibregl.ExpressionSpecification,
        // Tries each side of the dot before giving up; a name that fits
        // nowhere is hidden whole rather than drawn cut. Near the west and
        // east edges the name only grows inward, so it never leaves the slot.
        'text-variable-anchor-offset': [
          'case',
          ['<', ['get', 'lng'], capitals.westEdge],
          ['literal', ['left', [0.6, 0], 'top', [0, 0.5], 'bottom', [0, -0.5]]],
          ['>', ['get', 'lng'], capitals.eastEdge],
          ['literal', ['right', [-0.6, 0], 'top', [0, 0.5], 'bottom', [0, -0.5]]],
          ['literal', ['left', [0.6, 0], 'right', [-0.6, 0], 'top', [0, 0.5], 'bottom', [0, -0.5]]],
        ] as unknown as maplibregl.DataDrivenPropertyValueSpecification<maplibregl.VariableAnchorOffsetCollectionSpecification>,
        'text-justify': 'auto',
        'symbol-sort-key': ['get', 'rank'],
        'text-padding': 3,
      },
      paint: {
        'text-color': theme.capitalText,
        'text-halo-color': theme.capitalHalo,
        'text-halo-width': capitals.haloWidth,
      },
    },
    map.getLayer('report-clusters') ? 'report-clusters' : undefined,
  );
}

/** Cobre tudo que está fora do contorno oficial do IBGE e desenha a fronteira e as UFs. */
function addBrazilMask(map: maplibregl.Map) {
  // Country names are drawn over the mask and leak fragments of neighbours
  // across the border; the map only ever shows Brazil, so hide them all.
  for (const layer of map.getStyle().layers ?? []) {
    if (layer.type !== 'symbol') continue;
    if (/country/i.test(layer.id)) map.setLayoutProperty(layer.id, 'visibility', 'none');
    // Nome local do OpenStreetMap (português no Brasil), nunca o inglês (ver mapConfig).
    const localized = localizedLabelField(map.getLayoutProperty(layer.id, 'text-field'));
    if (localized)
      map.setLayoutProperty(
        layer.id,
        'text-field',
        localized as maplibregl.DataDrivenPropertyValueSpecification<string>,
      );
  }
  const theme = outlineTheme();
  const { lines } = MAP_DESIGN;
  // Overlays go under the basemap labels, so Brazilian names at the coast and
  // border are drawn whole instead of being sliced by the mask.
  // Styles interleave a few symbol layers (one-way arrows) among the lines, so
  // anchor on the first label after the last drawn geometry, above boundaries.
  const layers = map.getStyle().layers ?? [];
  const lastGeometry = layers.findLastIndex((layer) => layer.type !== 'symbol');
  const firstSymbol = layers[lastGeometry + 1]?.id;
  map.addSource('brazil-states', { type: 'geojson', data: BRAZIL_STATES_URL });
  map.addLayer(
    {
      id: 'brazil-states',
      type: 'line',
      source: 'brazil-states',
      paint: {
        'line-color': theme.states,
        'line-width': byZoom(lines.states) as unknown as maplibregl.ExpressionSpecification,
        'line-opacity': lines.statesOpacity,
      },
    },
    firstSymbol,
  );
  addCapitals(map, layers);
  loadBrazilOutline()
    .then((polygons) => {
      if (!map.getStyle() || map.getSource('brazil-mask')) return;
      // ...and labels anchored outside Brazil (neighbouring cities, roads) are
      // filtered out, since they would now be drawn on top of the mask.
      const territory = {
        type: 'Feature' as const,
        properties: {},
        geometry: { type: 'MultiPolygon' as const, coordinates: polygons },
      };
      for (const layer of map.getStyle().layers ?? []) {
        if (layer.type !== 'symbol' || !('source' in layer)) continue;
        if (layer.source === 'events' || layer.source === 'brazil-capitals') continue;
        const current = map.getFilter(layer.id);
        const base =
          current == null
            ? true
            : expression.isExpressionFilter(current)
              ? current
              : convertFilter(current);
        map.setFilter(layer.id, [
          'all',
          base,
          ['within', territory],
        ] as maplibregl.FilterSpecification);
      }
      map.addSource('brazil-mask', {
        type: 'geojson',
        data: { type: 'Feature', properties: {}, geometry: outsideMask(polygons) },
      });
      map.addLayer(
        {
          id: 'brazil-mask',
          type: 'fill',
          source: 'brazil-mask',
          paint: { 'fill-color': outlineTheme().mask, 'fill-antialias': true },
        },
        'brazil-states',
      );
      map.addLayer(
        {
          id: 'brazil-border',
          type: 'line',
          source: 'brazil-mask',
          layout: { 'line-join': 'round' },
          paint: {
            'line-color': outlineTheme().border,
            'line-width': byZoom(lines.border) as unknown as maplibregl.ExpressionSpecification,
          },
        },
        'brazil-states',
      );
    })
    .catch(() => {
      // Sem a máscara o mapa continua útil; só não recorta o entorno.
    });
}

/**
 * Replaces the plotted points; reframes only when the set of points changes. With a
 * selected point (a report just sent, a report opened) the frame is that point; otherwise
 * the points themselves, or the whole country when they are spread across it.
 */
function showEvents(
  map: maplibregl.Map,
  located: MapMarker[],
  fittedKey: { current: string },
  focusId: string | null | undefined,
  audience: MapAudience,
) {
  const source = map.getSource('events') as maplibregl.GeoJSONSource | undefined;
  if (!source) return;
  const markerIds = new Map<string, string>();
  for (const event of located) {
    const style = markerStyle(event);
    const family = familyFor(event.urmind_class);
    const id = markerImageId(style, family);
    markerIds.set(event.id, id);
    if (map.hasImage(id)) continue;
    const image = markerImage(style, family);
    if (image) map.addImage(id, image, { pixelRatio: 2 });
  }
  source.setData({
    type: 'FeatureCollection',
    features: located.map((event) => ({
      type: 'Feature' as const,
      geometry: { type: 'Point' as const, coordinates: [event.longitude!, event.latitude!] },
      properties: {
        id: event.id,
        popup: markerPopup(event, audience),
        marker: markerIds.get(event.id) ?? '',
      },
    })),
  });
  const key = located
    .map((event) => event.id)
    .sort()
    .join(',');
  if (!located.length || key === fittedKey.current) return;
  fittedKey.current = key;
  const focus = located.find((event) => event.id === focusId);
  if (focus) {
    map.jumpTo({ center: [focus.longitude!, focus.latitude!], zoom: MAP_DESIGN.framing.pointZoom });
    return;
  }
  const bounds = new maplibregl.LngLatBounds();
  located.forEach((event) => bounds.extend([event.longitude!, event.latitude!]));
  const { framing } = MAP_DESIGN;
  const camera = map.cameraForBounds(bounds, {
    padding: framing.pointsPadding,
    maxZoom: framing.pointsMaxZoom,
  });
  // Points spread over most of the country: zooming to them would only crop
  // Brazil's edges, so keep the whole-country framing instead.
  if (!camera || camera.zoom == null || camera.zoom < map.getMinZoom() + framing.countryZoomSpan)
    map.fitBounds(BRAZIL_FIT, { padding: BRAZIL_PADDING, duration: 0 });
  else map.jumpTo(camera);
}

/** Mínimo que o mapa precisa. `UrbanEvent` e o resumo público satisfazem isto. */
export interface MapMarker {
  id: string;
  urmind_class?: string | null;
  report_status?: CaptureMarker['report_status'];
  latitude?: number | null;
  longitude?: number | null;
  severity?: string | null;
  road_name?: string | null;
  status?: string;
  created_at?: string | null;
  occurred_at?: string | null;
}

/** Público: situação agrupada e nome simples. Equipe: estado técnico completo. */
export type MapAudience = 'public' | 'team';

const LEGEND = ['critical', 'high', 'medium', 'low', 'unknown'];
const SITUATION_ORDER: PublicSituation[] = [
  'received',
  'analyzing',
  'confirmed',
  'needs_location',
  'declined',
];
/** Cor do círculo de cada situação pública: a mesma de um estado técnico do grupo. */
const SITUATION_SAMPLE: Record<PublicSituation, string> = {
  received: 'received',
  analyzing: 'processing',
  confirmed: 'published',
  needs_location: 'location_required',
  declined: 'rejected',
};

/** Lista vazia estável: no modo de marcar um ponto, o mapa não mostra outros pontos. */
const NO_POINTS: MapMarker[] = [];

function markerLabel(event: MapMarker, audience: MapAudience) {
  if (audience === 'team')
    return event.report_status
      ? `${reportLabels[event.report_status]}${event.urmind_class ? ` · ${labelFor(event.urmind_class)}` : ''}`
      : labelFor(event.urmind_class ?? '');
  const headline = event.urmind_class ? issueHeadline(event.urmind_class, event.severity) : '';
  return event.report_status
    ? `${situationLabel(event.report_status)}${headline ? ` · ${headline}` : ''}`
    : headline || 'Ocorrência';
}

function markerPopup(event: MapMarker, audience: MapAudience) {
  const label = markerLabel(event, audience);
  if (event.report_status) return label;
  const road = event.road_name ? ` · ${event.road_name}` : '';
  return audience === 'team'
    ? `${label} · gravidade ${severityOf(event.severity).label.toLowerCase()}${road}`
    : `${label}${road}`;
}

export default function UrbanMap({
  events: allEvents,
  selectedId,
  onSelect,
  onPickLocation,
  initialCenter,
  detail,
  onCloseDetail,
  allowExport = false,
  emptyMessage = 'Ainda não há pontos para mostrar neste mapa.',
  showFilters = true,
  audience = 'public',
  pickedPoint,
  initialZoom,
  showList = true,
  showLegend = true,
}: {
  events: MapMarker[];
  selectedId?: string | null;
  onSelect?: (id: string) => void;
  onPickLocation?: (latitude: number, longitude: number) => void;
  /**
   * Onde o mapa abre. Sem ele, quem marca um ponto (`onPickLocation`) começa na região do
   * projeto piloto, como no Registrar; os demais mapas enquadram os pontos ou o Brasil.
   */
  initialCenter?: { latitude: number; longitude: number } | null;
  detail?: ReactNode;
  onCloseDetail?: () => void;
  allowExport?: boolean;
  /** Aviso sem pontos; `null` não mostra nada (por exemplo, quando a carga falhou). */
  emptyMessage?: string | null;
  /** Mapas de apoio (início) mostram os pontos sem a barra de filtros. */
  showFilters?: boolean;
  /** Público vê filtros simples e nomes simples; a equipe, o vocabulário técnico. */
  audience?: MapAudience;
  /** Ponto escolhido fora do mapa (busca de endereço): o marcador vai até ele. */
  pickedPoint?: { latitude: number; longitude: number } | null;
  /** Zoom inicial ao abrir em `initialCenter` (padrão: nível de rua). */
  initialZoom?: number;
  /** "Ver em lista" abaixo do mapa; desligado onde a página já mostra a lista. */
  showList?: boolean;
  /** Legenda; desligada quando o mapa mostra um ponto só, já descrito na página. */
  showLegend?: boolean;
}) {
  const exportController = useRef<AbortController | null>(null);
  const [exportError, setExportError] = useState('');
  const [exporting, setExporting] = useState(false);
  useEffect(() => () => exportController.current?.abort(), []);
  const [filters, setFilters] = useState<MapFilters>(() => {
    const query = new URLSearchParams(location.hash.split('?')[1] ?? '');
    return {
      status: query.get('map_status') ?? '',
      family: query.get('map_family') ?? '',
      issue: query.get('map_class') ?? '',
      from: query.get('map_from') ?? '',
      to: query.get('map_to') ?? '',
    };
  });
  const [publicFilters, setPublicFilters] = useState<PublicMapFilters>(() => {
    const query = new URLSearchParams(location.hash.split('?')[1] ?? '');
    return {
      issue: query.get('map_class') ?? '',
      severity: query.get('map_severity') ?? '',
      period: query.get('map_period') ?? '',
    };
  });
  const team = audience === 'team';
  const events = useMemo(
    () =>
      // Marcando um ponto, o mapa fica só com a mira: outros pontos confundiriam.
      onPickLocation
        ? NO_POINTS
        : team
          ? filterMapRecords(allEvents, filters)
          : filterPublicMap(allEvents, publicFilters),
    [allEvents, filters, publicFilters, onPickLocation, team],
  );
  const options = mapFilterOptions(allEvents, filters.family, {
    reports: reportLabels,
    events: statuses,
  });
  const publicOptions = useMemo(() => publicMapFilterOptions(allEvents), [allEvents]);
  const filtering = team
    ? Object.values(filters).some(Boolean)
    : Object.values(publicFilters).some(Boolean);
  function writeQuery(values: Record<string, string>) {
    const query = new URLSearchParams(location.hash.split('?')[1] ?? '');
    for (const [key, value] of Object.entries(values)) {
      if (value) query.set(key, value);
      else query.delete(key);
    }
    history.replaceState(
      null,
      '',
      `${location.hash.split('?')[0]}${query.size ? `?${query}` : ''}`,
    );
  }
  function changePublicFilters(next: PublicMapFilters) {
    setPublicFilters(next);
    onCloseDetail?.();
    writeQuery({ map_class: next.issue, map_severity: next.severity, map_period: next.period });
  }
  function clearFilters() {
    if (team) changeFilters({ status: '', family: '', issue: '', from: '', to: '' });
    else changePublicFilters(EMPTY_PUBLIC_FILTERS);
  }
  function changeFilters(requested: MapFilters) {
    // A classe escolhida precisa pertencer à família escolhida.
    const next =
      requested.issue && requested.family && familyFor(requested.issue) !== requested.family
        ? { ...requested, issue: '' }
        : requested;
    setFilters(next);
    onCloseDetail?.();
    writeQuery({
      map_status: next.status,
      map_family: next.family,
      map_class: next.issue,
      map_from: next.from,
      map_to: next.to,
    });
  }
  // Celular: os filtros do mapa ficam recolhidos em "Filtros", como na fila.
  const wide = useWideScreen();
  const container = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map>(null);
  const pickCallback = useRef(onPickLocation);
  pickCallback.current = onPickLocation;
  const [error, setError] = useState('');
  // WebGL ausente ou bloqueado: o mapa não desenha, mas a lista de pontos continua.
  const [failed, setFailed] = useState(false);
  const mapEnvironment = {
    VITE_MAP_STYLE_URL: import.meta.env.VITE_MAP_STYLE_URL,
    VITE_CARTO_BASEMAPS_API_KEY: import.meta.env.VITE_CARTO_BASEMAPS_API_KEY,
  };
  const primaryProvider = resolveMapProvider(mapEnvironment);
  const cartoFallback = resolveMapProvider(mapEnvironment, 'carto');
  const style = primaryProvider.styleUrl;

  // Points outside the IBGE outline would sit invisible under the mask while
  // still being counted as "visíveis"; they are left out of the map instead.
  const outlineReady = useBrazilOutline();
  const located = useMemo(
    () =>
      events.filter(
        (event) =>
          event.latitude != null &&
          event.longitude != null &&
          isInsideBrazil(event.latitude, event.longitude),
      ),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [events, outlineReady],
  );
  // The map is built once per provider/mode; data, selection and centre are
  // pushed into it, so a refresh or a parent re-render never resets the view.
  const locatedRef = useRef(located);
  locatedRef.current = located;
  const selectedRef = useRef(selectedId);
  selectedRef.current = selectedId;
  const selectCallback = useRef(onSelect);
  selectCallback.current = onSelect;
  const audienceRef = useRef(audience);
  audienceRef.current = audience;
  const fittedKey = useRef('');
  const pickMode = Boolean(onPickLocation);
  const serviceArea = pickMode && initialCenter === undefined;
  const startCenter = serviceArea ? MAP_DESIGN.framing.pilotArea : initialCenter;
  const startZoom = serviceArea ? MAP_DESIGN.framing.areaZoom : initialZoom;
  // Último ponto marcado aqui: quando ele volta como `pickedPoint`, o mapa não pula.
  const lastPicked = useRef<{ latitude: number; longitude: number } | null>(null);

  /** O ponto sob o alvo, marcado por quem arrastou, tocou ou pediu a própria localização. */
  function pickPoint(point: maplibregl.LngLat) {
    if (!isInsideBrazil(point.lat, point.lng)) {
      setError('Escolha um ponto dentro do território brasileiro.');
      return;
    }
    setError('');
    lastPicked.current = { latitude: point.lat, longitude: point.lng };
    pickCallback.current?.(point.lat, point.lng);
  }

  useEffect(() => {
    if (!container.current) return;
    let map: maplibregl.Map | undefined;
    try {
      const shownCenter =
        startCenter && isInsideBrazil(startCenter.latitude, startCenter.longitude)
          ? startCenter
          : null;
      fittedKey.current = '';
      map = new maplibregl.Map({
        container: container.current,
        style,
        // Dicas dos botões do mapa em português (o padrão do MapLibre é inglês).
        locale: MAP_LOCALE,
        ...(shownCenter
          ? {
              center: [shownCenter.longitude, shownCenter.latitude],
              zoom: startZoom ?? MAP_DESIGN.framing.pointZoom,
            }
          : { bounds: BRAZIL_FIT, fitBoundsOptions: { padding: BRAZIL_PADDING } }),
        renderWorldCopies: false,
        // Crédito OSM/OpenFreeMap no canto do mapa; compacto em tela estreita.
        attributionControl: {},
      });
      mapRef.current = map;
      // The minimum zoom always shows the whole country inside the slot, and
      // panning stops at that framing, whatever the slot's size or aspect ratio.
      const lockToBrazil = () => {
        if (!map) return;
        const camera = map.cameraForBounds(BRAZIL_FIT, { padding: BRAZIL_PADDING });
        if (!camera || camera.zoom == null) return;
        const saved = { center: map.getCenter(), zoom: map.getZoom() };
        map.setMaxBounds(null);
        map.setMinZoom(0);
        map.jumpTo(camera);
        const frame = map.getBounds();
        map.jumpTo({ center: saved.center, zoom: Math.max(saved.zoom, camera.zoom) });
        map.setMinZoom(camera.zoom);
        map.setMaxBounds(frame);
      };
      lockToBrazil();
      map.on('resize', lockToBrazil);
      basemapIsDark = isDarkBasemap(style);
      // Aproximar e afastar; a bússola não ajuda quem procura uma rua.
      map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');
      const geolocate = new maplibregl.GeolocateControl({
        positionOptions: { enableHighAccuracy: true },
        trackUserLocation: false,
      });
      map.addControl(geolocate, 'top-right');
      map
        .getContainer()
        .querySelector('.maplibregl-ctrl-geolocate')
        ?.setAttribute('aria-label', MAP_LOCALE['GeolocateControl.FindMyLocation']);
      if (pickMode) {
        // Arrastar, aproximar ou usar as setas marca o ponto sob o alvo quando o mapa para; um
        // toque leva o mapa até o ponto tocado; "minha localização" marca onde a pessoa está.
        // Movimento feito pelo próprio site (enquadrar, buscar endereço) não marca nada.
        let moved = false;
        map.on('movestart', (event) => {
          if ((event as { originalEvent?: unknown }).originalEvent) moved = true;
        });
        map.on('moveend', () => {
          if (!moved || !map) return;
          moved = false;
          pickPoint(map.getCenter());
        });
        map.on('click', (event) => {
          pickPoint(event.lngLat);
          const still = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
          map!.easeTo({ center: event.lngLat, duration: still ? 0 : 300 });
        });
        geolocate.on('geolocate', (position) =>
          pickPoint(new maplibregl.LngLat(position.coords.longitude, position.coords.latitude)),
        );
      }
      let fallbackUsed = false;
      let styleLoaded = false;
      map.on('error', () => {
        // Only a basemap that never loaded justifies switching provider; a
        // failed tile or overlay later on must not replace the whole style.
        if (!styleLoaded && !fallbackUsed && cartoFallback.name === 'CARTO') {
          fallbackUsed = true;
          basemapIsDark = isDarkBasemap(cartoFallback.styleUrl);
          map?.setStyle(cartoFallback.styleUrl);
          setError(
            'Base cartográfica principal indisponível; usando o fallback CARTO configurado.',
          );
          return;
        }
        setError(
          'Não foi possível carregar parte do mapa. Os registros continuam disponíveis na lista.',
        );
      });
      map.on('style.load', () => {
        styleLoaded = true;
        if (!map || map.getSource('events')) return;
        addSatellite(map);
        addBrazilMask(map);
        map.addSource('events', {
          type: 'geojson',
          cluster: !pickMode,
          clusterMaxZoom: 14,
          clusterRadius: 45,
          data: { type: 'FeatureCollection', features: [] },
        });
        const { cluster } = MAP_DESIGN.markers;
        map.addLayer({
          id: 'report-clusters',
          type: 'circle',
          source: 'events',
          filter: ['has', 'point_count'],
          paint: {
            'circle-color': cluster.fill,
            'circle-radius': cluster.radius,
            'circle-stroke-color': cluster.stroke,
            'circle-stroke-width': 2,
          },
        });
        map.addLayer({
          id: 'report-cluster-count',
          type: 'symbol',
          source: 'events',
          filter: ['has', 'point_count'],
          layout: {
            'text-field': ['get', 'point_count_abbreviated'],
            'text-font': basemapFont(map, map.getStyle().layers ?? []),
            'text-size': 13,
          },
          paint: { 'text-color': cluster.text },
        });
        // Road-sign markers: shape = severity, colour = severity or report
        // status, inner glyph = problem family (see mapMarkers.ts).
        map.addLayer({
          id: 'events',
          type: 'symbol',
          source: 'events',
          filter: ['!', ['has', 'point_count']],
          layout: {
            'icon-image': ['get', 'marker'],
            // Always drawn, but capital names placed below make way for them.
            'icon-allow-overlap': true,
            'icon-ignore-placement': false,
            'icon-size': [
              'case',
              ['==', ['get', 'id'], selectedRef.current ?? ''],
              MAP_DESIGN.markers.selectedScale,
              1,
            ],
          },
        });
        // A style swap (CARTO fallback) empties the map: refit the current data.
        fittedKey.current = '';
        showEvents(map, locatedRef.current, fittedKey, selectedRef.current, audienceRef.current);
      });
      // Diagnóstico do que a camada realmente desenhou (não apenas da lista recebida).
      map.on('idle', () => {
        if (!map?.getLayer('events') || !container.current) return;
        const renderedIds = new Set(
          map
            .queryRenderedFeatures({ layers: ['events'] })
            .map((feature) => feature.properties?.id)
            .filter((id): id is string => typeof id === 'string'),
        );
        container.current.dataset.renderedEventIds = [...renderedIds].join(',');
      });
      map.on('click', 'report-clusters', async (event) => {
        const feature = event.features?.[0];
        if (feature?.geometry.type !== 'Point' || !map) return;
        try {
          const source = map.getSource('events') as maplibregl.GeoJSONSource;
          const zoom = await source.getClusterExpansionZoom(Number(feature.properties?.cluster_id));
          if (mapRef.current === map)
            map.easeTo({ center: feature.geometry.coordinates as [number, number], zoom });
        } catch {
          setError('Não foi possível expandir o grupo. Use a lista de pontos.');
        }
      });
      map.on('click', 'events', (event) => {
        const feature = event.features?.[0];
        if (feature?.geometry.type !== 'Point' || !map) return;
        const properties = feature.properties as Record<string, string>;
        new maplibregl.Popup()
          .setLngLat(feature.geometry.coordinates as [number, number])
          .setText(properties.popup)
          .addTo(map);
        selectCallback.current?.(properties.id);
      });
      map.on('mouseenter', 'events', () => {
        if (map) map.getCanvas().style.cursor = 'pointer';
      });
      map.on('mouseleave', 'events', () => {
        if (map) map.getCanvas().style.cursor = '';
      });
    } catch {
      setFailed(true);
    }
    return () => {
      mapRef.current = null;
      map?.remove();
    };
    // startCenter only seeds the first view; later changes are applied below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    cartoFallback.attribution,
    cartoFallback.name,
    cartoFallback.styleUrl,
    pickMode,
    primaryProvider.attribution,
    primaryProvider.name,
    style,
  ]);

  useEffect(() => {
    const map = mapRef.current;
    if (map?.getSource('events'))
      showEvents(map, located, fittedKey, selectedRef.current, audienceRef.current);
  }, [located]);

  // Endereço escolhido na busca (ou coordenada digitada): o mapa leva o alvo até ele; a
  // pessoa ainda pode ajustar arrastando.
  const pickedLatitude = pickedPoint?.latitude;
  const pickedLongitude = pickedPoint?.longitude;
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !pickMode || pickedLatitude == null || pickedLongitude == null) return;
    if (!isInsideBrazil(pickedLatitude, pickedLongitude)) return;
    const last = lastPicked.current;
    if (last?.latitude === pickedLatitude && last.longitude === pickedLongitude) return;
    map.jumpTo({
      center: [pickedLongitude, pickedLatitude],
      zoom: Math.max(map.getZoom(), MAP_DESIGN.framing.pointZoom + 2),
    });
  }, [pickMode, pickedLatitude, pickedLongitude]);

  const centerLatitude = startCenter?.latitude;
  const centerLongitude = startCenter?.longitude;
  useEffect(() => {
    const map = mapRef.current;
    if (!map || centerLatitude == null || centerLongitude == null) return;
    if (!isInsideBrazil(centerLatitude, centerLongitude)) return;
    map.jumpTo({
      center: [centerLongitude, centerLatitude],
      zoom: startZoom ?? MAP_DESIGN.framing.pointZoom,
    });
    // The zoom only matters together with a new center.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [centerLatitude, centerLongitude]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map?.getLayer?.('events')) return;
    map.setLayoutProperty('events', 'icon-size', [
      'case',
      ['==', ['get', 'id'], selectedId ?? ''],
      MAP_DESIGN.markers.selectedScale,
      1,
    ]);
  }, [selectedId]);

  // Abrir um relato leva o mapa até ele; o ponto vem dos dados, nunca de um centro padrão.
  const selectedPoint = located.find((event) => event.id === selectedId);
  const selectedLatitude = selectedPoint?.latitude;
  const selectedLongitude = selectedPoint?.longitude;
  useEffect(() => {
    const map = mapRef.current;
    if (!map || selectedLatitude == null || selectedLongitude == null) return;
    map.easeTo({
      center: [selectedLongitude, selectedLatitude],
      zoom: Math.max(map.getZoom(), MAP_DESIGN.framing.pointZoom),
      duration: window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ? 0 : 600,
    });
  }, [selectedId, selectedLatitude, selectedLongitude]);

  const shownStatuses = new Set(located.map((event) => event.report_status).filter(Boolean));
  const shownSituations = new Set(
    located
      .filter((event) => event.report_status)
      .map((event) => publicSituation(event.report_status)),
  );
  const showReportLegend = shownStatuses.size > 0;
  // A legenda explica só o que está desenhado agora. Relato é sempre o círculo vazado
  // da situação; a placa de gravidade é só das ocorrências.
  const shownLevels = new Set(
    located
      .filter((event) => !event.report_status)
      .map((event) => severityOf(event.severity).level),
  );
  const showSeverityLegend = shownLevels.size > 0;
  const reportColors: Record<string, string> = MAP_DESIGN.markers.reportStatus;
  const { keyline } = MAP_DESIGN.markers;
  const count = located.length === 1 ? '1 ponto no mapa' : `${located.length} pontos no mapa`;
  // Com filtros, a contagem já aparece acima do mapa: a lista abaixo não repete o número.
  const counted = !onPickLocation && showFilters && allEvents.length > 0;
  return (
    <section className="panel map-panel">
      {!onPickLocation && showFilters && allEvents.length > 0 && !team && (
        <FiltersDisclosure wide={wide} count={count}>
          <div className="map-toolbar" role="group" aria-label="Filtros do mapa">
            {publicOptions.issues.length > 1 && (
              <label>
                Tipo de problema
                <select
                  aria-label="Tipo de problema"
                  value={publicFilters.issue}
                  onChange={(event) =>
                    changePublicFilters({ ...publicFilters, issue: event.target.value })
                  }
                >
                  <option value="">Todos os tipos</option>
                  {publicOptions.issues.map(([code, label]) => (
                    <option key={code} value={code}>
                      {label}
                    </option>
                  ))}
                </select>
              </label>
            )}
            {publicOptions.severities.length > 1 && (
              <label>
                Gravidade
                <select
                  aria-label="Gravidade"
                  value={publicFilters.severity}
                  onChange={(event) =>
                    changePublicFilters({ ...publicFilters, severity: event.target.value })
                  }
                >
                  <option value="">Todas</option>
                  {publicOptions.severities.map(([level, label]) => (
                    <option key={level} value={level}>
                      {label}
                    </option>
                  ))}
                </select>
              </label>
            )}
            {publicOptions.periods.length > 0 && (
              <label>
                Período
                <select
                  aria-label="Período"
                  value={publicFilters.period}
                  onChange={(event) =>
                    changePublicFilters({ ...publicFilters, period: event.target.value })
                  }
                >
                  <option value="">Qualquer data</option>
                  {publicOptions.periods.map(([days, label]) => (
                    <option key={days} value={days}>
                      {label}
                    </option>
                  ))}
                </select>
              </label>
            )}
            {/* No celular, a contagem fica fora de "Filtros" (ver FiltersDisclosure). */}
            {wide && (
              <p role="status" className="map-count">
                {count}
              </p>
            )}
          </div>
        </FiltersDisclosure>
      )}
      {!onPickLocation && showFilters && allEvents.length > 0 && team && (
        <FiltersDisclosure wide={wide} count={count}>
          <div className="map-toolbar" role="group" aria-label="Filtros do mapa">
            {options.statuses.length > 1 && (
              <label>
                Situação
                <select
                  value={filters.status}
                  onChange={(event) => changeFilters({ ...filters, status: event.target.value })}
                >
                  <option value="">Todas</option>
                  {options.statuses.map(([code, label]) => (
                    <option key={code} value={code}>
                      {label}
                    </option>
                  ))}
                </select>
              </label>
            )}
            {options.families.length > 1 && (
              <label>
                Grupo de problema
                <select
                  value={filters.family}
                  onChange={(event) => changeFilters({ ...filters, family: event.target.value })}
                >
                  <option value="">Todos os grupos</option>
                  {options.families.map(([code, label]) => (
                    <option key={code} value={code}>
                      {label}
                    </option>
                  ))}
                </select>
              </label>
            )}
            {options.issues.length > 1 && (
              <label>
                Tipo de problema
                <select
                  value={filters.issue}
                  onChange={(event) => changeFilters({ ...filters, issue: event.target.value })}
                >
                  <option value="">Todos os tipos</option>
                  {options.issues.map(([code, label]) => (
                    <option key={code} value={code}>
                      {label}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <DateField
              label="Recebido a partir de"
              value={filters.from}
              onChange={(from) => changeFilters({ ...filters, from })}
            />
            <DateField
              label="Recebido até"
              value={filters.to}
              onChange={(to) => changeFilters({ ...filters, to })}
            />
            {/* No celular, a contagem fica fora de "Filtros" (ver FiltersDisclosure). */}
            {wide && (
              <p role="status" className="map-count">
                {count}
              </p>
            )}
          </div>
        </FiltersDisclosure>
      )}
      <div className="map-wrap">
        {/* A moldura tem só a altura do mapa: a mira fica sobre ele. */}
        <div className="map-frame">
          <div
            className="map"
            style={{ height: MAP_DESIGN.slot.height }}
            ref={container}
            // Região com nome, não imagem: dentro ficam os botões e o mapa navegável.
            role="region"
            aria-label={
              pickMode
                ? 'Mapa para marcar o local: arraste o mapa, ou use as setas do teclado, até o alvo ficar sobre o local'
                : `Mapa com ${located.length} de ${events.length} pontos localizados`
            }
          />
          {pickMode && !failed && <span className="map-crosshair" aria-hidden="true" />}
        </div>
        {failed && (
          <div className="map-fallback" role="alert">
            <strong>O mapa não pôde ser exibido neste navegador</strong>
            <p>
              {onPickLocation
                ? 'Sem o mapa não é possível marcar o ponto agora. Você pode enviar o relato e marcar o local depois, em outro aparelho.'
                : located.length
                  ? 'Os pontos continuam disponíveis na lista abaixo do mapa.'
                  : 'Quando houver pontos, eles também aparecem em lista abaixo do mapa.'}{' '}
              Atualize o navegador ou tente abrir em outro.
            </p>
            <button type="button" className="secondary" onClick={() => location.reload()}>
              Recarregar página
            </button>
          </div>
        )}
        {!failed && !located.length && !onPickLocation && emptyMessage !== null && (
          <div className="map-empty" role="status">
            {filtering && allEvents.length > 0 ? (
              <>
                <span>
                  {team
                    ? 'Nenhum ponto corresponde a estes filtros.'
                    : 'Nenhuma ocorrência encontrada'}
                </span>
                <button type="button" className="secondary" onClick={clearFilters}>
                  Limpar filtros
                </button>
              </>
            ) : (
              <span>{emptyMessage}</span>
            )}
          </div>
        )}
        {showLegend && (showReportLegend || showSeverityLegend) && (
          <div className="map-legends">
            {showSeverityLegend && (
              <ul className="map-legend" aria-label="Legenda de gravidade">
                <li className="map-legend-title" aria-hidden="true">
                  Gravidade
                </li>
                {LEGEND.filter((level) => shownLevels.has(level)).map((level) => {
                  const sign = markerStyle({ severity: level });
                  return (
                    <li key={level}>
                      <SignSwatch style={sign} />
                      {/* Sob o título "Gravidade", sem repetir a palavra. */}
                      {level === 'unknown' ? 'A definir' : severityOf(level).label}
                    </li>
                  );
                })}
              </ul>
            )}
            {showReportLegend && team && (
              <ul className="map-legend" aria-label="Legenda da situação dos relatos">
                <li className="map-legend-title" aria-hidden="true">
                  Situação do relato
                </li>
                {/* Uma linha por cor: situações que o mapa pinta igual saem juntas
                    ("Confirmado ou publicado"), sem duas linhas com a mesma cor. */}
                {Object.values(
                  Object.entries(reportLabels)
                    .filter(([status]) =>
                      shownStatuses.has(status as CaptureMarker['report_status']),
                    )
                    .reduce<Record<string, { color: string; labels: string[] }>>(
                      (groups, [status, label]) => {
                        const color =
                          reportColors[status] ?? MAP_DESIGN.markers.reportStatus.received;
                        const group = (groups[color] ??= { color, labels: [] });
                        if (!group.labels.includes(label)) group.labels.push(label);
                        return groups;
                      },
                      {},
                    ),
                ).map(({ color, labels }) => (
                  <li key={color}>
                    <SignSwatch style={{ shape: 'report', fill: color, glyph: keyline }} />
                    {labels
                      .map((label, index) => (index ? label.toLowerCase() : label))
                      .join(' ou ')}
                  </li>
                ))}
              </ul>
            )}
            {showReportLegend && !team && (
              <ul className="map-legend" aria-label="Legenda da situação dos relatos">
                <li className="map-legend-title" aria-hidden="true">
                  Situação do relato
                </li>
                {SITUATION_ORDER.filter((situation) => shownSituations.has(situation)).map(
                  (situation) => (
                    <li key={situation}>
                      <SignSwatch
                        style={{
                          shape: 'report',
                          fill: reportColors[SITUATION_SAMPLE[situation]],
                          glyph: keyline,
                        }}
                      />
                      {publicSituationLabels[situation]}
                    </li>
                  ),
                )}
              </ul>
            )}
          </div>
        )}
        {/* Sobre o mapa, abaixo dos filtros: nunca cobre a barra de filtros. */}
        {detail && selectedId && (
          <aside className="map-detail" aria-label="Detalhe do ponto">
            <button type="button" className="secondary compact" onClick={onCloseDetail}>
              Fechar detalhe
            </button>
            {detail}
          </aside>
        )}
      </div>
      {/* Nada a listar nem exportar: sem rodapé vazio. */}
      <div
        className="map-footer"
        hidden={pickMode || ((!located.length || !showList) && !allowExport)}
      >
        {/* O canvas não é legível por leitor de tela: a mesma informação em texto. Onde a
            lista já está na página (Meus relatos), não se repete aqui. */}
        <details className="map-point-list" open={failed || undefined} hidden={!showList}>
          <summary>
            {located.length === 1
              ? 'Ver o ponto em lista'
              : counted
                ? 'Ver os pontos em lista'
                : `Ver os ${located.length} pontos em lista`}
          </summary>
          <ul>
            {located.map((event) => (
              <li key={event.id} data-event-id={event.id}>
                <button
                  type="button"
                  className="text-button"
                  onClick={() => onSelect?.(event.id)}
                  aria-label={`Selecionar ponto: ${markerLabel(event, audience)}`}
                >
                  {markerLabel(event, audience)}
                </button>{' '}
                {team ? (
                  <>
                    {event.report_status
                      ? ''
                      : `· gravidade ${severityOf(event.severity).label.toLowerCase()}`}{' '}
                    · {event.road_name ?? 'sem via associada'} · {event.latitude!.toFixed(5)},{' '}
                    {event.longitude!.toFixed(5)}
                  </>
                ) : (
                  event.road_name && <>· {event.road_name}</>
                )}
              </li>
            ))}
          </ul>
        </details>
        {allowExport && (
          <div className="map-export">
            <div>
              {(['csv', 'geojson'] as const).map((format) => (
                <button
                  key={format}
                  type="button"
                  className="secondary"
                  disabled={exporting}
                  onClick={async () => {
                    exportController.current?.abort();
                    const controller = new AbortController();
                    exportController.current = controller;
                    setExporting(true);
                    setExportError('');
                    try {
                      const blob = await api.exportReports(format, filters, controller.signal);
                      if (controller.signal.aborted) return;
                      const url = URL.createObjectURL(blob);
                      const link = document.createElement('a');
                      link.href = url;
                      link.download = `urmind-pontos-filtrados.${format}`;
                      link.click();
                      setTimeout(() => URL.revokeObjectURL(url), 1000);
                    } catch (error) {
                      if (!controller.signal.aborted)
                        setExportError(
                          error instanceof Error
                            ? error.message
                            : 'Não foi possível baixar agora. Tente de novo em instantes.',
                        );
                    } finally {
                      if (!controller.signal.aborted) setExporting(false);
                    }
                  }}
                >
                  {exporting
                    ? 'Preparando…'
                    : format === 'csv'
                      ? 'Baixar planilha'
                      : 'Baixar para programas de mapa'}
                </button>
              ))}
            </div>
            <small>Todos os relatos dos filtros atuais, sem fotos nem dados pessoais.</small>
            {exportError && <p role="alert">{exportError}</p>}
          </div>
        )}
        {/* Crédito da base cartográfica, compacto e sempre presente. */}
      </div>
      {error && (
        <p role="alert" className="notice">
          {error}
        </p>
      )}
    </section>
  );
}

/** Filtros abertos no computador; no celular, recolhidos em "Filtros" (como na fila). */
function FiltersDisclosure({
  wide,
  count,
  children,
}: {
  wide: boolean;
  /** "N pontos no mapa": no celular, à vista ao lado de "Filtros". */
  count: string;
  children: ReactNode;
}) {
  if (wide) return <>{children}</>;
  return (
    <div className="map-filters-row">
      <details className="filters-disclosure map-filters">
        <summary>Filtros</summary>
        {children}
      </details>
      <p role="status" className="map-count">
        {count}
      </p>
    </div>
  );
}

/** Legend swatch drawn from the same sign paths as the map markers. */
function SignSwatch({ style }: { style: MarkerStyle }) {
  const ring = style.shape === 'ring';
  return (
    <svg className="map-sign" viewBox="-2 -2 36 36" aria-hidden="true" focusable="false">
      <path
        d={SHAPE_PATHS[style.shape]}
        fill={ring ? '#ffffff' : style.fill}
        stroke={ring ? style.fill : MAP_DESIGN.markers.keyline}
        strokeWidth={ring ? 3 : 2.5}
        strokeLinejoin="round"
      />
      {style.shape === 'warning' && (
        <path d={WARNING_INSET} fill="none" stroke={MAP_DESIGN.markers.keyline} strokeWidth={1.6} />
      )}
      {style.shape === 'report' && (
        <circle
          cx={16}
          cy={16}
          r={REPORT_HOLE}
          fill="#ffffff"
          stroke={MAP_DESIGN.markers.keyline}
          strokeWidth={1.5}
        />
      )}
    </svg>
  );
}
