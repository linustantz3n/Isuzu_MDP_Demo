import React, { useEffect, useMemo, useState } from 'react';
import { BarChart3, Loader2, AlertCircle, Play, TrendingDown, ArrowUpDown, CheckCircle, XCircle } from 'lucide-react';
import BenchmarkCharts, { algoColor } from './BenchmarkCharts';
import BenchmarkRoutes from './BenchmarkRoutes';
import BenchmarkCompare from './BenchmarkCompare';
import BenchmarkBaselineChanges from './BenchmarkBaselineChanges';
import { API_BASE } from './api';

const FAMILY_LABELS = {
  naive: 'Naive',
  tsp_heuristic: 'TSP heuristic',
  exact: 'Exact',
  cluster_first: 'Cluster-first',
  vrp_heuristic: 'VRP heuristic',
  ortools: 'OR-Tools',
  baseline: 'Fixed order',
};

const OBJECTIVES = [
  { id: 'distance', label: 'Distance', gain: 'less distance' },
  { id: 'time', label: 'Time', gain: 'less drive time' },
  { id: 'energy', label: 'Energy', gain: 'less energy' },
  { id: 'blend', label: 'Blend', gain: 'less blended cost' },
];
const BLEND_METRICS = [
  { id: 'distance', label: 'Distance' },
  { id: 'time', label: 'Drive time' },
  { id: 'energy', label: 'Energy' },
];

const COLUMNS = [
  { key: 'label', label: 'Algorithm', get: r => r.label },
  { key: 'total_distance_mi', label: 'Distance (mi)', get: r => r.metrics?.total_distance_mi },
  { key: 'total_drive_time_min', label: 'Drive (min)', get: r => r.metrics?.total_drive_time_min },
  { key: 'total_energy_kwh', label: 'Energy (kWh)', get: r => r.metrics?.total_energy_kwh },
  { key: 'makespan_min', label: 'Makespan (min)', get: r => r.metrics?.makespan_min },
  { key: 'vehicles_used', label: 'Trucks', get: r => r.metrics?.vehicles_used },
  { key: 'max_stops_per_truck', label: 'Max stops', get: r => r.metrics?.max_stops_per_truck },
  { key: 'solve_time_ms', label: 'Solve (ms)', get: r => r.metrics?.solve_time_ms },
  { key: 'gap_to_best_objective_pct', label: 'vs Best', get: r => r.metrics?.gap_to_best_objective_pct },
  { key: 'gap_vs_ortools_objective_pct', label: 'vs OR-Tools', get: r => r.metrics?.gap_vs_ortools_objective_pct },
  { key: 'gap_to_optimal_objective_pct', label: 'vs Optimal', get: r => r.metrics?.gap_to_optimal_objective_pct, k1Only: true },
];

const productionTimeLimit = (numStops) => Math.max(5, Math.min(30, (numStops + 1) * 2));

