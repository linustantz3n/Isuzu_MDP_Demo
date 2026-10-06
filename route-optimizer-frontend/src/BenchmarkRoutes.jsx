import React, { useMemo, useState } from 'react';
import { ChevronRight, ExternalLink, Route } from 'lucide-react';
import { algoColor } from './BenchmarkCharts';
import BenchmarkRouteMap, { truckColor } from './BenchmarkRouteMap';

const METERS_PER_MILE = 1609.344;

// Google Maps URLs reliably accept at most 9 waypoints, so long routes are
// split into consecutive legs; each leg starts where the previous one ended.
const MAX_WAYPOINTS = 9;

const coord = (loc) => `${loc.lat},${loc.lng}`;

export function mapsLinks(route, locations) {
  const links = [];
  const step = MAX_WAYPOINTS + 1;
  for (let start = 0; start < route.length - 1; start += step) {
    const leg = route.slice(start, Math.min(start + step + 1, route.length));
    const origin = coord(locations[leg[0]]);
    const destination = coord(locations[leg[leg.length - 1]]);
    const waypoints = leg.slice(1, -1).map(i => coord(locations[i])).join('|');
    let url = `https://www.google.com/maps/dir/?api=1&travelmode=driving&origin=${origin}&destination=${destination}`;
    if (waypoints) url += `&waypoints=${encodeURIComponent(waypoints)}`;
    links.push(url);
  }
  return links;
}

const stopName = (loc) => loc.name || loc.address;

function TruckRoute({ index, route, locations }) {
  const links = mapsLinks(route.route, locations);
  return (
    <div className="bg-slate-950/60 border border-slate-800 rounded-lg p-3">
      <div className="flex flex-wrap items-center justify-between gap-2 mb-2">
        <p className="text-sm text-white font-medium flex items-center gap-2">
          <span className="w-2.5 h-2.5 rounded-full" style={{ backgroundColor: truckColor(index) }} />
          Truck {index + 1}
          <span className="text-slate-500 font-normal text-xs ml-2">
            {route.stops} stop{route.stops === 1 ? '' : 's'} · {(route.distance_m / METERS_PER_MILE).toFixed(1)} mi · {(route.time_s / 60).toFixed(0)} min
          </span>
        </p>
        <div className="flex flex-wrap gap-1.5">
          {links.map((url, i) => (
            <a
              key={i}
              href={url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-xs text-blue-400 hover:text-blue-300 bg-slate-800 hover:bg-slate-700 rounded-md px-2 py-1"
            >
              Google Maps{links.length > 1 ? ` (part ${i + 1}/${links.length})` : ''}
              <ExternalLink className="w-3 h-3" />
            </a>
          ))}
        </div>
      </div>
      <ol className="space-y-1">
        {route.route.map((locIdx, pos) => {
          const loc = locations[locIdx];
          const isDepot = locIdx === 0;
          return (
            <li key={pos} className="flex items-start gap-2 text-xs">
              <span className={`w-5 h-5 rounded-full flex-shrink-0 flex items-center justify-center text-[10px] font-semibold ${
                isDepot ? 'bg-slate-700 text-slate-300' : 'bg-slate-800 text-white'
              }`}>
                {isDepot ? 'D' : pos}
              </span>
              <span className="min-w-0">
                <span className={isDepot ? 'text-slate-400' : 'text-slate-200'}>
                  {isDepot ? `Depot — ${stopName(loc)}` : stopName(loc)}
                </span>
                {loc.name && <span className="block text-slate-600 truncate">{loc.address}</span>}
              </span>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

function AlgorithmRoutes({ row, locations, open, onToggle }) {
  const m = row.metrics;
  // Stable reference so the map only fetches directions when the routes change
  const routes = useMemo(() => m.routes.map(r => r.route), [m.routes]);
  return (
    <div className="border-b border-slate-800/60 last:border-b-0">
      <button
        onClick={onToggle}
        className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-slate-800/40 transition-colors"
      >
        <ChevronRight className={`w-4 h-4 text-slate-500 transition-transform ${open ? 'rotate-90' : ''}`} />
        <span className="w-2.5 h-2.5 rounded-full flex-shrink-0" style={{ backgroundColor: algoColor(row) }} />
        <span className="text-sm text-white flex-1">{row.label}</span>
        <span className="text-xs text-slate-500 font-mono">
          {m.vehicles_used} truck{m.vehicles_used === 1 ? '' : 's'} · {m.total_distance_mi.toFixed(1)} mi
        </span>
      </button>
      {/* Map and route cards mount only while expanded, so Maps/Directions calls happen on demand */}
      {open && (
        <div className="px-4 pb-4 space-y-3">
          <BenchmarkRouteMap routes={routes} locations={locations} />
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
            {m.routes.map((r, i) => (
              <TruckRoute key={i} index={i} route={r} locations={locations} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export default function BenchmarkRoutes({ rows, locations, selectedK }) {
  const [openIds, setOpenIds] = useState(new Set());
  const withRoutes = rows.filter(r => r.metrics?.routes);

  const toggle = (id) => {
    const next = new Set(openIds);
    next.has(id) ? next.delete(id) : next.add(id);
    setOpenIds(next);
  };

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-xl">
      <div className="px-4 pt-4 pb-3 border-b border-slate-800 flex items-start gap-2">
        <Route className="w-4 h-4 text-slate-400 mt-0.5" />
        <div>
          <h3 className="text-sm font-semibold text-white">Truck Routes</h3>
          <p className="text-slate-500 text-xs mt-0.5">
            Expand an algorithm to see its map and stop order for each truck at K={selectedK}. Every route starts and ends at the depot.
            Routes with more than {MAX_WAYPOINTS} stops are split into several Google Maps links.
          </p>
        </div>
      </div>
      {withRoutes.map(r => (
        <AlgorithmRoutes
          key={r.algorithm}
          row={r}
          locations={locations}
          open={openIds.has(r.algorithm)}
          onToggle={() => toggle(r.algorithm)}
        />
      ))}
    </div>
  );
}
