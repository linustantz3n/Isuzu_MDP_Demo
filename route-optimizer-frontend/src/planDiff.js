// Before / after comparison of two plan versions. Pure functions: the
// comparison panel and the chat's "what this changed" message both read them.

import { GOAL_KEYS, GOAL_META, goalWeights, reserveLabel } from './goals';

const locKey = (loc) => `${(+loc.lat).toFixed(5)},${(+loc.lng).toFixed(5)}`;

// "Marina Del Rey" from "13463 Washington Blvd, Marina Del Rey, CA 90292, USA".
export function shortPlace(loc) {
  const text = loc?.formatted_address || loc?.address || '?';
  const parts = text.split(',').map(s => s.trim()).filter(Boolean);
  return parts.length >= 3 ? parts[1] : parts[0] || text;
}

export function formatDuration(seconds) {
  if (seconds == null) return '—';
  const h = Math.floor(seconds / 3600);
  const m = Math.round((seconds % 3600) / 60);
  return h > 0 ? `${h}h ${String(m).padStart(2, '0')}m` : `${m} min`;
}

export function formatClock(seconds) {
  const m = Math.round((seconds || 0) / 60);
  return `+${Math.floor(m / 60)}:${String(m % 60).padStart(2, '0')}`;
}

// One plan's routes and headline numbers. With a fleet the fleet is the plan;
// otherwise it is the single truck's route.
export function planOf(results) {
  if (!results) return null;
  const fleet = results.ortools_fleet;
  if (fleet) {
    const routes = fleet.routes.map(r => ({
      key: r.vehicle_index ?? r.vehicle_id,
      label: r.vehicle_name || `Vehicle ${r.vehicle_id}`,
      driver: r.driver || '',
      status: r.status || null,
      battery_pct: r.battery_remaining_pct,
      depleted: r.battery_depleted,
      stops: r.ordered_locations.map((loc, i) => ({
        loc, key: locKey(loc), kind: r.stop_kinds?.[i] || (i === 0 || i === r.ordered_locations.length - 1 ? 'depot' : 'stop'),
        eta: r.eta_s?.[i],
      })),
    }));
    return {
      kind: 'fleet',
      label: `${fleet.routes.length} truck${fleet.routes.length !== 1 ? 's' : ''}`,
      distance_m: fleet.total_distance_m,
      time_s: fleet.makespan_s,
      energy_kwh: fleet.total_energy_kwh,
      min_battery_pct: Math.min(...fleet.routes.map(r => r.battery_remaining_pct ?? 100)),
      depleted: fleet.routes.filter(r => r.battery_depleted).length,
      unserved: (fleet.unserved || []).map(u => u.location),
      routes,
      disruption: fleet.disruption || null,
    };
  }
  const o = results.optimal;
  if (!o) return null;
  const n = o.ordered_locations.length;
  return {
    kind: 'single',
    label: '1 truck',
    distance_m: o.total_distance_m,
    time_s: o.total_time_s,
    energy_kwh: o.total_energy_kwh,
    min_battery_pct: o.battery_remaining_pct ?? null,
    depleted: o.battery_depleted ? 1 : 0,
    unserved: [],
    routes: [{
      key: 0, label: 'Vehicle 1', driver: '', status: null,
      battery_pct: o.battery_remaining_pct, depleted: o.battery_depleted,
      stops: [...o.ordered_locations, o.ordered_locations[0]].map((loc, i) => ({
        loc, key: locKey(loc), kind: i === 0 || i === n ? 'depot' : 'stop', eta: o.eta_s?.[i],
      })),
    }],
    disruption: null,
  };
}

// Where each delivery sits: which truck, which position among its deliveries.
function placements(plan) {
  const out = {};
  plan.routes.forEach(r => {
    let pos = 0;
    r.stops.forEach(s => {
      if (s.kind === 'depot' || s.kind === 'pickup') return;
      out[s.key] = { route: r.key, pos: pos++ };
    });
  });
  return out;
}

// Stops in `after` whose truck or position changed, keyed by location.
export function movedStops(before, after) {
  if (!before || !after) return new Set();
  const a = placements(before), b = placements(after);
  return new Set(Object.keys(b).filter(k => a[k] && (a[k].route !== b[k].route || a[k].pos !== b[k].pos)));
}

export function addedStops(before, after) {
  if (!before || !after) return new Set();
  const a = placements(before);
  return new Set(Object.keys(placements(after)).filter(k => !a[k]));
}

const pct = (b, a) => (b ? ((a - b) / b) * 100 : 0);

// Metric rows: lower is better except battery left.
export function metricRows(before, after) {
  if (!before || !after) return [];
  const rows = [
    { id: 'distance', label: 'Distance', b: before.distance_m, a: after.distance_m, fmt: v => `${(v / 1609.34).toFixed(1)} mi`, lower: true },
    { id: 'time', label: before.kind === 'fleet' || after.kind === 'fleet' ? 'Last truck back' : 'Route time', b: before.time_s, a: after.time_s, fmt: formatDuration, lower: true },
    { id: 'energy', label: 'Energy', b: before.energy_kwh, a: after.energy_kwh, fmt: v => `${v.toFixed(1)} kWh`, lower: true },
    { id: 'battery', label: 'Lowest battery left', b: before.min_battery_pct, a: after.min_battery_pct, fmt: v => (v == null ? '—' : `${Math.round(v)}%`), lower: false, points: true },
    { id: 'unserved', label: 'Unserved stops', b: before.unserved.length, a: after.unserved.length, fmt: v => `${v}`, lower: true, count: true },
  ];
  return rows.map(r => {
    const diff = (r.a ?? 0) - (r.b ?? 0);
    const rel = r.points || r.count ? diff : pct(r.b, r.a);
    const same = r.points || r.count ? Math.abs(diff) < 0.5 : Math.abs(rel) < 0.5;
    return {
      ...r,
      same,
      better: same ? null : (r.lower ? diff < 0 : diff > 0),
      delta: same ? 'same' : r.points ? `${diff > 0 ? '+' : ''}${Math.round(diff)} pts`
        : r.count ? `${diff > 0 ? '+' : ''}${diff}` : `${rel > 0 ? '+' : ''}${rel.toFixed(1)}%`,
    };
  });
}

