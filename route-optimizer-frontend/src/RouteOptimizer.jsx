import VehicleDriverControls from './VehicleDriverControls';
import { assignDriver, applyStyleOverrides, effectiveStyle } from './driverState';
import React, { useState, useEffect, useRef, useMemo } from 'react';
import { MapPin, Plus, Trash2, Route, Navigation, Loader2, AlertCircle, CheckCircle, Download, Home, Truck, Zap, Clock, TrendingDown, BarChart3, Activity, Star, AlertTriangle, TriangleAlert, ArrowLeftRight, RotateCcw, Play, Square, User } from 'lucide-react';
import RouteMap from './RouteMap';
import SchematicRouteMap from './SchematicRouteMap';
import { SIM_SPEEDS } from './DriveSimulation';
import FleetCharts from './FleetCharts';
import AIChatbot from './AIChatbot';
import BenchmarkPanel from './BenchmarkPanel';
import BenchmarkSimulation from './BenchmarkSimulation';
import GoalsPanel from './GoalsPanel';
import PlanComparison from './PlanComparison';
import { DEFAULT_GOALS, goalsSummary, trafficLabel } from './goals';
import { planOf, metricRows, impactText, shortPlace, formatClock, depletionText } from './planDiff';
import { API_BASE } from './api';

const locationsKey = (locs) => locs.map(l => `${l.lat},${l.lng}`).join('|');
const MAX_VERSIONS = 12;
const SAVED_DEMO = process.env.REACT_APP_SAVED_DEMO === 'true';

// Which sidebar section a chat change lands in, so it can flash.
const AXIS_SECTION = {
  goals: 'goals', priority_stops: 'goals', traffic: 'goals', driving_style: 'goals', depot: 'stops', addresses: 'stops',
  driver_assignments: 'vehicles', vehicle_style_overrides: 'vehicles', vehicle_count: 'vehicles', battery_packs: 'vehicles', vehicle_weight_lbs: 'vehicles', fleet: 'vehicles',
  temperature_c: 'conditions', unload_time_min: 'conditions', has_climate_control: 'conditions',
};

// Demo replanning: this many historical crash hotspots on the route, each toggled on/off as an accident
const NUM_ACCIDENTS = 3;

// Key of /api/replan_incidents scenarios: active accident ids, sorted and comma-joined
const scenarioKey = (ids) => [...ids].sort((a, b) => a - b).join(',');

// Legs searched for crash hotspots: Held-Karp's, or with a fleet every vehicle's legs together,
// each tagged with its vehicle and the input index of both ends (fleet stops keep input numbering)
const incidentLegs = (results) => (results.ortools_fleet
  ? results.ortools_fleet.routes.flatMap(route => route.legs.map((leg, k) => ({
    ...leg, vehicle_id: route.vehicle_id, from_idx: route.order[k], to_idx: route.order[k + 1],
  })))
  : results.optimal.legs);

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

// Where travel times come from: Google, or the traffic data package
// (4 days, Cerritos–Stanton area only; see traffic_data.py)
const TRAFFIC_SOURCES = [
  ['google', 'Google (typical, no traffic)'],
  ['forecast', 'Traffic forecast at departure'],
  ['historical', 'Historical traffic (same time, other day)'],
  ['ground_truth', 'Measured traffic (hindsight)'],
];
const TRAFFIC_DAYS = [
  ['2025-11-12', 'Wed Nov 12'],
  ['2025-11-18', 'Tue Nov 18'],
  ['2025-11-22', 'Sat Nov 22'],
  ['2025-11-23', 'Sun Nov 23'],
];
const label = (options, value) => options.find(([v]) => v === value)?.[1] ?? value;

