import React, { useState, useEffect, useCallback, useRef } from 'react';
import { GoogleMap, useJsApiLoader, DirectionsRenderer, Marker, InfoWindow } from '@react-google-maps/api';

const VEHICLE_COLORS = [
  '#a855f7', '#3b82f6', '#10b981', '#f59e0b',
  '#ef4444', '#06b6d4', '#8b5cf6', '#ec4899', '#84cc16', '#f97316'
];
const SINGLE_COLOR = '#facc15';

const MAP_CONTAINER_STYLE = { width: '100%', height: '500px' };

// Dark map theme to match the app
const MAP_STYLES = [
  { elementType: 'geometry', stylers: [{ color: '#1e293b' }] },
  { elementType: 'labels.text.fill', stylers: [{ color: '#94a3b8' }] },
  { elementType: 'labels.text.stroke', stylers: [{ color: '#1e293b' }] },
  { featureType: 'poi', stylers: [{ visibility: 'off' }] },
  { featureType: 'road', elementType: 'geometry', stylers: [{ color: '#334155' }] },
  { featureType: 'road', elementType: 'geometry.stroke', stylers: [{ color: '#0f172a' }] },
  { featureType: 'road', elementType: 'labels.text.fill', stylers: [{ color: '#64748b' }] },
  { featureType: 'road.highway', elementType: 'geometry', stylers: [{ color: '#475569' }] },
  { featureType: 'road.highway', elementType: 'geometry.stroke', stylers: [{ color: '#0f172a' }] },
  { featureType: 'transit', stylers: [{ visibility: 'off' }] },
  { featureType: 'water', elementType: 'geometry', stylers: [{ color: '#0f172a' }] },
  { featureType: 'water', elementType: 'labels.text.fill', stylers: [{ color: '#1e3a5f' }] },
  { featureType: 'administrative', elementType: 'geometry', stylers: [{ color: '#334155' }] },
  { featureType: 'landscape', elementType: 'geometry', stylers: [{ color: '#1e293b' }] },
];

