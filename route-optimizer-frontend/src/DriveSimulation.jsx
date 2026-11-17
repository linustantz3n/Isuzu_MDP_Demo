import React, { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { MarkerF, PolylineF } from '@react-google-maps/api';
import { API_BASE } from './api';

// Drive simulation: trucks drive their routes on the map on one shared clock, each
// leg taking its measured (ground-truth) time, sped up. One truck for the Held-Karp
// route, or every truck of an OR-Tools fleet.
//
// The clock keeps running while the system reacts:
//   • Crashes: the detour /api/replan_incidents already found is used when the truck
//     can join it; otherwise only that truck waits while /api/reroute_from looks for
//     one. A detour up to AUTO_DETOUR_MAX_S longer is taken automatically (with Undo);
//     a longer one holds only that truck until the operator decides.
//   • Rolling replan: every REPLAN_INTERVAL_S of sim time, each truck's remaining stops
//     are re-sequenced in the background (/api/replan_tick). A truck is locked to the
//     stop it is driving to; the result is applied only if it arrives before the truck
//     leaves that stop, otherwise it is logged as too late.
// The whole simulation pauses only for big events: a crash with no way around, or a
// replan that finds stops no longer fit a truck's battery.

export const SIM_SPEEDS = [60, 120, 300, 600];  // simulated seconds per real second
const PROMPT_AHEAD_M = 3000;  // react this far before the accident (room for a detour)
const REROUTE_COLOR = '#22c55e';
export const REPLAN_INTERVAL_S = 300;       // rolling replan every 5 simulated minutes
export const AUTO_DETOUR_MAX_S = 600;       // take detours up to 10 min longer without asking
const JOIN_M = 60;                          // a truck this close to a precomputed detour can join it
const ACCIDENT_ON_PATH_M = 80;              // an accident this close to a replanned leg lies on it
const TOAST_MS = 7000;                      // real milliseconds a notification stays up
const LOG_SHOWN = 6;
// Truck seen from above, pointing north (cab ahead of the box); rotated to its heading
const TRUCK_PATH = 'M -4,-2 L 4,-2 L 4,10 L -4,10 Z M -3.5,-9 L 3.5,-9 L 3.5,-3.5 L -3.5,-3.5 Z';

// --- Geometry on small areas: local flat projection in metres ---
const toXY = (p, ref) => [
  (p.lng - ref.lng) * 111320 * Math.cos(ref.lat * Math.PI / 180),
  (p.lat - ref.lat) * 110540,
];
const distM = (a, b) => Math.hypot(...toXY(b, a));
const headingDeg = (a, b) => {
  const [x, y] = toXY(b, a);
  return (Math.atan2(x, y) * 180 / Math.PI + 360) % 360;
};

// Path with cumulative metres at each point
const measure = (path) => {
  const cum = [0];
  for (let i = 1; i < path.length; i++) cum.push(cum[i - 1] + distM(path[i - 1], path[i]));
  return { path, cum, length: cum[cum.length - 1] };
};

// Position and heading at `along` metres
const pointAt = ({ path, cum }, along) => {
  let i = 1;
  while (i < cum.length - 1 && cum[i] < along) i++;
  const a = path[i - 1], b = path[i] || a;
  const seg = cum[i] - cum[i - 1];
  const t = seg > 0 ? Math.min(1, Math.max(0, (along - cum[i - 1]) / seg)) : 0;
  return {
    lat: a.lat + (b.lat - a.lat) * t,
    lng: a.lng + (b.lng - a.lng) * t,
    heading: seg > 0 ? headingDeg(a, b) : 0,
  };
};

// Metres along the path of the point closest to p
const alongOf = ({ path, cum }, p) => {
  let best = Infinity, bestAlong = 0;
  for (let i = 1; i < path.length; i++) {
    const [ax, ay] = toXY(path[i - 1], p);
    const [bx, by] = toXY(path[i], p);
    const dx = bx - ax, dy = by - ay;
    const sq = dx * dx + dy * dy;
    const t = sq ? Math.min(1, Math.max(0, -(ax * dx + ay * dy) / sq)) : 0;
    const d = Math.hypot(ax + t * dx, ay + t * dy);
    if (d < best) { best = d; bestAlong = cum[i - 1] + t * (cum[i] - cum[i - 1]); }
  }
  return bestAlong;
};

// Where on `line` a point at p can join it, or null if it is more than maxM away
const joinAt = (line, p, maxM = JOIN_M) => {
  if (line.path.length < 2) return null;
  const along = alongOf(line, p);
  return distM(pointAt(line, along), p) <= maxM ? along : null;
};

const fmtClock = (ms) => new Date(ms).toISOString().slice(11, 16);  // departure is local wall time
const fmtMin = (s) => `${Math.round(s / 60)} min`;
const fmtKm = (m) => `${(m / 1000).toFixed(1)} km`;
const fmtDiff = (s) => `${s >= 0 ? '+' : '−'}${fmtMin(Math.abs(s))}`;
const fmtKmDiff = (m) => `${m >= 0 ? '+' : '−'}${fmtKm(Math.abs(m))}`;
const diffClass = (s) => (s > 60 ? 'text-orange-400' : s < -60 ? 'text-green-400' : 'text-slate-300');
const fmtCountdown = (s) => {
  const total = Math.max(0, Math.ceil(s));
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`;
};

// When the next replan runs: the next multiple of the interval after t (seconds
// since departure), how long until then, and how many replans have already passed.
export const replanSchedule = (t, intervalS = REPLAN_INTERVAL_S) => {
  const count = Math.floor(t / intervalS);
  const nextS = (count + 1) * intervalS;
  return { nextS, leftS: nextS - t, count };
};

// What /api/replan_tick needs about one truck at sim time t, or null when there is
// nothing to re-sequence (done, heading home, or one stop left after its locked one).
// The truck is locked to the stop it is driving to or unloading at.
export const tickTruckState = (tr, t, meta) => {
  if (tr.phase === 'done' || !tr.order) return null;
  const k = tr.leg;
  const fromNode = tr.order[k + 1];
  if (fromNode == null || fromNode === 0) return null;
  const remaining = tr.order.slice(k + 2).filter(n => n !== 0);
  if (remaining.length < 2) return null;
  const leaveIn = tr.phase === 'unloading'
    ? Math.max(tr.unloadLeft, 0)
    : Math.max(tr.line.length - tr.along, 0) / Math.max(tr.mps, 1e-6) + meta.unloadS;
  return {
    vehicle_index: meta.vehicleIndex,
    locked_leg: k,
    from_node: fromNode,
    start_s: Math.round(t + leaveIn),
    remaining,
    used_kwh: tr.legs.slice(0, k + 1).reduce((sum, l) => sum + (l.energyKwh || 0), 0),
    cargo_per_stop_lbs: meta.cargoPerStopLbs || 0,
  };
};

// The truck's legs and node order with a replan result swapped in after its locked
// stop, or null if it is too late (the truck has already left that stop).
export const spliceReplan = (tr, r) => {
  if (tr.phase === 'done' || tr.leg !== r.locked_leg || tr.order?.[tr.leg + 1] !== r.order[0]) return null;
  const k = tr.leg;
  return {
    legs: [...tr.legs.slice(0, k + 1), ...r.legs.map(l => ({
      path: l.path, durationS: l.measured_s, plannedS: l.planned_s, to: l.to, toLabel: l.to_label,
      energyKwh: l.energy_kwh, reroute: null, replanned: true,
    }))],
    order: [...tr.order.slice(0, k + 1), ...r.order],
  };
};

// Accidents on a truck's legs after its legs from `fromLeg + 1` on were replaced:
// those on earlier legs stay, the rest are placed on whichever new leg passes them.
export const placeAccidents = (accidents, legs, fromLeg) => accidents.flatMap(a => {
  if (a.leg_index <= fromLeg) return [a];
  for (let j = fromLeg + 1; j < legs.length; j++) {
    if (joinAt(measure(legs[j].path), a, ACCIDENT_ON_PATH_M) !== null) return [{ ...a, leg_index: j }];
  }
  return [];
});

// Planned time for a truck's day: driving (planned leg times) plus unloading at every stop
const plannedTotalS = (truck) => truck.legs.reduce((s, l) => s + l.plannedS, 0) + truck.unloadS * (truck.legs.length - 1);

/**
 * trucks: [{id, label, color, unloadS, order (node indices, ending at the depot), vehicleIndex,
 *           cargoPerStopLbs,
 *           legs: [{path: [{lat, lng}], durationS (measured), plannedS, energyKwh, toLabel, to: {lat, lng},
 *                   reroute: precomputed detour for its active accidents or null}],
 *           accidents: active accidents on this truck's route [{id, lat, lng, leg_index (on this truck), road}]}]
 *   One entry: the single-truck view. Two or more: a fleet, each truck in its route colour.
 * departureTime: 'YYYY-MM-DDTHH:MM' (local time of the traffic data)
 * overlay: DOM node over the map for the clock, notifications and prompts
 * replanContext: {request, inputs, locations} of the plan, for rolling replans; null turns them off
 */
export default function DriveSimulation({ trucks, departureTime, speed, overlay, onEnd, replanContext = null }) {
  const sim = useRef(null);
  const [view, setView] = useState(null);       // what the map and overlay show
  const [prompt, setPrompt] = useState(null);   // a truck waiting for the operator: {truck, accident, option}
  const [big, setBig] = useState(null);         // a big event that pauses everything
  const [reroutes, setReroutes] = useState([]); // detours taken: [{id, path}]
  const [replanned, setReplanned] = useState({}); // truck index -> replanned leg paths
  const [log, setLog] = useState([]);           // what happened: [{t, who, text, sub}]
  const [toasts, setToasts] = useState([]);     // notifications: [{id, text, tone, undo, canUndo}]
  const speedRef = useRef(speed);
  speedRef.current = speed;
  const ctxRef = useRef(replanContext);
  ctxRef.current = replanContext;
  const onEndRef = useRef(onEnd);
  onEndRef.current = onEnd;
  const handlers = useRef({});
  const seq = useRef(0);

  const fleet = trucks.length > 1;
  const departureMs = Date.parse(`${departureTime}:00Z`);
  const plannedS = trucks.map(plannedTotalS);
  const plannedEndS = Math.max(...plannedS);
  const replanOn = !!replanContext && trucks.every(t => t.order);
  const allAccidents = [...new Map(trucks.flatMap(t => t.accidents).map(a => [a.id, a])).values()];

  const addLog = (entry) => setLog(l => [...l, entry]);
  const toast = (text, tone = 'info', undo = null, canUndo = null) => {
    const id = ++seq.current;
    setToasts(list => [...list, { id, text, tone, undo, canUndo }]);
    setTimeout(() => setToasts(list => list.filter(x => x.id !== id)), TOAST_MS);
  };
  const dismissToast = (id) => setToasts(list => list.filter(x => x.id !== id));

  // Start (or restart) the drive
  useEffect(() => {
    const s = {
      phase: 'running', t: 0,
      trucks: trucks.map((truck, index) => ({
        index, phase: 'driving', unloadLeft: 0, finishT: null, hold: null,
        legs: [...truck.legs], order: truck.order ? [...truck.order] : null,
        accidents: [...truck.accidents], handled: new Set(),
      })),
      replan: { nextS: REPLAN_INTERVAL_S, inFlight: null, runs: 0, changes: 0, skipped: 0, last: null },
      queue: [],     // trucks waiting for an operator prompt while another one is open
      promptOpen: false,
    };
    s.trucks.forEach(tr => handlers.current.startLeg(tr, 0));
    sim.current = s;
    setPrompt(null);
    setBig(null);
    setReroutes([]);
    setReplanned({});
    setLog([]);
    setToasts([]);

    // Move one truck forward by dt; returns the accident it has to react to, if any
    const advance = (tr, dt) => {
      if (tr.hold) return null;
      const truck = trucks[tr.index];
      if (tr.phase === 'driving') {
        tr.along += tr.mps * dt;
        const next = tr.pending[0];
        if (next && tr.along >= next.along - PROMPT_AHEAD_M) {
          tr.along = Math.max(0, Math.min(tr.along, next.along - PROMPT_AHEAD_M));
          return next;
        }
        if (tr.along >= tr.line.length) {
          tr.along = tr.line.length;
          if (tr.leg === tr.legs.length - 1) {
            tr.phase = 'done';
            tr.finishT = s.t;
          } else {
            tr.phase = 'unloading';
            tr.unloadLeft = truck.unloadS;
          }
        }
      } else if (tr.phase === 'unloading') {
        tr.unloadLeft -= dt;
        if (tr.unloadLeft <= 0) {
          handlers.current.startLeg(tr, tr.leg + 1);
          tr.along = tr.mps * -tr.unloadLeft;  // don't lose the overshoot
          tr.phase = 'driving';
        }
      }
      return null;
    };

    let last = performance.now();
    let frame;
    const tick = (now) => {
      const dt = Math.min((now - last) / 1000, 0.1) * speedRef.current;
      last = now;
      if (s.phase === 'running') {
        s.t += dt;
        for (const tr of s.trucks) {
          const accident = advance(tr, dt);
          if (accident) handlers.current.onCrashAhead(s, tr, accident);
        }
        // Rolling replan: due every interval; a tick that comes due while the last one
        // is still solving is skipped (the next one starts from fresher state anyway).
        if (handlers.current.replanOn() && s.t >= s.replan.nextS) {
          while (s.replan.nextS <= s.t) {
            s.replan.nextS += REPLAN_INTERVAL_S;
            if (s.replan.inFlight) s.replan.skipped += 1;
            else handlers.current.fireReplan(s);
          }
        }
        if (s.phase === 'running' && s.trucks.every(tr => tr.phase === 'done')) {
          s.phase = 'done';
          onEndRef.current && onEndRef.current();
        }
      }
      setView({
        phase: s.phase, t: s.t,
        replan: { ...s.replan, inFlight: s.replan.inFlight && { ...s.replan.inFlight } },
        trucks: s.trucks.map(tr => ({
          ...pointAt(tr.line, tr.along), phase: tr.phase, leg: tr.leg, unloadLeft: tr.unloadLeft,
          finishT: tr.finishT, hold: tr.hold, toLabel: tr.legs[tr.leg].toLabel, numLegs: tr.legs.length,
        })),
      });
      if (s.phase !== 'done') frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => { cancelAnimationFrame(frame); s.phase = 'done'; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [trucks, departureTime]);

  // ---- Truck legs ----------------------------------------------------------------
  const startLeg = (tr, k) => {
    const line = measure(tr.legs[k].path);
    tr.leg = k;
    tr.line = line;
    tr.along = 0;
    tr.mps = line.length / Math.max(tr.legs[k].durationS, 1);
    tr.pending = tr.accidents
      .filter(a => a.leg_index === k && !tr.handled.has(a.id))
      .map(a => ({ ...a, along: alongOf(line, a) }))
      .sort((a, b) => a.along - b.along);
  };

  // ---- Crashes -------------------------------------------------------------------
  const isLive = (s) => sim.current === s && s.phase !== 'done';

  const onCrashAhead = (s, tr, accident) => {
    tr.hold = 'crash';
    tr.handled.add(accident.id);
    const here = pointAt(tr.line, tr.along);
    const leg = tr.legs[tr.leg];
    // 1. The detour found when the accidents were switched on, if the truck can still join it
    if (leg.reroute) {
      const line = measure(leg.reroute.path);
      const along = joinAt(line, here);
      if (along !== null) {
        decideDetour(s, tr, accident, {
          source: 'precomputed', line, along, method: leg.reroute.method,
          ratio: leg.reroute.reroutedS / Math.max(leg.reroute.originalS, 1),
          deltaS: leg.reroute.reroutedS - leg.reroute.originalS,
          deltaM: leg.reroute.reroutedM - leg.reroute.originalM,
        });
        return;
      }
    }
    // 2. Otherwise ask for one from here; only this truck waits
    fetch(`${API_BASE}/reroute_from`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        position: here,
        destination: leg.to,
        accidents: tr.pending.map(a => ({ lat: a.lat, lng: a.lng })),
      }),
    })
      .then(r => r.json())
      .then(data => {
        if (!isLive(s)) return;
        if (data.status === 'error') throw new Error(data.message);
        if (data.method === 'clear') {
          tr.pending = tr.pending.slice(1);
          tr.hold = null;
          addLog({ t: s.t, who: trucks[tr.index].label, text: `#${accident.id}: road ahead already clear` });
          return;
        }
        decideDetour(s, tr, accident, {
          source: 'live', line: measure(data.rerouted.path), along: 0, method: data.method,
          ratio: data.rerouted.time_s / Math.max(data.original.time_s, 1),
          deltaS: data.rerouted.time_s - data.original.time_s,
          deltaM: data.rerouted.distance_m - data.original.distance_m,
        });
      })
      .catch(err => {
        if (!isLive(s)) return;
        // No way around: a big event, everything pauses
        s.phase = 'paused';
        setBig({ kind: 'crash', truck: tr.index, accident, message: err.message || 'No route around the accident' });
      });
  };

  const decideDetour = (s, tr, accident, option) => {
    if (option.deltaS <= AUTO_DETOUR_MAX_S) {
      applyDetour(s, tr, accident, option, 'auto');
    } else if (s.promptOpen) {
      tr.hold = 'prompt';
      s.queue.push({ truck: tr.index, accident, option });
    } else {
      tr.hold = 'prompt';
      s.promptOpen = true;
      setPrompt({ truck: tr.index, accident, option });
    }
  };

  const applyDetour = (s, tr, accident, option, how) => {
    const who = trucks[tr.index].label;
    const prev = { line: tr.line, along: tr.along, mps: tr.mps, pending: tr.pending, leg: tr.leg };
    const remainingS = (tr.line.length - tr.along) / tr.mps;
    const newS = remainingS * option.ratio;
    tr.line = option.line;
    tr.along = option.along;
    tr.mps = Math.max(option.line.length - option.along, 1) / Math.max(newS, 1);
    tr.pending = [];   // the detour avoids every accident ahead on this leg
    tr.hold = null;
    const id = ++seq.current;
    setReroutes(r => [...r, { id, path: option.line.path }]);
    const sub = `${fmtDiff(newS - remainingS)}, ${fmtKmDiff(option.deltaM)}`;
    addLog({ t: s.t, who, text: `#${accident.id}: ${how === 'auto' ? 'detoured' : 'rerouted'}`, sub });
    if (how === 'auto') {
      // Undo while the truck is still on (or next to) the road it left
      const canUndo = () => sim.current === s && tr.leg === prev.leg && joinAt(prev.line, pointAt(tr.line, tr.along)) !== null;
      toast(`${who} detoured around crash #${accident.id} (${sub})`, 'detour', () => {
        if (!canUndo()) return;
        const along = joinAt(prev.line, pointAt(tr.line, tr.along));
        tr.line = prev.line;
        tr.along = along;
        tr.mps = prev.mps;
        tr.pending = prev.pending.filter(p => p.id !== accident.id && p.along > along);
        setReroutes(r => r.filter(x => x.id !== id));
        addLog({ t: s.t, who, text: `#${accident.id}: detour undone, kept route` });
      }, canUndo);
    }
  };

  const keepRoute = (s, tr, accident) => {
    tr.pending = tr.pending.filter(p => p.id !== accident.id);
    tr.hold = null;
    addLog({ t: s.t, who: trucks[tr.index].label, text: `#${accident.id}: kept route` });
  };

  const nextPrompt = (s) => {
    const next = s.queue.shift();
    s.promptOpen = !!next;
    setPrompt(next || null);
  };

  const decide = (reroute) => {
    const s = sim.current;
    const tr = s.trucks[prompt.truck];
    if (reroute) applyDetour(s, tr, prompt.accident, prompt.option, 'operator');
    else keepRoute(s, tr, prompt.accident);
    nextPrompt(s);
  };

  // ---- Rolling replan -----------------------------------------------------------
  const fireReplan = (s) => {
    const ctx = ctxRef.current;
    if (!ctx) return;
    s.replan.runs += 1;
    const tickT = s.t;
    const states = s.trucks
      .map(tr => tickTruckState(tr, tickT, trucks[tr.index]))
      .filter(Boolean);
    if (!states.length) {
      s.replan.last = { t: tickT, text: 'nothing left to re-sequence' };
      return;
    }
    const id = ++seq.current;
    s.replan.inFlight = { id, t: tickT };
    const req = ctx.request;
    fetch(`${API_BASE}/replan_tick`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        locations: ctx.locations, vehicles: req.vehicles, energy_params: req.energy_params,
        goals: req.goals, priority_stops: req.priority_stops, elevations: ctx.inputs.elevations,
        departure_time: departureTime, now_s: tickT, trucks: states,
        expected_energy_model: req.expected_energy_model,
      }),
    })
      .then(r => r.json())
      .then(data => {
        if (!isLive(s) || s.replan.inFlight?.id !== id) return;
        s.replan.inFlight = null;
        if (data.status === 'error') throw new Error(data.message);
        applyReplan(s, tickT, data);
      })
      .catch(err => {
        if (!isLive(s)) return;
        s.replan.inFlight = null;
        s.replan.last = { t: tickT, text: `failed: ${err.message || 'no response'}` };
      });
  };

  const applyReplan = (s, tickT, data) => {
    const alerts = data.trucks.filter(r => r.alert);
    let changed = 0, late = 0;
    for (const r of data.trucks) {
      if (r.alert || !r.changed) continue;
      const tr = s.trucks.find(x => trucks[x.index].vehicleIndex === r.vehicle_index);
      if (!tr) continue;
      if (swapIn(s, tr, r, tickT, true)) changed += 1;
      else late += 1;
    }
    s.replan.changes += changed;
    const parts = [changed ? `${changed} truck${changed === 1 ? '' : 's'} re-sequenced` : 'no change'];
    if (late) parts.push(`${late} too late`);
    s.replan.last = { t: tickT, text: parts.join(' · '), forecast: data.issued_at?.slice(11, 16) };
    if (alerts.length) {
      s.phase = 'paused';
      setBig({ kind: 'replan', tickT, alerts });
    }
  };

  // Swap a replan result into a truck; false if the truck has already left its locked stop
  const swapIn = (s, tr, r, tickT, notify) => {
    const next = spliceReplan(tr, r);
    const who = trucks[tr.index].label;
    if (!next) {
      addLog({ t: s.t, who, text: `replan from ${fmtClock(departureMs + tickT * 1000)} arrived after its next stop; kept its plan` });
      return false;
    }
    const prev = { legs: tr.legs, order: tr.order, accidents: tr.accidents, leg: tr.leg };
    const k = tr.leg;
    tr.legs = next.legs;
    tr.order = next.order;
    // Accidents on legs already driven keep their place; every other active accident
    // (any truck's) is placed on whichever new leg passes it.
    const keep = prev.accidents.filter(a => a.leg_index <= k);
    const others = allAccidents
      .filter(a => !tr.handled.has(a.id) && !keep.some(x => x.id === a.id))
      .map(a => ({ ...a, leg_index: Infinity }));
    tr.accidents = [...keep, ...placeAccidents(others, tr.legs, k)];
    setReplanned(m => ({ ...m, [tr.index]: tr.legs.slice(k + 1).map(l => l.path) }));
    const stops = r.legs.filter(l => l.to_node !== 0).map(l => l.to_label).join(' → ');
    const sub = `${fmtDiff(-r.saved_time_s)}, ${fmtKmDiff(-r.saved_distance_m)} (forecast)`;
    addLog({ t: s.t, who, text: r.dropped?.length ? `replanned, dropped ${r.dropped.length} stop${r.dropped.length === 1 ? '' : 's'}` : 'new stop order', sub });
    if (notify) {
      const canUndo = () => sim.current === s && tr.leg === prev.leg && tr.legs === next.legs;
      toast(`${fmtClock(departureMs + tickT * 1000)} replan · ${who}: ${stops || 'straight back'} (${sub})`, 'replan', () => {
        if (!canUndo()) return;
        tr.legs = prev.legs;
        tr.order = prev.order;
        tr.accidents = prev.accidents;
        setReplanned(m => { const c = { ...m }; delete c[tr.index]; return c; });
        addLog({ t: s.t, who, text: 'replan undone, back to the previous order' });
      }, canUndo);
    }
    return true;
  };

  // A big event: resume, optionally applying what the replan found
  const resolveBig = (apply) => {
    const s = sim.current;
    if (big.kind === 'crash') {
      keepRoute(s, s.trucks[big.truck], big.accident);
    } else if (apply) {
      for (const r of big.alerts) {
        if (!r.changed) continue;
        const tr = s.trucks.find(x => trucks[x.index].vehicleIndex === r.vehicle_index);
        if (tr) swapIn(s, tr, r, big.tickT, false);
      }
    } else {
      for (const r of big.alerts) addLog({ t: s.t, who: '', text: `kept the current plan: ${r.message}` });
    }
    setBig(null);
    s.phase = 'running';
  };

  handlers.current = { startLeg, onCrashAhead, fireReplan, replanOn: () => replanOn };

  if (!view) return null;
  const nowMs = departureMs + view.t * 1000;
  const done = view.phase === 'done';
  const promptTruck = prompt ? trucks[prompt.truck] : null;
  const bigTruck = big?.kind === 'crash' ? trucks[big.truck] : null;

  // One truck's status line
  const status = (i) => {
    const v = view.trucks[i];
    if (v.phase === 'done') return 'Back at the depot';
    if (v.phase === 'unloading') return `Unloading at ${v.toLabel} · ${fmtMin(v.unloadLeft)} left`;
    if (v.hold === 'prompt') return 'Stopped: crash ahead, waiting for you';
    if (v.hold === 'crash') return 'Crash ahead: finding a detour…';
    return `Leg ${v.leg + 1}/${v.numLegs} → ${v.toLabel}`;
  };

  // Rolling replan status, on the same simulated clock (it freezes while the drive is paused)
  const r = view.replan;
  const replanLine = !done && (replanOn ? (
    <div className="mt-0.5 text-sky-300">
      <p>
        Next replan {fmtClock(departureMs + r.nextS * 1000)}
        <span className="text-slate-400"> · in {fmtCountdown(r.nextS - view.t)}</span>
      </p>
      <p className="text-[10px] text-slate-500">
        {r.inFlight
          ? <span className="text-sky-400 animate-pulse">Replanning from {fmtClock(departureMs + r.inFlight.t * 1000)}… trucks keep driving</span>
          : r.last
            ? <>Last {fmtClock(departureMs + r.last.t * 1000)}: {r.last.text}</>
            : <>Every {REPLAN_INTERVAL_S / 60} min · forecast traffic</>}
      </p>
      {(r.runs > 0) && (
        <p className="text-[10px] text-slate-500">
          {r.runs} replan{r.runs === 1 ? '' : 's'} · {r.changes} change{r.changes === 1 ? '' : 's'}
          {r.skipped > 0 && <> · {r.skipped} skipped (still solving)</>}
          {r.changes > 0 && <> · dashed = replanned</>}
        </p>
      )}
    </div>
  ) : (
    <p className="mt-0.5 text-[10px] text-slate-500">Rolling replan off: this plan has no traffic-data departure</p>
  ));

  const pausedLine = big
    ? <p className="mt-0.5 text-red-400 font-medium">Paused: {big.kind === 'crash' ? `no way around crash #${big.accident.id}` : 'replan needs a decision'}</p>
    : promptTruck && <p className="mt-0.5 text-amber-300">{promptTruck.label} waiting for you · others keep driving</p>;

  const events = log.length > 0 && (
    <ul className="mt-1.5 pt-1.5 border-t border-slate-800 space-y-0.5">
      {log.slice(-LOG_SHOWN).map((d, i) => (
        <li key={i} className="text-[11px]">
          <span className="text-slate-500">{fmtClock(departureMs + d.t * 1000)} </span>
          {fleet && d.who && <span className="text-slate-400">{d.who} · </span>}{d.text}
          {d.sub && <span className="text-slate-500"> ({d.sub})</span>}
        </li>
      ))}
    </ul>
  );

  const clockPanel = fleet ? (
    <div className="absolute top-3 left-3 pointer-events-auto bg-slate-900/95 border border-slate-700 rounded-lg px-3 py-2 text-xs text-slate-300 shadow-lg min-w-[230px] max-w-[300px]">
      <p className="text-[10px] uppercase tracking-wider text-slate-500 mb-1">Drive simulation · measured traffic · {trucks.length} trucks</p>
      <p className="text-lg font-semibold text-white leading-tight">{fmtClock(nowMs)}</p>
      {replanLine}
      {pausedLine}
      <ul className="mt-1.5 space-y-1">
        {trucks.map((truck, i) => {
          const v = view.trucks[i];
          return (
            <li key={truck.id} className="flex items-start gap-1.5">
              <span className="w-2 h-2 rounded-full mt-1 flex-shrink-0" style={{ backgroundColor: truck.color }} />
              <span className="min-w-0">
                <span className="font-medium text-slate-200">{truck.label}</span>
                <span className="block text-[11px] text-slate-400 truncate">
                  {v.phase === 'done'
                    ? <>Back {fmtClock(departureMs + v.finishT * 1000)} <span className={diffClass(v.finishT - plannedS[i])}>({fmtDiff(v.finishT - plannedS[i])} vs plan)</span></>
                    : status(i)}
                </span>
              </span>
            </li>
          );
        })}
      </ul>
      <p className="text-slate-500 mt-1.5">Planned last truck back {fmtClock(departureMs + plannedEndS * 1000)}</p>
      {done && (
        <p className={`mt-1 font-semibold ${diffClass(view.t - plannedEndS)}`}>
          Last truck back {fmtClock(nowMs)} ({fmtDiff(view.t - plannedEndS)} vs plan)
        </p>
      )}
      {events}
    </div>
  ) : (
    <div className="absolute top-3 left-3 pointer-events-auto bg-slate-900/95 border border-slate-700 rounded-lg px-3 py-2 text-xs text-slate-300 shadow-lg min-w-[190px] max-w-[300px]">
      <p className="text-[10px] uppercase tracking-wider text-slate-500 mb-1">Drive simulation · measured traffic</p>
      <p className="text-lg font-semibold text-white leading-tight">{fmtClock(nowMs)}</p>
      {replanLine}
      {pausedLine}
      <p className="mt-0.5">{status(0)}</p>
      <p className="text-slate-500 mt-0.5">Planned finish {fmtClock(departureMs + plannedEndS * 1000)}</p>
      {done && (
        <p className={`mt-1 font-semibold ${diffClass(view.t - plannedEndS)}`}>
          Actual finish {fmtClock(nowMs)} ({fmtDiff(view.t - plannedEndS)} vs plan)
        </p>
      )}
      {events}
    </div>
  );

  // Notifications: they never stop the clock
  const toastTone = { detour: 'border-green-500/40', replan: 'border-sky-500/40', info: 'border-slate-600' };
  const notifications = toasts.length > 0 && (
    <div className="absolute top-3 right-3 flex flex-col gap-2 items-end pointer-events-none max-w-[320px]">
      {toasts.map(t => (
        <div key={t.id} className={`pointer-events-auto bg-slate-900/95 border ${toastTone[t.tone] || toastTone.info} rounded-lg px-3 py-2 text-xs text-slate-200 shadow-lg flex items-start gap-2`}>
          <span className="min-w-0">{t.text}</span>
          {t.undo && t.canUndo?.() && (
            <button onClick={() => { t.undo(); dismissToast(t.id); }} className="text-sky-300 hover:text-sky-200 font-medium flex-shrink-0">Undo</button>
          )}
          <button onClick={() => dismissToast(t.id)} className="text-slate-500 hover:text-slate-300 flex-shrink-0" aria-label="Dismiss">×</button>
        </div>
      ))}
    </div>
  );

  const card = 'pointer-events-auto bg-slate-900 rounded-xl shadow-2xl px-4 py-3 max-w-md w-full';
  const button = 'px-3 py-1.5 rounded-lg text-xs font-medium text-white';

  const panel = (
    <>
      {clockPanel}
      {notifications}

      {/* A truck waiting for the operator (a long detour); the others keep driving */}
      {prompt && !big && (
        <div className="absolute inset-x-0 bottom-4 flex justify-center pointer-events-none px-4">
          <div className={`${card} border border-amber-500/50`}>
            <p className="text-sm font-semibold text-amber-300">
              ⚠ Crash #{prompt.accident.id} ahead{fleet && <> of <span style={{ color: promptTruck.color }}>{promptTruck.label}</span></>}
            </p>
            <p className="text-xs text-slate-400 mt-0.5">
              On {prompt.accident.road || 'the road'}{fleet && ' · only this truck is waiting'}
            </p>
            <p className="text-xs text-slate-300 mt-2">
              Detour via {prompt.option.method === 'alternative' ? "Google's alternative route" : 'a forced bypass'}:{' '}
              <span className="font-semibold">{fmtDiff(prompt.option.deltaS)}, {fmtKmDiff(prompt.option.deltaM)}</span>
              <span className="text-slate-500"> (Google estimate, over the {AUTO_DETOUR_MAX_S / 60} min auto limit)</span>
            </p>
            <div className="flex gap-2 mt-3">
              <button onClick={() => decide(true)} className={`${button} bg-green-600 hover:bg-green-500`}>Reroute</button>
              <button onClick={() => decide(false)} className={`${button} bg-slate-700 hover:bg-slate-600`}>Keep route</button>
            </div>
          </div>
        </div>
      )}

      {/* Big events: the whole simulation is paused */}
      {big && (
        <div className="absolute inset-x-0 bottom-4 flex justify-center pointer-events-none px-4">
          <div className={`${card} border border-red-500/60`}>
            <p className="text-[10px] uppercase tracking-wider text-red-400 mb-0.5">Simulation paused</p>
            {big.kind === 'crash' ? (
              <>
                <p className="text-sm font-semibold text-red-400">
                  No way around crash #{big.accident.id}{fleet && <> for <span style={{ color: bigTruck.color }}>{bigTruck.label}</span></>}
                </p>
                <p className="text-xs text-slate-400 mt-0.5">{big.message}</p>
                <div className="flex gap-2 mt-3">
                  <button onClick={() => resolveBig(false)} className={`${button} bg-slate-700 hover:bg-slate-600`}>Keep route and continue</button>
                </div>
              </>
            ) : (
              <>
                <p className="text-sm font-semibold text-red-400">Replan at {fmtClock(departureMs + big.tickT * 1000)}: battery problem</p>
                <ul className="mt-1 space-y-0.5">
                  {big.alerts.map(a => <li key={a.vehicle_index} className="text-xs text-slate-300">{a.message}</li>)}
                </ul>
                <div className="flex gap-2 mt-3">
                  {big.alerts.some(a => a.changed) && (
                    <button onClick={() => resolveBig(true)} className={`${button} bg-amber-600 hover:bg-amber-500`}>
                      Apply (leave those stops unserved)
                    </button>
                  )}
                  <button onClick={() => resolveBig(false)} className={`${button} bg-slate-700 hover:bg-slate-600`}>Keep current plan</button>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </>
  );

  return (
    <>
      {reroutes.map(({ id, path }) => (
        <PolylineF key={`sim-reroute-${id}`} path={path} options={{ strokeColor: REROUTE_COLOR, strokeOpacity: 0.95, strokeWeight: 5, zIndex: 13 }} />
      ))}
      {Object.entries(replanned).flatMap(([i, paths]) => paths.map((path, j) => (
        <PolylineF
          key={`sim-replan-${i}-${j}-${paths.length}`}
          path={path}
          options={{
            strokeOpacity: 0,
            zIndex: 12,
            icons: [{ icon: { path: 'M 0,-1 0,1', strokeOpacity: 1, strokeColor: trucks[i].color, strokeWeight: 3, scale: 3 }, offset: '0', repeat: '12px' }],
          }}
        />
      )))}
      {view.trucks.map((v, i) => (
        <MarkerF
          key={`sim-truck-${trucks[i].id}`}
          position={{ lat: v.lat, lng: v.lng }}
          icon={{
            path: TRUCK_PATH,
            scale: 1.7,
            rotation: v.heading,
            // One truck stays white; fleet trucks wear their route's colour
            fillColor: fleet ? trucks[i].color : '#f8fafc',
            fillOpacity: 1,
            strokeColor: fleet ? '#f8fafc' : '#0f172a',
            strokeWeight: 1.5,
            anchor: new window.google.maps.Point(0, 0),
          }}
          title={fleet ? `${trucks[i].label} (simulated)` : 'Simulated truck'}
          zIndex={40}
        />
      ))}
      {overlay && createPortal(panel, overlay)}
    </>
  );
}
