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
  formatBrDate,
  issueHeadline,
  labelFor,
  mapFilterOptions,
  maskBrDate,
  parseBrDate,
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
  type Polygon,
} from '../mapConfig';
import { reportLabels, statuses, type CaptureMarker } from '../domain/contracts';
import { BRAZIL_CAPITALS, MAP_DESIGN, byZoom, type MapColors } from '../mapDesign';
import {
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
function outlineTheme(): MapColors {
  return basemapIsDark ? MAP_DESIGN.colors.dark : MAP_DESIGN.colors.light;
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
    ? `${label} · severidade ${severityOf(event.severity).label}${road}`
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
}: {
  events: MapMarker[];
  selectedId?: string | null;
  onSelect?: (id: string) => void;
  onPickLocation?: (latitude: number, longitude: number) => void;
  initialCenter?: { latitude: number; longitude: number } | null;
  detail?: ReactNode;
  onCloseDetail?: () => void;
  allowExport?: boolean;
  emptyMessage?: string;
  /** Mapas de apoio (início) mostram os pontos sem a barra de filtros. */
  showFilters?: boolean;
  /** Público vê filtros simples e nomes simples; a equipe, o vocabulário técnico. */
  audience?: MapAudience;
  /** Ponto escolhido fora do mapa (busca de endereço): o marcador vai até ele. */
  pickedPoint?: { latitude: number; longitude: number } | null;
  /** Zoom inicial ao abrir em `initialCenter` (padrão: nível de rua). */
  initialZoom?: number;
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
      onPickLocation
        ? allEvents
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
  const container = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map>(null);
  const pickCallback = useRef(onPickLocation);
  pickCallback.current = onPickLocation;
  const [error, setError] = useState('');
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
  const pickedMarker = useRef<maplibregl.Marker | null>(null);

  /** Um toque no mapa ou "Marcar o centro do mapa": o mesmo ponto confirmado pela pessoa. */
  function pickPoint(map: maplibregl.Map, point: maplibregl.LngLat) {
    if (!isInsideBrazil(point.lat, point.lng)) {
      setError('Escolha um ponto dentro do território brasileiro.');
      return;
    }
    setError('');
    pickedMarker.current?.remove();
    pickedMarker.current = new maplibregl.Marker({ color: MAP_DESIGN.markers.picked })
      .setLngLat(point)
      .addTo(map);
    pickCallback.current?.(point.lat, point.lng);
  }

  useEffect(() => {
    if (!container.current) return;
    let map: maplibregl.Map | undefined;
    try {
      const shownCenter =
        initialCenter && isInsideBrazil(initialCenter.latitude, initialCenter.longitude)
          ? initialCenter
          : null;
      fittedKey.current = '';
      map = new maplibregl.Map({
        container: container.current,
        style,
        ...(shownCenter
          ? {
              center: [shownCenter.longitude, shownCenter.latitude],
              zoom: initialZoom ?? MAP_DESIGN.framing.pointZoom,
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
      map.addControl(new maplibregl.NavigationControl(), 'top-right');
      map.addControl(
        new maplibregl.GeolocateControl({
          positionOptions: { enableHighAccuracy: true },
          trackUserLocation: false,
        }),
        'top-right',
      );
      if (pickMode) map.on('click', (event) => pickPoint(map!, event.lngLat));
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
      setError('Mapa indisponível: verifique o suporte a WebGL do navegador.');
    }
    return () => {
      mapRef.current = null;
      map?.remove();
    };
    // initialCenter only seeds the first view; later changes are applied below.
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

  // Endereço escolhido na busca: o marcador e o mapa vão até ele; a pessoa ainda pode ajustar.
  const pickedLatitude = pickedPoint?.latitude;
  const pickedLongitude = pickedPoint?.longitude;
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !pickMode || pickedLatitude == null || pickedLongitude == null) return;
    if (!isInsideBrazil(pickedLatitude, pickedLongitude)) return;
    pickedMarker.current?.remove();
    pickedMarker.current = new maplibregl.Marker({ color: MAP_DESIGN.markers.picked })
      .setLngLat([pickedLongitude, pickedLatitude])
      .addTo(map);
    map.jumpTo({
      center: [pickedLongitude, pickedLatitude],
      zoom: Math.max(map.getZoom(), MAP_DESIGN.framing.pointZoom + 2),
    });
  }, [pickMode, pickedLatitude, pickedLongitude]);

  const centerLatitude = initialCenter?.latitude;
  const centerLongitude = initialCenter?.longitude;
  useEffect(() => {
    const map = mapRef.current;
    if (!map || centerLatitude == null || centerLongitude == null) return;
    if (!isInsideBrazil(centerLatitude, centerLongitude)) return;
    map.jumpTo({
      center: [centerLongitude, centerLatitude],
      zoom: initialZoom ?? MAP_DESIGN.framing.pointZoom,
    });
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
  // Público: a legenda explica só as gravidades que estão no mapa agora.
  const shownLevels = new Set(
    located
      .filter((event) => !event.report_status || event.severity != null)
      .map((event) => severityOf(event.severity).level),
  );
  const showSeverityLegend = located.some(
    (event) => !event.report_status || event.severity != null,
  );
  const reportColors: Record<string, string> = MAP_DESIGN.markers.reportStatus;
  const { keyline } = MAP_DESIGN.markers;
  const count = located.length === 1 ? '1 ponto visível' : `${located.length} pontos visíveis`;
  return (
    <section className="panel map-panel">
      {!onPickLocation && showFilters && allEvents.length > 0 && !team && (
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
                <option value="">Todos</option>
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
          <p role="status" className="map-count">
            {count}
          </p>
        </div>
      )}
      {!onPickLocation && showFilters && allEvents.length > 0 && team && (
        <div className="map-toolbar" role="group" aria-label="Filtros do mapa">
          {options.statuses.length > 1 && (
            <label>
              Status do ponto
              <select
                value={filters.status}
                onChange={(event) => changeFilters({ ...filters, status: event.target.value })}
              >
                <option value="">Todos</option>
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
              Família do ponto
              <select
                value={filters.family}
                onChange={(event) => changeFilters({ ...filters, family: event.target.value })}
              >
                <option value="">Todas</option>
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
              Classe do ponto
              <select
                value={filters.issue}
                onChange={(event) => changeFilters({ ...filters, issue: event.target.value })}
              >
                <option value="">Todas</option>
                {options.issues.map(([code, label]) => (
                  <option key={code} value={code}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
          )}
          <DateField
            label="Desde"
            value={filters.from}
            onChange={(from) => changeFilters({ ...filters, from })}
          />
          <DateField
            label="Até"
            value={filters.to}
            onChange={(to) => changeFilters({ ...filters, to })}
          />
          <p role="status" className="map-count">
            {count}
          </p>
        </div>
      )}
      <div className="map-wrap">
        <div
          className="map"
          style={{ height: MAP_DESIGN.slot.height }}
          ref={container}
          role="img"
          aria-label={`Mapa com ${located.length} de ${events.length} pontos localizados`}
        />
        {!located.length && !onPickLocation && (
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
        {(showReportLegend || showSeverityLegend) && (
          <div className="map-legends">
            {showSeverityLegend && (
              <ul
                className="map-legend"
                aria-label={team ? 'Legenda de severidade' : 'Legenda de gravidade'}
              >
                {LEGEND.filter((level) => team || shownLevels.has(level)).map((level) => {
                  const sign = markerStyle({ severity: level });
                  return (
                    <li key={level}>
                      <SignSwatch style={sign} />
                      {severityOf(level).label}
                    </li>
                  );
                })}
              </ul>
            )}
            {showReportLegend && team && (
              <ul className="map-legend" aria-label="Legenda de relatos">
                {Object.entries(reportLabels)
                  .filter(([status]) => shownStatuses.has(status as CaptureMarker['report_status']))
                  .map(([status, label]) => (
                    <li key={status}>
                      <SignSwatch
                        style={{
                          shape: 'circle',
                          fill: reportColors[status] ?? MAP_DESIGN.markers.reportStatus.received,
                          glyph: keyline,
                        }}
                      />
                      {label}
                    </li>
                  ))}
              </ul>
            )}
            {showReportLegend && !team && (
              <ul className="map-legend" aria-label="Legenda de relatos">
                {SITUATION_ORDER.filter((situation) => shownSituations.has(situation)).map(
                  (situation) => (
                    <li key={situation}>
                      <SignSwatch
                        style={{
                          shape: 'circle',
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
      {pickMode && (
        <div className="map-footer map-pick-center">
          {/* Alternativa ao toque: teclado (setas, + e -) move o mapa até o local. */}
          <button
            type="button"
            className="secondary"
            onClick={() => mapRef.current && pickPoint(mapRef.current, mapRef.current.getCenter())}
          >
            Marcar o centro do mapa
          </button>
          <small>Toque no local da foto ou mova o mapa até ele e marque o centro.</small>
        </div>
      )}
      {/* Nada a listar nem exportar: sem rodapé vazio. */}
      <div className="map-footer" hidden={pickMode || (!located.length && !allowExport)}>
        {/* O canvas não é legível por leitor de tela: a mesma informação em texto. */}
        <details className="map-point-list">
          <summary>Lista acessível de pontos ({located.length})</summary>
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
                    {event.report_status ? '' : `· severidade ${severityOf(event.severity).label}`}{' '}
                    · {event.road_name ?? 'via não associada'} · {event.latitude!.toFixed(5)},{' '}
                    {event.longitude!.toFixed(5)}
                  </>
                ) : (
                  <>· {event.road_name ?? 'via não identificada'}</>
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
                          error instanceof Error ? error.message : 'Exportação indisponível',
                        );
                    } finally {
                      if (!controller.signal.aborted) setExporting(false);
                    }
                  }}
                >
                  Exportar {format.toUpperCase()}
                </button>
              ))}
            </div>
            <small>
              Todos os relatos que correspondem aos filtros, em lotes no servidor; sem fotos ou
              identidades.
            </small>
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

/**
 * Data digitada como dd/mm/aaaa: o campo nativo de data segue o idioma do navegador e
 * mostraria mm/dd/yyyy numa interface em português. Guarda aaaa-mm-dd internamente.
 */
function DateField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (isoDate: string) => void;
}) {
  const [text, setText] = useState(() => formatBrDate(value));
  const [lastValue, setLastValue] = useState(value);
  if (value !== lastValue) {
    setLastValue(value);
    setText(formatBrDate(value));
  }
  const invalid = text.length === 10 && parseBrDate(text) === null;
  return (
    <label>
      {label}
      <input
        inputMode="numeric"
        autoComplete="off"
        placeholder="dd/mm/aaaa"
        maxLength={10}
        value={text}
        aria-invalid={invalid || undefined}
        onChange={(event) => {
          const next = maskBrDate(event.target.value);
          setText(next);
          if (!next) onChange('');
          else {
            const iso = parseBrDate(next);
            if (iso) onChange(iso);
          }
        }}
      />
      {invalid && <small className="field-error">Data inexistente</small>}
    </label>
  );
}

/** Legend swatch drawn from the same sign paths as the map markers. */
function SignSwatch({ style }: { style: MarkerStyle }) {
  const ring = style.shape === 'ring';
  return (
    <svg className="map-sign" viewBox="-2 -2 36 36" aria-hidden="true" focusable="false">
      <path
        d={SHAPE_PATHS[style.shape]}
        fill={ring ? MAP_DESIGN.markers.keyline : style.fill}
        stroke={ring ? style.fill : MAP_DESIGN.markers.keyline}
        strokeWidth={ring ? 3 : 2.5}
        strokeLinejoin="round"
      />
      {style.shape === 'warning' && (
        <path d={WARNING_INSET} fill="none" stroke={MAP_DESIGN.markers.keyline} strokeWidth={1.6} />
      )}
    </svg>
  );
}
