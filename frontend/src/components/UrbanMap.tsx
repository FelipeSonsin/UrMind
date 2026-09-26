import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import * as maplibregl from 'maplibre-gl';
import mapWorkerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url';
import 'maplibre-gl/dist/maplibre-gl.css';
import { convertFilter, expression } from '@maplibre/maplibre-gl-style-spec';
import {
  familyFor,
  filterMapRecords,
  labelFor,
  severityOf,
  type MapFilters,
} from '../domain/public';
import {
  BRAZIL_OUTLINE_BOUNDS,
  BRAZIL_OUTLINE_URL,
  BRAZIL_STATES_URL,
  isInBrazilMapViewport,
  isInsidePolygons,
  outlinePolygons,
  outsideMask,
  resolveMapProvider,
  type Polygon,
} from '../mapConfig';
import { reportLabels, type CaptureMarker } from '../domain/contracts';
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

const DARK_QUERY = '(prefers-color-scheme: dark)';
function outlineTheme(): MapColors {
  const dark = window.matchMedia?.(DARK_QUERY).matches ?? false;
  return dark ? MAP_DESIGN.colors.dark : MAP_DESIGN.colors.light;
}

function applyOutlineTheme(map: maplibregl.Map) {
  const theme = outlineTheme();
  const paints: [string, keyof maplibregl.AllPaintProperties, string][] = [
    ['brazil-mask', 'fill-color', theme.mask],
    ['brazil-border', 'line-color', theme.border],
    ['brazil-states', 'line-color', theme.states],
    ['brazil-capitals-dot', 'circle-color', theme.capitalText],
    ['brazil-capitals-dot', 'circle-stroke-color', theme.capitalHalo],
    ['brazil-capitals', 'text-color', theme.capitalText],
    ['brazil-capitals', 'text-halo-color', theme.capitalHalo],
  ];
  for (const [layer, property, value] of paints)
    if (map.getLayer(layer)) map.setPaintProperty(layer, property, value);
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
    if (layer.type === 'symbol' && /country/i.test(layer.id))
      map.setLayoutProperty(layer.id, 'visibility', 'none');
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

/** Replaces the plotted points; reframes only when the set of points changes. */
function showEvents(map: maplibregl.Map, located: MapMarker[], fittedKey: { current: string }) {
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
        label: markerLabel(event),
        report: Boolean(event.report_status),
        severity: severityOf(event.severity).label,
        road: event.road_name ?? '',
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

const LEGEND = ['critical', 'high', 'medium', 'low', 'unknown'];
function markerLabel(event: MapMarker) {
  return event.report_status
    ? `${reportLabels[event.report_status]}${event.urmind_class ? ` · ${labelFor(event.urmind_class)}` : ''}`
    : labelFor(event.urmind_class ?? '');
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
}: {
  events: MapMarker[];
  selectedId?: string | null;
  onSelect?: (id: string) => void;
  onPickLocation?: (latitude: number, longitude: number) => void;
  initialCenter?: { latitude: number; longitude: number } | null;
  detail?: ReactNode;
  onCloseDetail?: () => void;
  allowExport?: boolean;
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
  const events = useMemo(
    () => (onPickLocation ? allEvents : filterMapRecords(allEvents, filters)),
    [allEvents, filters, onPickLocation],
  );
  const options = {
    statuses: [...new Set(allEvents.map((row) => row.report_status ?? row.status).filter(Boolean))],
    families: [...new Set(allEvents.map((row) => familyFor(row.urmind_class)).filter(Boolean))],
    issues: [
      ...new Set(
        allEvents.map((row) => row.urmind_class).filter((value): value is string => Boolean(value)),
      ),
    ],
  };
  function changeFilters(next: MapFilters) {
    setFilters(next);
    onCloseDetail?.();
    const query = new URLSearchParams(location.hash.split('?')[1] ?? '');
    for (const [key, value] of Object.entries({
      map_status: next.status,
      map_family: next.family,
      map_class: next.issue,
      map_from: next.from,
      map_to: next.to,
    })) {
      if (value) query.set(key, value);
      else query.delete(key);
    }
    history.replaceState(
      null,
      '',
      `${location.hash.split('?')[0]}${query.size ? `?${query}` : ''}`,
    );
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
  const [activeProviderName, setActiveProviderName] = useState(primaryProvider.name);
  const [activeAttribution, setActiveAttribution] = useState(primaryProvider.attribution);

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
  const fittedKey = useRef('');
  const pickMode = Boolean(onPickLocation);

  useEffect(() => {
    if (!container.current) return;
    let map: maplibregl.Map | undefined;
    let schemeQuery: MediaQueryList | undefined;
    let schemeListener: (() => void) | undefined;
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
              zoom: MAP_DESIGN.framing.pointZoom,
            }
          : { bounds: BRAZIL_FIT, fitBoundsOptions: { padding: BRAZIL_PADDING } }),
        renderWorldCopies: false,
        attributionControl: { compact: true },
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
      schemeQuery = window.matchMedia?.(DARK_QUERY);
      schemeListener = () => map && applyOutlineTheme(map);
      schemeQuery?.addEventListener?.('change', schemeListener);
      setActiveProviderName(primaryProvider.name);
      setActiveAttribution(primaryProvider.attribution);
      map.addControl(new maplibregl.NavigationControl(), 'top-right');
      map.addControl(
        new maplibregl.GeolocateControl({
          positionOptions: { enableHighAccuracy: true },
          trackUserLocation: false,
        }),
        'top-right',
      );
      if (pickMode) {
        let pickedMarker: maplibregl.Marker | undefined;
        map.on('click', (event) => {
          const { lat, lng } = event.lngLat;
          if (!isInsideBrazil(lat, lng)) {
            setError('Escolha um ponto dentro do território brasileiro.');
            return;
          }
          setError('');
          pickedMarker?.remove();
          pickedMarker = new maplibregl.Marker({ color: '#123f36' })
            .setLngLat(event.lngLat)
            .addTo(map!);
          pickCallback.current?.(lat, lng);
        });
      }
      let fallbackUsed = false;
      let styleLoaded = false;
      map.on('error', () => {
        // Only a basemap that never loaded justifies switching provider; a
        // failed tile or overlay later on must not replace the whole style.
        if (!styleLoaded && !fallbackUsed && cartoFallback.name === 'CARTO') {
          fallbackUsed = true;
          map?.setStyle(cartoFallback.styleUrl);
          setActiveProviderName('CARTO');
          setActiveAttribution(cartoFallback.attribution);
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
            'circle-stroke-color': '#ffffff',
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
        showEvents(map, locatedRef.current, fittedKey);
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
          .setText(
            properties.report
              ? properties.label
              : `${properties.label} · severidade ${properties.severity}${properties.road ? ` · ${properties.road}` : ''}`,
          )
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
      if (schemeListener) schemeQuery?.removeEventListener?.('change', schemeListener);
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
    if (map?.getSource('events')) showEvents(map, located, fittedKey);
  }, [located]);

  const centerLatitude = initialCenter?.latitude;
  const centerLongitude = initialCenter?.longitude;
  useEffect(() => {
    const map = mapRef.current;
    if (!map || centerLatitude == null || centerLongitude == null) return;
    if (!isInsideBrazil(centerLatitude, centerLongitude)) return;
    map.jumpTo({ center: [centerLongitude, centerLatitude], zoom: MAP_DESIGN.framing.pointZoom });
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

  const showReportLegend = events.some((event) => event.report_status);
  const showSeverityLegend = events.some((event) => !event.report_status || event.severity != null);
  const reportColors: Record<string, string> = MAP_DESIGN.markers.reportStatus;
  return (
    <section className="panel map-panel">
      {!onPickLocation && (
        <div className="map-toolbar" role="group" aria-label="Filtros do mapa">
          <label>
            Status do ponto
            <select
              value={filters.status}
              onChange={(event) => changeFilters({ ...filters, status: event.target.value })}
            >
              <option value="">Todos</option>
              {options.statuses.map((status) => (
                <option key={status} value={status}>
                  {reportLabels[status as CaptureMarker['report_status']] ?? status}
                </option>
              ))}
            </select>
          </label>
          <label>
            Família do ponto
            <select
              value={filters.family}
              onChange={(event) => changeFilters({ ...filters, family: event.target.value })}
            >
              <option value="">Todas</option>
              {options.families.map((family) => (
                <option key={family}>{family}</option>
              ))}
            </select>
          </label>
          <label>
            Classe do ponto
            <select
              value={filters.issue}
              onChange={(event) => changeFilters({ ...filters, issue: event.target.value })}
            >
              <option value="">Todas</option>
              {options.issues.map((issue) => (
                <option key={issue} value={issue}>
                  {labelFor(issue)}
                </option>
              ))}
            </select>
          </label>
          <label>
            Desde (UTC)
            <input
              type="date"
              value={filters.from}
              onChange={(event) => changeFilters({ ...filters, from: event.target.value })}
            />
          </label>
          <label>
            Até (UTC)
            <input
              type="date"
              value={filters.to}
              onChange={(event) => changeFilters({ ...filters, to: event.target.value })}
            />
          </label>
          <p role="status" className="map-count">
            {located.length} pontos visíveis
          </p>
        </div>
      )}
      <div className="map-wrap">
        <div
          className="map"
          style={{ height: MAP_DESIGN.slot.height }}
          ref={container}
          role="img"
          aria-label={`Mapa com ${located.length} de ${events.length} ocorrências localizadas`}
        />
        {!located.length && !onPickLocation && (
          <p className="map-empty">Nenhum registro com localização disponível neste recorte.</p>
        )}
        {(showReportLegend || showSeverityLegend) && (
          <div className="map-legends">
            {showSeverityLegend && (
              <ul className="map-legend" aria-label="Legenda de severidade">
                {LEGEND.map((level) => {
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
            {showReportLegend && (
              <ul className="map-legend" aria-label="Legenda de relatos">
                {Object.entries(reportLabels).map(([status, label]) => (
                  <li key={status}>
                    <SignSwatch
                      style={{
                        shape: 'circle',
                        fill: reportColors[status] ?? '#64748b',
                        glyph: '#ffffff',
                      }}
                    />
                    {label}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>
      {detail && selectedId && (
        <aside className="map-detail" aria-label="Detalhe do ponto">
          <button type="button" className="secondary" onClick={onCloseDetail}>
            Fechar detalhe
          </button>
          {detail}
        </aside>
      )}
      <div className="map-footer">
        {/* O canvas não é legível por leitor de tela: a mesma informação em texto. */}
        <details className="map-point-list">
          <summary>Lista acessível de pontos ({located.length})</summary>
          <ul>
            {located.map((event) => (
              <li key={event.id} data-event-id={event.id}>
                <button
                  type="button"
                  onClick={() => onSelect?.(event.id)}
                  aria-label={`Selecionar ponto: ${markerLabel(event)}`}
                >
                  {markerLabel(event)}
                </button>{' '}
                {event.report_status ? '' : `· severidade ${severityOf(event.severity).label}`} ·{' '}
                {event.road_name ?? 'via não associada'} · {event.latitude}, {event.longitude}
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
        <p className="map-caption">
          {located.length} de {events.length} registros têm localização disponível neste mapa.{' '}
          {style
            ? `${activeProviderName}: ${activeAttribution}. Coordenadas originais; o ponto ajustado à via permanece separado.`
            : 'Somente coordenadas reais são exibidas; nenhuma rua é simulada.'}
        </p>
      </div>
      {error && (
        <p role="alert" className="notice">
          {error}
        </p>
      )}
    </section>
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
        stroke={ring ? style.fill : '#ffffff'}
        strokeWidth={ring ? 3 : 2.5}
        strokeLinejoin="round"
      />
      {style.shape === 'warning' && (
        <path d={WARNING_INSET} fill="none" stroke="#ffffff" strokeWidth={1.6} />
      )}
    </svg>
  );
}
