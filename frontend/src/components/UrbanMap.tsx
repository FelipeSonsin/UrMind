import { useEffect, useRef, useState } from 'react';
import * as maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import { labelFor, severityOf } from '../domain/public';

/** Mínimo que o mapa precisa. `UrbanEvent` e o resumo público satisfazem isto. */
export interface MapMarker {
  id: string;
  urmind_class: string;
  latitude?: number | null;
  longitude?: number | null;
  severity?: string | null;
  road_name?: string | null;
}

const SEVERITY_COLOR: Record<string, string> = {
  critical: '#7f1d1d',
  high: '#b45309',
  medium: '#a16207',
  low: '#123f36',
  unknown: '#66766f',
};
const LEGEND = ['critical', 'high', 'medium', 'low', 'unknown'];

export default function UrbanMap({
  events,
  selectedId,
  onSelect,
}: {
  events: MapMarker[];
  selectedId?: string | null;
  onSelect?: (id: string) => void;
}) {
  const container = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map>(null);
  const [error, setError] = useState('');
  const style = import.meta.env.VITE_MAP_STYLE_URL;

  useEffect(() => {
    if (!container.current) return;
    let map: maplibregl.Map | undefined;
    try {
      const located = events.filter((e) => e.latitude != null && e.longitude != null);
      map = new maplibregl.Map({
        container: container.current,
        style: style || {
          version: 8,
          sources: {},
          layers: [
            { id: 'background', type: 'background', paint: { 'background-color': '#e5eae2' } },
          ],
        },
        center: [0, 0],
        zoom: 1,
      });
      mapRef.current = map;
      map.addControl(new maplibregl.NavigationControl(), 'top-right');
      map.on('error', () =>
        setError(
          'Não foi possível carregar parte do mapa. Os registros continuam disponíveis na lista.',
        ),
      );
      map.on('load', () => {
        if (!map) return;
        map.addSource('events', {
          type: 'geojson',
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
                label: labelFor(event.urmind_class),
                severity: severityOf(event.severity).label,
                color: SEVERITY_COLOR[severityOf(event.severity).level],
                road: event.road_name ?? '',
              },
            })),
          },
        });
        map.addLayer({
          id: 'events',
          type: 'circle',
          source: 'events',
          paint: {
            'circle-radius': ['case', ['==', ['get', 'id'], selectedId ?? ''], 11, 7],
            'circle-color': ['get', 'color'],
            'circle-stroke-color': '#f5f6f2',
            'circle-stroke-width': 3,
          },
        });
        map.on('click', 'events', (event) => {
          const feature = event.features?.[0];
          if (feature?.geometry.type !== 'Point' || !map) return;
          const properties = feature.properties as Record<string, string>;
          new maplibregl.Popup()
            .setLngLat(feature.geometry.coordinates as [number, number])
            .setText(
              `${properties.label} · severidade ${properties.severity}${properties.road ? ` · ${properties.road}` : ''}`,
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
  }, [events, style]);

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
        {!located.length && (
          <p className="map-empty">
            Nenhuma ocorrência com coordenada publicada nesta área ainda. A malha viária do piloto
            já está carregada e o mapa passa a marcar os pontos assim que houver evidência.
          </p>
        )}
      </div>
      {/* O canvas não é legível por leitor de tela: a mesma informação em texto. */}
      <ul className="sr-only">
        {located.map((event) => (
          <li key={event.id}>
            {labelFor(event.urmind_class)} · severidade {severityOf(event.severity).label} ·{' '}
            {event.road_name ?? 'via não associada'} · {event.latitude}, {event.longitude}
          </li>
        ))}
      </ul>
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
      <p className="map-caption">
        {located.length} de {events.length} ocorrências têm coordenada publicada.{' '}
        {style
          ? 'Coordenadas originais. O ponto ajustado à via permanece separado.'
          : 'Base cartográfica não configurada. Somente coordenadas reais são exibidas; nenhuma rua é simulada.'}
      </p>
      {error && (
        <p role="alert" className="notice">
          {error}
        </p>
      )}
    </section>
  );
}