// What the operator changed between two versions' settings, as short chips.
export function settingChanges(before, after) {
  if (!before || !after) return [];
  const out = [];
  const gb = before.goals, ga = after.goals;
  GOAL_KEYS.forEach(k => {
    if (gb[k] !== ga[k]) out.push(`${GOAL_META[k].label} ${gb[k]} → ${ga[k]}`);
  });
  if (gb.battery_reserve_pct !== ga.battery_reserve_pct) {
    out.push(`Battery reserve: ${reserveLabel(gb.battery_reserve_pct)} → ${reserveLabel(ga.battery_reserve_pct)}`);
  }
  const vb = before.vehicles, va = after.vehicles;
  if (vb.length !== va.length) out.push(`Trucks ${vb.length} → ${va.length}`);
  va.forEach((v, i) => {
    const o = vb[i];
    if (!o) return;
    if (o.battery_packs !== v.battery_packs) out.push(`${v.name}: ${o.battery_packs} → ${v.battery_packs} packs`);
    else if (o.weight_lbs !== v.weight_lbs) out.push(`${v.name}: ${o.weight_lbs.toLocaleString()} → ${v.weight_lbs.toLocaleString()} lbs`);
  });
  const eb = before.energyParams, ea = after.energyParams;
  if (eb.temperature_c !== ea.temperature_c) out.push(`Temperature ${eb.temperature_c} → ${ea.temperature_c} °C`);
  if (eb.unload_time_min !== ea.unload_time_min) out.push(`Unload ${eb.unload_time_min} → ${ea.unload_time_min} min`);
  if (eb.has_climate_control !== ea.has_climate_control) out.push(`HVAC ${ea.has_climate_control ? 'on' : 'off'}`);
  if (before.solver !== after.solver) out.push(`Solver ${before.solver} → ${after.solver}`);
  const pb = before.priorityStops.join('|'), pa = after.priorityStops.join('|');
  if (pb !== pa) out.push(`Priority stops: ${after.priorityStops.map(s => s.split(',')[1]?.trim() || s).join(', ') || 'none'}`);
  const sb = before.stops.join('|'), sa = after.stops.join('|');
  if (sb !== sa || before.depot !== after.depot) out.push(`Stops ${before.stops.length} → ${after.stops.length}`);
  return out;
}

export function weightBar(goals) {
  const w = goalWeights(goals);
  return GOAL_KEYS.filter(k => w[k] > 0).map(k => ({ key: k, ...GOAL_META[k], share: w[k] }));
}

// The chat's plain-language summary of what a re-solve changed.
export function impactText(before, after, { lang = 'en', versionLabel = '', cleared = false } = {}) {
  const zh = lang === 'zh';
  if (!before || !after) return zh ? '已重新求解。' : 'Re-solved.';
  const rows = metricRows(before, after).filter(r => !r.same);
  const moved = movedStops(before, after);
  const changedTrucks = after.routes
    .filter(r => r.stops.some(s => moved.has(s.key)))
    .map(r => r.label);
  const names = {
    distance: zh ? '距离' : 'distance', time: zh ? '收工时间' : 'last truck back',
    energy: zh ? '电量' : 'energy', battery: zh ? '最低剩余电量' : 'lowest battery left',
    unserved: zh ? '无法配送的站点' : 'unserved stops',
  };
  const parts = rows.map(r => `${names[r.id]} ${r.fmt(r.b)} → ${r.fmt(r.a)} (${r.delta})`);
  let text = zh ? `已重新求解${versionLabel ? `（${versionLabel}）` : ''}。` : `Re-solved${versionLabel ? ` (${versionLabel})` : ''}. `;
  if (!parts.length && !changedTrucks.length) {
    text += zh ? '路线没有变化——新的设置在这里不改变最优方案。'
      : 'The routes came out the same — the new settings do not change the best plan here.';
  } else {
    if (parts.length) {
      const joined = parts.join(zh ? '；' : '; ');
      text += zh ? `${joined}。` : `${joined.charAt(0).toUpperCase()}${joined.slice(1)}.`;
    }
    if (changedTrucks.length) {
      text += zh ? ` 站点顺序有变化：${changedTrucks.join('、')}。` : ` Stop order changed for ${changedTrucks.join(', ')}.`;
    }
    if (after.unserved.length && after.unserved.length !== before.unserved.length) {
      const list = after.unserved.map(shortPlace).join(', ');
      text += zh ? ` 送不了：${list}。` : ` Can't be served: ${list}.`;
    }
  }
  if (cleared) {
    text += zh ? ' （之前模拟的抛锚已清除，这是从早上重新规划的方案。）'
      : ' (The simulated breakdown was cleared — this re-plans the day from the start.)';
  }
  text += zh ? ' 对比见结果上方的「变化对比」。' : ' See "What changed" above the results.';
  return text;
}
