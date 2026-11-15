import React, { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { MarkerF, PolylineF } from '@react-google-maps/api';
import { API_BASE } from './api';

// Drive simulation: trucks drive their routes on the map on one shared clock, each
// leg taking its measured (ground-truth) time, sped up. One truck for the Held-Karp
// route, or every truck of an OR-Tools fleet. When a truck approaches an active
// accident, the whole simulation pauses (so the trucks stay in step) and asks the
// operator whether that truck should reroute from where it is.

export const SIM_SPEEDS = [60, 120, 300, 600];  // simulated seconds per real second
const PROMPT_AHEAD_M = 3000;  // ask this far before the accident (room for a detour)
const REROUTE_COLOR = '#22c55e';
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

const fmtClock = (ms) => new Date(ms).toISOString().slice(11, 16);  // departure is local wall time
const fmtMin = (s) => `${Math.round(s / 60)} min`;
const fmtKm = (m) => `${(m / 1000).toFixed(1)} km`;
const fmtDiff = (s) => `${s >= 0 ? '+' : '−'}${fmtMin(Math.abs(s))}`;
const diffClass = (s) => (s > 60 ? 'text-orange-400' : s < -60 ? 'text-green-400' : 'text-slate-300');

// Planned time for a truck's day: driving (planned leg times) plus unloading at every stop
const plannedTotalS = (truck) => truck.legs.reduce((s, l) => s + l.plannedS, 0) + truck.unloadS * (truck.legs.length - 1);

/**
 * trucks: [{id, label, color, unloadS,
 *           legs: [{path: [{lat, lng}], durationS (measured), plannedS, toLabel, to: {lat, lng}}],
 *           accidents: active accidents on this truck's route [{id, lat, lng, leg_index (on this truck), road}]}]
 *   One entry: the single-truck view. Two or more: a fleet, each truck in its route colour.
 * departureTime: 'YYYY-MM-DDTHH:MM' (local time of the traffic data)
 * overlay: DOM node over the map for the clock and the prompt
 */
export default function DriveSimulation({ trucks, departureTime, speed, overlay, onEnd }) {
  const sim = useRef(null);
  const [view, setView] = useState(null);       // what the map and overlay show
  const [prompt, setPrompt] = useState(null);   // {truck (index), accident, status, result?, error?}
  const [reroutes, setReroutes] = useState([]); // paths of reroutes taken
  const [log, setLog] = useState([]);           // operator decisions
  const speedRef = useRef(speed);
  speedRef.current = speed;

  const fleet = trucks.length > 1;
  const departureMs = Date.parse(`${departureTime}:00Z`);
  const plannedS = trucks.map(plannedTotalS);
  const plannedEndS = Math.max(...plannedS);

  // Start (or restart) the drive
  useEffect(() => {
    const startLeg = (tr, k) => {
      const truck = trucks[tr.index];
      const line = measure(truck.legs[k].path);
      tr.leg = k;
      tr.line = line;
      tr.along = 0;
      tr.mps = line.length / Math.max(truck.legs[k].durationS, 1);
      tr.pending = truck.accidents
        .filter(a => a.leg_index === k)
        .map(a => ({ ...a, along: alongOf(line, a) }))
        .sort((a, b) => a.along - b.along);
    };
    const s = {
      phase: 'running', t: 0,
      trucks: trucks.map((_, index) => ({ index, phase: 'driving', unloadLeft: 0, finishT: null })),
    };
    s.trucks.forEach(tr => startLeg(tr, 0));
    sim.current = s;
    setPrompt(null);
    setReroutes([]);
    setLog([]);

    // Move one truck forward by dt; true if it has to stop for an accident ahead
    const advance = (tr, dt) => {
      const truck = trucks[tr.index];
      if (tr.phase === 'driving') {
        tr.along += tr.mps * dt;
        const next = tr.pending[0];
        if (next && tr.along >= next.along - PROMPT_AHEAD_M) {
          tr.along = Math.max(0, Math.min(tr.along, next.along - PROMPT_AHEAD_M));
          return true;
        }
        if (tr.along >= tr.line.length) {
          tr.along = tr.line.length;
          if (tr.leg === truck.legs.length - 1) {
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
          startLeg(tr, tr.leg + 1);
          tr.along = tr.mps * -tr.unloadLeft;  // don't lose the overshoot
          tr.phase = 'driving';
        }
      }
      return false;
    };

    let last = performance.now();
    let frame;
    const tick = (now) => {
      const dt = Math.min((now - last) / 1000, 0.1) * speedRef.current;
      last = now;
      if (s.phase === 'running') {
        s.t += dt;
        for (const tr of s.trucks) {
          // A second truck reaching an accident in the same frame waits at its
          // prompt point; it asks as soon as the first prompt is answered.
          if (advance(tr, dt) && s.phase === 'running') {
            s.phase = 'prompt';
            askReroute(s, tr, tr.pending[0]);
          }
        }
        if (s.phase === 'running' && s.trucks.every(tr => tr.phase === 'done')) {
          s.phase = 'done';
          onEnd && onEnd();
        }
      }
      setView({
        phase: s.phase, t: s.t,
        trucks: s.trucks.map(tr => ({
          ...pointAt(tr.line, tr.along), phase: tr.phase, leg: tr.leg, unloadLeft: tr.unloadLeft, finishT: tr.finishT,
        })),
      });
      if (s.phase !== 'done') frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [trucks, departureTime]);

  // Ask the backend for a route from the truck's position around the accidents ahead on this leg
  const askReroute = async (s, tr, accident) => {
    const ahead = tr.pending;
    setPrompt({ truck: tr.index, accident, status: 'loading' });
    try {
      const here = pointAt(tr.line, tr.along);
      const response = await fetch(`${API_BASE}/reroute_from`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          position: here,
          destination: trucks[tr.index].legs[tr.leg].to,
          accidents: ahead.map(a => ({ lat: a.lat, lng: a.lng })),
        }),
      });
      const data = await response.json();
      if (data.status === 'error') throw new Error(data.message);
      setPrompt({ truck: tr.index, accident, status: 'ready', result: data });
    } catch (err) {
      setPrompt({ truck: tr.index, accident, status: 'error', error: err.message || 'Reroute failed' });
    }
  };

  const decide = (reroute) => {
    const s = sim.current;
    const tr = s.trucks[prompt.truck];
    const who = trucks[prompt.truck].label;
    const { accident, result } = prompt;
    if (reroute && result && result.method !== 'clear') {
      // Drive the reroute instead of the rest of the leg. Its measured time is unknown
      // (Google's roads), so scale the rest of the leg's measured time by Google's ratio.
      const remainingS = (tr.line.length - tr.along) / tr.mps;
      const newS = remainingS * (result.rerouted.time_s / Math.max(result.original.time_s, 1));
      tr.line = measure(result.rerouted.path);
      tr.along = 0;
      tr.mps = tr.line.length / Math.max(newS, 1);
      tr.pending = [];  // the reroute avoids every accident ahead on this leg
      setReroutes(r => [...r, result.rerouted.path]);
      setLog(l => [...l, { id: accident.id, who, choice: 'Rerouted', deltaS: newS - remainingS,
        deltaM: result.rerouted.distance_m - result.original.distance_m }]);
    } else {
      tr.pending = tr.pending.slice(1);
      setLog(l => [...l, { id: accident.id, who, choice: result?.method === 'clear' ? 'Road ahead clear' : 'Kept route' }]);
    }
    setPrompt(null);
    s.phase = 'running';
  };

  if (!view) return null;
  const nowMs = departureMs + view.t * 1000;
  const done = view.phase === 'done';
  const promptTruck = prompt ? trucks[prompt.truck] : null;
  const promptLeg = prompt ? promptTruck.legs[sim.current.trucks[prompt.truck].leg] : null;

  // One truck's status line
  const status = (i) => {
    const v = view.trucks[i], truck = trucks[i];
    if (v.phase === 'done') return 'Back at the depot';
    if (v.phase === 'unloading') return `Unloading at ${truck.legs[v.leg].toLabel} · ${fmtMin(v.unloadLeft)} left`;
    if (prompt?.truck === i) return 'Stopped: crash reported ahead';
    return `Leg ${v.leg + 1}/${truck.legs.length} → ${truck.legs[v.leg].toLabel}`;
  };

  const decisions = log.length > 0 && (
    <ul className="mt-1.5 pt-1.5 border-t border-slate-800 space-y-0.5">
      {log.map((d, i) => (
        <li key={i} className="text-[11px]">
          {fleet && <span className="text-slate-400">{d.who} · </span>}#{d.id}: {d.choice}
          {d.deltaS != null && <span className="text-slate-500"> ({fmtDiff(d.deltaS)}, {d.deltaM >= 0 ? '+' : '−'}{fmtKm(Math.abs(d.deltaM))})</span>}
        </li>
      ))}
    </ul>
  );

  const clockPanel = fleet ? (
    <div className="absolute top-3 left-3 pointer-events-auto bg-slate-900/95 border border-slate-700 rounded-lg px-3 py-2 text-xs text-slate-300 shadow-lg min-w-[230px] max-w-[300px]">
      <p className="text-[10px] uppercase tracking-wider text-slate-500 mb-1">Drive simulation · measured traffic · {trucks.length} trucks</p>
      <p className="text-lg font-semibold text-white leading-tight">{fmtClock(nowMs)}</p>
      {/* `prompt`, not view.phase: view lags one animation frame behind a decision */}
      {promptTruck && <p className="mt-0.5 text-red-400">Paused: crash reported ahead of {promptTruck.label}</p>}
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
      {decisions}
    </div>
  ) : (
    <div className="absolute top-3 left-3 pointer-events-auto bg-slate-900/95 border border-slate-700 rounded-lg px-3 py-2 text-xs text-slate-300 shadow-lg min-w-[190px]">
      <p className="text-[10px] uppercase tracking-wider text-slate-500 mb-1">Drive simulation · measured traffic</p>
      <p className="text-lg font-semibold text-white leading-tight">{fmtClock(nowMs)}</p>
      <p className="mt-0.5">{status(0)}</p>
      <p className="text-slate-500 mt-0.5">Planned finish {fmtClock(departureMs + plannedEndS * 1000)}</p>
      {done && (
        <p className={`mt-1 font-semibold ${diffClass(view.t - plannedEndS)}`}>
          Actual finish {fmtClock(nowMs)} ({fmtDiff(view.t - plannedEndS)} vs plan)
        </p>
      )}
      {decisions}
    </div>
  );

  const panel = (
    <>
      {clockPanel}

      {/* Operator prompt */}
      {prompt && (
        <div className="absolute inset-x-0 bottom-4 flex justify-center pointer-events-none px-4">
          <div className="pointer-events-auto bg-slate-900 border border-red-500/50 rounded-xl shadow-2xl px-4 py-3 max-w-md w-full">
            <p className="text-sm font-semibold text-red-400">
              ⚠ Crash reported {fmtKm(Math.max(prompt.accident.along - sim.current.trucks[prompt.truck].along, 0))} ahead
              {fleet && <> of <span style={{ color: promptTruck.color }}>{promptTruck.label}</span></>}
            </p>
            <p className="text-xs text-slate-400 mt-0.5">
              Accident #{prompt.accident.id} on {prompt.accident.road || 'the road'} to {promptLeg.toLabel}
              {fleet && ' · other trucks wait'}
            </p>
            {prompt.status === 'loading' && <p className="text-xs text-slate-500 mt-2 animate-pulse">Finding a detour from here…</p>}
            {prompt.status === 'error' && <p className="text-xs text-red-400 mt-2">{prompt.error}</p>}
            {prompt.status === 'ready' && prompt.result.method === 'clear' && (
              <p className="text-xs text-slate-300 mt-2">Google's route from here already avoids it.</p>
            )}
            {prompt.status === 'ready' && prompt.result.method !== 'clear' && (() => {
              const { original, rerouted, method } = prompt.result;
              const dT = rerouted.time_s - original.time_s, dM = rerouted.distance_m - original.distance_m;
              return (
                <p className="text-xs text-slate-300 mt-2">
                  Detour via {method === 'alternative' ? "Google's alternative route" : 'a forced bypass'}:{' '}
                  <span className="font-semibold">{fmtDiff(dT)}, {dM >= 0 ? '+' : '−'}{fmtKm(Math.abs(dM))}</span>
                  <span className="text-slate-500"> (Google estimate)</span>
                </p>
              );
            })()}
            <div className="flex gap-2 mt-3">
              {prompt.status === 'ready' && prompt.result.method === 'clear' ? (
                <button onClick={() => decide(false)} className="px-3 py-1.5 rounded-lg text-xs font-medium bg-slate-700 hover:bg-slate-600 text-white">Continue</button>
              ) : (
                <>
                  <button
                    onClick={() => decide(true)}
                    disabled={prompt.status !== 'ready'}
                    className="px-3 py-1.5 rounded-lg text-xs font-medium bg-green-600 hover:bg-green-500 disabled:opacity-40 disabled:cursor-not-allowed text-white"
                  >
                    Reroute
                  </button>
                  <button onClick={() => decide(false)} className="px-3 py-1.5 rounded-lg text-xs font-medium bg-slate-700 hover:bg-slate-600 text-white">
                    Keep route
                  </button>
                </>
              )}
            </div>
          </div>
        </div>
      )}
    </>
  );

  return (
    <>
      {reroutes.map((path, i) => (
        <PolylineF key={`sim-reroute-${i}`} path={path} options={{ strokeColor: REROUTE_COLOR, strokeOpacity: 0.95, strokeWeight: 5, zIndex: 13 }} />
      ))}
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
