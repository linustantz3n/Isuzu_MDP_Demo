import React, { useState, useEffect, useRef } from 'react';
import { MapPin, Plus, Trash2, Route, Navigation, Loader2, AlertCircle, CheckCircle, Download, Home, Truck, Zap, Clock, TrendingDown, User, BarChart3, Star, AlertTriangle, ArrowLeftRight } from 'lucide-react';
import RouteMap from './RouteMap';
import FleetCharts from './FleetCharts';
import AIChatbot from './AIChatbot';
import BenchmarkPanel from './BenchmarkPanel';
import GoalsPanel from './GoalsPanel';
import PlanComparison from './PlanComparison';
import { DEFAULT_GOALS, goalsSummary } from './goals';
import { planOf, metricRows, impactText, shortPlace, formatClock } from './planDiff';
import { API_BASE } from './api';

const locationsKey = (locs) => locs.map(l => `${l.lat},${l.lng}`).join('|');
const MAX_VERSIONS = 12;

// Which sidebar section a chat change lands in, so it can flash.
const AXIS_SECTION = {
  goals: 'goals', priority_stops: 'goals', depot: 'stops', addresses: 'stops',
  vehicle_count: 'vehicles', battery_packs: 'vehicles', vehicle_weight_lbs: 'vehicles', fleet: 'vehicles',
  temperature_c: 'conditions', unload_time_min: 'conditions', has_climate_control: 'conditions',
};

const DEMO_ADDRESSES = [
  'Detroit, MI',
  'Ann Arbor, MI',
  'Flint, MI',
  'Lansing, MI',
  'Grand Rapids, MI',
  'Kalamazoo, MI',
  'Battle Creek, MI',
  'Jackson, MI',
  'Dearborn, MI',
  'Livonia, MI'
];