export default function RouteMap({ results, locations }) {
  const { isLoaded, loadError } = useJsApiLoader({
    googleMapsApiKey: process.env.REACT_APP_GOOGLE_MAPS_API_KEY,
  });

  const mapRef = useRef(null);
  const [routeDirections, setRouteDirections] = useState([]);
  const [fetchingRoutes, setFetchingRoutes] = useState(false);
  const [selectedMarker, setSelectedMarker] = useState(null);
  const [visibleRoutes, setVisibleRoutes] = useState(new Set());

  const fitToRoutes = useCallback((visibleSet, directions) => {
    if (!mapRef.current || !window.google) return;
    const visible = directions.filter(r => visibleSet.has(r.label));
    if (visible.length === 0) return;
    const bounds = new window.google.maps.LatLngBounds();
    visible.forEach(({ result }) => {
      const path = result.routes?.[0]?.overview_path;
      if (path) path.forEach(pt => bounds.extend(pt));
    });
    if (!bounds.isEmpty()) mapRef.current.fitBounds(bounds, 48);
  }, []);

  const toggleRoute = (label) => {
    const next = new Set(visibleRoutes);
    next.has(label) ? next.delete(label) : next.add(label);
    setVisibleRoutes(next);
  };

  const onMapLoad = useCallback((map) => {
    mapRef.current = map;
    map.setZoom(10);
  }, []);

  useEffect(() => {
    if (!isLoaded || !results || !locations?.length) return;

    setFetchingRoutes(true);
    setRouteDirections([]);
    setSelectedMarker(null);

    // Build list of routes to fetch road directions for
    const routesToFetch = [];

    // Held-Karp single vehicle — ordered_locations = [depot, stop1, stop2, ...]
    if (results.optimal?.ordered_locations?.length > 1) {
      const locs = results.optimal.ordered_locations;
      routesToFetch.push({
        origin: { lat: locs[0].lat, lng: locs[0].lng },
        destination: { lat: locs[0].lat, lng: locs[0].lng }, // closed loop back to depot
        waypoints: locs.slice(1).map(l => ({ location: { lat: l.lat, lng: l.lng }, stopover: true })),
        color: SINGLE_COLOR,
        label: 'Held-Karp',
        strokeWeight: 3,
        strokeOpacity: 0.75,
        zIndex: 1,
      });
    }

    // Fleet vehicles — ordered_locations = [depot, stop1, ..., depot] (depot at both ends)
    if (results.ortools_fleet?.routes) {
      results.ortools_fleet.routes.forEach((route, idx) => {
        const locs = route.ordered_locations;
        if (locs.length < 3) return;
        // Detect if depot is repeated at end (fleet format)
        const closedFormat = locs[0].lat === locs[locs.length - 1].lat
          && locs[0].lng === locs[locs.length - 1].lng;
        const waypoints = closedFormat
          ? locs.slice(1, -1).map(l => ({ location: { lat: l.lat, lng: l.lng }, stopover: true }))
          : locs.slice(1).map(l => ({ location: { lat: l.lat, lng: l.lng }, stopover: true }));

        routesToFetch.push({
          origin: { lat: locs[0].lat, lng: locs[0].lng },
          destination: { lat: locs[0].lat, lng: locs[0].lng },
          waypoints,
          color: VEHICLE_COLORS[idx % VEHICLE_COLORS.length],
          label: `Vehicle ${route.vehicle_id}`,
          strokeWeight: 5,
          strokeOpacity: 0.9,
          zIndex: 2 + idx,
        });
      });
    }

    const service = new window.google.maps.DirectionsService();

    const promises = routesToFetch.map(({ origin, destination, waypoints, color, label, strokeWeight, strokeOpacity, zIndex }) =>
      new Promise((resolve) => {
        service.route(
          {
            origin,
            destination,
            waypoints,
            travelMode: window.google.maps.TravelMode.DRIVING,
            optimizeWaypoints: false, // Order already optimized by our solver
          },
          (result, status) => {
            if (status === 'OK') {
              resolve({ result, color, label, strokeWeight, strokeOpacity, zIndex });
            } else {
              console.warn(`DirectionsService failed for ${label}: ${status}`);
              resolve(null);
            }
          }
        );
      })
    );

    Promise.all(promises).then((resolved) => {
      const valid = resolved.filter(Boolean);
      const allVisible = new Set(valid.map(r => r.label));
      setRouteDirections(valid);
      setVisibleRoutes(allVisible);
      setFetchingRoutes(false);
      // Delay fit until after DirectionsRenderers have mounted (avoids race with suppressFitBounds)
      setTimeout(() => fitToRoutes(allVisible, valid), 100);
    });
  }, [isLoaded, results, locations]);

  if (loadError) return (
    <div className="bg-slate-800 rounded-xl p-6 border border-red-500/30 text-red-400 text-sm">
      Google Maps failed to load: {loadError.message}
    </div>
  );

  if (!results || !locations?.length) return null;

  const fleetRoutes = results.ortools_fleet?.routes || [];
  const center = { lat: locations[0].lat, lng: locations[0].lng };

  return (
    <div className="bg-slate-800 rounded-xl border border-slate-700 overflow-hidden">
      {/* Legend / toggle header */}
      <div className="p-4 border-b border-slate-700 flex flex-wrap items-center gap-2">
        <h2 className="text-sm font-semibold text-white mr-2">Route Map</h2>
        {/* Held-Karp toggle */}
        {routeDirections.some(r => r.label === 'Held-Karp') && (
          <button
            onClick={() => toggleRoute('Held-Karp')}
            className={`flex items-center gap-2 px-3 py-1.5 rounded-lg border text-xs font-medium transition-all ${
              visibleRoutes.has('Held-Karp')
                ? 'border-transparent text-slate-900'
                : 'border-slate-700 text-slate-500 bg-transparent'
            }`}
            style={visibleRoutes.has('Held-Karp') ? { backgroundColor: SINGLE_COLOR } : {}}
          >
            <svg width="20" height="8">
              <line x1="0" y1="4" x2="20" y2="4" stroke={visibleRoutes.has('Held-Karp') ? '#0f172a' : SINGLE_COLOR} strokeWidth="2.5" />
            </svg>
            Held-Karp
          </button>
        )}
        {/* Fleet vehicle toggles */}
        {fleetRoutes.map((route, idx) => {
          const label = `Vehicle ${route.vehicle_id}`;
          const color = VEHICLE_COLORS[idx % VEHICLE_COLORS.length];
          const active = visibleRoutes.has(label);
          return (
            <button
              key={route.vehicle_id}
              onClick={() => toggleRoute(label)}
              className={`flex items-center gap-2 px-3 py-1.5 rounded-lg border text-xs font-medium transition-all ${
                active ? 'border-transparent text-white' : 'border-slate-700 text-slate-500 bg-transparent'
              }`}
              style={active ? { backgroundColor: color + '33', borderColor: color, color } : {}}
            >
              <svg width="20" height="8">
                <line x1="0" y1="4" x2="20" y2="4" stroke={active ? color : '#475569'} strokeWidth="3" />
              </svg>
              {label}
            </button>
          );
        })}
        {fetchingRoutes && (
          <span className="ml-auto text-xs text-slate-500 animate-pulse">Loading routes...</span>
        )}
      </div>

      {!isLoaded ? (
        <div style={MAP_CONTAINER_STYLE} className="flex items-center justify-center bg-slate-900 text-slate-500 text-sm">
          Loading Google Maps...
        </div>
      ) : (
        <GoogleMap
          mapContainerStyle={MAP_CONTAINER_STYLE}
          center={center}
          onLoad={onMapLoad}
          options={{ styles: MAP_STYLES, streetViewControl: false, mapTypeControl: false, gestureHandling: 'greedy' }}
        >
          {/* Road route polylines */}
          {routeDirections.filter(r => visibleRoutes.has(r.label)).map(({ result, color, label, strokeWeight, strokeOpacity, zIndex }) => (
            <DirectionsRenderer
              key={label}
              directions={result}
              options={{
                suppressMarkers: true,
                suppressFitBounds: true,
                polylineOptions: { strokeColor: color, strokeOpacity, strokeWeight, zIndex },
              }}
            />
          ))}

          {/* Stop markers */}
          {locations.map((loc, idx) => (
            <Marker
              key={idx}
              position={{ lat: loc.lat, lng: loc.lng }}
              icon={{
                path: window.google.maps.SymbolPath.CIRCLE,
                scale: idx === 0 ? 13 : 9,
                fillColor: idx === 0 ? '#3b82f6' : '#475569',
                fillOpacity: 1,
                strokeColor: '#ffffff',
                strokeWeight: 2,
              }}
              label={{
                text: idx === 0 ? 'D' : String(idx),
                color: '#ffffff',
                fontSize: '11px',
                fontWeight: 'bold',
              }}
              zIndex={20}
              onClick={() => setSelectedMarker(idx)}
            />
          ))}

          {/* Info window on click */}
          {selectedMarker !== null && (
            <InfoWindow
              position={{ lat: locations[selectedMarker].lat, lng: locations[selectedMarker].lng }}
              onCloseClick={() => setSelectedMarker(null)}
            >
              <div style={{ color: '#0f172a', minWidth: '180px', padding: '2px' }}>
                <p style={{ fontWeight: 'bold', marginBottom: '4px' }}>
                  {selectedMarker === 0 ? 'Depot' : `Stop ${selectedMarker}`}
                </p>
                <p style={{ fontSize: '12px', color: '#334155' }}>
                  {locations[selectedMarker].formatted_address || locations[selectedMarker].address}
                </p>
              </div>
            </InfoWindow>
          )}
        </GoogleMap>
      )}
    </div>
  );
}
