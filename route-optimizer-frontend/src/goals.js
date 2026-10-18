// Optimization goals. Mirrors GOAL_LEVEL_WEIGHT / goal_weights() in
// route_optimizer_backend.py — keep the two in step.

export const GOAL_KEYS = ['distance', 'time', 'energy', 'balance'];
export const GOAL_LEVELS = ['off', 'low', 'medium', 'high'];
export const LEVEL_WEIGHT = { off: 0, low: 1, medium: 2, high: 4 };
export const RESERVE_CHOICES = [null, 0, 10, 20, 30, 40, 50];

export const DEFAULT_GOALS = {
  distance: 'high', time: 'off', energy: 'off', balance: 'off', battery_reserve_pct: null,
};

export const GOAL_META = {
  distance: { label: 'Distance', color: '#38bdf8', hint: 'fewest total miles' },
  time: { label: 'Time', color: '#a78bfa', hint: 'fastest roads, by typical driving time' },
  energy: { label: 'Energy', color: '#facc15', hint: 'Model E kWh: speed, climbs, load on board' },
  balance: { label: 'Balance', color: '#34d399', hint: 'latest truck back sooner (2+ trucks)' },
};

export function goalWeights(goals) {
  const g = goals || DEFAULT_GOALS;
  const raw = Object.fromEntries(GOAL_KEYS.map(k => [k, LEVEL_WEIGHT[g[k]] ?? 0]));
  const total = Object.values(raw).reduce((a, b) => a + b, 0);
  // All off: the backend falls back to distance, so show that.
  if (!total) return { distance: 1, time: 0, energy: 0, balance: 0 };
  return Object.fromEntries(GOAL_KEYS.map(k => [k, raw[k] / total]));
}

export function goalsSummary(goals) {
  const g = goals || DEFAULT_GOALS;
  const parts = GOAL_KEYS.filter(k => g[k] !== 'off')
    .sort((a, b) => LEVEL_WEIGHT[g[b]] - LEVEL_WEIGHT[g[a]])
    .map(k => `${GOAL_META[k].label} ${g[k]}`);
  if (g.battery_reserve_pct != null) parts.push(`reserve ${g.battery_reserve_pct}%`);
  return parts.join(' · ');
}

export function reserveLabel(r) {
  if (r == null) return 'not enforced';
  return r === 0 ? 'never run flat' : `keep ${r}%`;
}
