import { useEffect, useRef, useState, type ReactNode } from 'react';
import * as maplibregl from 'maplibre-gl';
import mapWorkerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url';
import 'maplibre-gl/dist/maplibre-gl.css';
import { labelFor, severityOf } from '../domain/public';
import { resolveMapProvider } from '../mapConfig';
import { reportLabels, type CaptureMarker } from '../domain/contracts';

// Vite must emit the actual worker; MapLibre's sibling default URL does not
// survive bundling the library into a hashed application chunk.
maplibregl.setWorkerUrl(mapWorkerUrl);

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
}

const SEVERITY_COLOR: Record<string, string> = {
  critical: '#7f1d1d',
  high: '#b45309',
  medium: '#a16207',
  low: '#123f36',
  unknown: '#66766f',
};
const LEGEND = ['critical', 'high', 'medium', 'low', 'unknown'];
function markerLabel(event: MapMarker) {
  return event.report_status
    ? `${reportLabels[event.report_status]}${event.urmind_class ? ` · ${labelFor(event.urmind_class)}` : ''}`
    : labelFor(event.urmind_class ?? '');
}

export default function UrbanMap({
  events,
  selectedId,
  onSelect,
  onPickLocation,
  initialCenter,
  detail,
  onCloseDetail,
}: {
  events: MapMarker[];
  selectedId?: string | null;
  onSelect?: (id: string) => void;
  onPickLocation?: (latitude: number, longitude: number) => void;
  initialCenter?: { latitude: number; longitude: number } | null;
  detail?: ReactNode;
  onCloseDetail?: () => void;
}) {
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

  useEffect(() => {
    if (!container.current) return;
    let map: maplibregl.Map | undefined;
    try {
      delete container.current.dataset.renderedEventIds;
      const located = events.filter((e) => e.latitude != null && e.longitude != null);
      map = new maplibregl.Map({
        container: container.current,
        style,
        center: initialCenter ? [initialCenter.longitude, initialCenter.latitude] : [0, 0],
        zoom: initialCenter ? 15 : 1,
      });
      mapRef.current = map;
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
      if (pickCallback.current) {
        let pickedMarker: maplibregl.Marker | undefined;
        map.on('click', (event) => {
          pickedMarker?.remove();
          pickedMarker = new maplibregl.Marker({ color: '#123f36' })
            .setLngLat(event.lngLat)
            .addTo(map!);
          pickCallback.current?.(event.lngLat.lat, event.lngLat.lng);
        });
      }
      let fallbackUsed = false;
      let interactionsBound = false;
      map.on('error', () => {
        if (!fallbackUsed && cartoFallback.name === 'CARTO') {
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
        if (!map) return;
        if (map.getSource('events')) return;
        map.addSource('events', {
          type: 'geojson',
          cluster: !pickCallback.current,
          clusterMaxZoom: 14,
          clusterRadius: 45,
          data: {
            type: 'FeatureCollection',
            features: located.map((event) => ({
              type: 'Feature' as const,
              geometry: {
                type: 'Point' as const,
                coordinates: [event.longitude!, event.latitude!],
              },
              properties: {
                id: event.id,
                label: markerLabel(event),
                report: Boolean(event.report_status),
                severity: severityOf(event.severity).label,
                color: SEVERITY_COLOR[severityOf(event.severity).level],
                road: event.road_name ?? '',
              },
            })),
          },
        });
        map.addLayer({
          id: 'report-clusters',
          type: 'circle',
          source: 'events',
          filter: ['has', 'point_count'],
          paint: { 'circle-color': '#123f36', 'circle-radius': 22 },
        });
        map.addLayer({
          id: 'report-cluster-count',
          type: 'symbol',
          source: 'events',
          filter: ['has', 'point_count'],
          layout: { 'text-field': ['get', 'point_count_abbreviated'], 'text-size': 14 },
          paint: { 'text-color': '#ffffff' },
        });
        map.addLayer({
          id: 'events',
          type: 'circle',
          source: 'events',
          filter: ['!', ['has', 'point_count']],
          paint: {
            'circle-radius': ['case', ['==', ['get', 'id'], selectedId ?? ''], 11, 7],
            'circle-color': ['get', 'color'],
            'circle-stroke-color': '#f5f6f2',
            'circle-stroke-width': 3,
          },
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
        if (!interactionsBound) {
          interactionsBound = true;
          map.on('click', 'report-clusters', async (event) => {
            const feature = event.features?.[0];
            if (feature?.geometry.type !== 'Point' || !map) return;
            try {
              const source = map.getSource('events') as maplibregl.GeoJSONSource;
              const zoom = await source.getClusterExpansionZoom(
                Number(feature.properties?.cluster_id),
              );
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
            onSelect?.(properties.id);
          });
          map.on('mouseenter', 'events', () => {
            if (map) map.getCanvas().style.cursor = 'pointer';
          });
          map.on('mouseleave', 'events', () => {
            if (map) map.getCanvas().style.cursor = '';
          });
        }
        if (located.length) {
          const bounds = new maplibregl.LngLatBounds();
          located.forEach((event) => bounds.extend([event.longitude!, event.latitude!]));
          map.fitBounds(bounds, { padding: 70, maxZoom: 16, duration: 0 });
        }
      });
    } catch {
      setError('Mapa indisponível: verifique o suporte a WebGL do navegador.');
    }
    return () => {
      mapRef.current = null;
      map?.remove();
    };
    // selectedId muda só o raio do círculo; recriar o mapa por isso seria desperdício.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    cartoFallback.attribution,
    cartoFallback.name,
    cartoFallback.styleUrl,
    events,
    primaryProvider.attribution,
    primaryProvider.name,
    style,
    initialCenter?.latitude,
    initialCenter?.longitude,
  ]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map?.getLayer?.('events')) return;
    map.setPaintProperty('events', 'circle-radius', [
      'case',
      ['==', ['get', 'id'], selectedId ?? ''],
      11,
      7,
    ]);
  }, [selectedId]);

  const located = events.filter((event) => event.latitude != null && event.longitude != null);
  return (
    <section className="panel map-panel">
      <div className="map-wrap">
        <div
          className="map"
          ref={container}
          role="img"
          aria-label={`Mapa com ${located.length} de ${events.length} ocorrências localizadas`}
        />
        {!located.length && !onPickLocation && (
          <p className="map-empty">Nenhum registro com localização disponível neste recorte.</p>
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
      {events.some((event) => event.report_status) && (
        <ul className="map-legend" aria-label="Legenda de relatos">
          {Object.entries(reportLabels).map(([status, label]) => (
            <li key={status}>{label}</li>
          ))}
        </ul>
      )}
      {events.some((event) => !event.report_status || event.severity != null) && (
        <ul className="map-legend" aria-label="Legenda de severidade">
          {LEGEND.map((level) => {
            const presentation = severityOf(level);
            return (
              <li key={level}>
                <i aria-hidden="true" style={{ background: SEVERITY_COLOR[level] }} />
                <span aria-hidden="true">{presentation.shape}</span> {presentation.label}
              </li>
            );
          })}
        </ul>
      )}
      <p className="map-caption">
        {located.length} de {events.length} registros têm localização disponível neste mapa.{' '}
        {style
          ? `${activeProviderName}: ${activeAttribution}. Coordenadas originais; o ponto ajustado à via permanece separado.`
          : 'Somente coordenadas reais são exibidas; nenhuma rua é simulada.'}
      </p>
      {error && (
        <p role="alert" className="notice">
          {error}
        </p>
      )}
    </section>
  );
}
