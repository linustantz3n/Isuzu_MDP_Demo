import React, { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { MarkerF, PolylineF } from '@react-google-maps/api';
import { API_BASE } from './api';

// Drive simulation: a truck drives the Held-Karp route on the map, each leg taking
// its measured (ground-truth) time, sped up. Approaching an active accident, it
// stops and asks the operator whether to reroute from where it is.

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

/**
 * legs: [{path: [{lat, lng}], durationS (measured), plannedS, toLabel, to: {lat, lng}}]
 * accidents: active accidents [{id, lat, lng, leg_index, road}]
 * departureTime: 'YYYY-MM-DDTHH:MM' (local time of the traffic data)
 * overlay: DOM node over the map for the clock and the prompt
 */
export default function DriveSimulation({ legs, accidents, departureTime, unloadS, speed, overlay, onEnd }) {
  const sim = useRef(null);
  const [view, setView] = useState(null);       // what the map and overlay show
  const [prompt, setPrompt] = useState(null);   // {accident, status, result?, error?}
  const [reroutes, setReroutes] = useState([]); // paths of reroutes taken
  const [log, setLog] = useState([]);           // operator decisions
  const speedRef = useRef(speed);
  speedRef.current = speed;

  const departureMs = Date.parse(`${departureTime}:00Z`);
  const plannedS = legs.reduce((s, l) => s + l.plannedS, 0) + unloadS * (legs.length - 1);

  // Start (or restart) the drive
  useEffect(() => {
    const startLeg = (s, k) => {
      const line = measure(legs[k].path);
      s.leg = k;
      s.line = line;
      s.along = 0;
      s.mps = line.length / Math.max(legs[k].durationS, 1);
      s.pending = accidents
        .filter(a => a.leg_index === k)
        .map(a => ({ ...a, along: alongOf(line, a) }))
        .sort((a, b) => a.along - b.along);
    };
    const s = { phase: 'driving', t: 0, unloadLeft: 0 };
    startLeg(s, 0);
    sim.current = s;
    setPrompt(null);
    setReroutes([]);
    setLog([]);

    let last = performance.now();
    let frame;
    const tick = (now) => {
      const dt = Math.min((now - last) / 1000, 0.1) * speedRef.current;
      last = now;
      if (s.phase === 'driving') {
        s.t += dt;
        s.along += s.mps * dt;
        const next = s.pending[0];
        if (next && s.along >= next.along - PROMPT_AHEAD_M) {
          s.along = Math.max(0, Math.min(s.along, next.along - PROMPT_AHEAD_M));
          s.phase = 'prompt';
          askReroute(s, next);
        } else if (s.along >= s.line.length) {
          s.along = s.line.length;
          if (s.leg === legs.length - 1) {
            s.phase = 'done';
            onEnd && onEnd();
          } else {
            s.phase = 'unloading';
            s.unloadLeft = unloadS;
          }
        }
      } else if (s.phase === 'unloading') {
        s.t += dt;
        s.unloadLeft -= dt;
        if (s.unloadLeft <= 0) {
          s.t += s.unloadLeft;  // don't overshoot
          startLeg(s, s.leg + 1);
          s.phase = 'driving';
        }
      }
      setView({ ...pointAt(s.line, s.along), phase: s.phase, leg: s.leg, t: s.t, unloadLeft: s.unloadLeft });
      if (s.phase !== 'done') frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [legs, accidents, departureTime, unloadS]);

  // Ask the backend for a route from the truck's position around the accidents ahead on this leg
  const askReroute = async (s, accident) => {
    const ahead = s.pending;
    setPrompt({ accident, status: 'loading' });
    try {
      const here = pointAt(s.line, s.along);
      const response = await fetch(`${API_BASE}/reroute_from`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          position: here,
          destination: legs[s.leg].to,
          accidents: ahead.map(a => ({ lat: a.lat, lng: a.lng })),
        }),
      });
      const data = await response.json();
      if (data.status === 'error') throw new Error(data.message);
      setPrompt({ accident, status: 'ready', result: data });
    } catch (err) {
      setPrompt({ accident, status: 'error', error: err.message || 'Reroute failed' });
    }
  };

  const decide = (reroute) => {
    const s = sim.current;
    const { accident, result } = prompt;
    if (reroute && result && result.method !== 'clear') {
      // Drive the reroute instead of the rest of the leg. Its measured time is unknown
      // (Google's roads), so scale the rest of the leg's measured time by Google's ratio.
      const remainingS = (s.line.length - s.along) / s.mps;
      const newS = remainingS * (result.rerouted.time_s / Math.max(result.original.time_s, 1));
      s.line = measure(result.rerouted.path);
      s.along = 0;
      s.mps = s.line.length / Math.max(newS, 1);
      s.pending = [];  // the reroute avoids every accident ahead on this leg
      setReroutes(r => [...r, result.rerouted.path]);
      setLog(l => [...l, { id: accident.id, choice: 'Rerouted', deltaS: newS - remainingS,
        deltaM: result.rerouted.distance_m - result.original.distance_m }]);
    } else {
      s.pending = s.pending.slice(1);
      setLog(l => [...l, { id: accident.id, choice: result?.method === 'clear' ? 'Road ahead clear' : 'Kept route' }]);
    }
    setPrompt(null);
    s.phase = 'driving';
  };

  if (!view) return null;
  const nowMs = departureMs + view.t * 1000;
  const leg = legs[view.leg];
  const done = view.phase === 'done';
  const diffS = view.t - plannedS;

  const panel = (
    <>
      {/* Clock and progress */}
      <div className="absolute top-3 left-3 pointer-events-auto bg-slate-900/95 border border-slate-700 rounded-lg px-3 py-2 text-xs text-slate-300 shadow-lg min-w-[190px]">
        <p className="text-[10px] uppercase tracking-wider text-slate-500 mb-1">Drive simulation · measured traffic</p>
        <p className="text-lg font-semibold text-white leading-tight">{fmtClock(nowMs)}</p>
        <p className="mt-0.5">
          {done ? 'Back at the depot'
            : view.phase === 'unloading' ? `Unloading at ${legs[view.leg].toLabel} · ${fmtMin(view.unloadLeft)} left`
            : view.phase === 'prompt' ? 'Stopped: crash reported ahead'
            : `Leg ${view.leg + 1}/${legs.length} → ${leg.toLabel}`}
        </p>
        <p className="text-slate-500 mt-0.5">Planned finish {fmtClock(departureMs + plannedS * 1000)}</p>
        {done && (
          <p className={`mt-1 font-semibold ${diffS > 60 ? 'text-orange-400' : diffS < -60 ? 'text-green-400' : 'text-slate-300'}`}>
            Actual finish {fmtClock(nowMs)} ({diffS >= 0 ? '+' : '−'}{fmtMin(Math.abs(diffS))} vs plan)
          </p>
        )}
        {log.length > 0 && (
          <ul className="mt-1.5 pt-1.5 border-t border-slate-800 space-y-0.5">
            {log.map((d, i) => (
              <li key={i} className="text-[11px]">
                #{d.id}: {d.choice}
                {d.deltaS != null && <span className="text-slate-500"> ({d.deltaS >= 0 ? '+' : '−'}{fmtMin(Math.abs(d.deltaS))}, {d.deltaM >= 0 ? '+' : '−'}{fmtKm(Math.abs(d.deltaM))})</span>}
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* Operator prompt */}
      {prompt && (
        <div className="absolute inset-x-0 bottom-4 flex justify-center pointer-events-none px-4">
          <div className="pointer-events-auto bg-slate-900 border border-red-500/50 rounded-xl shadow-2xl px-4 py-3 max-w-md w-full">
            <p className="text-sm font-semibold text-red-400">
              ⚠ Crash reported {fmtKm(Math.max(prompt.accident.along - sim.current.along, 0))} ahead
            </p>
            <p className="text-xs text-slate-400 mt-0.5">
              Accident #{prompt.accident.id} on {prompt.accident.road || 'the road'} to {leg.toLabel}
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
                  <span className="font-semibold">{dT >= 0 ? '+' : '−'}{fmtMin(Math.abs(dT))}, {dM >= 0 ? '+' : '−'}{fmtKm(Math.abs(dM))}</span>
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
      <MarkerF
        position={{ lat: view.lat, lng: view.lng }}
        icon={{
          path: TRUCK_PATH,
          scale: 1.7,
          rotation: view.heading,
          fillColor: '#f8fafc',
          fillOpacity: 1,
          strokeColor: '#0f172a',
          strokeWeight: 1.5,
          anchor: new window.google.maps.Point(0, 0),
        }}
        title="Simulated truck"
        zIndex={40}
      />
      {overlay && createPortal(panel, overlay)}
    </>
  );
}
