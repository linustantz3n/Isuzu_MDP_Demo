import React, { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { GoogleMap, useJsApiLoader, Marker, MarkerF, PolylineF, InfoWindow } from '@react-google-maps/api';
import DriveSimulation from './DriveSimulation';
import { fetchRoadRoute, pointAlongLeg } from './roadRoutes';
import { depletionText, formatDuration } from './planDiff';

export const VEHICLE_COLORS = [
  '#a855f7', '#3b82f6', '#10b981', '#f59e0b',
  '#ef4444', '#06b6d4', '#8b5cf6', '#ec4899', '#84cc16', '#f97316'
];
const SINGLE_COLOR = '#facc15';
const BLOCKED_COLOR = '#ef4444';
const REROUTE_COLOR = '#22c55e';
const CRASH_COLOR = '#fb923c';
// Warning triangle for crash hotspots, so they don't look like the round stop markers
const HOTSPOT_PATH = 'M 0,-1.15 L 1.1,0.75 L -1.1,0.75 Z';
const CHARGE_COLOR = '#22d3ee';
const CHARGER_FILL = '#facc15';
const BOLT_PATH = 'M 1,-10 L -6,2 L -1,2 L -2,10 L 6,-2 L 1,-2 Z';

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

// replan: /api/replan_incidents response; activeAccidents: ids switched on;
// activeLegs: the legs rerouted for that combination of accidents
// chargingPlans: /api/plan_charging plans (charging stop, detour and reserve point per vehicle)
// drive: {runId, speed} while the drive simulation runs (measured traffic), else null. It drives
//   the fleet's trucks when there is a fleet, otherwise the Held-Karp route.
export default function RouteMap({ results, locations, replan, activeAccidents = [], activeLegs = [], chargingPlans = [], drive = null, onDriveEnd }) {
  const { isLoaded, loadError } = useJsApiLoader({
    googleMapsApiKey: process.env.REACT_APP_GOOGLE_MAPS_API_KEY,
  });

  const mapRef = useRef(null);
  const [routeDirections, setRouteDirections] = useState([]);
  const [fetchingRoutes, setFetchingRoutes] = useState(false);
  const [routeErrors, setRouteErrors] = useState([]);
  const [retry, setRetry] = useState(0);
  const [selectedMarker, setSelectedMarker] = useState(null);
  const [selectedDepletion, setSelectedDepletion] = useState(null);
  const [visibleRoutes, setVisibleRoutes] = useState(new Set());
  const [overlay, setOverlay] = useState(null);  // DOM node over the map for the drive simulation

  // Drive simulation trucks: Google's road path for each leg of the route (or, where an
  // accident is on, the path it was found on), timed by the measured traffic. One truck for
  // the Held-Karp route; with a fleet, every truck's route. Accidents use the leg numbering
  // of /api/replan_incidents: with a fleet, all trucks' legs in route order.
  const driveTrucks = useMemo(() => {
    const roadLegs = (directions, route, offset) => {
      const actual = route?.traffic?.actual_leg_times_s;
      const dirLegs = directions?.geometry.legs;
      if (!actual || !dirLegs || dirLegs.length !== actual.length) return null;
      const stops = route.ordered_locations;
      const atDepot = (k) => k + 1 >= stops.length || (k + 1 === stops.length - 1 && route.order?.[k + 1] === 0);
      return dirLegs.map((leg, k) => ({
        path: activeLegs.find(l => l.leg_index === offset + k)?.original.path
          || leg.path,
        durationS: actual[k],
        plannedS: route.legs[k]?.time_s ?? actual[k],
        to: { lat: stops[(k + 1) % stops.length].lat, lng: stops[(k + 1) % stops.length].lng },
        toLabel: atDepot(k) ? 'Depot' : `Stop ${k + 1}`,
      }));
    };
    const accidentsOn = (offset, n) => (replan ? replan.accidents : [])
      .filter(a => activeAccidents.includes(a.id) && a.leg_index >= offset && a.leg_index < offset + n)
      .map(a => ({ ...a, leg_index: a.leg_index - offset }));
    const unloadOf = (route, stops) => (route.unload_time_s || 0) / Math.max(stops, 1);

    const fleet = results?.ortools_fleet?.routes;
    if (fleet?.length) {
      const trucks = [];
      let offset = 0;
      for (const [idx, route] of fleet.entries()) {
        const legs = roadLegs(routeDirections.find(r => r.label === `Vehicle ${route.vehicle_id}`), route, offset);
        if (!legs) return null;  // every truck needs its road and measured times
        trucks.push({
          id: route.vehicle_id,
          label: route.vehicle_name || `Vehicle ${route.vehicle_id}`,
          color: VEHICLE_COLORS[idx % VEHICLE_COLORS.length],
          legs,
          accidents: accidentsOn(offset, route.legs.length),
          unloadS: unloadOf(route, route.num_delivery_stops),
        });
        offset += route.legs.length;
      }
      return trucks;
    }
    const hk = results?.optimal;
    const legs = roadLegs(routeDirections.find(r => r.label === 'Held-Karp'), hk, 0);
    if (!legs) return null;
    return [{ id: 'hk', label: 'The truck', color: SINGLE_COLOR, legs,
              accidents: accidentsOn(0, legs.length), unloadS: unloadOf(hk, hk.ordered_locations.length - 1) }];
  }, [results, routeDirections, activeLegs, replan, activeAccidents]);
  const driveDeparture = results?.ortools_fleet?.routes?.[0]?.traffic?.departure_time
    || results?.optimal?.traffic?.departure_time;

  const fitToRoutes = useCallback((visibleSet, directions) => {
    if (!mapRef.current || !window.google) return;
    const visible = directions.filter(r => visibleSet.has(r.label));
    if (visible.length === 0) return;
    const bounds = new window.google.maps.LatLngBounds();
    visible.forEach(({ geometry }) => {
      const path = geometry.path;
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

    let cancelled = false;
    let fitTimer;
    setFetchingRoutes(true);
    setRouteErrors([]);
    setRouteDirections([]);
    setSelectedMarker(null);
    setSelectedDepletion(null);

    const routesToFetch = [];
    const hasFleet = results.ortools_fleet?.routes?.length > 0;
    if (results.optimal?.ordered_locations?.length > 1) {
      const locs = results.optimal.ordered_locations;
      const closed = locs[0].lat === locs[locs.length - 1].lat && locs[0].lng === locs[locs.length - 1].lng;
      routesToFetch.push({
        points: closed ? locs : [...locs, locs[0]], color: SINGLE_COLOR,
        label: 'Held-Karp', name: 'The truck',
        depletion: hasFleet ? null : results.optimal.battery_depletion,
        strokeWeight: 3, strokeOpacity: 0.75, zIndex: 1,
      });
    }
    (results.ortools_fleet?.routes || []).forEach((route, idx) => {
      if (route.ordered_locations.length < 2) return;
      // A broken truck's open route ends at its breakdown location.
      routesToFetch.push({
        points: route.ordered_locations, color: VEHICLE_COLORS[idx % VEHICLE_COLORS.length],
        label: `Vehicle ${route.vehicle_id}`, name: route.vehicle_name || `Vehicle ${route.vehicle_id}`,
        depletion: route.battery_depletion, strokeWeight: 5, strokeOpacity: 0.9, zIndex: 2 + idx,
      });
    });
    Promise.all(routesToFetch.map(({ points, depletion, ...style }) =>
      fetchRoadRoute(points).then(geometry => {
        const position = depletion ? pointAlongLeg(geometry, depletion.leg_index, depletion.fraction) : null;
        return { ...style, geometry, depletion: position ? { ...depletion, position } : null };
      }).catch(error => ({ error: `${style.name}: ${error.message}` }))
    )).then(resolved => {
      if (cancelled) return;
      const valid = resolved.filter(r => !r.error);
      const allVisible = new Set(valid.map(r => r.label));
      setRouteErrors(resolved.filter(r => r.error).map(r => r.error));
      setRouteDirections(valid);
      setVisibleRoutes(allVisible);
      setFetchingRoutes(false);
      fitTimer = setTimeout(() => fitToRoutes(allVisible, valid), 100);
    });
    return () => { cancelled = true; clearTimeout(fitTimer); };
  }, [isLoaded, results, locations, retry, fitToRoutes]);

  // Zoom to the hotspots and the legs they can block (once, not on every toggle)
  useEffect(() => {
    if (!isLoaded || !replan || !mapRef.current) return;
    const bounds = new window.google.maps.LatLngBounds();
    Object.values(replan.scenarios).flat().forEach(leg => leg.original.path.forEach(pt => bounds.extend(pt)));
    replan.accidents.forEach(a => bounds.extend(a));
    mapRef.current.fitBounds(bounds, 48);
  }, [isLoaded, replan]);

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
        {/* Replanning legend */}
        {replan && (
          <>
            <span className="flex items-center gap-2 px-3 py-1.5 text-xs text-red-400">
              <svg width="20" height="8">
                <line x1="0" y1="4" x2="20" y2="4" stroke={BLOCKED_COLOR} strokeWidth="3" strokeDasharray="4 3" />
              </svg>
              Blocked
            </span>
            <span className="flex items-center gap-2 px-3 py-1.5 text-xs text-green-400">
              <svg width="20" height="8">
                <line x1="0" y1="4" x2="20" y2="4" stroke={REROUTE_COLOR} strokeWidth="3" />
              </svg>
              Reroute
            </span>
            <span className="flex items-center gap-2 px-3 py-1.5 text-xs text-red-400">
              <svg width="14" height="12">
                <path d="M 7,1 L 13,11 L 1,11 Z" fill="none" stroke={BLOCKED_COLOR} strokeWidth="1.5" strokeLinejoin="round" />
              </svg>
              Crash hotspot
            </span>
            <span className="flex items-center gap-2 px-3 py-1.5 text-xs text-orange-400">
              <svg width="10" height="10">
                <circle cx="5" cy="5" r="4" fill={CRASH_COLOR} />
              </svg>
              Past fatal crash
            </span>
          </>
        )}
        {/* Charging legend */}
        {chargingPlans.some(p => p.needed) && (
          <>
            <span className="flex items-center gap-2 px-3 py-1.5 text-xs text-cyan-300">
              <svg width="12" height="14" viewBox="-7 -11 14 22">
                <path d={BOLT_PATH} fill={CHARGER_FILL} stroke="#0f172a" strokeWidth="1.5" />
              </svg>
              Charging stop
            </span>
            <span className="flex items-center gap-2 px-3 py-1.5 text-xs text-cyan-300">
              <svg width="20" height="8">
                <line x1="0" y1="4" x2="20" y2="4" stroke={CHARGE_COLOR} strokeWidth="3" />
              </svg>
              Via charger
            </span>
            <span className="flex items-center gap-2 px-3 py-1.5 text-xs text-orange-400">
              <svg width="10" height="10">
                <circle cx="5" cy="5" r="4" fill="#f97316" stroke="#ffffff" strokeWidth="1" />
              </svg>
              Battery hits reserve
            </span>
          </>
        )}
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

      {routeErrors.length > 0 && <div role="alert" className="p-3 text-xs text-amber-300 border-b border-slate-700">
        <p>Some road routes could not be loaded.</p>
        {routeErrors.map((message, i) => <p key={i}>{message}</p>)}
        <button onClick={() => setRetry(n => n + 1)} className="underline mt-1">Retry road routes</button>
      </div>}
      {!isLoaded ? (
        <div style={MAP_CONTAINER_STYLE} className="flex items-center justify-center bg-slate-900 text-slate-500 text-sm">
          Loading Google Maps...
        </div>
      ) : (
        <div className="relative">
        <GoogleMap
          mapContainerStyle={MAP_CONTAINER_STYLE}
          center={center}
          onLoad={onMapLoad}
          options={{ styles: MAP_STYLES, streetViewControl: false, mapTypeControl: false, gestureHandling: 'greedy' }}
        >
          {/* Road route polylines */}
          {routeDirections.filter(r => visibleRoutes.has(r.label)).map(({ geometry, color, label, strokeWeight, strokeOpacity, zIndex }) => (
            <PolylineF key={label} path={geometry.path}
              options={{ strokeColor: color, strokeOpacity, strokeWeight, zIndex }} />
          ))}

          {/* Replanned legs: blocked originals (dashed), reroutes, accident markers, past crashes */}
          {replan && (
            <>
              {activeLegs.map(leg => (
                <React.Fragment key={leg.leg_index}>
                  <PolylineF
                    path={leg.original.path}
                    options={{
                      strokeOpacity: 0,
                      zIndex: 10,
                      icons: [{
                        icon: { path: 'M 0,-1 0,1', strokeColor: BLOCKED_COLOR, strokeOpacity: 1, scale: 3 },
                        offset: '0',
                        repeat: '12px',
                      }],
                    }}
                  />
                  <PolylineF
                    path={leg.rerouted.path}
                    options={{ strokeColor: REROUTE_COLOR, strokeOpacity: 0.95, strokeWeight: 5, zIndex: 11 }}
                  />
                </React.Fragment>
              ))}
              {replan.accidents.filter(a => activeAccidents.includes(a.id)).flatMap(a => a.crashes.map((crash, i) => (
                <MarkerF
                  key={`crash-${a.id}-${i}`}
                  position={crash}
                  icon={{
                    path: window.google.maps.SymbolPath.CIRCLE,
                    scale: 4,
                    fillColor: CRASH_COLOR,
                    fillOpacity: 0.85,
                    strokeColor: '#0f172a',
                    strokeWeight: 1,
                  }}
                  title={`Fatal crash, ${crash.year}`}
                  zIndex={25}
                />
              )))}
              {replan.accidents.map(a => {
                const active = activeAccidents.includes(a.id);
                return (
                  <MarkerF
                    key={`accident-${a.id}-${active}`}
                    position={{ lat: a.lat, lng: a.lng }}
                    icon={{
                      path: HOTSPOT_PATH,
                      scale: 13,
                      fillColor: active ? BLOCKED_COLOR : '#0f172a',
                      fillOpacity: active ? 1 : 0.9,
                      strokeColor: active ? '#ffffff' : BLOCKED_COLOR,
                      strokeWeight: 2,
                      labelOrigin: new window.google.maps.Point(0, 0.3),
                    }}
                    label={{ text: String(a.id), color: active ? '#ffffff' : BLOCKED_COLOR, fontSize: '12px', fontWeight: 'bold' }}
                    title={`${active ? 'Accident' : 'Hotspot (off)'} ${a.id}: ${a.road || 'unnamed road'} (${a.crash_count} past fatal crashes)`}
                    zIndex={active ? 30 : 26}
                  />
                );
              })}
            </>
          )}

          {/* Charging stops: road via the charger, charger, other chargers, where the battery would hit the reserve */}
          {chargingPlans.filter(p => p.stop).map(p => (
            <React.Fragment key={`charge-${p.vehicle_id}`}>
              {[p.stop.to_charger_path, p.stop.from_charger_path].map((path, i) => (
                <PolylineF key={i} path={path} options={{ strokeColor: CHARGE_COLOR, strokeOpacity: 0.95, strokeWeight: 4, zIndex: 12 }} />
              ))}
              <MarkerF
                position={{ lat: p.stop.charger.lat, lng: p.stop.charger.lng }}
                icon={{ path: BOLT_PATH, scale: 1.4, fillColor: CHARGER_FILL, fillOpacity: 1, strokeColor: '#0f172a', strokeWeight: 1.5 }}
                title={`Vehicle ${p.vehicle_id} charging stop: ${p.stop.charger.name}${p.stop.charger.max_kw ? ` (${p.stop.charger.max_kw} kW)` : ''}`}
                zIndex={31}
              />
            </React.Fragment>
          ))}
          {chargingPlans.flatMap(p => (p.alternatives || []).map((c, i) => (
            <MarkerF
              key={`charger-alt-${p.vehicle_id}-${i}`}
              position={{ lat: c.lat, lng: c.lng }}
              icon={{ path: BOLT_PATH, scale: 0.9, fillColor: '#64748b', fillOpacity: 0.9, strokeColor: '#0f172a', strokeWeight: 1 }}
              title={`Other charger for vehicle ${p.vehicle_id}: ${c.name}${c.max_kw ? ` (${c.max_kw} kW)` : ''}`}
              zIndex={22}
            />
          )))}
          {chargingPlans.filter(p => p.needed).map(p => (
            <MarkerF
              key={`reserve-${p.vehicle_id}`}
              position={{ lat: p.reserve_point.lat, lng: p.reserve_point.lng }}
              icon={{ path: window.google.maps.SymbolPath.CIRCLE, scale: 5, fillColor: '#f97316', fillOpacity: 1, strokeColor: '#ffffff', strokeWeight: 1.5 }}
              title={`Vehicle ${p.vehicle_id}: battery would hit the ${p.reserve_pct}% reserve here without charging`}
              zIndex={24}
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

          {/* Drive simulation: the truck, reroutes it takes; clock and prompt go in the overlay */}
          {drive && driveTrucks && (
            <DriveSimulation
              key={drive.runId}
              trucks={driveTrucks}
              departureTime={driveDeparture}
              speed={drive.speed}
              overlay={overlay}
              onEnd={onDriveEnd}
            />
          )}

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
        <div ref={setOverlay} className="absolute inset-0 pointer-events-none" />
        </div>
      )}
    </div>
  );
}
