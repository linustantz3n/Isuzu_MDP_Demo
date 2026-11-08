import React, { useMemo } from 'react';
import { History } from 'lucide-react';
import { algoColor } from './BenchmarkCharts';
import { COMPARE_STATS, compareRoutes, statDiff } from './compareRoutes';

const STAT_KEYS = ['total_distance_mi', 'total_drive_time_min', 'total_energy_kwh'];
const STATS = COMPARE_STATS.filter(s => STAT_KEYS.includes(s.key));

const pctCell = (d) => {
  if (d.pct == null || Math.abs(d.delta) < 1e-9) return { text: '—', tone: 'text-slate-500' };
  return {
    text: `${d.pct > 0 ? '+' : '−'}${Math.abs(d.pct).toFixed(1)}%`,
    tone: d.delta < 0 ? 'text-green-400' : 'text-red-400',
  };
};

// One line per algorithm: what it changed relative to the fixed stop order, or "No changes".
export default function BenchmarkBaselineChanges({ rows, selectedK }) {
  const baseline = rows.find(r => r.family === 'baseline');

  const summaries = useMemo(() => {
    if (!baseline) return [];
    const baseRoutes = baseline.metrics.routes.map(r => r.route);
    return rows
      .filter(r => r.family !== 'baseline' && r.metrics?.routes)
      .map(r => ({
        row: r,
        cmp: compareRoutes(baseRoutes, r.metrics.routes.map(x => x.route)),
        diffs: STATS.map(s => statDiff(baseline.metrics, r.metrics, s)),
      }));
  }, [rows, baseline]);

  if (!baseline) return null;

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-xl">
      <div className="px-4 pt-4 pb-3 border-b border-slate-800 flex items-start gap-2">
        <History className="w-4 h-4 text-slate-400 mt-0.5" />
        <div>
          <h3 className="text-sm font-semibold text-white">Changes vs Fixed Stop Order</h3>
          <p className="text-slate-500 text-xs mt-0.5">
            The stop order is locked to the fixed plan (K={selectedK}), so each algorithm should return it unchanged.
            This confirms it: any difference would show up here.
          </p>
        </div>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-800 text-[11px] font-semibold text-slate-500 uppercase tracking-wider">
              <th className="px-3 py-2 text-left">Algorithm</th>
              <th className="px-3 py-2 text-left">Result</th>
              <th className="px-3 py-2 text-right whitespace-nowrap">Stops moved</th>
              <th className="px-3 py-2 text-right whitespace-nowrap">Trucks changed</th>
              <th className="px-3 py-2 text-right">Distance</th>
              <th className="px-3 py-2 text-right">Time</th>
              <th className="px-3 py-2 text-right">Energy</th>
            </tr>
          </thead>
          <tbody>
            {summaries.map(({ row, cmp, diffs }) => {
              const changedTrucks = cmp.pairs.length - cmp.exactTrucks;
              return (
                <tr key={row.algorithm} className="border-b border-slate-800/60 last:border-b-0">
                  <td className="px-3 py-2.5">
                    <span className="inline-flex items-center gap-2 text-white whitespace-nowrap">
                      <span className="w-2.5 h-2.5 rounded-full flex-shrink-0" style={{ backgroundColor: algoColor(row) }} />
                      {row.label}
                    </span>
                  </td>
                  <td className="px-3 py-2.5">
                    {cmp.noChanges
                      ? <span className="text-green-400 font-medium">No changes</span>
                      : <span className="text-amber-400">
                          {cmp.stopsOnDifferentTruck === 0 && cmp.reversedTrucks === 0
                            ? 'Reordered stops'
                            : cmp.stopsOnDifferentTruck === 0
                              ? 'Reordered or reversed routes'
                              : 'Reassigned stops'}
                        </span>}
                  </td>
                  <td className="px-3 py-2.5 text-right font-mono text-slate-300">
                    {cmp.stopsOnDifferentTruck} <span className="text-slate-600">/ {cmp.totalStops}</span>
                  </td>
                  <td className="px-3 py-2.5 text-right font-mono text-slate-300">
                    {changedTrucks} <span className="text-slate-600">/ {cmp.pairs.length}</span>
                  </td>
                  {diffs.map((d, i) => {
                    const c = pctCell(d);
                    return <td key={STATS[i].key} className={`px-3 py-2.5 text-right font-mono ${c.tone}`}>{c.text}</td>;
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="text-[11px] text-slate-600 px-4 py-2.5">
        "Stops moved" counts stops on a different truck than in the fixed plan, after pairing each truck with its closest
        match. "Trucks changed" counts trucks whose stops or visiting order differ. Use Compare Two Algorithms below to
        see the routes truck by truck.
      </p>
    </div>
  );
}
