import React, { useState, useEffect, useCallback, useRef } from 'react';
import { GoogleMap, useJsApiLoader, DirectionsRenderer, Marker, InfoWindow } from '@react-google-maps/api';
import { depletionText, formatDuration } from './planDiff';

export const VEHICLE_COLORS = [
  '#a855f7', '#3b82f6', '#10b981', '#f59e0b',
  '#ef4444', '#06b6d4', '#8b5cf6', '#ec4899', '#84cc16', '#f97316'
];
const SINGLE_COLOR = '#facc15';

const MAP_CONTAINER_STYLE = { width: '100%', height: '500px' };

// Dark map theme to match the app
export const MAP_STYLES = [
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

// Each stop's place in the plan: the truck that delivers it and its position
// on that truck's route. The map numbers stops by this delivery order, not by
// their position on the form. Keyed by location index.
export function stopSequence(results) {
  const seq = {};
  const fleet = results?.ortools_fleet?.routes;
  if (fleet?.length) {
    fleet.forEach((route, idx) => {
      // Deliveries only: not the depot, nor a cargo pickup at a breakdown site.
      const delivered = route.order.filter((loc, j) =>
        loc !== 0 && ['stop', 'done', 'rescue'].includes(route.stop_kinds?.[j] ?? 'stop'));
      delivered.forEach((loc, k) => {
        if (!(loc in seq)) {
          seq[loc] = { n: k + 1, of: delivered.length, color: VEHICLE_COLORS[idx % VEHICLE_COLORS.length],
                       truck: route.vehicle_name || `Vehicle ${route.vehicle_id}` };
        }
      });
    });
  } else if (results?.optimal?.order) {
    const delivered = results.optimal.order.filter(loc => loc !== 0);
    delivered.forEach((loc, k) => { seq[loc] = { n: k + 1, of: delivered.length, color: SINGLE_COLOR, truck: null }; });
  }
  return seq;
}

// Metres between two google.maps.LatLng points (haversine).
function metres(a, b) {
  const R = 6371000, rad = Math.PI / 180;
  const dLat = (b.lat() - a.lat()) * rad, dLng = (b.lng() - a.lng()) * rad;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(a.lat() * rad) * Math.cos(b.lat() * rad) * Math.sin(dLng / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(h));
}

// The point `fraction` of the way along leg `legIndex` of a directions result,
// following the road as drawn. The legs match the route's legs one to one:
// one per pair of consecutive locations.
function pointAlongLeg(result, legIndex, fraction) {
  const leg = result.routes?.[0]?.legs?.[legIndex];
  const path = (leg?.steps || []).flatMap(s => s.path || []);
  if (path.length === 0) return null;
  const segs = path.slice(1).map((p, i) => metres(path[i], p));
  let left = fraction * segs.reduce((a, b) => a + b, 0);
  for (let i = 0; i < segs.length; i++) {
    if (left <= segs[i]) {
      const w = segs[i] > 0 ? left / segs[i] : 0;
      const a = path[i], b = path[i + 1];
      return { lat: a.lat() + w * (b.lat() - a.lat()), lng: a.lng() + w * (b.lng() - a.lng()) };
    }
    left -= segs[i];
  }
  const end = path[path.length - 1];
  return { lat: end.lat(), lng: end.lng() };
}

export default function RouteMap({ results, locations }) {
  const { isLoaded, loadError } = useJsApiLoader({
    googleMapsApiKey: process.env.REACT_APP_GOOGLE_MAPS_API_KEY,
  });

  const mapRef = useRef(null);
  const [routeDirections, setRouteDirections] = useState([]);
  const [fetchingRoutes, setFetchingRoutes] = useState(false);
  const [selectedMarker, setSelectedMarker] = useState(null);
  const [selectedDepletion, setSelectedDepletion] = useState(null);
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
    setSelectedDepletion(null);

    // Build list of routes to fetch road directions for
    const routesToFetch = [];
    const hasFleet = results.ortools_fleet?.routes?.length > 0;

    // Held-Karp single vehicle — ordered_locations = [depot, stop1, stop2, ...]
    if (results.optimal?.ordered_locations?.length > 1) {
      const locs = results.optimal.ordered_locations;
      routesToFetch.push({
        origin: { lat: locs[0].lat, lng: locs[0].lng },
        destination: { lat: locs[0].lat, lng: locs[0].lng }, // closed loop back to depot
        waypoints: locs.slice(1).map(l => ({ location: { lat: l.lat, lng: l.lng }, stopover: true })),
        color: SINGLE_COLOR,
        label: 'Held-Karp',
        name: 'The truck',
        // With a fleet the single-truck route is only a comparison, not a truck
        // in the plan, so where it would run out is not marked.
        depletion: hasFleet ? null : results.optimal.battery_depletion,
        strokeWeight: 3,
        strokeOpacity: 0.75,
        zIndex: 1,
      });
    }

    // Fleet vehicles — ordered_locations = [depot, stop1, ..., depot] (depot at both ends)
    if (results.ortools_fleet?.routes) {
      results.ortools_fleet.routes.forEach((route, idx) => {
        const locs = route.ordered_locations;
        if (locs.length < 2) return;
        // Depot at both ends is a full day; a truck that broke down ends where
        // it stopped, so its line ends there instead of returning to the depot.
        const closedFormat = locs[0].lat === locs[locs.length - 1].lat
          && locs[0].lng === locs[locs.length - 1].lng;
        const end = locs[locs.length - 1];
        const waypoints = locs.slice(1, -1).map(l => ({ location: { lat: l.lat, lng: l.lng }, stopover: true }));

        routesToFetch.push({
          origin: { lat: locs[0].lat, lng: locs[0].lng },
          destination: closedFormat ? { lat: locs[0].lat, lng: locs[0].lng } : { lat: end.lat, lng: end.lng },
          waypoints,
          color: VEHICLE_COLORS[idx % VEHICLE_COLORS.length],
          label: `Vehicle ${route.vehicle_id}`,
          name: route.vehicle_name || `Vehicle ${route.vehicle_id}`,
          depletion: route.battery_depletion,
          strokeWeight: 5,
          strokeOpacity: 0.9,
          zIndex: 2 + idx,
        });
      });
    }

    const service = new window.google.maps.DirectionsService();

    const promises = routesToFetch.map(({ origin, destination, waypoints, color, label, name, depletion, strokeWeight, strokeOpacity, zIndex }) =>
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
              // Where the battery runs out, placed on the road as drawn.
              const position = depletion ? pointAlongLeg(result, depletion.leg_index, depletion.fraction) : null;
              resolve({ result, color, label, name, depletion: position ? { ...depletion, position } : null,
                        strokeWeight, strokeOpacity, zIndex });
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
  }, [isLoaded, results, locations]); // eslint-disable-line react-hooks/exhaustive-deps

  if (loadError) return (
    <div className="bg-slate-800 rounded-xl p-6 border border-red-500/30 text-red-400 text-sm">
      Google Maps failed to load: {loadError.message}
    </div>
  );

  if (!results || !locations?.length) return null;

  const fleetRoutes = results.ortools_fleet?.routes || [];
  const center = { lat: locations[0].lat, lng: locations[0].lng };
  const seq = stopSequence(results);
  const priority = new Set(results.priority_stops || []);
  // Trucks whose battery runs out, on routes currently shown.
  const depletions = routeDirections.filter(r => r.depletion && visibleRoutes.has(r.label));
  const openDepletion = depletions.find(r => r.label === selectedDepletion);

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
        <span className="text-[11px] text-slate-500">
          {fleetRoutes.length ? 'numbers: delivery order on each truck' : 'numbers: delivery order'}
        </span>
        {depletions.length > 0 && (
          <span className="flex items-center gap-1.5 text-xs text-red-300" title="Click a marker on the map for details">
            <span className="w-3.5 h-3.5 rounded-full bg-red-600 border-2 border-white text-[7px] font-bold text-white flex items-center justify-center">0</span>
            battery runs out
          </span>
        )}
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

          {/* Stop markers: delivery order on the truck that serves the stop, in
              that truck's colour; grey "–" for a stop no truck serves */}
          {locations.map((loc, idx) => {
            const s = idx === 0 ? null : seq[idx];
            const fill = idx === 0 ? '#e2e8f0' : s ? s.color : '#475569';
            const darkText = idx === 0 || fill === SINGLE_COLOR;
            return (
              <Marker
                key={idx}
                position={{ lat: loc.lat, lng: loc.lng }}
                icon={{
                  path: window.google.maps.SymbolPath.CIRCLE,
                  scale: idx === 0 ? 13 : 10,
                  fillColor: fill,
                  fillOpacity: 1,
                  strokeColor: idx === 0 ? '#0f172a' : '#ffffff',
                  strokeWeight: 2,
                }}
                label={{
                  text: idx === 0 ? 'D' : s ? String(s.n) : '–',
                  color: darkText ? '#0f172a' : '#ffffff',
                  fontSize: '11px',
                  fontWeight: 'bold',
                }}
                title={idx === 0 ? 'Depot' : s ? `${s.truck ? `${s.truck}, ` : ''}stop ${s.n} of ${s.of}` : 'Not served'}
                zIndex={20}
                onClick={() => { setSelectedDepletion(null); setSelectedMarker(idx); }}
              />
            );
          })}

          {/* Where a truck's battery runs out: red, ringed in the route's colour */}
          {depletions.map(({ label, color, depletion }) => (
            <Marker
              key={`depleted-${label}`}
              position={depletion.position}
              icon={{
                path: window.google.maps.SymbolPath.CIRCLE,
                scale: 12,
                fillColor: '#dc2626',
                fillOpacity: 1,
                strokeColor: color,
                strokeWeight: 4,
              }}
              label={{ text: '0%', color: '#ffffff', fontSize: '9px', fontWeight: 'bold' }}
              title={`${label}: battery runs out here`}
              zIndex={30}
              onClick={() => { setSelectedMarker(null); setSelectedDepletion(label); }}
            />
          ))}

          {/* Info window on click */}
          {selectedMarker !== null && (() => {
            const s = seq[selectedMarker];
            const title = selectedMarker === 0 ? 'Depot'
              : s ? `${s.truck ? `${s.truck} · ` : ''}stop ${s.n} of ${s.of}`
              : 'Not served in this plan';
            return (
              <InfoWindow
                position={{ lat: locations[selectedMarker].lat, lng: locations[selectedMarker].lng }}
                onCloseClick={() => setSelectedMarker(null)}
              >
                <div style={{ color: '#0f172a', minWidth: '180px', padding: '2px', textAlign: 'left' }}>
                  <p style={{ fontWeight: 'bold', marginBottom: '4px' }}>
                    {title.charAt(0).toUpperCase() + title.slice(1)}
                  </p>
                  {priority.has(selectedMarker) && (
                    <p style={{ fontSize: '12px', color: '#b45309', marginBottom: '2px' }}>★ Priority stop: served first on its truck</p>
                  )}
                  <p style={{ fontSize: '12px', color: '#334155' }}>
                    {locations[selectedMarker].formatted_address || locations[selectedMarker].address}
                  </p>
                  {selectedMarker > 0 && (
                    <p style={{ fontSize: '11px', color: '#64748b', marginTop: '4px' }}>Stop {selectedMarker} on the form</p>
                  )}
                </div>
              </InfoWindow>
            );
          })()}

          {openDepletion && (() => {
            const d = openDepletion.depletion;
            const text = depletionText(d);
            return (
              <InfoWindow position={d.position} onCloseClick={() => setSelectedDepletion(null)}>
                <div style={{ color: '#0f172a', minWidth: '200px', maxWidth: '260px', padding: '2px', textAlign: 'left' }}>
                  <p style={{ fontWeight: 'bold', marginBottom: '4px', color: '#b91c1c' }}>
                    {openDepletion.name}: battery runs out here
                  </p>
                  <p style={{ fontSize: '12px', color: '#334155' }}>
                    {text.charAt(0).toUpperCase() + text.slice(1)}.
                  </p>
                  <p style={{ fontSize: '12px', color: '#334155', marginTop: '2px' }}>
                    {d.at_s != null && <>About {formatDuration(d.at_s)} after dispatch. </>}
                    The route needs {d.route_energy_kwh.toFixed(1)} of {d.capacity_kwh} kWh.
                  </p>
                  <p style={{ fontSize: '11px', color: '#64748b', marginTop: '4px' }}>
                    Approximate: the energy model gives one total per leg, so it is spread evenly along the leg.
                  </p>
                </div>
              </InfoWindow>
            );
          })()}
        </GoogleMap>
      )}
    </div>
  );
}