// Reserve a fleet route's battery is checked against for charging stops: the
// battery reserve goal it was planned with, or 20% when no reserve goal is set.
const CHARGE_RESERVE_PCT = 20;
const reserveOf = (route) => route.battery_reserve_pct ?? CHARGE_RESERVE_PCT;
const belowReserve = (route) => route.battery_remaining_pct < reserveOf(route);

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
  const [drivers, setDrivers] = useState([]);
  const [savedDemo, setSavedDemo] = useState(null);
  const solveSequence = useRef(0);
  const refreshDrivers = async () => {
    try {
      const response = await fetch(`${API_BASE}/memory`);
      const data = await response.json();
      if (data.status === 'ok') setDrivers(data.drivers || []);
    } catch { /* Driver profiles can be loaded when the backend becomes available. */ }
  };
  useEffect(() => { refreshDrivers(); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const [lastLocations, setLastLocations] = useState([]);
  const [replan, setReplan] = useState(null);
  const [replanning, setReplanning] = useState(false);
  const [replanError, setReplanError] = useState(null);
  const [activeAccidents, setActiveAccidents] = useState([]);
  // Drive simulation on measured traffic (the fleet's trucks, or the Held-Karp route without a fleet):
  // {runId, speed}, or null when off
  const [drive, setDrive] = useState(null);
  const [chargingPlans, setChargingPlans] = useState(null);  // /api/plan_charging plans by vehicle_id
  const [planningCharging, setPlanningCharging] = useState(false);
  const [chargingError, setChargingError] = useState(null);
  // Travel times from the traffic data package (source/day/time); 'google' = not used.
  // Separate from `traffic` below (Google's prediction for a departure time).
  const [trafficData, setTrafficData] = useState({ source: 'google', day: '2025-11-12', time: '07:30' });
  // 'auto' lets the backend price Held-Karp before running it; the chatbot can
  // pin a specific solver via the `solver` axis.
  const [solver, setSolver] = useState('auto');
  const [energyParams, setEnergyParams] = useState({
    temperature_c: 20,
    total_weight_lbs: 19500,
    base_weight_lbs: 7000,
    unload_time_min: 30,
    has_climate_control: true,
  });
  // Traffic: null = Google's typical times, 'now' or 'HH:MM' = traffic for that departure.
  const [traffic, setTraffic] = useState(null);
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

  const loadSavedDemo = async () => {
    try {
      const response = await fetch(`${API_BASE}/demo/bootstrap`);
      const data = await response.json();
      if (data.status !== 'ok') throw new Error(data.message || 'Could not load the saved demo');
      solveSequence.current += 1;
      setSavedDemo(data);
      setVersions([]);
      setReplan(null);
      setChargingPlans(null);
      setStale(null);
      setDepot(data.locations[0].address);
      setAddresses(data.locations.slice(1).map(p => p.address));
      setValidatedDepot(data.locations[0]);
      setValidatedLocations(data.locations.slice(1));
      setVehicles(data.vehicles);
      setGoals(data.goals);
      setTraffic(null);
      setTrafficData({ source: 'google', day: '2025-11-12', time: '07:30' });
      setResults(null);
      setDrive(null);
      setProactive(null);
      setError(null);
    } catch (err) { setError(err.message); }
  };
  useEffect(() => { if (SAVED_DEMO) loadSavedDemo(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const updateTrafficData = (field, value) => {
    setTrafficData({ ...trafficData, [field]: value });
    if (results) setStale('Travel times changed — click Optimize Routes to re-solve.');
  };

  const changeDriverConfig = (next, label) => {
    solveSequence.current += 1;
    setVehicles(next);
    setDrive(null);
    setProactive(null);
    if (results) {
      setStale('Driver settings changed — updating the plan.');
      setResolveRequest({ id: Date.now() + Math.random(), changes: [{ label }], text: label, lang: 'en' });
    }
  };

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

  // Load a sample input from public/: first line is the depot, the rest are stops
  const loadDemoAddresses = async (file = '/demo_stops.txt') => {
    try {
      const res = await fetch(file);
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
    traffic,
    trafficData: { ...trafficData },
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
  const callOptimize = async (locations, previous, generation = ++solveSequence.current) => {
    if (generation !== solveSequence.current) { const err = new Error('Configuration changed'); err.superseded = true; throw err; }
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
      traffic: traffic ? { departure: traffic } : null,
      traffic_source: trafficData.source,
      departure_time: trafficData.source === 'google' ? undefined : `${trafficData.day}T${trafficData.time}`,
    };
    // Kept with the version (without the matrices), so the suggestion check can
    // re-solve the same request with a fix.
    if (SAVED_DEMO) body.fleet_time_limit_s = 2;
    const request = { ...body };
    if (savedDemo && !traffic && trafficData.source === 'google' && locationsKey(savedDemo.locations) === locationsKey(locations)) body.precomputed = savedDemo.precomputed;
    // Matrices fetched for other traffic settings are not reused.
    if (previous?.inputs && locationsKey(previous.locations) === locationsKey(locations)
        && (previous.inputs.traffic?.departure ?? null) === traffic
        && (previous.inputs.traffic_source || 'google') === trafficData.source
        && (trafficData.source === 'google' || previous.inputs.departure_time === `${trafficData.day}T${trafficData.time}`)) {
      body.precomputed = previous.inputs;
    }
    const response = await fetch(`${API_BASE}/optimize`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
    const data = await response.json();
    if (generation !== solveSequence.current) {
      const error = new Error('A newer configuration superseded this solve'); error.superseded = true; throw error;
    }
    if (data.status === 'error') throw new Error(data.message);
    request.vehicles = data.vehicles || vehicles;
    request.expected_energy_model = data.energy_model;
    return {
      // `solver` rides along on results so the panel and the data stay in sync.
      results: { ...data.results, solver: data.solver, goals: data.goals, priority_stops: data.priority_stops || [],
                 traffic: data.traffic || null, vehicles: data.vehicles || vehicles, energy_model: data.energy_model },
      inputs: { distance: data.matrices.distance, time: data.matrices.time, elevations: data.elevations,
                traffic: data.traffic || null, traffic_source: trafficData.source,
                departure_time: trafficData.source === 'google' ? null : `${trafficData.day}T${trafficData.time}` },
      request,
    };
  };

  const commitVersion = ({ results: r, locations, inputs, request, label, source, trigger, settings, baseId }) => {
    const prev = versions[versions.length - 1];
    const v = {
      id: (prev?.id || 0) + 1, label, source, trigger: trigger || null,
      results: r, locations, inputs, request: request || null, settings: settings || { ...snapshotSettings(), vehicles: request?.vehicles || vehicles },
    };
    setVersions(list => [...list, v].slice(-MAX_VERSIONS));
    setCompareBase(baseId ?? prev?.id ?? null);
    setShowCompare(true);
    setResults(r);
    if (request?.vehicles) setVehicles(request.vehicles);
    setLastLocations(locations);
    setStale(null);
    setReplan(null);
    setReplanError(null);
    setDrive(null);
    setChargingPlans(null);
    setChargingError(null);
    if (prev) mainRef.current?.scrollTo({ top: 0, behavior: 'smooth' });
    return v;
  };

  const currentVersion = versions[versions.length - 1];
  // What the drive simulation's rolling replan re-solves with: the request and inputs of
  // the plan on screen. Only plans timed on traffic data have a departure to replan from.
  const shownVersion = versions.find(v => v.results === results);
  const driveReplanContext = useMemo(() => (
    shownVersion?.request?.departure_time && shownVersion.inputs?.elevations
      ? { request: shownVersion.request, inputs: shownVersion.inputs, locations: shownVersion.locations }
      : null
  ), [shownVersion]);
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
      if (!err.superseded) setError(err.message || 'Failed to optimize route');
    } finally {
      setLoading(false);
    }
  };

  // Find historical crash hotspots on the route; reroutes for every on/off combination come back at once
  const findHotspots = async () => {
    setReplanning(true);
    setReplanError(null);

    try {
      const response = await fetch(`${API_BASE}/replan_incidents`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          legs: incidentLegs(results),
          num_accidents: NUM_ACCIDENTS,
          energy_params: energyParams,
          expected_energy_model: results.energy_model
        })
      });

      const data = await response.json();

      if (data.status === 'error') {
        throw new Error(data.message);
      }

      setReplan(data);
      setActiveAccidents([]);
    } catch (err) {
      setReplanError(err.message || 'Failed to replan route');
    } finally {
      setReplanning(false);
    }
  };

  // Find a charging stop for every fleet vehicle that would end below the SoC reserve
  const findChargingStops = async () => {
    setPlanningCharging(true);
    setChargingError(null);

    try {
      const lowRoutes = results.ortools_fleet.routes.filter(belowReserve);
      const response = await fetch(`${API_BASE}/plan_charging`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          vehicles: lowRoutes.map(r => ({ vehicle_id: r.vehicle_id, battery_kwh: r.battery_capacity_kwh, reserve_pct: reserveOf(r), legs: r.legs })),
          energy_params: energyParams,
          expected_energy_model: results.energy_model
        })
      });

      const data = await response.json();

      if (data.status === 'error') {
        throw new Error(data.message);
      }

      setChargingPlans(Object.fromEntries(data.plans.map(p => [p.vehicle_id, p])));
    } catch (err) {
      setChargingError(err.message || 'Failed to find charging stops');
    } finally {
      setPlanningCharging(false);
    }
  };

  const toggleAccident = (id) => {
    setActiveAccidents(ids => (ids.includes(id) ? ids.filter(i => i !== id) : [...ids, id]));
  };

  const activeLegs = (replan && activeAccidents.length > 0 && replan.scenarios[scenarioKey(activeAccidents)]) || [];
  // ── Chat-driven re-solve ──────────────────────────────────────────────
  // The chatbot applies its changes, then hands the turn here. The re-solve
  // runs in an effect so it sees the state those changes produced.
  const handleChatTurn = (turn) => new Promise(resolve => {
    if (turn.lang) chatLang.current = turn.lang;
    const wantsRun = turn.action?.type === 'resolve';
    if (!results && !wantsRun && turn.action?.type !== 'breakdown') return resolve(null);
    const id = Date.now() + Math.random();
    resolveWaiters.current[id] = resolve;
    setResolveRequest({ id, ...turn });
  });

  // After each solve, ask whether the plan has a problem worth fixing. The
  // backend tests candidate fixes by re-solving with the same matrices, and the
  // chat offers one that works as a yes / no question. A suggestion already
  // made for these same settings (taken or turned down) is not made again.
  const [proactive, setProactive] = useState(null);
  const suggested = useRef(new Set());
  const chatLang = useRef('en');
  useEffect(() => {
    const v = versions[versions.length - 1];
    if (!v?.request || v.source === 'breakdown' || v.results?.ortools_fleet?.disruption) return;
    let cancelled = false;
    const generation = solveSequence.current;
    (async () => {
      try {
        const res = await fetch(`${API_BASE}/suggest`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ ...v.request, precomputed: v.inputs, results: v.results,
            plan_context: JSON.stringify(v.settings), lang: 'en' }),
        });
        const s = (await res.json()).suggestion;
        if (cancelled || generation !== solveSequence.current || !s) return;
        const key = `${s.kind}|${s.fix?.label || '-'}|${JSON.stringify(v.settings)}`;
        if (suggested.current.has(key)) return;
        suggested.current.add(key);
        setProactive({ id: `v${v.id}-${s.kind}`, ...s });
      } catch { /* a suggestion is optional */ }
    })();
    return () => { cancelled = true; };
  }, [versions]);

  const runBreakdown = async (req) => {
    const generation = ++solveSequence.current;
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
        expected_energy_model: base.results.energy_model,
        goals: base.settings.goals,
        plan,
        event: { vehicle_index: a.vehicle_index, after_stop: a.after_stop, at_minute: a.at_minute, spare_truck: a.spare_truck },
        precomputed: base.inputs,
        lang: req.lang,
      })
    });
    const data = await response.json();
    if (data.status === 'error') throw new Error(data.message);
    if (generation !== solveSequence.current) { const err = new Error('Configuration changed'); err.superseded = true; throw err; }
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
    const generation = ++solveSequence.current;
    const before = currentVersion;
    let locations = useDepot && validatedDepot && validatedLocations.length >= 2
      ? [validatedDepot, ...validatedLocations] : null;
    if (!locations) {
      setResolvingNote('Geocoding the new stops…');
      locations = await geocodeAll();
    }
    setResolvingNote('Re-solving with your chat changes…');
    const out = await callOptimize(locations, before, generation);
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
        if (!err.superseded) setStale(`Couldn't re-solve: ${err.message}`);
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
      case 'vehicle_style_overrides':
        solveSequence.current += 1;
        setVehicles(prev => applyStyleOverrides(prev, value));
        setDrive(null);
        setProactive(null);
        break;
      case 'driver_assignments':
        solveSequence.current += 1;
        setVehicles(value);
        setDrive(null);
        setProactive(null);
        break;
      case 'traffic':
        setTraffic(value?.departure || null);
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
            driver: v.driver || '', driver_id: v.driver_id || null, driver_snapshot: v.driver_snapshot || null,
            driver_default_style: v.driver_default_style || 'normal', driving_style_override: v.driving_style_override ?? null,
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
    fleet: vehicles.map(v => ({ ...v })),
    temperature_c: energyParams.temperature_c,
    unload_time_min: energyParams.unload_time_min,
    has_climate_control: energyParams.has_climate_control,
    plan_context: JSON.stringify(snapshotSettings()),
    traffic: { departure: traffic },
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
    { id: 'simulate', label: 'Drive Sim', icon: Activity },
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
        <span className="text-xs text-slate-600 hidden lg:block">Held-Karp TSP · OR-Tools CVRP · City NN Energy Model</span>
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

      {/* ── Drive simulation tab ── */}
      <div className={`flex flex-1 min-h-0 ${activeTab === 'simulate' ? '' : 'hidden'}`}>
        <BenchmarkSimulation />
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
              traffic={traffic}
              onTrafficChange={(t) => { setTraffic(t); if (results) setStale('Traffic changed — click Optimize Routes to re-solve.'); }}
              drivingStyle={vehicles.every(v => effectiveStyle(v) === effectiveStyle(vehicles[0])) ? effectiveStyle(vehicles[0]) : 'mixed'}
              onStyleChange={st => changeDriverConfig(vehicles.map(v => ({ ...v, driving_style_override: st })), `All trucks → ${st} for this run`)}
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
                City NN · synthetic driving traces · each truck uses its effective driving style and unloading weight
              </p>
              <div className="mt-2 p-2 bg-slate-900 rounded-lg border border-slate-800">
                <p className="text-[9px] text-slate-500 mb-1 font-semibold uppercase tracking-wider">Calculated with:</p>
                <p className="text-[9px] text-slate-400 font-mono leading-relaxed break-all">
                  Energy = City NN propulsion + auxiliary energy over driving time
                </p>
                <p className="text-[9px] text-slate-600 mt-1 leading-relaxed">
                  Synthetic acceleration, speed, elevation, temperature and load features. Predictions are estimates; Eco savings are checked by replanning.
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
                    <VehicleDriverControls vehicle={v} drivers={drivers}
                      onAssign={driver => changeDriverConfig(assignDriver(vehicles, v.id, driver), `${v.name}: driver ${driver?.name || 'unassigned'}`)}
                      onOverride={style => changeDriverConfig(applyStyleOverrides(vehicles, [{ vehicle_id: v.id, driving_style_override: style }]), `${v.name}: ${style || 'driver default'}`)} />
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

            {/* Travel Times */}
            <section>
              <h3 className="text-[11px] font-semibold text-slate-500 uppercase tracking-widest mb-3">Travel Times</h3>
              <select
                value={trafficData.source}
                onChange={(e) => updateTrafficData('source', e.target.value)}
                className="w-full px-2 py-1.5 bg-slate-800 border border-slate-700 rounded-lg text-xs text-white focus:ring-1 focus:ring-[#C8002F] focus:border-transparent"
              >
                {TRAFFIC_SOURCES.map(([value, text]) => <option key={value} value={value}>{text}</option>)}
              </select>
              {trafficData.source !== 'google' && (
                <>
                  <div className="grid grid-cols-2 gap-2 mt-2">
                    <select
                      value={trafficData.day}
                      onChange={(e) => updateTrafficData('day', e.target.value)}
                      className="w-full px-2 py-1 bg-slate-800 border border-slate-700 rounded text-xs text-white focus:ring-1 focus:ring-[#C8002F] focus:border-transparent"
                    >
                      {TRAFFIC_DAYS.map(([value, text]) => <option key={value} value={value}>{text}</option>)}
                    </select>
                    <input
                      type="time" step="300"
                      value={trafficData.time}
                      onChange={(e) => updateTrafficData('time', e.target.value)}
                      className="w-full px-2 py-1 bg-slate-800 border border-slate-700 rounded text-xs text-white text-center focus:ring-1 focus:ring-[#C8002F] focus:border-transparent"
                    />
                  </div>
                  <p className="text-[10px] text-slate-600 mt-2 leading-relaxed">
                    Traffic data covers only the Cerritos–Stanton area (load Demo 0). Overrides the Google traffic setting. Turn the Time goal up so traffic can change the stop order; the Held-Karp route is then driven on the measured speeds.
                  </p>
                </>
              )}
            </section>

            <div className="border-t border-slate-800" />

            {/* Delivery Stops */}
            <section className={`rounded-lg transition-shadow duration-500 ${flash.stops ? 'ring-2 ring-emerald-400/70 shadow-lg shadow-emerald-500/10 p-2 -m-2' : ''}`}>
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-[11px] font-semibold text-slate-500 uppercase tracking-widest">Delivery Stops</h3>
                <div className="flex items-center gap-3">
                  <button
                    disabled={SAVED_DEMO}
                    onClick={() => loadDemoAddresses('/demo_stops_traffic_area.txt')}
                    title="Stops inside the traffic data area (Cerritos – Stanton)"
                    className="text-xs text-purple-400 hover:text-purple-300 transition-colors flex items-center gap-1"
                  >
                    <Download className="w-3 h-3" /> Demo 0
                  </button>
                  <button
                    onClick={() => loadDemoAddresses('/demo_stops.txt')}
                    title="Anaheim depot with stops across LA / Orange County"
                    className="text-xs text-purple-400 hover:text-purple-300 transition-colors flex items-center gap-1"
                  >
                    <Download className="w-3 h-3" /> Demo 1
                  </button>
                </div>
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
            {SAVED_DEMO && <div className="rounded-lg border border-sky-500/30 bg-sky-950/30 p-3 text-xs text-sky-200">
              <p className="font-semibold">Anaheim saved-data demo</p>
              <p className="mt-1 text-sky-200/70">City NN runs locally. Road matrices are cached; the map shows stop order. Live traffic and Google map searches are unavailable.</p>
              <button className="underline mt-2" onClick={loadSavedDemo}>Reload sample fleet</button>
            </div>}
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
                    {!fleet && ` · ${hk?.driver || 'Unassigned driver'} · ${hk?.effective_driving_style || 'normal'} (${hk?.style_source === 'run_override' ? 'run override' : 'driver default'})`}
                    {fleet && ` · ${fleet.num_vehicles_used} vehicle${fleet.num_vehicles_used !== 1 ? 's' : ''} dispatched`}
                    {fleet && fleet.num_vehicles_requested !== fleet.num_vehicles_used && ` (${fleet.num_vehicles_requested} requested)`}
                    {results.solver && ` · solver: ${results.solver.requested === 'auto' ? 'auto' : results.solver.baseline_label}`}
                    {results.goals && ` · goals: ${goalsSummary(results.goals)}`}
                    {' · City NN energy'}
                  </p>
                  {hk.traffic && (
                    <p className="text-xs text-slate-500 mt-1">
                      Travel times: {label(TRAFFIC_SOURCES, hk.traffic.source)} · departing {label(TRAFFIC_DAYS, hk.traffic.departure_time.slice(0, 10))} {hk.traffic.departure_time.slice(11, 16)}
                      {' · '}driven on ground truth:{' '}
                      <span className={`font-semibold ${hk.traffic.actual_travel_time_s > hk.traffic.planned_travel_time_s ? 'text-red-400' : 'text-green-400'}`}>
                        {Math.round(hk.traffic.actual_travel_time_s / 60)} min
                      </span>
                      {' '}of driving (planned {Math.round(hk.traffic.planned_travel_time_s / 60)} min)
                    </p>
                  )}
                  {!hk?.traffic && (
                  <p className="text-xs mt-0.5 text-slate-500">
                    {results.traffic
                      ? <>Traffic: <span className="text-sky-300">{trafficLabel(results.traffic.departure)}</span>
                          {' '}· departs {results.traffic.departure_local} ({results.traffic.time_zone})</>
                      : 'Traffic: off — Google\'s typical travel times'}
                  </p>
                  )}
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
                    {hk.battery_depletion && (
                      <p className="text-[10px] text-red-300/80 text-right -mt-1">{depletionText(hk.battery_depletion)}</p>
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

              {/* Incident replanning — on the fleet's routes when there is one, else Held-Karp's */}
              {hk.legs.length > 0 && (() => {
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
                        {/* Drive simulation: needs traffic data (measured leg times) for every route it drives */}
                        {(fleet ? fleet.routes.length > 0 && fleet.routes.every(r => r.traffic) : hk.traffic) && (drive ? (
                          <>
                            <select
                              value={drive.speed}
                              onChange={(e) => setDrive({ ...drive, speed: Number(e.target.value) })}
                              className="px-2 py-1.5 bg-slate-800 border border-slate-700 text-slate-300 rounded-lg text-xs"
                              title="Simulation speed"
                            >
                              {SIM_SPEEDS.map(s => <option key={s} value={s}>{s}× speed</option>)}
                            </select>
                            <button
                              onClick={() => setDrive(null)}
                              className="flex items-center gap-1.5 px-3 py-1.5 bg-slate-800 hover:bg-slate-700 border border-slate-700 text-slate-300 rounded-lg text-xs transition-colors"
                            >
                              <Square className="w-3 h-3" /> Stop Drive
                            </button>
                          </>
                        ) : (
                          <button
                            onClick={() => setDrive({ runId: Date.now(), speed: SIM_SPEEDS[1] })}
                            title={`Drive ${fleet ? 'every truck\'s route' : 'the route'} on the measured traffic; trucks detour around active accidents and replan every 5 min while driving`}
                            className="flex items-center gap-1.5 px-3 py-1.5 bg-green-500/10 hover:bg-green-500/20 border border-green-500/30 text-green-400 rounded-lg text-xs font-medium transition-colors"
                          >
                            <Play className="w-3 h-3" /> Simulate Drive
                          </button>
                        ))}
                        {replan && (
                          <button
                            onClick={() => { setReplan(null); setActiveAccidents([]); setDrive(null); }}
                            className="flex items-center gap-1.5 px-3 py-1.5 bg-slate-800 hover:bg-slate-700 border border-slate-700 text-slate-300 rounded-lg text-xs transition-colors"
                          >
                            <RotateCcw className="w-3 h-3" /> Reset
                          </button>
                        )}
                        <button
                          title={SAVED_DEMO ? 'Requires live Google Maps credentials' : undefined}
                          onClick={findHotspots}
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
                                  onChange={() => toggleAccident(a.id)}
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
                );
              })()}

              {/* Map */}
              {SAVED_DEMO ? <SchematicRouteMap results={results} locations={lastLocations} /> : <RouteMap
                results={results}
                locations={lastLocations}
                replan={replan}
                activeAccidents={activeAccidents}
                activeLegs={activeLegs}
                chargingPlans={chargingPlans ? Object.values(chargingPlans) : []}
                drive={drive}
                replanContext={driveReplanContext}
              />}

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
                      {results.ortools_fleet.routes.some(belowReserve) && (
                        <button
                          title={SAVED_DEMO ? 'Requires live Google Maps credentials' : undefined}
                          onClick={findChargingStops}
                          disabled={SAVED_DEMO || planningCharging || chargingPlans !== null}
                          className="ml-auto flex items-center gap-1.5 px-3 py-1.5 bg-cyan-500/10 hover:bg-cyan-500/20 disabled:opacity-40 disabled:cursor-not-allowed border border-cyan-500/30 text-cyan-300 rounded-lg text-xs font-medium transition-colors"
                        >
                          {planningCharging
                            ? <><Loader2 className="w-3 h-3 animate-spin" /> Finding chargers...</>
                            : <><Zap className="w-3 h-3" /> Find Charging Stops</>
                          }
                        </button>
                      )}
                    </div>
                    {chargingError && (
                      <p className="px-4 pt-3 text-xs text-red-400">{chargingError}</p>
                    )}
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
                            <span className="text-[10px] text-slate-400">{route.effective_driving_style || 'normal'} · {route.style_source === 'run_override' ? 'run override' : route.driver_id ? 'driver default' : 'default'}</span>
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
                                  belowReserve(route) ? 'text-orange-400' :
                                  'text-green-400'
                                }`}>
                                  {route.battery_depleted ? 'DEPLETED'
                                    : belowReserve(route) ? `${route.battery_remaining_pct}% · BELOW ${reserveOf(route)}% RESERVE`
                                    : `${route.battery_remaining_pct}%`}
                                </span>
                              </div>
                              <div className="relative h-1.5 bg-slate-700 rounded-full overflow-hidden">
                                {/* Reserve the vehicle must be back at the depot with */}
                                <div className="absolute inset-y-0 w-px bg-slate-400 z-10" style={{ left: `${reserveOf(route)}%` }} />
                                <div
                                  className={`h-full rounded-full transition-all ${
                                    route.battery_depleted ? 'bg-red-500' :
                                    belowReserve(route) ? 'bg-orange-500' :
                                    'bg-green-500'
                                  }`}
                                  style={{ width: `${Math.min(100, route.battery_remaining_pct || 0)}%` }}
                                />
                              </div>
                              {route.battery_depletion && (
                                <p className="text-[10px] text-red-300/80 mt-1">{depletionText(route.battery_depletion)}</p>
                              )}
                              {chargingPlans?.[route.vehicle_id] && (() => {
                                const { stop } = chargingPlans[route.vehicle_id];
                                const pct = (kwh) => `${Math.round(kwh / route.battery_capacity_kwh * 100)}%`;
                                return stop ? (
                                  <p className="mt-1.5 text-[10px] text-cyan-300">
                                    With charging stop: back at depot with {pct(stop.final_soc_kwh)} ·{' '}
                                    <span className="text-slate-400">
                                      +{Math.round(stop.added_time_s / 60)} min ({Math.max(0, Math.round(stop.detour_time_s / 60))} min detour + {Math.round(stop.charge_time_s / 60)} min charging)
                                    </span>
                                  </p>
                                ) : (
                                  <p className="mt-1.5 text-[10px] text-red-400">
                                    No charger along this route keeps the {reserveOf(route)}% reserve with a single stop.
                                  </p>
                                );
                              })()}
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
                                const charge = chargingPlans?.[route.vehicle_id]?.stop;
                                const pct = (kwh) => `${Math.round(kwh / route.battery_capacity_kwh * 100)}%`;
                                return (
                                  <React.Fragment key={i}>
                                  <div className="flex items-start gap-2">
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
                                  {charge && charge.leg_index === i && (
                                    <div className="flex items-start gap-2">
                                      <div className="w-5 h-5 flex-shrink-0 bg-cyan-500/20 rounded flex items-center justify-center">
                                        <Zap className="w-3 h-3 text-cyan-300" />
                                      </div>
                                      <div className="min-w-0">
                                        <p className="text-cyan-300 text-xs truncate leading-5">
                                          {charge.charger.name}{charge.charger.max_kw ? ` · ${charge.charger.max_kw} kW` : ''}
                                          <span className="text-slate-500"> — {charge.charger.address}</span>
                                        </p>
                                        <p className="text-[10px] text-slate-500">
                                          arrive {pct(charge.arrival_soc_kwh)} → charge {charge.charge_kwh.toFixed(1)} kWh at {charge.charge_kw} kW ({Math.round(charge.charge_time_s / 60)} min) → leave {pct(charge.depart_soc_kwh)}
                                        </p>
                                      </div>
                                    </div>
                                  )}
                                  </React.Fragment>
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
      <AIChatbot onProfilesChanged={refreshDrivers} formState={chatFormState} onApplyChange={applyChatChange} onTurn={handleChatTurn} proactive={proactive}
                 onDockChange={setChatDocked} />
    </div>
  );
}

export default RouteOptimizer;
