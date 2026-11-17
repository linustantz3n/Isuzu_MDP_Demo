import React from 'react';

const COLORS = ['#a855f7', '#3b82f6', '#10b981', '#f59e0b', '#ef4444'];

export default function SchematicRouteMap({ results, locations = [] }) {
  if (!locations.length) return null;
  const xs = locations.map(p => p.lng), ys = locations.map(p => p.lat);
  const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
  const scale = Math.min(780 / Math.max(maxX - minX, .01), 320 / Math.max(maxY - minY, .01));
  const point = p => [60 + (p.lng - minX) * scale, 365 - (p.lat - minY) * scale];
  const fleet = results?.ortools_fleet?.routes;
  const routes = fleet || (results?.optimal ? [{ ...results.optimal, vehicle_name: 'Truck 1',
    order: [...results.optimal.order, 0] }] : []);
  return <section className="bg-slate-900 border border-slate-800 rounded-xl p-4 text-left">
    <h3 className="text-sm font-semibold text-white">Stop order schematic</h3>
    <p className="text-xs text-slate-400 mt-1">Straight connections show delivery order, not road geometry. Planning uses saved road distances and times.</p>
    <svg viewBox="0 0 900 420" className="w-full" role="img" aria-label="Delivery stop order by truck">
      {routes.map((r, i) => <polyline key={r.vehicle_id || i} points={r.order.map(n => point(locations[n]).join(',')).join(' ')}
        fill="none" stroke={COLORS[i % COLORS.length]} strokeWidth="3" strokeOpacity=".7" />)}
      {locations.map((p, i) => {
        const [x, y] = point(p);
        return <g key={i} transform={`translate(${x},${y})`}>
          <circle r={i ? 12 : 15} fill={i ? '#334155' : '#C8002F'} stroke="#e2e8f0" strokeWidth="2" />
          <text textAnchor="middle" dy="4" fill="white" fontSize="11">{i || 'D'}</text>
          <title>{p.formatted_address || p.address}</title>
        </g>;
      })}
    </svg>
    <div className="flex gap-4 flex-wrap text-xs">{routes.map((r, i) => <span key={r.vehicle_id || i} style={{ color: COLORS[i % COLORS.length] }}>
      {r.vehicle_name} · {r.driver || 'Unassigned'} · {r.effective_driving_style || 'normal'} · {r.order.join(' → ')}
    </span>)}</div>
  </section>;
}