function RouteOptimizer() {
  const [activeTab, setActiveTab] = useState('solver');
  const [addresses, setAddresses] = useState(['', '', '']);
  const [depot, setDepot] = useState('');
  const useDepot = true;
  const [validatedLocations, setValidatedLocations] = useState([]);
  const [validatedDepot, setValidatedDepot] = useState(null);
  const [loading, setLoading] = useState(false);
  const [validating, setValidating] = useState(false);
  const [error, setError] = useState(null);
  const [results, setResults] = useState(null);
  const [vehicles, setVehicles] = useState([
    { id: 1, name: 'Vehicle 1', weight_lbs: 13000, battery_packs: 4, battery_kwh: 80 }
  ]);
  const [lastLocations, setLastLocations] = useState([]);
  // 'auto' lets the backend price Held-Karp before running it; the chatbot can
  // pin a specific solver via the `solver` axis.
  const [solver, setSolver] = useState('auto');
  const [energyParams, setEnergyParams] = useState({
    temperature_c: 20,
    total_weight_lbs: 19500,
    base_weight_lbs: 7000,
    unload_time_min: 30,
    has_climate_control: true
  });
  // What the solver optimizes for. The assistant picks these from plain language.
  const [goals, setGoals] = useState(DEFAULT_GOALS);
  const [goalsSetBy, setGoalsSetBy] = useState(null);
  const [priorityStops, setPriorityStops] = useState([]);
  // Every solve is kept as a version so any two can be compared side by side.
  const [versions, setVersions] = useState([]);
  const [compareBase, setCompareBase] = useState(null);
  const [showCompare, setShowCompare] = useState(true);
  const [stale, setStale] = useState(null);
  const [resolvingNote, setResolvingNote] = useState(null);
  const [flash, setFlash] = useState({});
  // The open chat panel takes the right edge; on wide screens the page moves over for it.
  const [chatDocked, setChatDocked] = useState(false);
  const dockRoom = chatDocked ? 'xl:mr-[31.5rem]' : '';
  const [resolveRequest, setResolveRequest] = useState(null);
  const resolveWaiters = useRef({});
  const mainRef = useRef(null);

  const addVehicle = () => {
    const nextId = vehicles.length > 0 ? Math.max(...vehicles.map(v => v.id)) + 1 : 1;
    setVehicles([...vehicles, { id: nextId, name: `Vehicle ${nextId}`, weight_lbs: 13000, battery_packs: 4, battery_kwh: 80 }]);
    setResults(null);
  };

  const removeVehicle = (id) => {
    if (vehicles.length > 1) {
      setVehicles(vehicles.filter(v => v.id !== id));
      setResults(null);
    }
  };

  // Each battery pack weighs ~200 kg (440.9 lbs); default is 4 packs at 13,000 lbs baseline
  const PACK_WEIGHT_LBS = 200 * 2.20462; // ≈ 440.9 lbs per pack
  const BASE_WEIGHT_NO_BATTERY = 13000 - 4 * PACK_WEIGHT_LBS; // chassis weight without batteries

  const updateVehicle = (id, field, value) => {
    setVehicles(vehicles.map(v => {
      if (v.id !== id) return v;
      if (field === 'battery_packs') {
        const packs = Math.max(3, Math.min(9, value));
        const kwh = packs * 20;
        const newWeight = Math.round(BASE_WEIGHT_NO_BATTERY + packs * PACK_WEIGHT_LBS);
        return { ...v, battery_packs: packs, battery_kwh: kwh, weight_lbs: newWeight };
      }
      return { ...v, [field]: value };
    }));
    setResults(null);
  };

  const loadDemoAddresses = async () => {
    try {
      const res = await fetch('/demo_stops.txt');
      const text = await res.text();
      const lines = text.split('\n').map(l => l.trim()).filter(Boolean);
      const [depotLine, ...stopLines] = lines;
      setDepot(depotLine);
      setAddresses(stopLines.length ? stopLines : ['']);
    } catch {
      // fallback to hardcoded if fetch fails
      setAddresses(DEMO_ADDRESSES);
      setDepot('');
    }
    setValidatedLocations([]);
    setValidatedDepot(null);
    setResults(null);
    setError(null);
  };

  const addAddress = () => {
    setAddresses([...addresses, '']);
    setValidatedLocations([]);
    setResults(null);
  };

  const removeAddress = (index) => {
    if (addresses.length > 2) {
      setAddresses(addresses.filter((_, i) => i !== index));
      setValidatedLocations([]);
      setResults(null);
    }
  };

  const updateAddress = (index, value) => {
    const newAddresses = [...addresses];
    newAddresses[index] = value;
    setAddresses(newAddresses);
    setValidatedLocations([]);
    setResults(null);
  };

  const updateDepot = (value) => {
    setDepot(value);
    setValidatedDepot(null);
    setResults(null);
  };

  // Geocode the depot and every stop. Returns the locations and stores them;
  // throws with a readable message on failure.
  const geocodeAll = async () => {
    const validAddresses = addresses.filter(a => a.trim());
    if (validAddresses.length < 2) throw new Error('Please enter at least 2 addresses');
    if (useDepot && !depot.trim()) throw new Error('Please enter a depot address');

    const depotResponse = await fetch(`${API_BASE}/geocode`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ address: depot })
    });
    const depotData = await depotResponse.json();
    if (depotData.status === 'error') {
      throw new Error(`Failed to geocode depot: ${depotData.message}`);
    }

    const response = await fetch(`${API_BASE}/geocode_batch`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ addresses: validAddresses })
    });
    const data = await response.json();
    if (data.status === 'error') throw new Error(data.message);
    if (data.errors && data.errors.length > 0) {
      throw new Error(`Failed to geocode: ${data.errors.map(e => e.address).join(', ')}`);
    }
    const validLocations = data.results.filter(r => r !== null);
    if (validLocations.length < 2) throw new Error('Need at least 2 valid locations after geocoding');

    setValidatedDepot(depotData.result);
    setValidatedLocations(validLocations);
    return [depotData.result, ...validLocations];
  };

  const validateAddresses = async () => {
    setValidating(true);
    setError(null);
    setResults(null);
    try {
      await geocodeAll();
    } catch (err) {
      setError(err.message || 'Failed to validate addresses');
      setValidatedLocations([]);
      setValidatedDepot(null);
    } finally {
      setValidating(false);
    }
  };

  // Settings a version was solved with, for the before / after comparison.
  const snapshotSettings = () => ({
    goals: { ...goals },
    priorityStops: [...priorityStops],
    vehicles: vehicles.map(v => ({ ...v })),
    energyParams: { ...energyParams },
    solver,
    depot,
    stops: addresses.filter(a => a.trim()),
  });

  // Stop i on the form is location i + 1 (the depot is 0).
  const priorityIndices = () => {
    const stops = addresses.filter(a => a.trim());
    return priorityStops.map(p => stops.indexOf(p)).filter(i => i >= 0).map(i => i + 1);
  };

  // One solve. Sends back the matrices of the last solve of these same
  // locations, so a re-solve after a chat change makes no Google calls.
  const callOptimize = async (locations, previous) => {
    const body = {
      locations,
      mode: 'distance',
      return_to_start: useDepot,
      num_vehicles: vehicles.length,
      vehicles,
      energy_params: energyParams,
      solver,
      goals,
      priority_stops: priorityIndices(),
    };
    if (previous?.inputs && locationsKey(previous.locations) === locationsKey(locations)) {
      body.precomputed = previous.inputs;
    }
    const response = await fetch(`${API_BASE}/optimize`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
    const data = await response.json();
    if (data.status === 'error') throw new Error(data.message);
    return {
      // `solver` rides along on results so the panel and the data stay in sync.
      results: { ...data.results, solver: data.solver, goals: data.goals },
      inputs: { distance: data.matrices.distance, time: data.matrices.time, elevations: data.elevations },
    };
  };

  const commitVersion = ({ results: r, locations, inputs, label, source, trigger, settings, baseId }) => {
    const prev = versions[versions.length - 1];
    const v = {
      id: (prev?.id || 0) + 1, label, source, trigger: trigger || null,
      results: r, locations, inputs, settings: settings || snapshotSettings(),
    };
    setVersions(list => [...list, v].slice(-MAX_VERSIONS));
    setCompareBase(baseId ?? prev?.id ?? null);
    setShowCompare(true);
    setResults(r);
    setLastLocations(locations);
    setStale(null);
    if (prev) mainRef.current?.scrollTo({ top: 0, behavior: 'smooth' });
    return v;
  };

  const currentVersion = versions[versions.length - 1];
  // The plan a breakdown is simulated on: the latest one that is not itself a breakdown.
  const cleanVersion = [...versions].reverse().find(v => !v.results.ortools_fleet?.disruption);

  const optimizeRoute = async () => {
    if (validatedLocations.length < 2) {
      setError('Please validate addresses first');
      return;
    }

    if (useDepot && !validatedDepot) {
      setError('Please validate depot address first');
      return;
    }

    setLoading(true);
    setError(null);

    try {
      const locationsToOptimize = useDepot
        ? [validatedDepot, ...validatedLocations]
        : validatedLocations;
      const out = await callOptimize(locationsToOptimize, currentVersion);
      commitVersion({
        ...out, locations: locationsToOptimize,
        label: versions.length ? 'Re-run by hand' : 'Initial plan', source: 'manual',
      });
    } catch (err) {
      setError(err.message || 'Failed to optimize route');
    } finally {
      setLoading(false);
    }
  };

  // ── Chat-driven re-solve ──────────────────────────────────────────────
  // The chatbot applies its changes, then hands the turn here. The re-solve
  // runs in an effect so it sees the state those changes produced.
  const handleChatTurn = (turn) => new Promise(resolve => {
    const wantsRun = turn.action?.type === 'resolve';
    if (!results && !wantsRun && turn.action?.type !== 'breakdown') return resolve(null);
    const id = Date.now() + Math.random();
    resolveWaiters.current[id] = resolve;
    setResolveRequest({ id, ...turn });
  });

  const runBreakdown = async (req) => {
    const base = cleanVersion;
    if (!base) return { error: true, text: 'Run Optimize first — a breakdown is simulated on the current plan.' };
    const r = base.results;
    const plan = r.ortools_fleet
      ? r.ortools_fleet.routes.map(rt => ({ vehicle_index: rt.vehicle_index, order: rt.order }))
      : [{ vehicle_index: 0, order: [...r.optimal.order, 0] }];
    const a = req.action;
    const response = await fetch(`${API_BASE}/replan`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        locations: base.locations,
        vehicles: base.settings.vehicles,
        energy_params: base.settings.energyParams,
        goals: base.settings.goals,
        plan,
        event: { vehicle_index: a.vehicle_index, after_stop: a.after_stop, at_minute: a.at_minute, spare_truck: a.spare_truck },
        precomputed: base.inputs,
        lang: req.lang,
      })
    });
    const data = await response.json();
    if (data.status === 'error') throw new Error(data.message);
    if (data.nothing) return { text: data.message };
    const after = { ...r, ortools_fleet: data.results.ortools_fleet };
    const v = commitVersion({
      results: after, locations: base.locations, inputs: base.inputs, settings: base.settings,
      label: `Breakdown · ${a.vehicle_label}`, source: 'breakdown',
      trigger: { text: req.text, note: data.message }, baseId: base.id,
    });
    const before = planOf(r), now = planOf(after);
    return { text: `${data.message} (v${v.id} — see "What changed" above the results.)`, rows: metricRows(before, now) };
  };

  const runChatResolve = async (req) => {
    const before = currentVersion;
    let locations = useDepot && validatedDepot && validatedLocations.length >= 2
      ? [validatedDepot, ...validatedLocations] : null;
    if (!locations) {
      setResolvingNote('Geocoding the new stops…');
      locations = await geocodeAll();
    }
    setResolvingNote('Re-solving with your chat changes…');
    const out = await callOptimize(locations, before);
    const label = req.changes[0]?.label || (before ? 'Re-run' : 'Initial plan');
    const v = commitVersion({
      ...out, locations, label: req.changes.length > 1 ? `${label} +${req.changes.length - 1}` : label,
      source: 'chat', trigger: { text: req.text },
    });
    const pb = planOf(before?.results), pa = planOf(out.results);
    if (!pb) {
      return { text: `Optimized: ${pa.label}, ${(pa.distance_m / 1609.34).toFixed(1)} mi, ${pa.energy_kwh.toFixed(1)} kWh. Tell me what matters today and I'll re-tune it.` };
    }
    return {
      text: impactText(pb, pa, { lang: req.lang, versionLabel: `v${v.id}`, cleared: !!before.results.ortools_fleet?.disruption }),
      rows: metricRows(pb, pa),
    };
  };

  useEffect(() => {
    if (!resolveRequest) return;
    const req = resolveRequest;
    setResolveRequest(null);
    (async () => {
      let out;
      setResolvingNote(req.action?.type === 'breakdown' ? 'Simulating the breakdown and re-planning…' : 'Re-solving with your chat changes…');
      try {
        out = req.action?.type === 'breakdown' ? await runBreakdown(req) : await runChatResolve(req);
      } catch (err) {
        setStale(`Couldn't re-solve: ${err.message}`);
        out = { error: true, text: `Couldn't re-solve: ${err.message}` };
      } finally {
        setResolvingNote(null);
      }
      resolveWaiters.current[req.id]?.(out);
      delete resolveWaiters.current[req.id];
    })();
  }, [resolveRequest]); // eslint-disable-line react-hooks/exhaustive-deps

  // A priority star belongs to a stop; when the stop is edited away, so is the star.
  useEffect(() => {
    const stops = addresses.map(a => a.trim());
    setPriorityStops(prev => (prev.every(p => stops.includes(p)) ? prev : prev.filter(p => stops.includes(p))));
  }, [addresses]);

  const flashSection = (name) => {
    if (!name) return;
    setFlash(f => ({ ...f, [name]: true }));
    setTimeout(() => setFlash(f => ({ ...f, [name]: false })), 2600);
  };

  const togglePriority = (address) => {
    const a = address.trim();
    if (!a) return;
    setPriorityStops(prev => prev.includes(a) ? prev.filter(p => p !== a) : [...prev, a]);
    if (results) setStale('Priority stops changed — click Optimize Routes to re-solve.');
  };

  const updateGoalsByHand = (next) => {
    setGoals(next);
    setGoalsSetBy(null);
    if (results) setStale('Goals changed — click Optimize Routes to re-solve.');
  };

  const formatDistance = (meters) => {
    const miles = meters / 1609.34;
    return `${miles.toFixed(1)} mi`;
  };

  const formatTime = (seconds) => {
    const hours = Math.floor(seconds / 3600);
    const mins = Math.floor((seconds % 3600) / 60);
    if (hours > 0) return `${hours}h ${mins}m`;
    return `${mins} min`;
  };

  const isAddressValidated = (index) => {
    const address = addresses[index]?.trim();
    if (!address) return false;
    return validatedLocations.some(loc =>
      loc.address.toLowerCase().includes(address.toLowerCase()) ||
      loc.formatted_address.toLowerCase().includes(address.toLowerCase())
    );
  };

  const isDepotValidated = () => {
    return useDepot && depot.trim() && validatedDepot !== null;
  };

  // Chatbot "apply change" outcome: one call touches exactly one axis. The
  // plan on screen stays put — it is the "before" of the re-solve that
  // follows the chat turn (handleChatTurn) — and the section that changed flashes.
  const applyChatChange = ({ axis, op, value, evidence }) => {
    flashSection(AXIS_SECTION[axis]);
    if (results) setStale('Settings changed — the plan below is from before.');
    switch (axis) {
      case 'depot':
        setDepot(value);
        setValidatedDepot(null);
        break;
      case 'addresses':
        setAddresses(prev => {
          const current = prev.filter(a => a.trim());
          let next;
          if (op === 'add') next = [...current, ...value];
          else if (op === 'remove') next = current.filter(a => !value.some(v => a.toLowerCase().includes(v.toLowerCase())));
          else next = [...value];
          while (next.length < 2) next.push('');
          return next;
        });
        setValidatedLocations([]);
        break;
      case 'priority_stops':
        setPriorityStops(prev => {
          if (op === 'add') return [...prev, ...value.filter(v => !prev.includes(v))];
          if (op === 'remove') return prev.filter(p => !value.includes(p));
          return [...value];
        });
        break;
      case 'goals':
        setGoals(value);
        setGoalsSetBy({ evidence });
        break;
      case 'vehicle_count':
        setVehicles(prev => {
          if (value <= prev.length) return prev.slice(0, value);
          const out = [...prev];
          while (out.length < value) {
            const id = out.length ? Math.max(...out.map(v => v.id)) + 1 : 1;
            out.push({ id, name: `Vehicle ${id}`, weight_lbs: 13000, battery_packs: 4, battery_kwh: 80 });
          }
          return out;
        });
        break;
      case 'battery_packs':
        setVehicles(prev => prev.map(v => ({
          ...v,
          battery_packs: value,
          battery_kwh: value * 20,
          weight_lbs: Math.round(BASE_WEIGHT_NO_BATTERY + value * PACK_WEIGHT_LBS),
        })));
        break;
      case 'vehicle_weight_lbs':
        setVehicles(prev => prev.map(v => ({ ...v, weight_lbs: value })));
        break;
      case 'temperature_c':
      case 'unload_time_min':
      case 'has_climate_control':
        setEnergyParams(prev => ({ ...prev, [axis]: value }));
        break;
      case 'solver':
        setSolver(value);
        break;
      case 'fleet':
        // A memory recall: one vehicle per recalled driver. A driver profile may
        // carry its truck's packs and weight; whatever it leaves out, that
        // vehicle slot keeps (packs alone re-derive the weight, as above).
        setVehicles(prev => value.map((v, i) => {
          const base = prev[i] || { name: `Vehicle ${i + 1}`, battery_packs: 4, weight_lbs: 13000 };
          const packs = v.battery_packs ?? base.battery_packs;
          const weight = v.weight_lbs ?? (v.battery_packs != null
            ? Math.round(BASE_WEIGHT_NO_BATTERY + packs * PACK_WEIGHT_LBS)
            : base.weight_lbs);
          return {
            id: i + 1,
            name: base.name,
            battery_packs: packs,
            battery_kwh: packs * 20,
            weight_lbs: weight,
            driver: v.driver || '',
          };
        }));
        break;
      default:
        break;
    }
  };

  // Snapshot the chatbot sends with every message so relative requests
  // ("one more truck") resolve against what is actually on screen.
  const chatFormState = {
    depot,
    addresses: addresses.filter(a => a.trim()),
    vehicle_count: vehicles.length,
    battery_packs: vehicles[0]?.battery_packs ?? 4,
    vehicle_weight_lbs: vehicles[0]?.weight_lbs ?? 13000,
    fleet: vehicles.map(v => ({
      name: v.name, battery_packs: v.battery_packs,
      weight_lbs: v.weight_lbs, driver: v.driver || '',
    })),
    temperature_c: energyParams.temperature_c,
    unload_time_min: energyParams.unload_time_min,
    has_climate_control: energyParams.has_climate_control,
    solver,
    goals,
    priority_stops: priorityStops,
    // The plan a breakdown would be simulated on, and what each truck's route needs.
    plan: (() => {
      const p = planOf(cleanVersion?.results);
      if (!p) return null;
      const fleetRoutes = cleanVersion.results.ortools_fleet?.routes;
      return {
        kind: p.kind,
        vehicles: fleetRoutes
          ? fleetRoutes.map(r => ({
              number: r.vehicle_index + 1, vehicle_index: r.vehicle_index, name: r.vehicle_name,
              driver: r.driver || '', stops: r.num_delivery_stops,
              energy_kwh: Math.round(r.total_energy_kwh * 10) / 10, battery_pct: r.battery_remaining_pct,
            }))
          : [{
              number: 1, vehicle_index: 0, name: vehicles[0]?.name || 'Vehicle 1', driver: vehicles[0]?.driver || '',
              stops: cleanVersion.results.optimal.weight_params?.num_delivery_stops ?? 0,
              energy_kwh: Math.round(cleanVersion.results.optimal.total_energy_kwh * 10) / 10,
              battery_pct: cleanVersion.results.optimal.battery_remaining_pct,
            }],
      };
    })(),
  };

  const hk = results?.optimal;
  const fleet = results?.ortools_fleet;

  const delta = (val, ref) => {
    if (val == null || ref == null || ref === 0) return null;
    const diff = ref - val;
    const p = Math.abs((diff / ref * 100)).toFixed(0);
    return { better: diff > 0, p, sign: diff > 0 ? '−' : '+' };
  };

  const TABS = [
    { id: 'solver', label: 'Solver', icon: Route },
    { id: 'benchmark', label: 'Benchmark', icon: BarChart3 },
  ];

  return (
    <div className="h-screen flex flex-col bg-slate-950">
      {/* ── Top navbar ─────────────────────────────────────── */}
      <header className="h-14 bg-slate-900 border-b border-slate-800 flex items-center px-6 flex-shrink-0 gap-4">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 bg-[#C8002F] rounded-lg flex items-center justify-center flex-shrink-0">
            <Truck className="w-4 h-4 text-white" />
          </div>
          <div className="leading-none">
            <p className="text-white font-bold text-sm">Isuzu BEV Fleet</p>
            <p className="text-slate-500 text-xs mt-0.5">Route Optimizer</p>
          </div>
        </div>
        <div className="h-6 w-px bg-slate-800 mx-1" />
        <nav className="flex items-center gap-1 bg-slate-950 border border-slate-800 rounded-lg p-0.5">
          {TABS.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              onClick={() => setActiveTab(id)}
              className={`flex items-center gap-1.5 px-3 py-1 rounded-md text-xs font-medium transition-colors ${
                activeTab === id ? 'bg-[#C8002F] text-white' : 'text-slate-400 hover:text-white'
              }`}
            >
              <Icon className="w-3.5 h-3.5" /> {label}
            </button>
          ))}
        </nav>
        <span className="text-xs text-slate-600 hidden lg:block">Held-Karp TSP · OR-Tools CVRP · Mercedes eActros Energy Model</span>
        <div className={`ml-auto flex items-center gap-3 ${activeTab === 'solver' ? '' : 'hidden'}`}>
          {loading && (
            <span className="text-xs text-blue-400 flex items-center gap-1.5">
              <Loader2 className="w-3 h-3 animate-spin" /> Optimizing...
            </span>
          )}
          {results && !loading && (
            <span className="text-xs text-green-400 flex items-center gap-1.5">
              <CheckCircle className="w-3 h-3" /> Solution ready
            </span>
          )}
        </div>
      </header>

      {/* ── Benchmark tab (kept mounted so results survive tab switches) ── */}
      <div className={`flex flex-1 min-h-0 ${dockRoom} ${activeTab === 'benchmark' ? '' : 'hidden'}`}>
        <BenchmarkPanel />
      </div>

      {/* ── Body ───────────────────────────────────────────── */}
      <div className={`flex flex-1 min-h-0 ${dockRoom} ${activeTab === 'solver' ? '' : 'hidden'}`}>

        {/* ── Sidebar ──────────────────────────────────────── */}
        <aside className="w-80 bg-slate-900 border-r border-slate-800 flex flex-col flex-shrink-0">
          <div className="flex-1 overflow-y-auto p-5 space-y-6">

            <GoalsPanel
              goals={goals}
              onChange={updateGoalsByHand}
              priorityStops={priorityStops}
              onRemovePriority={togglePriority}
              setBy={goalsSetBy}
              hot={flash.goals}
              vehicleCount={vehicles.length}
            />

            <div className="border-t border-slate-800" />

            {/* Vehicle Parameters */}
            <section>
              <h3 className="text-[11px] font-semibold text-slate-500 uppercase tracking-widest mb-3">Vehicle Parameters</h3>

              {/* Depot input */}
              <div className="mb-4">
                <div className="flex items-center gap-2 mb-1.5">
                  <Home className="w-3.5 h-3.5 text-slate-500" />
                  <label className="text-xs text-slate-400">Depot Address</label>
                  {isDepotValidated() && <CheckCircle className="w-3 h-3 text-green-400 ml-auto" />}
                </div>
                <input
                  type="text"
                  value={depot}
                  onChange={(e) => updateDepot(e.target.value)}
                  placeholder="e.g. 1400 S Douglass Rd, Anaheim, CA"
                  className={`w-full px-3 py-2 bg-slate-800 border ${isDepotValidated() ? 'border-green-500/60 bg-green-500/5' : 'border-slate-700'} rounded-lg text-sm text-white placeholder-slate-600 focus:ring-1 focus:ring-[#C8002F] focus:border-transparent transition-colors`}
                />
              </div>

              {/* Ambient conditions */}
              <div className={`space-y-3 mb-4 rounded-lg transition-shadow duration-500 ${flash.conditions ? 'ring-2 ring-emerald-400/70 shadow-lg shadow-emerald-500/10 p-2 -m-2' : ''}`}>
                <div>
                  <div className="flex justify-between items-center mb-1.5">
                    <label className="text-xs text-slate-400">Ambient Temperature</label>
                    <span className="text-xs font-mono text-slate-300 bg-slate-800 px-2 py-0.5 rounded">
                      {energyParams.temperature_c}°C / {(energyParams.temperature_c * 9/5 + 32).toFixed(0)}°F
                    </span>
                  </div>
                  <input
                    type="range" min="-10" max="40"
                    value={energyParams.temperature_c}
                    onChange={(e) => setEnergyParams({...energyParams, temperature_c: parseInt(e.target.value)})}
                    className="w-full h-1.5 bg-slate-700 rounded-full appearance-none cursor-pointer accent-[#C8002F]"
                  />
                  <div className="flex justify-between text-[10px] text-slate-600 mt-1">
                    <span>−10°C</span><span>40°C</span>
                  </div>
                </div>

                {/* Unload Time per Stop */}
                <div>
                  <div className="flex justify-between items-center mb-1.5">
                    <label className="text-xs text-slate-400">Unload Time / Stop</label>
                    <span className="text-xs font-mono text-slate-300 bg-slate-800 px-2 py-0.5 rounded">
                      {energyParams.unload_time_min} min
                    </span>
                  </div>
                  <input
                    type="range" min="5" max="120" step="5"
                    value={energyParams.unload_time_min}
                    onChange={(e) => setEnergyParams({...energyParams, unload_time_min: parseInt(e.target.value)})}
                    className="w-full h-1.5 bg-slate-700 rounded-full appearance-none cursor-pointer accent-[#C8002F]"
                  />
                  <div className="flex justify-between text-[10px] text-slate-600 mt-1">
                    <span>5 min</span><span>120 min</span>
                  </div>
                </div>

                <div className="flex items-center justify-between">
                  <label className="text-xs text-slate-400">Climate Control (HVAC)</label>
                  <label className="relative inline-flex items-center cursor-pointer">
                    <input
                      type="checkbox"
                      checked={energyParams.has_climate_control}
                      onChange={(e) => setEnergyParams({...energyParams, has_climate_control: e.target.checked})}
                      className="sr-only peer"
                    />
                    <div className="w-9 h-5 bg-slate-700 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-[#C8002F]"></div>
                  </label>
                </div>
              </div>

              <p className="text-[10px] text-slate-600 leading-relaxed">
                Model E energy regression · real-world validated · weight decreases leg-by-leg as cargo is delivered
              </p>
              <div className="mt-2 p-2 bg-slate-900 rounded-lg border border-slate-800">
                <p className="text-[9px] text-slate-500 mb-1 font-semibold uppercase tracking-wider">Calculated with:</p>
                <p className="text-[9px] text-slate-400 font-mono leading-relaxed break-all">
                  E/km = β₀ + β₁v + β₂T + β₃W + β₄Δh + β₅v² + β₆(W·g) + β₇(v·g) + β₈(v²T) + β₉(v²·alt)
                </p>
                <p className="text-[9px] text-slate-600 mt-1 leading-relaxed">
                  v=speed (km/h) · T=temp (°C) · W=weight (t) · Δh=elev gain (m) · g=grade (m/km) · alt=altitude (m) · R²=0.9997
                </p>
              </div>
            </section>

            <div className="border-t border-slate-800" />

            {/* Fleet Vehicles */}
            <section className={`rounded-lg transition-shadow duration-500 ${flash.vehicles ? 'ring-2 ring-emerald-400/70 shadow-lg shadow-emerald-500/10 p-2 -m-2' : ''}`}>
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-[11px] font-semibold text-slate-500 uppercase tracking-widest">Fleet Vehicles</h3>
                <button
                  onClick={addVehicle}
                  className="text-xs text-[#C8002F] hover:text-red-400 transition-colors flex items-center gap-1"
                >
                  <Plus className="w-3 h-3" /> Add
                </button>
              </div>

              <div className="space-y-2">
                {vehicles.map((v) => (
                  <div key={v.id} className="bg-slate-800 border border-slate-700 rounded-lg p-3">
                    <div className="flex items-center justify-between mb-2">
                      <input
                        type="text"
                        value={v.name}
                        onChange={(e) => updateVehicle(v.id, 'name', e.target.value)}
                        className="text-xs font-semibold text-slate-300 bg-transparent border-none outline-none w-full"
                      />
                      {vehicles.length > 1 && (
                        <button onClick={() => removeVehicle(v.id)} className="text-slate-600 hover:text-red-400 flex-shrink-0 ml-1">
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      )}
                    </div>
                    {v.driver && (
                      <div className="flex items-center gap-2 mb-2 -mt-1">
                        <span className="text-[10px] text-slate-500 flex items-center gap-1">
                          <User className="w-2.5 h-2.5" />{v.driver}
                        </span>
                      </div>
                    )}
                    <div className="grid grid-cols-2 gap-2">
                      <div>
                        <label className="text-[10px] text-slate-500 block mb-1">Battery Packs</label>
                        <select
                          value={v.battery_packs}
                          onChange={(e) => updateVehicle(v.id, 'battery_packs', parseInt(e.target.value))}
                          className="w-full px-2 py-1 bg-slate-700 border border-slate-600 rounded text-xs text-white text-center focus:ring-1 focus:ring-[#C8002F] focus:border-transparent"
                        >
                          {[3,4,5,6,7,8,9].map(p => (
                            <option key={p} value={p}>{p} packs · {p*20} kWh</option>
                          ))}
                        </select>
                      </div>
                      <div>
                        <label className="text-[10px] text-slate-500 block mb-1">Weight (lbs)</label>
                        <input
                          type="number" min="10000" max="19500" step="100"
                          value={v.weight_lbs}
                          onChange={(e) => updateVehicle(v.id, 'weight_lbs', parseInt(e.target.value) || 13000)}
                          className="w-full px-2 py-1 bg-slate-700 border border-slate-600 rounded text-xs text-white text-center focus:ring-1 focus:ring-[#C8002F] focus:border-transparent"
                        />
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </section>

            <div className="border-t border-slate-800" />

            {/* Delivery Stops */}
            <section className={`rounded-lg transition-shadow duration-500 ${flash.stops ? 'ring-2 ring-emerald-400/70 shadow-lg shadow-emerald-500/10 p-2 -m-2' : ''}`}>
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-[11px] font-semibold text-slate-500 uppercase tracking-widest">Delivery Stops</h3>
                <button
                  onClick={loadDemoAddresses}
                  className="text-xs text-purple-400 hover:text-purple-300 transition-colors flex items-center gap-1"
                >
                  <Download className="w-3 h-3" /> Demo
                </button>
              </div>

              <div className="space-y-1.5">
                {addresses.map((address, index) => (
                  <div key={index} className="flex gap-1">
                    <div className="flex-1 relative">
                      <input
                        type="text"
                        value={address}
                        onChange={(e) => updateAddress(index, e.target.value)}
                        placeholder={`Stop ${index + 1}`}
                        className={`w-full px-3 py-2 pr-7 text-sm bg-slate-800 border ${
                          isAddressValidated(index) && validatedLocations.length > 0
                            ? 'border-green-500/60 bg-green-500/5'
                            : 'border-slate-700'
                        } rounded-lg text-white placeholder-slate-600 focus:ring-1 focus:ring-[#C8002F] focus:border-transparent transition-colors`}
                      />
                      {isAddressValidated(index) && validatedLocations.length > 0 && (
                        <CheckCircle className="w-3 h-3 text-green-400 absolute right-2.5 top-1/2 -translate-y-1/2" />
                      )}
                    </div>
                    <button
                      onClick={() => togglePriority(address)}
                      disabled={!address.trim()}
                      title={priorityStops.includes(address.trim()) ? 'Priority stop — served first on its truck' : 'Mark as priority'}
                      className={`px-1 transition-colors ${priorityStops.includes(address.trim()) ? 'text-amber-400' : 'text-slate-700 hover:text-amber-400'} disabled:opacity-30`}
                    >
                      <Star className="w-3.5 h-3.5" fill={priorityStops.includes(address.trim()) ? 'currentColor' : 'none'} />
                    </button>
                    {addresses.length > 2 && (
                      <button
                        onClick={() => removeAddress(index)}
                        className="px-1.5 text-slate-700 hover:text-red-400 transition-colors rounded"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    )}
                  </div>
                ))}
              </div>

              <button
                onClick={addAddress}
                className="w-full mt-2 py-1.5 border border-dashed border-slate-700 hover:border-slate-500 text-slate-600 hover:text-slate-400 rounded-lg transition-colors flex items-center justify-center gap-1 text-xs"
              >
                <Plus className="w-3.5 h-3.5" /> Add Stop
              </button>
            </section>
          </div>

          {/* Sidebar footer — action buttons */}
          <div className="flex-shrink-0 p-4 border-t border-slate-800 space-y-2">
            {error && (
              <div className="bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2 text-red-400 text-xs flex items-start gap-2">
                <AlertCircle className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" />
                <span className="leading-relaxed">{error}</span>
              </div>
            )}

            {validatedLocations.length > 0 && !results && !error && (
              <div className="text-xs text-green-400 flex items-center gap-1.5 px-1 py-1">
                <CheckCircle className="w-3.5 h-3.5" />
                {validatedLocations.length} address{validatedLocations.length !== 1 ? 'es' : ''} ready
                {validatedDepot && ' + depot'}
              </div>
            )}

            <button
              onClick={validateAddresses}
              disabled={validating || addresses.filter(a => a.trim()).length < 2 || !depot.trim()}
              className="w-full py-2 bg-slate-800 hover:bg-slate-700 disabled:opacity-30 disabled:cursor-not-allowed text-slate-300 text-sm font-medium rounded-lg border border-slate-700 hover:border-slate-600 transition-all flex items-center justify-center gap-2"
            >
              {validating
                ? <><Loader2 className="w-3.5 h-3.5 animate-spin" /> Validating...</>
                : <><CheckCircle className="w-3.5 h-3.5" /> Validate Addresses</>
              }
            </button>

            <button
              onClick={optimizeRoute}
              disabled={loading || !!resolvingNote || validatedLocations.length < 2 || (useDepot && !validatedDepot)}
              className="w-full py-2.5 bg-[#C8002F] hover:bg-[#a80027] disabled:opacity-30 disabled:cursor-not-allowed text-white text-sm font-semibold rounded-lg transition-all flex items-center justify-center gap-2 shadow-lg shadow-[#C8002F]/20"
            >
              {loading
                ? <><Loader2 className="w-4 h-4 animate-spin" /> Optimizing...</>
                : <><Route className="w-4 h-4" /> Optimize Routes</>
              }
            </button>
          </div>
        </aside>

        {/* ── Main content ─────────────────────────────────── */}
        <main ref={mainRef} className="flex-1 overflow-y-auto bg-slate-950">
          {!results && resolvingNote ? (
            <div className="h-full flex items-center justify-center">
              <span className="text-sm text-emerald-400 flex items-center gap-2"><Loader2 className="w-4 h-4 animate-spin" />{resolvingNote}</span>
            </div>
          ) : !results ? (
            /* Empty state */
            <div className="h-full flex items-center justify-center p-8">
              <div className="text-center max-w-md">
                <div className="w-24 h-24 bg-slate-900 border border-slate-800 rounded-2xl flex items-center justify-center mx-auto mb-6">
                  <Route className="w-12 h-12 text-slate-800" />
                </div>
                <h2 className="text-white font-semibold text-xl mb-2">Ready to Optimize</h2>
                <p className="text-slate-500 text-sm leading-relaxed mb-8">
                  Configure your fleet parameters, enter delivery stops, then run the optimizer to compare single-vehicle Held-Karp against multi-vehicle OR-Tools CVRP.
                </p>
                <div className="space-y-2 text-left">
                  {[
                    ['1', 'Configure vehicle parameters — weight, temperature, HVAC'],
                    ['2', 'Enter delivery stop addresses or load demo data'],
                    ['3', 'Validate addresses, then click Optimize Routes'],
                  ].map(([n, text]) => (
                    <div key={n} className="flex items-center gap-3 bg-slate-900 border border-slate-800 rounded-xl px-4 py-3">
                      <span className="w-6 h-6 rounded-full bg-slate-800 border border-slate-700 text-slate-500 text-xs font-mono flex items-center justify-center flex-shrink-0">{n}</span>
                      <span className="text-sm text-slate-400">{text}</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          ) : hk && (
            <div className="p-6 space-y-5 max-w-5xl">

              {/* Re-solve status */}
              {resolvingNote && (
                <div className="sticky top-0 z-10 bg-emerald-950/90 backdrop-blur border border-emerald-600/40 rounded-lg px-4 py-2 text-xs text-emerald-200 flex items-center gap-2">
                  <Loader2 className="w-3.5 h-3.5 animate-spin" /> {resolvingNote}
                </div>
              )}
              {stale && !resolvingNote && (
                <div className="bg-amber-950/40 border border-amber-600/40 rounded-lg px-4 py-2 text-xs text-amber-200 flex items-center gap-2 text-left">
                  <AlertCircle className="w-3.5 h-3.5 flex-shrink-0" /> {stale}
                </div>
              )}

              {/* Before / after of the last change */}
              {showCompare && versions.length > 1 && compareBase != null && (
                <PlanComparison
                  versions={versions}
                  baseId={compareBase}
                  currentId={currentVersion.id}
                  onBaseChange={setCompareBase}
                  onClose={() => setShowCompare(false)}
                />
              )}
              {!showCompare && versions.length > 1 && (
                <button onClick={() => setShowCompare(true)} className="text-xs text-emerald-400 hover:text-emerald-300 flex items-center gap-1.5">
                  <ArrowLeftRight className="w-3.5 h-3.5" /> Show what changed ({versions.length} versions)
                </button>
              )}

              {/* Simulated breakdown */}
              {fleet?.disruption && (
                <div className="bg-red-950/30 border border-red-600/40 rounded-xl px-4 py-3 flex items-start gap-3 text-left">
                  <AlertTriangle className="w-4 h-4 text-red-400 flex-shrink-0 mt-0.5" />
                  <div>
                    <p className="text-sm font-semibold text-red-200">Simulated breakdown · {fleet.disruption.vehicle_label}</p>
                    <p className="text-xs text-red-200/80 mt-1 leading-relaxed">{fleet.disruption.message}</p>
                    <p className="text-[10px] text-red-300/60 mt-1">
                      Stranded cargo is collected from the broken-down truck ({fleet.disruption.transfer_min_per_order} min per stop) by the truck that takes it over.
                    </p>
                  </div>
                </div>
              )}

              {/* Stops no truck could take within its limits */}
              {fleet?.unserved?.length > 0 && !fleet.disruption && (
                <div className="bg-amber-950/30 border border-amber-600/40 rounded-xl px-4 py-3 flex items-start gap-3 text-left">
                  <AlertTriangle className="w-4 h-4 text-amber-400 flex-shrink-0 mt-0.5" />
                  <p className="text-xs text-amber-100/90 leading-relaxed">
                    <span className="font-semibold">{fleet.unserved.length} stop{fleet.unserved.length !== 1 ? 's' : ''} can't be served within the battery limits:</span>{' '}
                    {fleet.unserved.map(u => shortPlace(u.location)).join(', ')}. Ask the assistant “what are the limits of a 4-pack truck?”, or add packs or a truck.
                  </p>
                </div>
              )}

              {/* Results header */}
              <div className="flex items-start justify-between">
                <div>
                  <h2 className="text-white font-semibold text-lg">Optimization Results</h2>
                  <p className="text-slate-500 text-sm mt-0.5">
                    {lastLocations.length} stops
                    {fleet && ` · ${fleet.num_vehicles_used} vehicle${fleet.num_vehicles_used !== 1 ? 's' : ''} dispatched`}
                    {fleet && fleet.num_vehicles_requested !== fleet.num_vehicles_used && ` (${fleet.num_vehicles_requested} requested)`}
                    {results.solver && ` · solver: ${results.solver.requested === 'auto' ? 'auto' : results.solver.baseline_label}`}
                    {results.goals && ` · goals: ${goalsSummary(results.goals)}`}
                  </p>
                  {results.solver?.note && (
                    <p className="text-[11px] text-amber-400/80 mt-1 flex items-center gap-1.5">
                      <AlertCircle className="w-3 h-3 flex-shrink-0" />
                      {results.solver.note}
                    </p>
                  )}
                </div>
                {fleet && (
                  <div className="text-right">
                    <p className="text-[10px] text-slate-600 uppercase tracking-wider">Fleet Makespan</p>
                    <p className="text-2xl font-bold text-purple-400">{formatTime(fleet.makespan_s)}</p>
                    <p className="text-[10px] text-slate-600 mt-0.5">wall-clock completion</p>
                  </div>
                )}
              </div>

              {/* Summary stat cards */}
              <div className={`grid gap-3 ${fleet ? 'grid-cols-3' : 'grid-cols-2'}`}>
                {/* Held-Karp card */}
                <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
                  <div className="flex items-center gap-2 mb-3">
                    <Route className="w-4 h-4 text-yellow-400" />
                    <span className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider">
                      {results.solver?.baseline_label || 'Held-Karp'}
                    </span>
                    <span className="ml-auto text-[10px] text-slate-600">
                      {results.solver?.baseline_exact === false ? 'approx' : 'exact'} · 1 vehicle
                    </span>
                  </div>
                  <div className="space-y-2">
                    <div className="flex justify-between items-center">
                      <span className="text-xs text-slate-500 flex items-center gap-1.5"><MapPin className="w-3 h-3" />Distance</span>
                      <span className="text-sm text-white font-semibold">{formatDistance(hk.total_distance_m)}</span>
                    </div>
                    <div className="flex justify-between items-center">
                      <span className="text-xs text-slate-500 flex items-center gap-1.5"><Clock className="w-3 h-3" />Total Time</span>
                      <span className="text-sm text-white font-semibold">{formatTime(hk.total_time_s)}</span>
                    </div>
                    {hk.travel_time_s != null && (
                      <div className="pl-4 space-y-1">
                        <div className="flex justify-between items-center">
                          <span className="text-[10px] text-slate-600">↳ Travel</span>
                          <span className="text-[10px] text-slate-500">{formatTime(hk.travel_time_s)}</span>
                        </div>
                        <div className="flex justify-between items-center">
                          <span className="text-[10px] text-slate-600">↳ Unload ({hk.weight_params?.num_delivery_stops ?? '—'} stops)</span>
                          <span className="text-[10px] text-slate-500">{formatTime(hk.unload_time_s)}</span>
                        </div>
                      </div>
                    )}
                    {hk.total_energy_kwh != null && (
                      <div className="flex justify-between items-center">
                        <span className="text-xs text-slate-500 flex items-center gap-1.5"><Zap className="w-3 h-3" />Energy</span>
                        <span className="text-sm text-yellow-400 font-semibold">{hk.total_energy_kwh.toFixed(1)} kWh</span>
                      </div>
                    )}
                    {hk.avg_energy_per_mile != null && (
                      <div className="flex justify-between items-center">
                        <span className="text-xs text-slate-500 flex items-center gap-1.5"><Zap className="w-3 h-3 opacity-50" />Efficiency</span>
                        <span className="text-sm text-yellow-300 font-semibold">{hk.avg_energy_per_mile.toFixed(2)} kWh/mi</span>
                      </div>
                    )}
                    {hk.battery_capacity_kwh != null && (
                      <div className="flex justify-between items-center gap-3" title="Checked against vehicle 1's battery">
                        <span className="text-xs text-slate-500 flex items-center gap-1.5 flex-shrink-0"><Zap className="w-3 h-3 opacity-50" />Battery left</span>
                        <span className={`text-sm font-semibold text-right ${hk.battery_depleted ? 'text-red-400' : hk.battery_remaining_pct < 20 ? 'text-orange-400' : 'text-green-400'}`}>
                          {hk.battery_depleted ? `DEPLETED · ${hk.total_energy_kwh.toFixed(0)}/${hk.battery_capacity_kwh} kWh` : `${hk.battery_remaining_pct}%`}
                        </span>
                      </div>
                    )}
                  </div>
                </div>

                {/* Fleet card */}
                {fleet && (
                  <div className="bg-slate-900 border border-purple-500/25 rounded-xl p-4">
                    <div className="flex items-center gap-2 mb-3">
                      <Truck className="w-4 h-4 text-purple-400" />
                      <span className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider">Fleet OR-Tools</span>
                      <span className="ml-auto text-[10px] text-slate-600">heuristic · {fleet.num_vehicles_used}v</span>
                    </div>
                    <div className="space-y-2">
                      <div className="flex justify-between items-center">
                        <span className="text-xs text-slate-500 flex items-center gap-1.5"><MapPin className="w-3 h-3" />Total dist.</span>
                        <span className="text-sm text-white font-semibold">{formatDistance(fleet.total_distance_m)}</span>
                      </div>
                      <div className="flex justify-between items-center">
                        <span className="text-xs text-slate-500 flex items-center gap-1.5"><Clock className="w-3 h-3" />Total Time</span>
                        <span className="text-sm text-purple-400 font-semibold">{formatTime(fleet.makespan_s)}</span>
                      </div>
                      {fleet.total_travel_time_s != null && (
                        <div className="pl-4 space-y-1">
                          <div className="flex justify-between items-center">
                            <span className="text-[10px] text-slate-600">↳ Travel (longest)</span>
                            <span className="text-[10px] text-slate-500">{formatTime(fleet.travel_makespan_s ?? fleet.total_travel_time_s)}</span>
                          </div>
                          <div className="flex justify-between items-center">
                            <span className="text-[10px] text-slate-600">↳ Unload (all)</span>
                            <span className="text-[10px] text-slate-500">{formatTime(fleet.total_unload_time_s)}</span>
                          </div>
                        </div>
                      )}
                      {fleet.total_energy_kwh != null && (
                        <div className="flex justify-between items-center">
                          <span className="text-xs text-slate-500 flex items-center gap-1.5"><Zap className="w-3 h-3" />Total energy</span>
                          <span className="text-sm text-yellow-400 font-semibold">{fleet.total_energy_kwh.toFixed(1)} kWh</span>
                        </div>
                      )}
                      {fleet.avg_energy_per_mile != null && (
                        <div className="flex justify-between items-center">
                          <span className="text-xs text-slate-500 flex items-center gap-1.5"><Zap className="w-3 h-3 opacity-50" />Efficiency</span>
                          <span className="text-sm text-yellow-300 font-semibold">{fleet.avg_energy_per_mile.toFixed(2)} kWh/mi</span>
                        </div>
                      )}
                    </div>
                  </div>
                )}

                {/* Comparison card */}
                {fleet && (() => {
                  const timeDelta = delta(fleet.makespan_s, hk.total_time_s);
                  const energyDelta = fleet.total_energy_kwh != null && hk.total_energy_kwh != null
                    ? delta(fleet.total_energy_kwh, hk.total_energy_kwh) : null;
                  return (
                    <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
                      <div className="flex items-center gap-2 mb-3">
                        <TrendingDown className="w-4 h-4 text-green-400" />
                        <span className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider">Δ vs Single</span>
                      </div>
                      <div className="space-y-2">
                        <div className="flex justify-between items-center">
                          <span className="text-xs text-slate-500">Time savings</span>
                          <span className={`text-sm font-bold ${timeDelta?.better ? 'text-green-400' : 'text-red-400'}`}>
                            {timeDelta ? `${timeDelta.sign}${timeDelta.p}%` : '—'}
                          </span>
                        </div>
                        {energyDelta && (
                          <div className="flex justify-between items-center">
                            <span className="text-xs text-slate-500">Energy Δ</span>
                            <span className={`text-sm font-bold ${energyDelta.better ? 'text-green-400' : 'text-red-400'}`}>
                              {`${energyDelta.sign}${energyDelta.p}%`}
                            </span>
                          </div>
                        )}
                        <div className="flex justify-between items-center">
                          <span className="text-xs text-slate-500">Driver hours</span>
                          <span className="text-sm text-slate-300 font-semibold">{formatTime(fleet.total_driver_time_s)}</span>
                        </div>
                      </div>
                    </div>
                  );
                })()}
              </div>

              {/* Comparison table */}
              <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
                <div className="px-5 py-3.5 border-b border-slate-800 flex items-center justify-between">
                  <h3 className="text-sm font-semibold text-white">Side-by-Side Comparison</h3>
                  {fleet && (
                    <span className="text-[10px] text-slate-600">Makespan = wall-clock completion · vehicles dispatch simultaneously</span>
                  )}
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-slate-800 bg-slate-900/50">
                        <th className="text-left px-5 py-3 text-slate-500 font-medium text-xs uppercase tracking-wider w-36">Metric</th>
                        <th className="text-center px-5 py-3">
                          <span className="text-yellow-400 font-semibold">Held-Karp</span>
                          <span className="block text-[10px] text-slate-600 font-normal mt-0.5">single vehicle · exact optimal</span>
                        </th>
                        {fleet && (
                          <th className="text-center px-5 py-3">
                            <span className="text-purple-400 font-semibold">Fleet OR-Tools</span>
                            <span className="block text-[10px] text-slate-600 font-normal mt-0.5">{fleet.num_vehicles_used} vehicles · heuristic</span>
                          </th>
                        )}
                        {fleet && (
                          <th className="text-center px-5 py-3">
                            <span className="text-slate-400 font-medium text-xs uppercase tracking-wider">Change</span>
                          </th>
                        )}
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-800/50">
                      {/* Distance row */}
                      <tr className="hover:bg-slate-800/20 transition-colors">
                        <td className="px-5 py-3.5">
                          <div className="flex items-center gap-2 text-slate-400 text-xs">
                            <MapPin className="w-3.5 h-3.5 text-slate-600" /> Distance
                          </div>
                          {fleet && <span className="block text-[10px] text-slate-600 mt-0.5 pl-5">total all vehicles</span>}
                        </td>
                        <td className="px-5 py-3.5 text-center text-white font-semibold">{formatDistance(hk.total_distance_m)}</td>
                        {fleet && <td className="px-5 py-3.5 text-center text-white font-semibold">{formatDistance(fleet.total_distance_m)}</td>}
                        {fleet && (() => {
                          const d = delta(fleet.total_distance_m, hk.total_distance_m);
                          return <td className={`px-5 py-3.5 text-center font-bold ${d?.better ? 'text-green-400' : 'text-red-400'}`}>{d ? `${d.sign}${d.p}%` : '—'}</td>;
                        })()}
                      </tr>
                      {/* Time row */}
                      <tr className="hover:bg-slate-800/20 transition-colors">
                        <td className="px-5 py-3.5">
                          <div className="flex items-center gap-2 text-slate-400 text-xs">
                            <Clock className="w-3.5 h-3.5 text-slate-600" /> Time
                          </div>
                          {fleet && <span className="block text-[10px] text-slate-600 mt-0.5 pl-5">makespan</span>}
                        </td>
                        <td className="px-5 py-3.5 text-center text-white font-semibold">{formatTime(hk.total_time_s)}</td>
                        {fleet && <td className="px-5 py-3.5 text-center text-purple-300 font-semibold">{formatTime(fleet.makespan_s)}</td>}
                        {fleet && (() => {
                          const d = delta(fleet.makespan_s, hk.total_time_s);
                          return <td className={`px-5 py-3.5 text-center font-bold ${d?.better ? 'text-green-400' : 'text-red-400'}`}>{d ? `${d.sign}${d.p}%` : '—'}</td>;
                        })()}
                      </tr>
                      {/* Energy row */}
                      {hk.total_energy_kwh != null && (
                        <tr className="hover:bg-slate-800/20 transition-colors">
                          <td className="px-5 py-3.5">
                            <div className="flex items-center gap-2 text-slate-400 text-xs">
                              <Zap className="w-3.5 h-3.5 text-slate-600" /> Energy
                            </div>
                            {fleet && <span className="block text-[10px] text-slate-600 mt-0.5 pl-5">total all vehicles</span>}
                          </td>
                          <td className="px-5 py-3.5 text-center text-yellow-400 font-semibold">{hk.total_energy_kwh.toFixed(2)} kWh</td>
                          {fleet && <td className="px-5 py-3.5 text-center text-yellow-400 font-semibold">{fleet.total_energy_kwh?.toFixed(2) ?? '—'} kWh</td>}
                          {fleet && (() => {
                            const d = fleet.total_energy_kwh != null ? delta(fleet.total_energy_kwh, hk.total_energy_kwh) : null;
                            return <td className={`px-5 py-3.5 text-center font-bold ${d?.better ? 'text-green-400' : d ? 'text-red-400' : 'text-slate-600'}`}>{d ? `${d.sign}${d.p}%` : '—'}</td>;
                          })()}
                        </tr>
                      )}
                      {/* Driver hours row */}
                      {fleet && (
                        <tr className="hover:bg-slate-800/20 transition-colors bg-slate-800/10">
                          <td className="px-5 py-3.5">
                            <div className="flex items-center gap-2 text-slate-400 text-xs">
                              <Truck className="w-3.5 h-3.5 text-slate-600" /> Driver Hours
                            </div>
                            <span className="block text-[10px] text-slate-600 mt-0.5 pl-5">sum all vehicles</span>
                          </td>
                          <td className="px-5 py-3.5 text-center text-slate-600 text-xs">—</td>
                          <td className="px-5 py-3.5 text-center text-slate-300 font-semibold">{formatTime(fleet.total_driver_time_s)}</td>
                          <td className="px-5 py-3.5 text-center text-[10px] text-slate-600">labour cost proxy</td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>

                {/* Maps links */}
                <div className="px-5 py-4 border-t border-slate-800 flex flex-wrap gap-2">
                  <a
                    href={hk.google_maps_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex items-center gap-2 px-4 py-2 bg-yellow-500/10 hover:bg-yellow-500/20 border border-yellow-500/25 text-yellow-400 rounded-lg transition-colors text-sm"
                  >
                    <Navigation className="w-3.5 h-3.5" /> Held-Karp Route
                  </a>
                  {fleet && fleet.routes.map((route) => (
                    <a
                      key={route.vehicle_id}
                      href={route.google_maps_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="flex items-center gap-2 px-4 py-2 bg-purple-500/10 hover:bg-purple-500/20 border border-purple-500/25 text-purple-300 rounded-lg transition-colors text-sm"
                    >
                      <Navigation className="w-3.5 h-3.5" /> Vehicle {route.vehicle_id}
                    </a>
                  ))}
                </div>
              </div>

              {/* Map */}
              <RouteMap results={results} locations={lastLocations} />

              {/* Charts */}
              <FleetCharts results={results} />

              {/* Stop order details */}
              <div className="grid md:grid-cols-2 gap-4">
                {/* Held-Karp stop list */}
                <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
                  <div className="px-4 py-3 border-b border-slate-800 flex items-center gap-2">
                    <Route className="w-3.5 h-3.5 text-yellow-400" />
                    <span className="text-sm font-medium text-slate-300">Held-Karp Stop Order</span>
                  </div>
                  <div className="p-4 space-y-2">
                    {results.optimal.ordered_locations.map((loc, i) => (
                      <div key={i} className="flex items-start gap-2.5">
                        <div className={`w-6 h-6 flex-shrink-0 rounded-md flex items-center justify-center text-xs font-bold ${
                          i === 0 ? 'bg-blue-500/20 text-blue-400' : 'bg-slate-800 text-slate-400'
                        }`}>
                          {i === 0 ? 'D' : i}
                        </div>
                        <div className="flex-1 min-w-0">
                          <p className="text-slate-300 text-xs truncate">{loc.formatted_address}</p>
                          {i < results.optimal.legs.length && (
                            <p className={`text-[10px] mt-0.5 ${results.optimal.legs[i].return_leg ? 'text-blue-400/70' : 'text-slate-600'}`}>
                              {results.optimal.legs[i].return_leg ? '↩ ' : '↓ '}
                              {formatDistance(results.optimal.legs[i].distance_m)} · {formatTime(results.optimal.legs[i].time_s)}
                            </p>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>

                {/* Fleet stop list */}
                {results.ortools_fleet && (
                  <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
                    <div className="px-4 py-3 border-b border-slate-800 flex items-center gap-2">
                      <Truck className="w-3.5 h-3.5 text-purple-400" />
                      <span className="text-sm font-medium text-slate-300">Fleet Stop Assignments</span>
                    </div>
                    <div className="p-4 space-y-5">
                      {results.ortools_fleet.routes.map((route) => (
                        <div key={route.vehicle_id}>
                          <div className="flex items-center gap-2 mb-2">
                            <span className="w-5 h-5 bg-purple-500/20 rounded flex items-center justify-center text-purple-400 text-[10px] font-bold flex-shrink-0">
                              {route.vehicle_id}
                            </span>
                            <span className="text-xs font-semibold text-purple-300">{route.vehicle_name || `Vehicle ${route.vehicle_id}`}</span>
                            {route.driver && (
                              <span className="text-[10px] text-slate-500 flex items-center gap-1">
                                <User className="w-2.5 h-2.5" />{route.driver}
                              </span>
                            )}
                            {route.status && route.status !== 'continues' && (
                              <span className={`text-[9px] px-1.5 rounded border ${
                                route.status === 'broken_down' ? 'bg-red-500/15 text-red-300 border-red-500/40'
                                  : route.status === 'spare' ? 'bg-sky-500/15 text-sky-300 border-sky-500/40'
                                  : 'bg-emerald-500/15 text-emerald-300 border-emerald-500/40'}`}>
                                {route.status === 'broken_down' ? 'broke down' : route.status === 'spare' ? 'spare truck' : 'took stranded stops'}
                              </span>
                            )}
                            <span className="ml-auto text-[10px] text-slate-600">
                              {formatDistance(route.total_distance_m)} · {formatTime(route.total_time_s)}
                            </span>
                          </div>
                          {/* Battery state */}
                          {route.battery_capacity_kwh != null && (
                            <div className="mb-2">
                              <div className="flex justify-between text-[10px] mb-1">
                                <span className="text-slate-500 flex items-center gap-1">
                                  <Zap className="w-2.5 h-2.5" />
                                  {route.battery_remaining_kwh} / {route.battery_capacity_kwh} kWh
                                </span>
                                <span className={`font-semibold ${
                                  route.battery_depleted ? 'text-red-400' :
                                  route.battery_remaining_pct < 20 ? 'text-orange-400' :
                                  'text-green-400'
                                }`}>
                                  {route.battery_depleted ? 'DEPLETED' : `${route.battery_remaining_pct}%`}
                                </span>
                              </div>
                              <div className="h-1.5 bg-slate-700 rounded-full overflow-hidden">
                                <div
                                  className={`h-full rounded-full transition-all ${
                                    route.battery_depleted ? 'bg-red-500' :
                                    route.battery_remaining_pct < 20 ? 'bg-orange-500' :
                                    'bg-green-500'
                                  }`}
                                  style={{ width: `${Math.min(100, route.battery_remaining_pct || 0)}%` }}
                                />
                              </div>
                            </div>
                          )}
                          <div className="space-y-1.5 pl-1">
                            {(() => {
                              let n = 0;
                              const last = route.ordered_locations.length - 1;
                              return route.ordered_locations.map((loc, i) => {
                                const kind = route.stop_kinds?.[i] || (i === 0 || i === last ? 'depot' : 'stop');
                                const broke = route.status === 'broken_down' && i === last && kind !== 'depot';
                                const delivery = kind === 'stop' || kind === 'done' || kind === 'rescue';
                                if (delivery) n += 1;
                                const badge = kind === 'depot' ? 'D' : kind === 'pickup' ? '⇄' : broke ? '!' : n;
                                const badgeCls = broke ? 'bg-red-500/20 text-red-300'
                                  : kind === 'pickup' ? 'bg-cyan-500/15 text-cyan-300'
                                  : kind === 'rescue' ? 'bg-emerald-500/20 text-emerald-300'
                                  : kind === 'done' ? 'bg-slate-800 text-slate-600'
                                  : 'bg-slate-800 text-slate-500';
                                const note = broke ? 'broke down here' : kind === 'pickup' ? 'pick up stranded cargo'
                                  : kind === 'rescue' ? 'stranded stop' : kind === 'done' ? 'delivered' : '';
                                return (
                                  <div key={i} className="flex items-start gap-2">
                                    <div className={`w-5 h-5 flex-shrink-0 rounded flex items-center justify-center text-[10px] font-bold ${badgeCls}`}>
                                      {badge}
                                    </div>
                                    <p className={`text-xs truncate leading-5 flex-1 ${kind === 'done' ? 'text-slate-600' : 'text-slate-400'}`}>
                                      {loc.formatted_address}
                                      {note && <span className={`ml-1.5 text-[10px] ${broke ? 'text-red-400' : kind === 'rescue' ? 'text-emerald-400' : kind === 'pickup' ? 'text-cyan-400' : 'text-slate-600'}`}>· {note}</span>}
                                    </p>
                                    {route.eta_s?.[i] != null && i > 0 && (
                                      <span className="text-[10px] text-slate-600 font-mono leading-5" title="Arrival, after dispatch">{formatClock(route.eta_s[i])}</span>
                                    )}
                                  </div>
                                );
                              });
                            })()}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>

            </div>
          )}
        </main>
      </div>
      <AIChatbot formState={chatFormState} onApplyChange={applyChatChange} onTurn={handleChatTurn}
                 onDockChange={setChatDocked} />
    </div>
  );
}

export default RouteOptimizer;