const fmtPct = (v) => (v == null ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(1)}%`);
const pctClass = (v) => (v == null ? 'text-slate-600' : v <= 0.05 ? 'text-green-400' : v < 5 ? 'text-amber-400' : 'text-red-400');

export default function BenchmarkPanel() {
  const [scenarios, setScenarios] = useState([]);
  const [algorithms, setAlgorithms] = useState([]);
  const [scenarioId, setScenarioId] = useState('');
  const [selectedAlgos, setSelectedAlgos] = useState(new Set());
  const [kText, setKText] = useState('3');
  const [includeSmaller, setIncludeSmaller] = useState(false);
  const [preserveOrder, setPreserveOrder] = useState(false);
  const [fixedPlan, setFixedPlan] = useState(null);
  const [fixedPlanError, setFixedPlanError] = useState(null);
  const [timeLimit, setTimeLimit] = useState('');
  const [objectiveMode, setObjectiveMode] = useState('distance');
  const [blendWeights, setBlendWeights] = useState({ distance: 50, time: 25, energy: 25 });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);
  const [selectedK, setSelectedK] = useState(1);
  const [sort, setSort] = useState({ key: 'total_distance_mi', dir: 1 });

  useEffect(() => {
    Promise.all([
      fetch(`${API_BASE}/benchmark/scenarios`).then(r => r.json()),
      fetch(`${API_BASE}/benchmark/algorithms`).then(r => r.json()),
    ])
      .then(([s, a]) => {
        setScenarios(s.scenarios || []);
        setAlgorithms(a.algorithms || []);
        setSelectedAlgos(new Set((a.algorithms || []).map(x => x.id)));
        if (s.scenarios?.length) setScenarioId(s.scenarios[0].id);
      })
      .catch(() => setError('Could not reach the backend. Is route_optimizer_backend.py running on port 5001?'));
  }, []);

  const scenario = scenarios.find(s => s.id === scenarioId);
  const kLimit = scenario ? scenario.num_stops : 1;
  const effectiveK = Math.max(1, Math.min(Math.round(Number(kText)) || 1, kLimit));
  const perRunLimit = timeLimit ? Number(timeLimit) : (scenario ? productionTimeLimit(scenario.num_stops) : 5);
  const fleetRuns = includeSmaller ? effectiveK : 1;
  const estimateS = selectedAlgos.has('ortools') && !preserveOrder ? fleetRuns * perRunLimit : 1;

  // The fixed stop order is listed as soon as the option is on, before any run
  useEffect(() => {
    if (!preserveOrder || !scenarioId) { setFixedPlan(null); return undefined; }
    let cancelled = false;
    setFixedPlanError(null);
    fetch(`${API_BASE}/benchmark/fixed_plan?scenario_id=${encodeURIComponent(scenarioId)}&num_vehicles=${effectiveK}`)
      .then(r => r.json())
      .then(d => {
        if (cancelled) return;
        if (d.status !== 'ok') throw new Error(d.message || 'Could not load the fixed stop order');
        setFixedPlan(d);
      })
      .catch(e => { if (!cancelled) { setFixedPlan(null); setFixedPlanError(e.message); } });
    return () => { cancelled = true; };
  }, [preserveOrder, scenarioId, effectiveK]);

  const blendTotal = BLEND_METRICS.reduce((sum, m) => sum + blendWeights[m.id], 0);
  const blendInvalid = objectiveMode === 'blend' && blendTotal <= 0;

  const toggleAlgo = (id) => {
    const next = new Set(selectedAlgos);
    next.has(id) ? next.delete(id) : next.add(id);
    setSelectedAlgos(next);
  };

  const runBenchmark = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/benchmark/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          scenario_id: scenarioId,
          num_vehicles: effectiveK,
          include_smaller_fleets: includeSmaller,
          preserve_stop_order: preserveOrder,
          algorithms: [...selectedAlgos],
          ortools_time_limit_s: timeLimit ? Number(timeLimit) : null,
          objective: { mode: objectiveMode, weights: blendWeights },
        }),
      });
      const data = await res.json();
      if (data.status !== 'ok') throw new Error(data.message || 'Benchmark failed');
      setResult(data);
      setSelectedK(data.fleet_sizes.includes(selectedK) ? selectedK : data.max_vehicles);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  const tableRows = useMemo(() => {
    if (!result) return [];
    const col = COLUMNS.find(c => c.key === sort.key);
    return result.rows
      .filter(r => r.num_vehicles === selectedK)
      .sort((a, b) => {
        const va = col.get(a), vb = col.get(b);
        if (va == null) return 1;
        if (vb == null) return -1;
        return (va > vb ? 1 : va < vb ? -1 : 0) * sort.dir;
      });
  }, [result, selectedK, sort]);

  const objectiveGain = OBJECTIVES.find(o => o.id === result?.objective?.mode)?.gain || 'less distance';
  const columns = COLUMNS.filter(c => !c.k1Only || selectedK === 1);
  const toggleSort = (key) => setSort(s => ({ key, dir: s.key === key ? -s.dir : 1 }));

  return (
    <div className="flex flex-1 min-h-0">
      {/* ── Sidebar ──────────────────────────────────────── */}
      <aside className="w-80 bg-slate-900 border-r border-slate-800 flex flex-col flex-shrink-0">
        <div className="flex-1 overflow-y-auto p-5 space-y-6">
          <section>
            <h3 className="text-[11px] font-semibold text-slate-500 uppercase tracking-widest mb-3">Scenario</h3>
            <select
              value={scenarioId}
              onChange={e => setScenarioId(e.target.value)}
              className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-[#C8002F]"
            >
              {scenarios.map(s => (
                <option key={s.id} value={s.id}>{s.name} ({s.num_stops} stops)</option>
              ))}
            </select>
            {scenario && (
              <p className="text-xs text-slate-500 mt-2 leading-relaxed">
                {scenario.description}<br />
                <span className="text-slate-600">Depot: {scenario.depot}</span>
                {scenario.total_demand != null && (
                  <><br /><span className="text-slate-600">Total demand: {scenario.total_demand} units</span></>
                )}
              </p>
            )}
          </section>

          <section>
            <h3 className="text-[11px] font-semibold text-slate-500 uppercase tracking-widest mb-3">Fleet Size</h3>
            <div className="flex items-center gap-3">
              <label htmlFor="fleet-k" className="text-sm text-slate-300">Run K =</label>
              <input
                id="fleet-k"
                type="number"
                min={1}
                max={Math.max(1, kLimit)}
                value={kText}
                onChange={e => setKText(e.target.value)}
                onBlur={() => setKText(String(effectiveK))}
                className="w-20 bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-sm font-mono text-white focus:outline-none focus:border-[#C8002F]"
              />
              <span className="text-xs text-slate-600">of up to {Math.max(1, kLimit)} trucks</span>
            </div>
            <label className="flex items-start gap-2.5 cursor-pointer mt-3">
              <input
                type="checkbox"
                checked={includeSmaller}
                onChange={e => setIncludeSmaller(e.target.checked)}
                className="mt-0.5 accent-[#C8002F]"
              />
              <span className="text-sm text-slate-300">
                Also run every smaller fleet
                <span className="block text-[11px] text-slate-600">K = 1 to {effectiveK}, to see how results change with fleet size</span>
              </span>
            </label>
            <p className="text-xs text-slate-600 mt-2">
              Every truck is capped at ⌈stops / K⌉ stops, the same limit OR-Tools enforces.
            </p>
          </section>

          <section>
            <h3 className="text-[11px] font-semibold text-slate-500 uppercase tracking-widest mb-3">Stop Order</h3>
            <label className="flex items-start gap-2.5 cursor-pointer">
              <input
                type="checkbox"
                checked={preserveOrder}
                onChange={e => setPreserveOrder(e.target.checked)}
                className="mt-0.5 accent-[#C8002F]"
              />
              <span className="text-sm text-slate-300">
                Preserve stop order
                <span className="block text-[11px] text-slate-600 leading-relaxed">
                  Locks every truck to the fixed plan below: each truck keeps its assigned stops and visits them in
                  the same order, and nothing is added, moved or reordered. Every algorithm returns this plan unchanged.
                </span>
              </span>
            </label>
            {preserveOrder && (
              <div className="mt-3 bg-slate-950/60 border border-slate-800 rounded-lg p-3">
                {fixedPlanError && <p className="text-xs text-red-400">{fixedPlanError}</p>}
                {!fixedPlan && !fixedPlanError && <p className="text-xs text-slate-500">Loading...</p>}
                {fixedPlan && (
                  <>
                    <p className="text-xs text-slate-400 mb-2">
                      Fixed order for K = {fixedPlan.num_vehicles}: the stop list in its listed order, split into
                      consecutive groups, one per truck. Each truck visits its stops in the order shown.
                    </p>
                    <div className="max-h-64 overflow-y-auto space-y-2.5 pr-1">
                      {fixedPlan.trucks.map(t => (
                        <div key={t.truck}>
                          <p className="text-xs font-medium text-white">
                            Truck {t.truck}
                            <span className="text-slate-600 font-normal"> · {t.stops.length} stop{t.stops.length === 1 ? '' : 's'}</span>
                          </p>
                          <ol className="mt-0.5 space-y-0.5">
                            {t.stops.map((s, i) => (
                              <li key={s.index} className="text-[11px] text-slate-400 flex gap-1.5">
                                <span className="text-slate-600 w-4 text-right flex-shrink-0">{i + 1}</span>
                                <span className="min-w-0">{s.name}</span>
                              </li>
                            ))}
                          </ol>
                        </div>
                      ))}
                    </div>
                    {includeSmaller && effectiveK > 1 && (
                      <p className="text-[11px] text-slate-600 mt-2">Smaller fleets use the same rule at their own K.</p>
                    )}
                  </>
                )}
              </div>
            )}
          </section>

          <section>
            <h3 className="text-[11px] font-semibold text-slate-500 uppercase tracking-widest mb-3">Optimize For</h3>
            <div className="grid grid-cols-4 gap-1 bg-slate-800 rounded-lg p-1">
              {OBJECTIVES.map(o => (
                <button
                  key={o.id}
                  onClick={() => setObjectiveMode(o.id)}
                  className={`py-1.5 rounded-md text-xs font-medium transition-colors ${
                    objectiveMode === o.id ? 'bg-[#C8002F] text-white' : 'text-slate-400 hover:text-white'
                  }`}
                >
                  {o.label}
                </button>
              ))}
            </div>
            {objectiveMode === 'blend' && (
              <div className="mt-3 space-y-2.5">
                {BLEND_METRICS.map(m => (
                  <div key={m.id}>
                    <div className="flex items-center justify-between text-xs mb-0.5">
                      <span className="text-slate-300">{m.label}</span>
                      <span className="font-mono text-slate-400">
                        {blendTotal > 0 ? Math.round(blendWeights[m.id] / blendTotal * 100) : 0}%
                      </span>
                    </div>
                    <input
                      type="range"
                      min={0}
                      max={100}
                      step={5}
                      value={blendWeights[m.id]}
                      onChange={e => setBlendWeights({ ...blendWeights, [m.id]: Number(e.target.value) })}
                      className="w-full accent-[#C8002F]"
                    />
                  </div>
                ))}
                {blendInvalid && <p className="text-xs text-red-400">Set at least one weight above zero.</p>}
              </div>
            )}
            <p className="text-xs text-slate-600 mt-2 leading-relaxed">
              What the algorithms minimize. Distance, time and energy are always measured and shown.
              Sweep and K-means choose which stops go on each truck by location, so the objective
              only changes the stop order within each truck.
            </p>
          </section>

          <section>
            <h3 className="text-[11px] font-semibold text-slate-500 uppercase tracking-widest mb-3">Algorithms</h3>
            <div className="space-y-1.5">
              {algorithms.map(a => (
                <label key={a.id} className="flex items-start gap-2.5 cursor-pointer group" title={a.description}>
                  <input
                    type="checkbox"
                    checked={selectedAlgos.has(a.id)}
                    onChange={() => toggleAlgo(a.id)}
                    className="mt-0.5 accent-[#C8002F]"
                  />
                  <span className="flex-1">
                    <span className="text-sm text-slate-300 group-hover:text-white">{a.label}</span>
                    <span className="block text-[11px] text-slate-600">
                      {FAMILY_LABELS[a.family]} · {a.vehicles === 'single' ? 'K=1 only' : a.vehicles === 'fleet' ? 'K≥2 only' : 'any K'}
                    </span>
                  </span>
                </label>
              ))}
            </div>
          </section>

          <section>
            <h3 className="text-[11px] font-semibold text-slate-500 uppercase tracking-widest mb-3">OR-Tools Time Limit</h3>
            <input
              type="number"
              min={1}
              max={60}
              placeholder={`Production (${scenario ? productionTimeLimit(scenario.num_stops) : '—'} s)`}
              value={timeLimit}
              onChange={e => setTimeLimit(e.target.value)}
              className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white placeholder-slate-500 focus:outline-none focus:border-[#C8002F]"
            />
            <p className="text-xs text-slate-600 mt-1">
              Guided Local Search always runs to its time limit. Leave blank to benchmark the exact production config.
            </p>
          </section>
        </div>

        <div className="p-5 border-t border-slate-800 space-y-3">
          {error && (
            <div className="flex items-start gap-2 text-xs text-red-400 bg-red-950/40 border border-red-900 rounded-lg p-3">
              <AlertCircle className="w-4 h-4 flex-shrink-0 mt-px" />
              <span>{error}</span>
            </div>
          )}
          <button
            onClick={runBenchmark}
            disabled={loading || !scenarioId || selectedAlgos.size === 0 || blendInvalid}
            className="w-full bg-[#C8002F] hover:bg-[#a80027] disabled:bg-slate-800 disabled:text-slate-600 text-white font-semibold py-2.5 rounded-lg flex items-center justify-center gap-2 text-sm transition-colors"
          >
            {loading
              ? <><Loader2 className="w-4 h-4 animate-spin" /> Running...</>
              : <><Play className="w-4 h-4" /> Run Benchmark</>}
          </button>
          <p className="text-[11px] text-slate-600 text-center">Estimated run time ~{estimateS} s</p>
        </div>
      </aside>

      {/* ── Main ─────────────────────────────────────────── */}
      <main className="flex-1 overflow-y-auto bg-slate-950">
        {!result ? (
          <div className="h-full flex items-center justify-center p-8">
            <div className="text-center max-w-md">
              <div className="w-24 h-24 bg-slate-900 border border-slate-800 rounded-2xl flex items-center justify-center mx-auto mb-6">
                {loading
                  ? <Loader2 className="w-12 h-12 text-slate-700 animate-spin" />
                  : <BarChart3 className="w-12 h-12 text-slate-800" />}
              </div>
              <h2 className="text-white font-semibold text-xl mb-2">
                {loading ? 'Running benchmark...' : 'Algorithm Comparison'}
              </h2>
              <p className="text-slate-500 text-sm leading-relaxed">
                Runs every selected algorithm on the same saved scenario for each fleet size and scores
                them identically from the scenario's distance and time matrices. Answers: how much better
                is OR-Tools than naive routing, and on which dimensions?
              </p>
            </div>
          </div>
        ) : (
          <div className="p-6 space-y-5 max-w-6xl">
            <div>
              <h2 className="text-white font-semibold text-lg">{result.scenario.name}</h2>
              <p className="text-slate-500 text-xs">
                {result.scenario.num_stops} stops · {result.fleet_sizes.length === 1 ? `K = ${result.fleet_sizes[0]}` : `K = ${result.fleet_sizes.join(', ')}`} · {result.preserve_stop_order ? 'stop order locked · ' : ''}objective: {result.objective.label} · closed routes from depot
              </p>
            </div>

            {/* Headline cards */}
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
              {result.headline.map(h => (
                <button
                  key={h.num_vehicles}
                  onClick={() => setSelectedK(h.num_vehicles)}
                  className={`text-left bg-slate-900 border rounded-xl p-4 transition-colors ${
                    selectedK === h.num_vehicles ? 'border-[#C8002F]' : 'border-slate-800 hover:border-slate-700'
                  }`}
                >
                  <p className="text-[11px] font-semibold text-slate-500 uppercase tracking-widest mb-2">
                    {h.num_vehicles} truck{h.num_vehicles > 1 ? 's' : ''}
                  </p>
                  {h.vs_naive && (
                    <div className="flex items-baseline gap-2">
                      <TrendingDown className="w-4 h-4 text-green-400 self-center" />
                      <span className="text-2xl font-bold text-white">{h.vs_naive.objective_saved_pct.toFixed(1)}%</span>
                      <span className="text-xs text-slate-400">{objectiveGain}</span>
                    </div>
                  )}
                  {h.vs_naive && (
                    <p className="text-xs text-slate-400 mt-1">
                      vs {h.vs_naive.label}:{' '}
                      {[
                        ['distance', h.vs_naive.distance_saved_pct, 'distance'],
                        ['time', h.vs_naive.time_saved_pct, 'drive time'],
                        ['energy', h.vs_naive.energy_saved_pct, 'energy'],
                      ].filter(([id]) => result.objective.mode === 'blend' || id !== result.objective.mode)
                        .map(([, pct, name]) => `${pct.toFixed(1)}% ${name}`).join(' · ')}
                      <span className="text-slate-600"> ({h.vs_naive.distance_saved_mi} mi, {h.vs_naive.energy_saved_kwh} kWh)</span>
                    </p>
                  )}
                  {h.vs_best_heuristic && (
                    <p className="text-xs text-slate-500 mt-1">
                      vs best other heuristic ({h.vs_best_heuristic.label}):{' '}
                      <span className="text-slate-300">{h.vs_best_heuristic.objective_saved_pct.toFixed(1)}% {objectiveGain.replace('less ', '')}</span>
                    </p>
                  )}
                </button>
              ))}
            </div>

            {/* K selector (only useful when more than one fleet size was run) */}
            {result.fleet_sizes.length > 1 && <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs text-slate-500 mr-1">Fleet size</span>
              {result.fleet_sizes.map(k => (
                <button
                  key={k}
                  onClick={() => setSelectedK(k)}
                  className={`px-3 py-1 rounded-full text-xs font-medium transition-colors ${
                    selectedK === k ? 'bg-[#C8002F] text-white' : 'bg-slate-800 text-slate-400 hover:text-white'
                  }`}
                >
                  K={k}
                </button>
              ))}
            </div>}

            {/* Results table */}
            <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-slate-800">
                    {columns.map(c => (
                      <th
                        key={c.key}
                        onClick={() => toggleSort(c.key)}
                        className={`px-3 py-2.5 text-[11px] font-semibold text-slate-500 uppercase tracking-wider cursor-pointer hover:text-slate-300 whitespace-nowrap ${
                          c.key === 'label' ? 'text-left' : 'text-right'
                        }`}
                      >
                        <span className="inline-flex items-center gap-1">
                          {c.label}
                          <ArrowUpDown className={`w-3 h-3 ${sort.key === c.key ? 'text-slate-300' : 'text-slate-700'}`} />
                        </span>
                      </th>
                    ))}
                    <th className="px-3 py-2.5 text-[11px] font-semibold text-slate-500 uppercase tracking-wider text-center">Feasible</th>
                  </tr>
                </thead>
                <tbody>
                  {tableRows.map(r => {
                    const m = r.metrics;
                    return (
                      <tr key={r.algorithm} className={`border-b border-slate-800/60 ${r.algorithm === 'ortools' ? 'bg-[#C8002F]/10' : r.family === 'baseline' ? 'bg-amber-400/5' : ''}`}>
                        <td className="px-3 py-2.5">
                          <div className="flex items-center gap-2">
                            <span className="w-2.5 h-2.5 rounded-full flex-shrink-0" style={{ backgroundColor: algoColor(r) }} />
                            <span className="text-white whitespace-nowrap">{r.label}</span>
                          </div>
                          <span className="text-[11px] text-slate-600 pl-[18px]">{FAMILY_LABELS[r.family]}</span>
                        </td>
                        {r.error ? (
                          <td colSpan={columns.length} className="px-3 py-2.5 text-red-400 text-xs">Error: {r.error}</td>
                        ) : (
                          <>
                            <td className="px-3 py-2.5 text-right font-mono text-white">{m.total_distance_mi.toFixed(2)}</td>
                            <td className="px-3 py-2.5 text-right font-mono text-slate-300">{m.total_drive_time_min.toFixed(1)}</td>
                            <td className="px-3 py-2.5 text-right font-mono text-slate-300">{m.total_energy_kwh.toFixed(1)}</td>
                            <td className="px-3 py-2.5 text-right font-mono text-slate-300">{m.makespan_min.toFixed(1)}</td>
                            <td className="px-3 py-2.5 text-right font-mono text-slate-300">{m.vehicles_used}</td>
                            <td className="px-3 py-2.5 text-right font-mono text-slate-300">{m.max_stops_per_truck}/{m.stop_cap}</td>
                            <td className="px-3 py-2.5 text-right font-mono text-slate-500">
                              {r.locked ? 'locked' : m.solve_time_ms.toLocaleString()}
                            </td>
                            <td className={`px-3 py-2.5 text-right font-mono ${pctClass(m.gap_to_best_objective_pct)}`}>{fmtPct(m.gap_to_best_objective_pct)}</td>
                            <td className={`px-3 py-2.5 text-right font-mono ${pctClass(m.gap_vs_ortools_objective_pct)}`}>{fmtPct(m.gap_vs_ortools_objective_pct)}</td>
                            {selectedK === 1 && (
                              <td className={`px-3 py-2.5 text-right font-mono ${pctClass(m.gap_to_optimal_objective_pct)}`}>{fmtPct(m.gap_to_optimal_objective_pct)}</td>
                            )}
                          </>
                        )}
                        <td className="px-3 py-2.5 text-center">
                          {m?.feasible
                            ? <CheckCircle className="w-4 h-4 text-green-400 inline" />
                            : <span title={m?.issues?.join('; ') || r.error}><XCircle className="w-4 h-4 text-red-400 inline" /></span>}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              <p className="text-[11px] text-slate-600 px-3 py-2">
                Gaps are the {result.objective.label} (the quantity being minimized) relative to the reference in the same fleet size.
                Makespan = longest single truck's drive time.
                {selectedK === 1 && ' "vs Optimal" compares against Held-Karp, the exact single-truck optimum.'}
                {' '}Energy is scored with the production Model E regression ({result.energy.temperature_c}°C, {result.energy.loaded_weight_lbs.toLocaleString()} lb loaded / {result.energy.base_weight_lbs.toLocaleString()} lb empty, each truck lighter after every stop).
                {!result.energy.has_elevation && ' This scenario has no elevation data, so energy assumes flat terrain.'}
              </p>
            </div>

            <BenchmarkCharts result={result} selectedK={selectedK} />

            <BenchmarkBaselineChanges
              rows={result.rows.filter(r => r.num_vehicles === selectedK && r.metrics)}
              selectedK={selectedK}
            />

            <BenchmarkCompare
              rows={result.rows.filter(r => r.num_vehicles === selectedK && r.metrics)}
              locations={result.scenario.locations}
              selectedK={selectedK}
            />

            <BenchmarkRoutes rows={tableRows} locations={result.scenario.locations} selectedK={selectedK} />
          </div>
        )}
      </main>
    </div>
  );
}
