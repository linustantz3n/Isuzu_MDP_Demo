import React, { useEffect, useMemo, useState } from 'react';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine, Legend
} from 'recharts';
import {
  Activity, AlertCircle, ArrowRight, BatteryWarning, CheckCircle, Clock, Home, Loader2, MapPin, MinusCircle,
  Play, RotateCcw, Route, Shuffle, Square, TriangleAlert, Zap
} from 'lucide-react';
import RouteMap from './RouteMap';
import SchematicRouteMap from './SchematicRouteMap';
import { SIM_SPEEDS, REPLAN_INTERVAL_S } from './DriveSimulation';

// Simulation tab: solve the stops and fleet on measured traffic (hindsight), then drive that plan.
// The stops, fleet, goals and conditions are the same inputs as the Solver tab's (shared state).
//   • Crash hotspots: accidents at historical fatal-crash spots, switched on and off here
//     (/api/replan_incidents); trucks detour around them while driving.
//   • Rolling replan: every 5 simulated minutes each truck's remaining stops are re-sequenced.
//   • Battery drain what-if: every leg uses more energy than the model predicted, so the
//     rolling replan may reorder or drop stops to keep the battery above its reserve.
// The drive itself is DriveSimulation.jsx, rendered inside RouteMap.

const SAVED_DEMO = process.env.REACT_APP_SAVED_DEMO === 'true';

// Validated as a set on the dark panel surface (#0f172a): dataviz validate_palette, all checks pass
const SERIES = {
  planned: { label: 'Planned', color: '#3987e5', dash: '6 4' },
  actual: { label: 'With replanning', color: '#199e70' },
  blind: { label: 'Without replanning', color: '#d95926' },
};

const ACTIONS = {
  continue: { label: 'Continue', icon: ArrowRight, cls: 'text-slate-400 bg-slate-800 border-slate-700' },
  reorder: { label: 'Reorder', icon: Shuffle, cls: 'text-sky-300 bg-sky-950/50 border-sky-800' },
  drop: { label: 'Drop stops', icon: MinusCircle, cls: 'text-amber-300 bg-amber-950/40 border-amber-800' },
  return_to_depot: { label: 'Return to depot', icon: Home, cls: 'text-red-300 bg-red-950/40 border-red-900' },
};

const TOOLTIP_STYLE = {
  backgroundColor: '#1e293b', border: '1px solid #475569', borderRadius: '8px', color: '#e2e8f0', fontSize: '12px',
};
const AXIS_STYLE = { fill: '#94a3b8', fontSize: 11 };
const INPUT = 'w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-sm font-mono text-white focus:outline-none focus:border-[#C8002F] disabled:opacity-50';
const SECTION_TITLE = 'text-[11px] font-semibold text-slate-500 uppercase tracking-widest mb-3';

const nameOf = (locations, i) => (i === 0 ? 'Depot' : locations?.[i]?.name || locations?.[i]?.address || `Stop ${i}`);
const formatDistance = (meters) => `${(meters / 1609.34).toFixed(1)} mi`;

// Legs searched for crash hotspots: Held-Karp's, or with a fleet every vehicle's legs together,
// each tagged with its vehicle and the input index of both ends (fleet stops keep input numbering)
export const incidentLegs = (results) => (results.ortools_fleet
  ? results.ortools_fleet.routes.flatMap(route => route.legs.map((leg, k) => ({
    ...leg, vehicle_id: route.vehicle_id, from_idx: route.order[k], to_idx: route.order[k + 1],
  })))
  : results.optimal.legs);

// Simulate Drive needs traffic data (measured leg times) for every route it drives
export const canDrive = (results) => {
  const fleet = results?.ortools_fleet;
  return fleet ? fleet.routes.length > 0 && fleet.routes.every(r => r.traffic) : !!results?.optimal?.traffic;
};

function Stat({ label, value, sub, warn }) {
  return (
    <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
      <p className="text-[11px] font-semibold text-slate-500 uppercase tracking-widest">{label}</p>
      <p className={`text-2xl font-bold mt-1 ${warn ? 'text-amber-300' : 'text-white'}`}>{value}</p>
      {sub && <p className="text-xs text-slate-500 mt-0.5">{sub}</p>}
    </div>
  );
}

function SocTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  const row = payload[0].payload;
  return (
    <div style={TOOLTIP_STYLE} className="px-3 py-2">
      <p className="text-slate-400 mb-1">{label === 0 ? 'Start' : `After leg ${label}`}</p>
      {Object.entries(SERIES).map(([key, s]) => row[key] != null && (
        <p key={key} className="flex items-center gap-2">
          <span className="w-2.5 h-0.5 inline-block" style={{ backgroundColor: s.color }} />
          <span className="text-slate-300">{s.label}:</span>
          <span className="font-mono text-white">{row[key]}%</span>
          <span className="text-slate-500 truncate max-w-[180px]">{row[`${key}Name`]}</span>
        </p>
      ))}
    </div>
  );
}

function SocChart({ truck, reserve }) {
  const data = useMemo(() => {
    const traces = { planned: truck.planned.soc_trace, actual: truck.soc_trace, blind: truck.blind.soc_trace };
    const len = Math.max(...Object.values(traces).map(t => t.length));
    return Array.from({ length: len }, (_, step) => {
      const row = { step };
      Object.entries(traces).forEach(([key, t]) => {
        if (t[step]) { row[key] = t[step].soc_pct; row[`${key}Name`] = t[step].name; }
      });
      return row;
    });
  }, [truck]);
  const low = Math.min(0, ...data.flatMap(r => [r.planned, r.actual, r.blind].filter(v => v != null)));

  return (
    <div className="bg-slate-900 rounded-xl p-5 border border-slate-800">
      <h3 className="text-sm font-semibold text-white">Battery after each leg · {truck.label}</h3>
      <p className="text-slate-500 text-xs mt-0.5 mb-3">
        State of charge (%) on arrival at each stop. Dashed line: the {reserve}% reserve.
      </p>
      <ResponsiveContainer width="100%" height={280}>
        <LineChart data={data} margin={{ left: 0, right: 16, top: 8, bottom: 4 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#334155" vertical={false} />
          <XAxis dataKey="step" stroke="#475569" tick={AXIS_STYLE}
                 tickFormatter={s => (s === 0 ? 'Start' : `Leg ${s}`)} />
          <YAxis stroke="#475569" tick={AXIS_STYLE} domain={[Math.floor(low / 10) * 10, 100]} unit="%" width={48} />
          <Tooltip content={<SocTooltip />} cursor={{ stroke: '#64748b', strokeWidth: 1 }} />
          <Legend wrapperStyle={{ fontSize: 12, color: '#cbd5e1' }} />
          <ReferenceLine y={reserve} stroke="#94a3b8" strokeDasharray="2 4"
                         label={{ value: `${reserve}% reserve`, fill: '#94a3b8', fontSize: 11, position: 'insideBottomRight' }} />
          {Object.entries(SERIES).map(([key, s]) => (
            <Line key={key} type="linear" dataKey={key} name={s.label} stroke={s.color} strokeWidth={2}
                  strokeDasharray={s.dash} dot={{ r: 4, strokeWidth: 2, stroke: '#0f172a', fill: s.color }}
                  activeDot={{ r: 6, stroke: '#0f172a', strokeWidth: 2 }} connectNulls={false} isAnimationActive={false} />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

function EventLog({ truck, locations, clock }) {
  return (
    <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-x-auto">
      <div className="px-4 pt-4 pb-2">
        <h3 className="text-sm font-semibold text-white">Decisions at each replan · {truck.label}</h3>
        <p className="text-slate-500 text-xs mt-0.5">
          Every {REPLAN_INTERVAL_S / 60} simulated minutes the truck predicts what the rest of its route needs (at the
          worst drain it has seen) and compares it with its battery above the reserve.
        </p>
      </div>
      {truck.events.length === 0 ? (
        <p className="px-4 pb-4 text-xs text-slate-600">No replan for this truck yet.</p>
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-800 text-[11px] font-semibold text-slate-500 uppercase tracking-wider">
              <th className="px-3 py-2 text-left">Time · next stop</th>
              <th className="px-3 py-2 text-right">Battery</th>
              <th className="px-3 py-2 text-right">Drain seen</th>
              <th className="px-3 py-2 text-right">Need (kWh)</th>
              <th className="px-3 py-2 text-right">Budget (kWh)</th>
              <th className="px-3 py-2 text-left">Decision</th>
            </tr>
          </thead>
          <tbody>
            {truck.events.map((e, i) => {
              const a = ACTIONS[e.action];
              const Icon = a.icon;
              const short = e.predicted_need_kwh != null && e.predicted_need_kwh > e.budget_kwh;
              return (
                <tr key={i} className="border-b border-slate-800/60 align-top">
                  <td className="px-3 py-2 text-white"><span className="font-mono text-slate-400">{clock(e.t)}</span> · {e.name}</td>
                  <td className="px-3 py-2 text-right font-mono text-slate-300">{e.soc_pct}%</td>
                  <td className="px-3 py-2 text-right font-mono text-slate-300">{e.drain_estimate}×</td>
                  <td className={`px-3 py-2 text-right font-mono ${short ? 'text-amber-300' : 'text-slate-300'}`}>{e.predicted_need_kwh ?? '—'}</td>
                  <td className="px-3 py-2 text-right font-mono text-slate-300">{e.budget_kwh ?? '—'}</td>
                  <td className="px-3 py-2">
                    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-md border text-xs ${a.cls}`}>
                      <Icon className="w-3 h-3" /> {a.label}
                    </span>
                    {e.action !== 'continue' && (
                      <div className="text-[11px] text-slate-500 mt-1 space-y-0.5">
                        {e.dropped.length > 0 && (
                          <p>Dropped: {e.dropped.map(s => <span key={s} className="line-through text-slate-400 mr-2">{nameOf(locations, s)}</span>)}</p>
                        )}
                        <p>Next: {e.remaining_after.length ? e.remaining_after.map(s => nameOf(locations, s)).join(' → ') : 'back to depot'}</p>
                      </div>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}

// Battery drain report: live while the drive runs
function DrainReport({ report, locations, clock }) {
  const [truckIdx, setTruckIdx] = useState(0);
  const trucks = report.trucks;
  const truck = trucks[Math.min(truckIdx, trucks.length - 1)];
  const sum = (f) => trucks.reduce((n, t) => n + f(t), 0);
  const d = report.drain || {};
  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-white font-semibold text-lg">Battery drain</h2>
        <p className="text-slate-500 text-xs">
          {trucks.length} truck{trucks.length === 1 ? '' : 's'} · {report.reserve}% reserve · drain {d.factor}×
          {d.event && `, ${d.event.factor}× after stop ${d.event.after_stop}`}
          {!report.done && ' · so far, while the drive runs'}
        </p>
      </div>

      <div className="grid grid-cols-2 xl:grid-cols-4 gap-3">
        <Stat label="Stops served" value={`${sum(t => t.served)}/${sum(t => t.planned_stops)}`}
              sub={sum(t => t.dropped.length) ? `${sum(t => t.dropped.length)} dropped` : 'none dropped'}
              warn={sum(t => t.dropped.length) > 0} />
        <Stat label="Replans" value={sum(t => t.replans)} sub="reorders, drops and early returns" />
        <Stat label="Below reserve, replanning" value={sum(t => t.below_reserve)}
              sub={`of ${trucks.length} trucks`} warn={sum(t => t.below_reserve) > 0} />
        <Stat label="Below reserve, no replanning" value={sum(t => t.blind.below_reserve)}
              sub={sum(t => t.blind.ran_out_at != null) ? `${sum(t => t.blind.ran_out_at != null)} would run out` : 'same drain, original plan'}
              warn={sum(t => t.blind.below_reserve) > 0} />
      </div>

      {trucks.length > 1 && (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs text-slate-500 mr-1">Truck</span>
          {trucks.map((t, i) => {
            const active = t === truck;
            return (
              <button key={t.label} onClick={() => setTruckIdx(i)}
                      className={`flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-medium border transition-colors ${active ? 'text-white' : 'border-slate-700 text-slate-400 hover:text-white'}`}
                      style={active ? { backgroundColor: t.color + '33', borderColor: t.color } : {}}>
                <span className="w-2 h-2 rounded-full" style={{ backgroundColor: t.color }} />
                {t.label}
                {t.dropped.length > 0 && <span className="text-amber-300">· −{t.dropped.length}</span>}
                {t.below_reserve && <BatteryWarning className="w-3 h-3 text-red-300" />}
              </button>
            );
          })}
        </div>
      )}

      <div className="bg-slate-900 border border-slate-800 rounded-xl p-4 text-sm">
        <p className="text-slate-400">
          Planned {truck.planned_stops} stops, served {truck.served}
          {truck.dropped.length > 0 && <>; dropped{' '}
            {truck.dropped.map(s => <span key={s} className="line-through text-slate-300 mr-1.5">{nameOf(locations, s)}</span>)}
          </>}
          .
        </p>
        <p className="text-slate-400 mt-1 flex items-center gap-1.5 flex-wrap">
          <CheckCircle className="w-3.5 h-3.5 text-green-400" />
          {truck.done ? 'Ends at' : 'Now at'} <span className="font-mono text-white">{truck.final_soc_pct}%</span>
          (lowest {truck.min_soc_pct}%) · planned {truck.planned.final_soc_pct}% · without replanning{' '}
          <span className={`font-mono ${truck.blind.below_reserve ? 'text-amber-300' : 'text-white'}`}>
            {truck.blind.ran_out_at != null ? 'runs out' : `${truck.blind.final_soc_pct}%`}
          </span>
        </p>
      </div>
      <SocChart truck={truck} reserve={report.reserve} />
      <EventLog truck={truck} locations={locations} clock={clock} />
    </div>
  );
}

// Crash hotspots on the plan (moved from the Solver tab): find them, switch accidents on and off,
// and see the rerouted legs and the before/after totals. The delivery order stays fixed.
function IncidentCard({ results, replan, replanning, replanError, activeAccidents, activeLegs, drive, onFind, onToggle, onReset }) {
  const hk = results.optimal;
  const fleet = results.ortools_fleet;
  if (!hk.legs.length) return null;
  const n = hk.ordered_locations.length;
  const legs = incidentLegs(results);
  const stopLabel = (i) => (i === 0 || i === n ? 'Depot' : `Stop ${i}`);
  const stopName = (idx) => (idx === 0 ? 'Depot' : `Stop ${idx}`);
  const legLabel = (i) => (fleet
    ? `V${legs[i].vehicle_id} · ${stopName(legs[i].from_idx)} → ${stopName(legs[i].to_idx)}`
    : `${stopLabel(i)} → ${stopLabel(i + 1)}`);
  const diffClass = (d) => (d > 0 ? 'text-red-400' : 'text-green-400');
  const sign = (d) => (d > 0 ? '+' : '−');
  // After replanning = planned total + change on the rerouted legs
  const change = (field, legsOf = activeLegs) => legsOf.reduce((sum, leg) => sum + leg.rerouted[field] - leg.original[field], 0);
  const visitOrder = fleet
    ? fleet.routes.map(r => `V${r.vehicle_id}: ${r.order.map(i => (i === 0 ? 'D' : i)).join(' → ')}`).join('  ·  ')
    : [...Array(n).keys(), ...(hk.return_to_start ? [n] : [])]
      .map(i => (i === 0 || i === n ? 'D' : i)).join(' → ');
  // Fleet time is the makespan: the last vehicle back, each delayed by its own reroutes
  const planned = fleet
    ? { distance: fleet.total_distance_m, time: fleet.makespan_s, energy: fleet.total_energy_kwh, scope: 'all vehicles', timeScope: 'makespan' }
    : { distance: hk.total_distance_m, time: hk.total_time_s, energy: hk.total_energy_kwh, scope: 'whole route', timeScope: 'whole route' };
  const timeAfter = fleet
    ? Math.max(...fleet.routes.map(r => r.total_time_s + change('time_s', activeLegs.filter(l => l.vehicle_id === r.vehicle_id))))
    : planned.time + change('time_s');
  const years = replan && [
    Math.min(...replan.accidents.map(a => a.years[0])),
    Math.max(...replan.accidents.map(a => a.years[1])),
  ];
  return (
    <div className="bg-slate-900 border border-red-500/25 rounded-xl p-4">
      <div className="flex items-center gap-2">
        <TriangleAlert className="w-4 h-4 text-red-400" />
        <span className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider">Incident Replanning</span>
        <span className="text-[10px] text-slate-600">historical crash hotspots · delivery order fixed</span>
        <div className="ml-auto flex gap-2">
          {replan && (
            <button
              onClick={onReset}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-slate-800 hover:bg-slate-700 border border-slate-700 text-slate-300 rounded-lg text-xs transition-colors"
            >
              <RotateCcw className="w-3 h-3" /> Reset
            </button>
          )}
          <button
            title={SAVED_DEMO ? 'Requires live Google Maps credentials' : undefined}
            onClick={onFind}
            disabled={SAVED_DEMO || replanning || replan !== null}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-red-500/10 hover:bg-red-500/20 disabled:opacity-40 disabled:cursor-not-allowed border border-red-500/30 text-red-400 rounded-lg text-xs font-medium transition-colors"
          >
            {replanning
              ? <><Loader2 className="w-3 h-3 animate-spin" /> Finding hotspots...</>
              : <><TriangleAlert className="w-3 h-3" /> Find Crash Hotspots</>
            }
          </button>
        </div>
      </div>

      {replanError && (
        <p className="mt-3 text-xs text-red-400">{replanError}</p>
      )}

      {replan && (
        <div className="mt-4 space-y-3">
          <p className="text-xs text-slate-400">
            {fleet ? "The fleet routes'" : "The route's"} {replan.accidents.length} worst fatal-crash hotspot{replan.accidents.length === 1 ? '' : 's'} ({replan.data_source}, {years[0]}–{years[1]})
            {replan.accidents.length < replan.num_requested && (
              <span className="text-slate-600"> — only {replan.accidents.length} avoidable hotspot{replan.accidents.length === 1 ? '' : 's'} on this route</span>
            )}.
            {' '}Switch one on to simulate an accident there.
          </p>
          <p className="text-xs text-slate-500">
            Stop order preserved (trucks are already loaded):{' '}
            <span className="text-slate-300 font-mono">{visitOrder}</span>
            {' '}— only the roads between affected stops change.
          </p>

          {/* Accident list */}
          <div className="space-y-1.5">
            {replan.accidents.map(a => (
              <div key={a.id} className="flex items-center gap-2.5 text-xs">
                <label className="relative inline-flex items-center cursor-pointer flex-shrink-0">
                  <input
                    type="checkbox"
                    checked={activeAccidents.includes(a.id)}
                    onChange={() => onToggle(a.id)}
                    disabled={drive !== null}
                    title={drive ? 'Stop the drive simulation to change accidents' : undefined}
                    className="sr-only peer"
                  />
                  <div className="w-9 h-5 bg-slate-700 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-red-500"></div>
                </label>
                <span className={`flex-shrink-0 w-5 h-5 rounded-full text-white text-[11px] font-bold flex items-center justify-center ${activeAccidents.includes(a.id) ? 'bg-red-500' : 'bg-slate-600'}`}>{a.id}</span>
                <span className="text-white font-medium">{a.road || 'Unnamed road'}</span>
                <span className="text-slate-500">on {legLabel(a.leg_index)}</span>
                <span className="ml-auto text-slate-400">
                  {a.crash_count} fatal crash{a.crash_count === 1 ? '' : 'es'} within 1 km
                  <span className="text-slate-600"> ({a.years[0] === a.years[1] ? a.years[0] : `${a.years[0]}–${a.years[1]}`})</span>
                </span>
              </div>
            ))}
          </div>

          {/* Rerouted legs */}
          <div className="space-y-1 border-t border-slate-800 pt-3">
            {activeLegs.length === 0 && (
              <p className="text-xs text-slate-600">No active accidents — route unchanged.</p>
            )}
            {activeLegs.map(leg => {
              const d = leg.rerouted.time_s - leg.original.time_s;
              return (
                <div key={leg.leg_index} className="flex items-center gap-2 text-xs">
                  <span className="text-slate-300">{legLabel(leg.leg_index)}</span>
                  <span className="text-slate-600">avoids #{leg.accident_ids.join(', #')} via {leg.method === 'alternative' ? 'Google alternative route' : 'forced detour'}</span>
                  <span className={`ml-auto font-semibold ${diffClass(d)}`}>{sign(d)}{Math.round(Math.abs(d) / 60)} min</span>
                </div>
              );
            })}
          </div>

          {/* Whole-route (or whole-fleet) before/after */}
          {activeLegs.length > 0 && <div className="grid grid-cols-3 gap-3">
            {[
              ['Distance', planned.scope, MapPin, planned.distance, planned.distance + change('distance_m'), formatDistance,
                (d) => `${sign(d)}${formatDistance(Math.abs(d))}`],
              // Round (not floor like formatTime) so before/after match the diff
              ['Time', planned.timeScope, Clock, planned.time, timeAfter,
                (s) => { const m = Math.round(s / 60); return m >= 60 ? `${Math.floor(m / 60)}h ${m % 60}m` : `${m} min`; },
                (d) => `${sign(d)}${Math.round(Math.abs(d) / 60)} min`],
              ['Energy', planned.scope, Zap, planned.energy, planned.energy + change('energy_kwh'), (e) => `${e.toFixed(1)} kWh`,
                (d) => `${sign(d)}${Math.abs(d).toFixed(1)} kWh`],
            ].filter(([, , , before]) => before != null).map(([label, scope, Icon, before, after, fmt, fmtDiff]) => (
              <div key={label} className="bg-slate-800/50 border border-slate-800 rounded-lg px-3 py-2.5">
                <p className="text-[10px] text-slate-500 flex items-center gap-1.5 mb-1"><Icon className="w-3 h-3" />{label} · {scope}</p>
                <p className="text-sm text-white font-semibold">
                  <span className="text-slate-500 line-through font-normal">{fmt(before)}</span> → {fmt(after)}
                </p>
                <p className={`text-[11px] font-semibold mt-0.5 ${diffClass(after - before)}`}>{fmtDiff(after - before)}</p>
              </div>
            ))}
          </div>}
        </div>
      )}
    </div>
  );}

/**
 * results / locations: this tab's plan (solved on measured traffic) and its locations, or null
 * planSetup / planStops / validateButton: the Solver tab's input sections, rendered here too
 *   (null while the tab is hidden, so the inputs exist once in the page)
 * trafficDays, simTraffic {day, time}, setSimTraffic: when the measured traffic starts
 * onOptimize, optimizing, optimizeError, canOptimize: solve on measured traffic
 * replan, replanning, replanError, activeAccidents, activeLegs, onFindHotspots, onToggleAccident,
 *   onResetIncidents: crash hotspot state, kept in RouteOptimizer (it resets it with each new plan)
 * drive / setDrive: {runId, speed, drain} while the drive runs, else null
 * replanContext: {request, inputs, locations} for the rolling replan; null turns it off
 * active: whether this tab is showing (its map is only mounted once the tab has been opened)
 */
export default function SimulationPanel({
  results, locations, planSetup, planStops, validateButton, trafficDays, simTraffic, setSimTraffic,
  onOptimize, optimizing, optimizeError, canOptimize, replan, replanning, replanError, activeAccidents, activeLegs,
  onFindHotspots, onToggleAccident, onResetIncidents, drive, setDrive, replanContext, active,
}) {
  const [drain, setDrain] = useState(1.3);
  const [useEvent, setUseEvent] = useState(false);
  const [eventAfter, setEventAfter] = useState('2');
  const [eventDrain, setEventDrain] = useState(1.6);
  const [report, setReport] = useState(null);
  const [opened, setOpened] = useState(active);

  useEffect(() => { if (active) setOpened(true); }, [active]);
  useEffect(() => { setReport(null); }, [results]);

  const driveable = canDrive(results);
  const departure = results?.ortools_fleet?.routes?.[0]?.traffic?.departure_time
    || results?.optimal?.traffic?.departure_time;
  const clock = (t) => (departure ? new Date(Date.parse(`${departure}:00Z`) + t * 1000).toISOString().slice(11, 16) : '');

  const start = () => {
    setReport(null);
    setDrive({
      runId: Date.now(), speed: SIM_SPEEDS[1],
      drain: { factor: drain, event: useEvent ? { after_stop: Number(eventAfter), factor: eventDrain } : null },
    });
  };

  return (
    <div className="flex flex-1 min-h-0">
      {/* ── Sidebar ──────────────────────────────────────── */}
      <aside className="w-80 bg-slate-900 border-r border-slate-800 flex flex-col flex-shrink-0">
        <div className="flex-1 overflow-y-auto p-5 space-y-6">
          {planSetup}

          <div className="border-t border-slate-800" />

          <section>
            <h3 className={SECTION_TITLE}>Measured traffic</h3>
            <div className="grid grid-cols-2 gap-2">
              <select
                aria-label="Simulation day"
                value={simTraffic.day}
                onChange={(e) => setSimTraffic({ ...simTraffic, day: e.target.value })}
                className="w-full px-2 py-1 bg-slate-800 border border-slate-700 rounded text-xs text-white focus:ring-1 focus:ring-[#C8002F] focus:border-transparent"
              >
                {trafficDays.map(([value, text]) => <option key={value} value={value}>{text}</option>)}
              </select>
              <input
                aria-label="Simulation departure"
                type="time" step="300"
                value={simTraffic.time}
                onChange={(e) => setSimTraffic({ ...simTraffic, time: e.target.value })}
                className="w-full px-2 py-1 bg-slate-800 border border-slate-700 rounded text-xs text-white text-center focus:ring-1 focus:ring-[#C8002F] focus:border-transparent"
              />
            </div>
            <p className="text-[10px] text-slate-600 mt-2 leading-relaxed">
              The plan is solved and driven on the speeds measured that day (hindsight). Traffic data covers only the
              Cerritos–Stanton area (load Demo 0).
            </p>
          </section>

          <div className="border-t border-slate-800" />

          {planStops}

          <div className="border-t border-slate-800" />

          <section>
            <h3 className={SECTION_TITLE}>Drain</h3>
            <div className="flex items-center justify-between text-xs mb-0.5">
              <span className="text-slate-300">Actual vs model energy</span>
              <span className="font-mono text-slate-300">{drain.toFixed(2)}×</span>
            </div>
            <input type="range" min={1} max={2} step={0.05} value={drain} disabled={drive !== null}
                   onChange={e => setDrain(Number(e.target.value))} className="w-full accent-[#C8002F]" />
            <p className="text-[11px] text-slate-600">1.30× = every leg uses 30% more than planned. 1.00× = no extra drain.</p>

            <label className="flex items-start gap-2.5 cursor-pointer mt-4">
              <input type="checkbox" checked={useEvent} disabled={drive !== null}
                     onChange={e => setUseEvent(e.target.checked)} className="mt-0.5 accent-[#C8002F]" />
              <span className="text-sm text-slate-300">
                Mid-route event
                <span className="block text-[11px] text-slate-600">Drain changes partway through (cold snap, traffic, headwind)</span>
              </span>
            </label>
            {useEvent && (
              <div className="mt-3 space-y-2 pl-6">
                <label className="flex items-center gap-3">
                  <span className="text-sm text-slate-300 w-24">After stop</span>
                  <input type="number" min={0} value={eventAfter} disabled={drive !== null}
                         onChange={e => setEventAfter(e.target.value)} className={INPUT} />
                </label>
                <div>
                  <div className="flex items-center justify-between text-xs mb-0.5">
                    <span className="text-slate-300">New drain</span>
                    <span className="font-mono text-slate-300">{eventDrain.toFixed(2)}×</span>
                  </div>
                  <input type="range" min={1} max={2.5} step={0.05} value={eventDrain} disabled={drive !== null}
                         onChange={e => setEventDrain(Number(e.target.value))} className="w-full accent-[#C8002F]" />
                </div>
              </div>
            )}
            <p className="text-xs text-slate-600 mt-3 leading-relaxed">
              Battery, reserve and driving styles come from the inputs above. Each replan checks the rest of
              the route at the drain seen so far; if the battery no longer covers it, the truck reorders, and if that
              is not enough it drops the fewest stops it can.
            </p>
          </section>
        </div>

        <div className="flex-shrink-0 p-4 border-t border-slate-800 space-y-2">
          {optimizeError && (
            <div className="flex items-start gap-2 text-xs text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2">
              <AlertCircle className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" />
              <span className="leading-relaxed">{optimizeError}</span>
            </div>
          )}
          {results && !driveable && (
            <div className="flex items-start gap-2 text-xs text-slate-400 bg-slate-800/60 border border-slate-700 rounded-lg p-3">
              <AlertCircle className="w-4 h-4 flex-shrink-0 mt-px" />
              <span>This plan has no measured leg times to drive on. Optimize again.</span>
            </div>
          )}
          {validateButton}
          <button
            onClick={onOptimize}
            disabled={optimizing || !canOptimize || drive !== null}
            title={drive ? 'Stop the drive to solve again' : undefined}
            className="w-full py-2 bg-slate-800 hover:bg-slate-700 disabled:opacity-30 disabled:cursor-not-allowed text-white text-sm font-semibold rounded-lg border border-slate-700 transition-all flex items-center justify-center gap-2"
          >
            {optimizing
              ? <><Loader2 className="w-4 h-4 animate-spin" /> Optimizing...</>
              : <><Route className="w-4 h-4" /> Optimize on Measured Traffic</>}
          </button>
          {drive ? (
            <div className="flex gap-2">
              <select
                value={drive.speed}
                onChange={(e) => setDrive({ ...drive, speed: Number(e.target.value) })}
                className="flex-1 px-2 py-2 bg-slate-800 border border-slate-700 text-slate-300 rounded-lg text-xs"
                title="Simulation speed"
              >
                {SIM_SPEEDS.map(s => <option key={s} value={s}>{s}× speed</option>)}
              </select>
              <button
                onClick={() => setDrive(null)}
                className="flex-1 flex items-center justify-center gap-1.5 px-3 py-2 bg-slate-800 hover:bg-slate-700 border border-slate-700 text-slate-300 rounded-lg text-sm transition-colors"
              >
                <Square className="w-3.5 h-3.5" /> Stop Drive
              </button>
            </div>
          ) : (
            <button onClick={start} disabled={!driveable || optimizing}
                    title="Drive the plan on the measured traffic; trucks detour around active accidents and replan every 5 min while driving"
                    className="w-full bg-[#C8002F] hover:bg-[#a80027] disabled:bg-slate-800 disabled:text-slate-600 text-white font-semibold py-2.5 rounded-lg flex items-center justify-center gap-2 text-sm transition-colors">
              <Play className="w-4 h-4" /> Simulate Drive
            </button>
          )}
        </div>
      </aside>

      {/* ── Main ─────────────────────────────────────────── */}
      <main className="flex-1 overflow-y-auto bg-slate-950">
        {!results ? (
          <div className="h-full flex items-center justify-center p-8">
            <div className="text-center max-w-md">
              <div className="w-24 h-24 bg-slate-900 border border-slate-800 rounded-2xl flex items-center justify-center mx-auto mb-6">
                <Activity className="w-12 h-12 text-slate-800" />
              </div>
              <h2 className="text-white font-semibold text-xl mb-2">{optimizing ? 'Optimizing…' : 'Simulation'}</h2>
              <p className="text-slate-500 text-sm leading-relaxed">
                Set the stops and fleet on the left, pick a day and departure, validate the addresses and click
                Optimize on Measured Traffic. Then Simulate Drive drives that plan on the measured speeds, with
                accidents at historical crash hotspots, a replan every {REPLAN_INTERVAL_S / 60} minutes and, as a
                what-if, trucks using more energy than the model predicts.
              </p>
            </div>
          </div>
        ) : (
          <div className="p-6 space-y-5 max-w-6xl">
            <IncidentCard
              results={results}
              replan={replan}
              replanning={replanning}
              replanError={replanError}
              activeAccidents={activeAccidents}
              activeLegs={activeLegs}
              drive={drive}
              onFind={onFindHotspots}
              onToggle={onToggleAccident}
              onReset={onResetIncidents}
            />

            {opened && (SAVED_DEMO ? <SchematicRouteMap results={results} locations={locations} /> : <RouteMap
              results={results}
              locations={locations}
              replan={replan}
              activeAccidents={activeAccidents}
              activeLegs={activeLegs}
              drive={drive}
              onDriveReport={setReport}
              replanContext={replanContext}
            />)}

            {report && <DrainReport report={report} locations={locations} clock={clock} />}
          </div>
        )}
      </main>
    </div>
  );
}
