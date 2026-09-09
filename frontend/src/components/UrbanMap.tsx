import { useEffect, useRef, useState } from 'react';
import * as maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import type { UrbanEvent } from '../domain/contracts';
import { classes } from '../domain/contracts';

export default function UrbanMap({ events }: { events: UrbanEvent[] }) {
  const container = useRef<HTMLDivElement>(null);
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
              properties: { label: classes[event.urmind_class], id: event.id },
            })),
          },
        });
        map.addLayer({
          id: 'events',
          type: 'circle',
          source: 'events',
          paint: {
            'circle-radius': 7,
            'circle-color': '#123f36',
            'circle-stroke-color': '#c9f17d',
            'circle-stroke-width': 3,
          },
        });
        map.on('click', 'events', (event) => {
          const feature = event.features?.[0];
          if (feature?.geometry.type === 'Point' && map)
            new maplibregl.Popup()
              .setLngLat(feature.geometry.coordinates as [number, number])
              .setText(String(feature.properties?.label || 'Ocorrência'))
              .addTo(map);
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
    return () => map?.remove();
  }, [events, style]);
  return (
    <section className="panel map-panel">
      <div
        className="map"
        ref={container}
        aria-label="Mapa de coordenadas originais das ocorrências"
      />
      <p className="map-caption">
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
