import React, { useEffect, useMemo, useState } from 'react';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine, Legend
} from 'recharts';
import {
  Activity, Loader2, AlertCircle, Play, CheckCircle, ArrowRight, Shuffle, MinusCircle, Home, BatteryWarning
} from 'lucide-react';
import BenchmarkRouteMap, { truckColor } from './BenchmarkRouteMap';
import { API_BASE } from './api';

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

const INPUT = 'w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-sm font-mono text-white focus:outline-none focus:border-[#C8002F]';
const SECTION_TITLE = 'text-[11px] font-semibold text-slate-500 uppercase tracking-widest mb-3';

const nameOf = (locations, i) => (i === 0 ? 'Depot' : locations[i].name || locations[i].address);

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
      <h3 className="text-sm font-semibold text-white">Battery after each leg · Truck {truck.vehicle}</h3>
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

function EventLog({ truck, locations }) {
  return (
    <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-x-auto">
      <div className="px-4 pt-4 pb-2">
        <h3 className="text-sm font-semibold text-white">Decisions at each stop · Truck {truck.vehicle}</h3>
        <p className="text-slate-500 text-xs mt-0.5">
          After every delivery the truck predicts what the rest of its route needs (at the worst drain it has seen)
          and compares it with its battery above the reserve.
        </p>
      </div>
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-slate-800 text-[11px] font-semibold text-slate-500 uppercase tracking-wider">
            <th className="px-3 py-2 text-left">Stop</th>
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
            const short = e.predicted_need_kwh > e.budget_kwh;
            return (
              <tr key={i} className="border-b border-slate-800/60 align-top">
                <td className="px-3 py-2 text-white">{e.name}</td>
                <td className="px-3 py-2 text-right font-mono text-slate-300">{e.soc_pct}%</td>
                <td className="px-3 py-2 text-right font-mono text-slate-300">{e.drain_estimate}×</td>
                <td className={`px-3 py-2 text-right font-mono ${short ? 'text-amber-300' : 'text-slate-300'}`}>{e.predicted_need_kwh}</td>
                <td className="px-3 py-2 text-right font-mono text-slate-300">{e.budget_kwh}</td>
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
    </div>
  );
}

export default function BenchmarkSimulation() {
  const [scenarios, setScenarios] = useState([]);
  const [scenarioId, setScenarioId] = useState('');
  const [k, setK] = useState('4');
  const [batteryKwh, setBatteryKwh] = useState('110');
  const [reserve, setReserve] = useState('20');
  const [drain, setDrain] = useState(1.3);
  const [useEvent, setUseEvent] = useState(false);
  const [eventAfter, setEventAfter] = useState('2');
  const [eventDrain, setEventDrain] = useState(1.6);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);
  const [truckIdx, setTruckIdx] = useState(0);
  const [mapView, setMapView] = useState('actual');

  useEffect(() => {
    fetch(`${API_BASE}/benchmark/scenarios`)
      .then(r => r.json())
      .then(s => {
        setScenarios(s.scenarios || []);
        const first = (s.scenarios || []).find(x => x.id === 'case1_plymouth_mi') || s.scenarios?.[0];
        if (first) setScenarioId(first.id);
      })
      .catch(() => setError('Could not reach the backend. Is route_optimizer_backend.py running on port 5001?'));
  }, []);

  const scenario = scenarios.find(s => s.id === scenarioId);

  const run = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/benchmark/simulate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          scenario_id: scenarioId,
          num_vehicles: Number(k),
          energy_params: { battery_kwh: Number(batteryKwh), battery_reserve_pct: Number(reserve) },
          drain: { factor: drain, event: useEvent ? { after_stop: Number(eventAfter), factor: eventDrain } : null },
          ortools_time_limit_s: 3,
        }),
      });
      const data = await res.json();
      if (data.status !== 'ok') throw new Error(data.message || 'Simulation failed');
      setResult(data);
      // Open on the truck that had to change plans, if any
      const changed = data.trucks.findIndex(t => t.replans > 0);
      setTruckIdx(changed >= 0 ? changed : 0);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  const truck = result?.trucks[truckIdx];
  const locations = result?.scenario.locations;
  const s = result?.summary;
  const reservePct = result?.energy.battery_reserve_pct;
  const mapRoutes = useMemo(
    () => result?.trucks.map(t => (mapView === 'actual' ? t.actual_route : t.planned_route)),
    [result, mapView]
  );

  return (
    <div className="flex flex-1 min-h-0">
      {/* ── Sidebar ──────────────────────────────────────── */}
      <aside className="w-80 bg-slate-900 border-r border-slate-800 flex flex-col flex-shrink-0">
        <div className="flex-1 overflow-y-auto p-5 space-y-6">
          <section>
            <h3 className={SECTION_TITLE}>Scenario</h3>
            <select value={scenarioId} onChange={e => setScenarioId(e.target.value)}
                    className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-[#C8002F]">
              {scenarios.map(x => <option key={x.id} value={x.id}>{x.name} ({x.num_stops} stops)</option>)}
            </select>
            <label className="flex items-center gap-3 mt-3">
              <span className="text-sm text-slate-300 w-24">Trucks</span>
              <input type="number" min={1} max={scenario?.num_stops || 1} value={k}
                     onChange={e => setK(e.target.value)} className={INPUT} />
            </label>
          </section>

          <section>
            <h3 className={SECTION_TITLE}>Battery</h3>
            <label className="flex items-center gap-3">
              <span className="text-sm text-slate-300 w-24">Capacity</span>
              <input type="number" min={1} value={batteryKwh} onChange={e => setBatteryKwh(e.target.value)} className={INPUT} />
              <span className="text-xs text-slate-500">kWh</span>
            </label>
            <label className="flex items-center gap-3 mt-2">
              <span className="text-sm text-slate-300 w-24">Reserve</span>
              <input type="number" min={0} max={99} value={reserve} onChange={e => setReserve(e.target.value)} className={INPUT} />
              <span className="text-xs text-slate-500">%</span>
            </label>
            <p className="text-xs text-slate-600 mt-2 leading-relaxed">
              The starting plan keeps every truck above the reserve using the energy model. The simulation then
              drives it with more drain than the model expected.
            </p>
          </section>

          <section>
            <h3 className={SECTION_TITLE}>Drain</h3>
            <div className="flex items-center justify-between text-xs mb-0.5">
              <span className="text-slate-300">Actual vs model energy</span>
              <span className="font-mono text-slate-300">{drain.toFixed(2)}×</span>
            </div>
            <input type="range" min={1} max={2} step={0.05} value={drain}
                   onChange={e => setDrain(Number(e.target.value))} className="w-full accent-[#C8002F]" />
            <p className="text-[11px] text-slate-600">1.30× = every leg uses 30% more than planned.</p>

            <label className="flex items-start gap-2.5 cursor-pointer mt-4">
              <input type="checkbox" checked={useEvent} onChange={e => setUseEvent(e.target.checked)} className="mt-0.5 accent-[#C8002F]" />
              <span className="text-sm text-slate-300">
                Mid-route event
                <span className="block text-[11px] text-slate-600">Drain changes partway through (cold snap, traffic, headwind)</span>
              </span>
            </label>
            {useEvent && (
              <div className="mt-3 space-y-2 pl-6">
                <label className="flex items-center gap-3">
                  <span className="text-sm text-slate-300 w-24">After stop</span>
                  <input type="number" min={0} value={eventAfter} onChange={e => setEventAfter(e.target.value)} className={INPUT} />
                </label>
                <div>
                  <div className="flex items-center justify-between text-xs mb-0.5">
                    <span className="text-slate-300">New drain</span>
                    <span className="font-mono text-slate-300">{eventDrain.toFixed(2)}×</span>
                  </div>
                  <input type="range" min={1} max={2.5} step={0.05} value={eventDrain}
                         onChange={e => setEventDrain(Number(e.target.value))} className="w-full accent-[#C8002F]" />
                </div>
              </div>
            )}
          </section>
        </div>

        <div className="p-5 border-t border-slate-800 space-y-3">
          {error && (
            <div className="flex items-start gap-2 text-xs text-red-400 bg-red-950/40 border border-red-900 rounded-lg p-3">
              <AlertCircle className="w-4 h-4 flex-shrink-0 mt-px" />
              <span>{error}</span>
            </div>
          )}
          <button onClick={run} disabled={loading || !scenarioId}
                  className="w-full bg-[#C8002F] hover:bg-[#a80027] disabled:bg-slate-800 disabled:text-slate-600 text-white font-semibold py-2.5 rounded-lg flex items-center justify-center gap-2 text-sm transition-colors">
            {loading ? <><Loader2 className="w-4 h-4 animate-spin" /> Simulating...</> : <><Play className="w-4 h-4" /> Simulate Drive</>}
          </button>
          <p className="text-[11px] text-slate-600 text-center">Each replan takes about 1 s</p>
        </div>
      </aside>

      {/* ── Main ─────────────────────────────────────────── */}
      <main className="flex-1 overflow-y-auto bg-slate-950">
        {!result ? (
          <div className="h-full flex items-center justify-center p-8">
            <div className="text-center max-w-md">
              <div className="w-24 h-24 bg-slate-900 border border-slate-800 rounded-2xl flex items-center justify-center mx-auto mb-6">
                {loading ? <Loader2 className="w-12 h-12 text-slate-700 animate-spin" /> : <Activity className="w-12 h-12 text-slate-800" />}
              </div>
              <h2 className="text-white font-semibold text-xl mb-2">{loading ? 'Simulating...' : 'Drive Simulation'}</h2>
              <p className="text-slate-500 text-sm leading-relaxed">
                What if trucks use more energy than the model predicts? Each truck drives its plan and, at every
                stop, checks whether its battery still covers the rest of the route plus the reserve. If not it
                reorders, and if that is not enough it drops the fewest stops it can.
              </p>
            </div>
          </div>
        ) : (
          <div className="p-6 space-y-5 max-w-6xl">
            <div>
              <h2 className="text-white font-semibold text-lg">{result.scenario.name}</h2>
              <p className="text-slate-500 text-xs">
                {result.num_vehicles} trucks · {result.energy.battery_kwh} kWh battery · {reservePct}% reserve ·
                drain {result.drain.factor}×
                {result.drain.event && `, ${result.drain.event.factor}× after stop ${result.drain.event.after_stop}`}
              </p>
            </div>

            <div className="grid grid-cols-2 xl:grid-cols-4 gap-3">
              <Stat label="Stops served" value={`${s.served}/${s.stops}`}
                    sub={s.dropped ? `${s.dropped} dropped` : 'none dropped'} warn={s.dropped > 0} />
              <Stat label="Replans" value={s.replans} sub="reorders, drops and early returns" />
              <Stat label="Below reserve, replanning" value={s.trucks_below_reserve}
                    sub={`of ${result.trucks.length} trucks`} warn={s.trucks_below_reserve > 0} />
              <Stat label="Below reserve, no replanning" value={s.blind_trucks_below_reserve}
                    sub={s.blind_trucks_ran_out ? `${s.blind_trucks_ran_out} would run out` : 'same drain, original plan'}
                    warn={s.blind_trucks_below_reserve > 0} />
            </div>

            {/* Truck selector */}
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs text-slate-500 mr-1">Truck</span>
              {result.trucks.map((t, i) => {
                const color = truckColor(i);
                const active = i === truckIdx;
                return (
                  <button key={t.vehicle} onClick={() => setTruckIdx(i)}
                          className={`flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-medium border transition-colors ${active ? 'text-white' : 'border-slate-700 text-slate-400 hover:text-white'}`}
                          style={active ? { backgroundColor: color + '33', borderColor: color } : {}}>
                    <span className="w-2 h-2 rounded-full" style={{ backgroundColor: color }} />
                    Truck {t.vehicle}
                    {t.dropped.length > 0 && <span className="text-amber-300">· −{t.dropped.length}</span>}
                    {t.below_reserve && <BatteryWarning className="w-3 h-3 text-red-300" />}
                  </button>
                );
              })}
            </div>

            {truck && (
              <>
                <div className="bg-slate-900 border border-slate-800 rounded-xl p-4 text-sm">
                  <p className="text-slate-400">
                    Planned {truck.planned_route.length - 2} stops, served {truck.served.length}
                    {truck.dropped.length > 0 && <>; dropped{' '}
                      {truck.dropped.map(d => <span key={d} className="line-through text-slate-300 mr-1.5">{nameOf(locations, d)}</span>)}
                    </>}
                    .
                  </p>
                  <p className="text-slate-400 mt-1 flex items-center gap-1.5 flex-wrap">
                    <CheckCircle className="w-3.5 h-3.5 text-green-400" />
                    Ends at <span className="font-mono text-white">{truck.final_soc_pct}%</span>
                    (lowest {truck.min_soc_pct}%) · planned {truck.planned.final_soc_pct}% · without replanning{' '}
                    <span className={`font-mono ${truck.blind.below_reserve ? 'text-amber-300' : 'text-white'}`}>
                      {truck.blind.ran_out_at != null ? 'runs out' : `${truck.blind.final_soc_pct}%`}
                    </span>
                  </p>
                </div>
                <SocChart truck={truck} reserve={reservePct} />
                <EventLog truck={truck} locations={locations} />
              </>
            )}

            <div className="space-y-2">
              <div className="flex items-center gap-2">
                <h3 className="text-sm font-semibold text-white mr-2">Routes</h3>
                {[['actual', 'As driven'], ['planned', 'As planned']].map(([id, label]) => (
                  <button key={id} onClick={() => setMapView(id)}
                          className={`px-3 py-1 rounded-full text-xs font-medium transition-colors ${mapView === id ? 'bg-[#C8002F] text-white' : 'bg-slate-800 text-slate-400 hover:text-white'}`}>
                    {label}
                  </button>
                ))}
              </div>
              <BenchmarkRouteMap key={mapView} routes={mapRoutes} locations={locations} />
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
