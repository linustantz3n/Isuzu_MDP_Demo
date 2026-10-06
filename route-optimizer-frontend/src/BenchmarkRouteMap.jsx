import React, { useState, useEffect, useCallback, useRef } from 'react';
import { GoogleMap, useJsApiLoader, DirectionsRenderer, Marker, InfoWindow } from '@react-google-maps/api';
import { VEHICLE_COLORS, MAP_STYLES } from './RouteMap';

const MAP_CONTAINER_STYLE = { width: '100%', height: '420px' };

export const truckColor = (idx) => VEHICLE_COLORS[idx % VEHICLE_COLORS.length];

// Road geometry for a stop sequence never changes, so keep it for the session:
// collapsing and re-expanding an algorithm (or two algorithms sharing a truck
// route) costs no extra Directions API calls.
const directionsCache = new Map();

function fetchDirections(route, locations) {
  const pts = route.map(i => ({ lat: locations[i].lat, lng: locations[i].lng }));
  const key = pts.map(p => `${p.lat},${p.lng}`).join(';');
  if (!directionsCache.has(key)) {
    const service = new window.google.maps.DirectionsService();
    const request = new Promise((resolve) => {
      service.route(
        {
          origin: pts[0],
          destination: pts[pts.length - 1],
          waypoints: pts.slice(1, -1).map(location => ({ location, stopover: true })),
          travelMode: window.google.maps.TravelMode.DRIVING,
          optimizeWaypoints: false, // keep the algorithm's order
        },
        (result, status) => {
          if (status === 'OK') return resolve(result);
          console.warn(`DirectionsService failed: ${status}`);
          directionsCache.delete(key); // allow a retry next time
          resolve(null);
        }
      );
    });
    directionsCache.set(key, request);
  }
  return directionsCache.get(key);
}

