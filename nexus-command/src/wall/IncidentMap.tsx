import { useEffect, useRef, useState } from 'react';
import type { Map as MapLibreMap, Marker as MapLibreMarker } from 'maplibre-gl';

const AUBURN: [number, number] = [-85.4808, 32.6067];

function corridorAround(lng: number, lat: number) {
  const dx = 0.0014;
  const dy = 0.00045;
  return {
    type: 'Feature' as const,
    properties: {},
    geometry: {
      type: 'Polygon' as const,
      coordinates: [[
        [lng - dx, lat - dy],
        [lng + dx, lat - dy],
        [lng + dx, lat + dy],
        [lng - dx, lat + dy],
        [lng - dx, lat - dy],
      ]],
    },
  };
}

export function IncidentMap({
  center,
  markerLabel,
}: {
  center: [number, number] | null;
  markerLabel: string;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const markerRef = useRef<MapLibreMarker | null>(null);
  const [ready, setReady] = useState(false);
  const point = center ?? AUBURN;

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    let cancelled = false;
    let created: MapLibreMap | null = null;
    let observer: ResizeObserver | null = null;
    void import('maplibre-gl').then(({ default: maplibregl }) => {
      if (cancelled || !containerRef.current) return;
      created = new maplibregl.Map({
        container: containerRef.current,
        style: {
          version: 8,
          sources: {
            satellite: {
              type: 'raster',
              tiles: ['https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'],
              tileSize: 256,
              maxzoom: 19,
              attribution: 'Esri',
            },
          },
          layers: [{
            id: 'satellite',
            type: 'raster',
            source: 'satellite',
            paint: {
              'raster-brightness-min': 0,
              'raster-brightness-max': 0.4,
              'raster-saturation': -0.4,
            },
          }],
        },
        center: point,
        zoom: 15,
        minZoom: 13,
        maxZoom: 16,
        dragRotate: false,
        scrollZoom: false,
        doubleClickZoom: false,
        touchZoomRotate: false,
        touchPitch: false,
        keyboard: false,
        attributionControl: false,
        renderWorldCopies: false,
      });
      mapRef.current = created;
      created.once('load', () => { if (!cancelled) setReady(true); });
      observer = new ResizeObserver(() => created?.resize());
      observer.observe(containerRef.current);
    });
    return () => {
      cancelled = true;
      observer?.disconnect();
      markerRef.current?.remove();
      created?.remove();
      mapRef.current = null;
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map) return;
    const feature = corridorAround(point[0], point[1]);
    const collection = { type: 'FeatureCollection' as const, features: [feature] };
    const source = map.getSource('corridor') as { setData: (data: unknown) => void } | undefined;
    if (source) source.setData(collection);
    else {
      map.addSource('corridor', { type: 'geojson', data: collection });
      map.addLayer({
        id: 'corridor-fill',
        type: 'fill',
        source: 'corridor',
        paint: { 'fill-color': '#e5484d', 'fill-opacity': 0.35 },
      });
      map.addLayer({
        id: 'corridor-line',
        type: 'line',
        source: 'corridor',
        paint: { 'line-color': '#e5484d', 'line-width': 8 },
      });
    }
    const ring = feature.geometry.coordinates[0];
    const lngs = ring.map(pair => pair[0]);
    const lats = ring.map(pair => pair[1]);
    const box = map.getContainer().getBoundingClientRect();
    map.fitBounds(
      [[Math.min(...lngs), Math.min(...lats)], [Math.max(...lngs), Math.max(...lats)]],
      {
        padding: {
          top: box.height * 0.15,
          bottom: box.height * 0.15,
          left: box.width * 0.15,
          right: box.width * 0.15,
        },
        duration: 400,
      },
    );
  }, [point[0], point[1], ready]);

  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map) return;
    void import('maplibre-gl').then(({ default: maplibregl }) => {
      if (!mapRef.current) return;
      markerRef.current?.remove();
      const root = document.createElement('div');
      root.className = 'wall-marker-wrap';
      root.innerHTML = `<div class="wall-marker"></div><div class="wall-marker-label">${markerLabel}</div>`;
      markerRef.current = new maplibregl.Marker({ element: root, anchor: 'bottom' })
        .setLngLat(point)
        .addTo(mapRef.current);
    });
  }, [markerLabel, point[0], point[1], ready]);

  return (
    <section className="wall-map" aria-label="Affected area">
      <div ref={containerRef} className="map-canvas" />
      <div className="wall-map__chrome" aria-hidden="true">
        <span className="wall-map__north">N</span>
        <span className="wall-map__scale"><i /><span className="wall-figure">200</span> m</span>
        <span className="wall-map__attr">Basemap Esri World Imagery</span>
      </div>
    </section>
  );
}
