import React, { useMemo, useState } from 'react';
import { ArrowLeftRight, GitCompare, Map as MapIcon } from 'lucide-react';
import { algoColor } from './BenchmarkCharts';
import BenchmarkRouteMap, { truckColor } from './BenchmarkRouteMap';
import { COMPARE_STATS, compareRoutes, statDiff } from './compareRoutes';

const METERS_PER_MILE = 1609.344;

const RELATION_LABELS = {
  identical: { text: 'Same route', tone: 'text-green-400' },
  reversed: { text: 'Same route, opposite direction', tone: 'text-green-400' },
  reordered: { text: 'Same stops, different order', tone: 'text-amber-400' },
  'different-stops': { text: 'Different stops', tone: 'text-red-400' },
  'only-a': { text: 'Only in A', tone: 'text-red-400' },
  'only-b': { text: 'Only in B', tone: 'text-red-400' },
};

const fmt = (v, digits) => (v == null ? '—' : Number(v).toLocaleString(undefined, {
  minimumFractionDigits: digits, maximumFractionDigits: digits,
}));
const signed = (v, digits) => `${v > 0 ? '+' : v < 0 ? '−' : ''}${fmt(Math.abs(v), digits)}`;
const stopName = (loc) => loc.name || loc.address;

function TruckColumn({ side, label, row, truckIdx, stops, otherOnly, locations, color }) {
  if (truckIdx === null) {
    return (
      <div className="bg-slate-950/60 border border-dashed border-slate-800 rounded-lg p-3 text-xs text-slate-600">
        <p className="text-slate-500 font-medium mb-1">{side} · {label}</p>
        No truck in this solution
      </div>
    );
  }
  const r = row.metrics.routes[truckIdx];
  const onlyHere = new Set(otherOnly);
  return (
    <div className="bg-slate-950/60 border border-slate-800 rounded-lg p-3 min-w-0">
      <p className="text-xs font-medium text-white flex items-center gap-2">
        <span className="w-2.5 h-2.5 rounded-full flex-shrink-0" style={{ backgroundColor: color }} />
        <span className="text-slate-500">{side}</span> {label}
      </p>
      <p className="text-[11px] text-slate-500 mt-0.5 mb-2">
        {r.stops} stop{r.stops === 1 ? '' : 's'} · {(r.distance_m / METERS_PER_MILE).toFixed(1)} mi ·{' '}
        {(r.time_s / 60).toFixed(0)} min · {r.energy_kwh.toFixed(1)} kWh
      </p>
      <ol className="space-y-1">
        {stops.map((s, i) => (
          <li key={s} className="flex items-start gap-2 text-xs">
            <span className="w-5 h-5 rounded-full bg-slate-800 text-white flex-shrink-0 flex items-center justify-center text-[10px] font-semibold">
              {i + 1}
            </span>
            <span className={`min-w-0 ${onlyHere.has(s) ? 'text-amber-300' : 'text-slate-200'}`}>
              {stopName(locations[s])}
              {onlyHere.has(s) && <span className="text-[10px] text-amber-500 ml-1.5">not on the other truck</span>}
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}

export default function BenchmarkCompare({ rows, locations, selectedK }) {
  const candidates = rows.filter(r => r.metrics?.routes);
  const byId = (id) => candidates.find(r => r.algorithm === id);

  // null = use the default for this run; a pick overrides it
  const [aPick, setAPick] = useState(null);
  const [bPick, setBPick] = useState(null);
  const [showMaps, setShowMaps] = useState(false);

  // With a fixed stop order the natural comparison is before (fixed plan) vs after (OR-Tools)
  const hasBaseline = Boolean(byId('fixed_order'));
  const defaultA = hasBaseline ? 'fixed_order' : 'ortools';
  const defaultB = hasBaseline ? 'ortools' : 'kmeans_2opt';

  // Fall back gracefully when the picked algorithm has no result at this fleet size
  const rowA = byId(aPick) || byId(defaultA) || candidates[0];
  const wantB = byId(bPick) || byId(defaultB);
  const rowB = wantB && wantB !== rowA ? wantB : candidates.find(r => r !== rowA);

  const routesA = useMemo(() => rowA?.metrics.routes.map(r => r.route) || [], [rowA]);
  const routesB = useMemo(() => rowB?.metrics.routes.map(r => r.route) || [], [rowB]);
  const cmp = useMemo(() => compareRoutes(routesA, routesB), [routesA, routesB]);

  if (candidates.length < 2) return null;

  const slotsA = [];
  const slotsB = [];
  cmp.pairs.forEach(p => {
    if (p.aIdx !== null) slotsA[p.aIdx] = p.slot;
    if (p.bIdx !== null) slotsB[p.bIdx] = p.slot;
  });

  const diffs = COMPARE_STATS.map(stat => ({ stat, ...statDiff(rowA.metrics, rowB.metrics, stat) }));
  const headlineDiffs = diffs.filter(d => ['total_distance_mi', 'total_drive_time_min', 'total_energy_kwh'].includes(d.stat.key));
  const swap = () => { setAPick(rowB.algorithm); setBPick(rowA.algorithm); };

  const select = (value, onChange, otherRow) => (
    <select
      value={value}
      onChange={e => onChange(e.target.value)}
      className="bg-slate-800 border border-slate-700 rounded-lg px-2.5 py-1.5 text-sm text-white focus:outline-none focus:border-[#C8002F]"
    >
      {candidates.map(r => (
        <option key={r.algorithm} value={r.algorithm} disabled={r === otherRow}>{r.label}</option>
      ))}
    </select>
  );

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-xl">
      <div className="px-4 pt-4 pb-3 border-b border-slate-800">
        <div className="flex items-start gap-2 mb-3">
          <GitCompare className="w-4 h-4 text-slate-400 mt-0.5" />
          <div>
            <h3 className="text-sm font-semibold text-white">Compare Two Algorithms</h3>
            <p className="text-slate-500 text-xs mt-0.5">
              Pick two algorithms at K={selectedK} to see how their statistics and truck routes differ.
            </p>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs font-semibold text-slate-500">A</span>
          {select(rowA.algorithm, setAPick, rowB)}
          <button onClick={swap} title="Swap A and B" className="p-1.5 rounded-lg text-slate-400 hover:text-white hover:bg-slate-800">
            <ArrowLeftRight className="w-4 h-4" />
          </button>
          <span className="text-xs font-semibold text-slate-500">B</span>
          {select(rowB.algorithm, setBPick, rowA)}
        </div>
      </div>

      <div className="p-4 space-y-5">
        {/* Plain-language summary */}
        <div className="text-sm text-slate-300 space-y-1.5">
          {cmp.noChanges && (
            <p className="text-green-400 font-medium">
              No changes: both produce exactly the same routes (same trucks, same stops, same order).
            </p>
          )}
          <p>
            Compared with <span className="text-white">{rowA.label}</span>,{' '}
            <span className="text-white">{rowB.label}</span> uses{' '}
            {headlineDiffs.map((d, i) => (
              <span key={d.stat.key}>
                {i > 0 && (i === headlineDiffs.length - 1 ? ', and ' : ', ')}
                {Math.abs(d.pct ?? 0) < 0.05
                  ? <>about the same {d.stat.label.replace('Total ', '')}</>
                  : <><span className={d.delta < 0 ? 'text-green-400' : 'text-red-400'}>{Math.abs(d.pct).toFixed(1)}% {d.delta < 0 ? 'less' : 'more'}</span>{' '}{d.stat.label.replace('Total ', '')}</>}
              </span>
            ))}.
          </p>
          <p>
            {cmp.stopsOnDifferentTruck === 0
              ? 'Both assign every stop to the same trucks.'
              : <><span className="text-white">{cmp.stopsOnDifferentTruck} of {cmp.totalStops}</span> stops are on a different truck.</>}{' '}
            {cmp.identicalTrucks} of {cmp.pairs.length} truck{cmp.pairs.length === 1 ? '' : 's'} follow the same route
            {cmp.sameStopsTrucks > 0 && `, ${cmp.sameStopsTrucks} visit the same stops in a different order`}
            {cmp.differentTrucks > 0 && `, ${cmp.differentTrucks} serve different stops`}.
          </p>
          <p className="text-xs text-slate-600">
            Trucks have no identity across algorithms, so each truck in A is paired with the truck in B that shares the most of its stops.
          </p>
        </div>

        {/* Stats */}
        <div className="overflow-x-auto border border-slate-800 rounded-lg">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-800 text-[11px] font-semibold text-slate-500 uppercase tracking-wider">
                <th className="px-3 py-2 text-left">Metric</th>
                <th className="px-3 py-2 text-right whitespace-nowrap">
                  <span className="inline-flex items-center gap-1.5">
                    <span className="w-2 h-2 rounded-full" style={{ backgroundColor: algoColor(rowA) }} />A · {rowA.label}
                  </span>
                </th>
                <th className="px-3 py-2 text-right whitespace-nowrap">
                  <span className="inline-flex items-center gap-1.5">
                    <span className="w-2 h-2 rounded-full" style={{ backgroundColor: algoColor(rowB) }} />B · {rowB.label}
                  </span>
                </th>
                <th className="px-3 py-2 text-right">B − A</th>
                <th className="px-3 py-2 text-right">Change</th>
              </tr>
            </thead>
            <tbody>
              {diffs.map(({ stat, a, b, delta, pct, winner }) => {
                const unit = stat.unit ? ` ${stat.unit}` : '';
                const deltaTone = winner === 'b' ? 'text-green-400' : winner === 'a' ? 'text-red-400' : 'text-slate-400';
                return (
                  <tr key={stat.key} className="border-b border-slate-800/60 last:border-b-0">
                    <td className="px-3 py-2 text-slate-300">{stat.label}</td>
                    <td className={`px-3 py-2 text-right font-mono ${winner === 'a' ? 'text-green-400 font-semibold' : 'text-slate-300'}`}>
                      {fmt(a, stat.digits)}{unit}
                    </td>
                    <td className={`px-3 py-2 text-right font-mono ${winner === 'b' ? 'text-green-400 font-semibold' : 'text-slate-300'}`}>
                      {fmt(b, stat.digits)}{unit}
                    </td>
                    <td className={`px-3 py-2 text-right font-mono ${deltaTone}`}>
                      {Math.abs(delta) < 1e-9 ? '—' : `${signed(delta, stat.digits)}${unit}`}
                    </td>
                    <td className={`px-3 py-2 text-right font-mono ${deltaTone}`}>
                      {pct == null || Math.abs(delta) < 1e-9 ? '—' : `${signed(pct, 1)}%`}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <p className="text-[11px] text-slate-600 px-3 py-2">Green marks the lower (better) value for distance, time, energy and makespan.</p>
        </div>

        {/* Maps: mounted only on request */}
        <div>
          <button
            onClick={() => setShowMaps(!showMaps)}
            className="inline-flex items-center gap-2 text-sm text-slate-300 hover:text-white bg-slate-800 hover:bg-slate-700 rounded-lg px-3 py-1.5"
          >
            <MapIcon className="w-4 h-4" />
            {showMaps ? 'Hide maps' : 'Show maps side by side'}
          </button>
          {showMaps && (
            <div className="mt-3 grid grid-cols-1 xl:grid-cols-2 gap-3">
              {[['A', rowA, routesA, slotsA], ['B', rowB, routesB, slotsB]].map(([tag, row, routes, slots]) => (
                <div key={`${tag}-${row.algorithm}`}>
                  <p className="text-xs text-slate-400 mb-1.5">
                    <span className="font-semibold text-slate-500">{tag}</span> · {row.label}
                  </p>
                  <BenchmarkRouteMap routes={routes} locations={locations} slots={slots} />
                </div>
              ))}
              <p className="text-[11px] text-slate-600 xl:col-span-2">
                Paired trucks share a color and number on both maps.
              </p>
            </div>
          )}
        </div>

        {/* Truck-by-truck */}
        <div className="space-y-3">
          <h4 className="text-[11px] font-semibold text-slate-500 uppercase tracking-widest">Truck by truck</h4>
          {cmp.pairs.map(p => {
            const rel = RELATION_LABELS[p.relation];
            return (
              <div key={p.slot}>
                <p className="text-sm text-white font-medium flex items-center gap-2 mb-1.5">
                  <span className="w-2.5 h-2.5 rounded-full" style={{ backgroundColor: truckColor(p.slot) }} />
                  Truck {p.slot + 1}
                  <span className={`text-xs font-normal ${rel.tone}`}>
                    {p.relation === 'different-stops'
                      ? `${Math.max(p.onlyA.length, p.onlyB.length)} stop${Math.max(p.onlyA.length, p.onlyB.length) === 1 ? '' : 's'} differ`
                      : rel.text}
                  </span>
                </p>
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
                  <TruckColumn
                    side="A" label={rowA.label} row={rowA} truckIdx={p.aIdx} color={algoColor(rowA)}
                    stops={p.aIdx === null ? [] : routesA[p.aIdx].slice(1, -1)} otherOnly={p.onlyA} locations={locations}
                  />
                  <TruckColumn
                    side="B" label={rowB.label} row={rowB} truckIdx={p.bIdx} color={algoColor(rowB)}
                    stops={p.bIdx === null ? [] : routesB[p.bIdx].slice(1, -1)} otherOnly={p.onlyB} locations={locations}
                  />
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