export default function BenchmarkRouteMap({ routes, locations }) {
  const { isLoaded, loadError } = useJsApiLoader({
    googleMapsApiKey: process.env.REACT_APP_GOOGLE_MAPS_API_KEY,
  });

  const mapRef = useRef(null);
  const [directions, setDirections] = useState([]);
  const [loading, setLoading] = useState(false);
  const [visible, setVisible] = useState(() => new Set(routes.map((_, i) => i)));
  const [selectedMarker, setSelectedMarker] = useState(null);

  const fitTo = useCallback((indices) => {
    if (!mapRef.current || !window.google) return;
    const bounds = new window.google.maps.LatLngBounds();
    indices.forEach(t => routes[t].forEach(i => bounds.extend({ lat: locations[i].lat, lng: locations[i].lng })));
    if (!bounds.isEmpty()) mapRef.current.fitBounds(bounds, 48);
  }, [routes, locations]);

  useEffect(() => {
    if (!isLoaded) return;
    let cancelled = false;
    setLoading(true);
    Promise.all(routes.map(r => fetchDirections(r, locations))).then(results => {
      if (cancelled) return;
      setDirections(results);
      setLoading(false);
    });
    return () => { cancelled = true; };
  }, [isLoaded, routes, locations]);

  const toggle = (idx) => {
    const next = new Set(visible);
    next.has(idx) ? next.delete(idx) : next.add(idx);
    setVisible(next);
  };
  const setAll = (on) => setVisible(on ? new Set(routes.map((_, i) => i)) : new Set());

  if (loadError) return (
    <div className="rounded-lg p-4 border border-red-500/30 text-red-400 text-sm">
      Google Maps failed to load: {loadError.message}
    </div>
  );

  // Marker for every stop on a visible truck, labelled with its position in that truck's route
  const stopMarkers = [];
  routes.forEach((route, t) => {
    if (!visible.has(t)) return;
    route.slice(1, -1).forEach((locIdx, pos) => stopMarkers.push({ locIdx, truck: t, pos: pos + 1 }));
  });
  const depot = locations[0];
  const selected = selectedMarker && locations[selectedMarker.locIdx];

  return (
    <div className="rounded-lg border border-slate-800 overflow-hidden">
      <div className="p-3 border-b border-slate-800 flex flex-wrap items-center gap-2 bg-slate-950/60">
        {routes.map((route, idx) => {
          const color = truckColor(idx);
          const active = visible.has(idx);
          return (
            <button
              key={idx}
              onClick={() => toggle(idx)}
              className={`flex items-center gap-2 px-2.5 py-1 rounded-lg border text-xs font-medium transition-all ${
                active ? '' : 'border-slate-700 text-slate-500 bg-transparent'
              }`}
              style={active ? { backgroundColor: color + '33', borderColor: color, color } : {}}
            >
              <svg width="16" height="8">
                <line x1="0" y1="4" x2="16" y2="4" stroke={active ? color : '#475569'} strokeWidth="3" />
              </svg>
              Truck {idx + 1}
            </button>
          );
        })}
        {routes.length > 1 && (
          <span className="flex gap-1 text-xs">
            <button onClick={() => setAll(true)} className="px-2 py-1 text-slate-400 hover:text-white">All</button>
            <button onClick={() => setAll(false)} className="px-2 py-1 text-slate-400 hover:text-white">None</button>
          </span>
        )}
        <button onClick={() => fitTo([...visible])} className="ml-auto px-2 py-1 text-xs text-slate-400 hover:text-white">
          Fit to visible
        </button>
        {loading && <span className="text-xs text-slate-500 animate-pulse">Loading routes...</span>}
      </div>

      {!isLoaded ? (
        <div style={MAP_CONTAINER_STYLE} className="flex items-center justify-center bg-slate-900 text-slate-500 text-sm">
          Loading Google Maps...
        </div>
      ) : (
        <GoogleMap
          mapContainerStyle={MAP_CONTAINER_STYLE}
          center={{ lat: depot.lat, lng: depot.lng }}
          zoom={10}
          onLoad={(map) => { mapRef.current = map; fitTo(routes.map((_, i) => i)); }}
          options={{ styles: MAP_STYLES, streetViewControl: false, mapTypeControl: false, gestureHandling: 'greedy' }}
        >
          {directions.map((result, idx) => result && visible.has(idx) && (
            <DirectionsRenderer
              key={idx}
              directions={result}
              options={{
                suppressMarkers: true,
                suppressFitBounds: true,
                polylineOptions: { strokeColor: truckColor(idx), strokeOpacity: 0.9, strokeWeight: 5, zIndex: 2 + idx },
              }}
            />
          ))}

          <Marker
            position={{ lat: depot.lat, lng: depot.lng }}
            icon={{
              path: window.google.maps.SymbolPath.CIRCLE, scale: 13,
              fillColor: '#3b82f6', fillOpacity: 1, strokeColor: '#ffffff', strokeWeight: 2,
            }}
            label={{ text: 'D', color: '#ffffff', fontSize: '11px', fontWeight: 'bold' }}
            zIndex={30}
            onClick={() => setSelectedMarker({ locIdx: 0 })}
          />
          {stopMarkers.map(({ locIdx, truck, pos }) => (
            <Marker
              key={`${truck}-${locIdx}`}
              position={{ lat: locations[locIdx].lat, lng: locations[locIdx].lng }}
              icon={{
                path: window.google.maps.SymbolPath.CIRCLE, scale: 9,
                fillColor: truckColor(truck), fillOpacity: 1, strokeColor: '#ffffff', strokeWeight: 2,
              }}
              label={{ text: String(pos), color: '#ffffff', fontSize: '11px', fontWeight: 'bold' }}
              zIndex={20}
              onClick={() => setSelectedMarker({ locIdx, truck, pos })}
            />
          ))}

          {selected && (
            <InfoWindow
              position={{ lat: selected.lat, lng: selected.lng }}
              onCloseClick={() => setSelectedMarker(null)}
            >
              <div style={{ color: '#0f172a', minWidth: '180px', padding: '2px' }}>
                <p style={{ fontWeight: 'bold', marginBottom: '4px' }}>
                  {selectedMarker.locIdx === 0
                    ? 'Depot'
                    : `Truck ${selectedMarker.truck + 1} · Stop ${selectedMarker.pos}`}
                </p>
                {selected.name && <p style={{ fontSize: '12px', marginBottom: '2px' }}>{selected.name}</p>}
                <p style={{ fontSize: '12px', color: '#334155' }}>{selected.formatted_address || selected.address}</p>
              </div>
            </InfoWindow>
          )}
        </GoogleMap>
      )}
    </div>
  );
}
