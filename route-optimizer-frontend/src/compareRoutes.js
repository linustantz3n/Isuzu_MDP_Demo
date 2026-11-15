// Pure logic for the two-algorithm comparison: which truck in A corresponds to which
// truck in B, and how their stops differ. Trucks have no identity across algorithms,
// so they are paired to maximise shared stops.

// Minimum-cost assignment on a square matrix (Hungarian algorithm); returns rowToCol.
export function hungarian(cost) {
  const n = cost.length;
  const u = Array(n + 1).fill(0);
  const v = Array(n + 1).fill(0);
  const p = Array(n + 1).fill(0);
  const way = Array(n + 1).fill(0);
  for (let i = 1; i <= n; i++) {
    p[0] = i;
    let j0 = 0;
    const minv = Array(n + 1).fill(Infinity);
    const used = Array(n + 1).fill(false);
    do {
      used[j0] = true;
      const i0 = p[j0];
      let delta = Infinity;
      let j1 = 0;
      for (let j = 1; j <= n; j++) {
        if (used[j]) continue;
        const cur = cost[i0 - 1][j - 1] - u[i0] - v[j];
        if (cur < minv[j]) { minv[j] = cur; way[j] = j0; }
        if (minv[j] < delta) { delta = minv[j]; j1 = j; }
      }
      for (let j = 0; j <= n; j++) {
        if (used[j]) { u[p[j]] += delta; v[j] -= delta; } else { minv[j] -= delta; }
      }
      j0 = j1;
    } while (p[j0] !== 0);
    do {
      const j1 = way[j0];
      p[j0] = p[j1];
      j0 = j1;
    } while (j0);
  }
  const rowToCol = Array(n);
  for (let j = 1; j <= n; j++) rowToCol[p[j] - 1] = j - 1;
  return rowToCol;
}

const stopsOf = (route) => route.slice(1, -1);
const sameList = (x, y) => x.length === y.length && x.every((v, i) => v === y[i]);
const sortedNum = (x) => [...x].sort((a, b) => a - b);

// routesA / routesB: arrays of closed routes [0, s1, ..., sn, 0]
export function compareRoutes(routesA, routesB) {
  const stopsA = routesA.map(stopsOf);
  const stopsB = routesB.map(stopsOf);
  const size = Math.max(stopsA.length, stopsB.length);

  // cost = -(shared stops); padding rows/columns (unmatched trucks) cost 0
  const cost = Array.from({ length: size }, (_, i) => Array.from({ length: size }, (_, j) => {
    if (i >= stopsA.length || j >= stopsB.length) return 0;
    const inB = new Set(stopsB[j]);
    return -stopsA[i].filter(s => inB.has(s)).length;
  }));
  const rowToCol = size ? hungarian(cost) : [];

  const pairs = [];
  const usedB = new Set();
  stopsA.forEach((_, i) => {
    const j = rowToCol[i] < stopsB.length ? rowToCol[i] : null;
    if (j !== null) usedB.add(j);
    pairs.push({ aIdx: i, bIdx: j });
  });
  stopsB.forEach((_, j) => { if (!usedB.has(j)) pairs.push({ aIdx: null, bIdx: j }); });

  let shiftedTotal = 0;
  const detailed = pairs.map((pair, slot) => {
    const a = pair.aIdx === null ? [] : stopsA[pair.aIdx];
    const b = pair.bIdx === null ? [] : stopsB[pair.bIdx];
    const inA = new Set(a);
    const inB = new Set(b);
    const shared = a.filter(s => inB.has(s));
    const onlyA = a.filter(s => !inB.has(s));
    const onlyB = b.filter(s => !inA.has(s));
    shiftedTotal += onlyA.length;

    let relation;
    if (pair.aIdx === null) relation = 'only-b';
    else if (pair.bIdx === null) relation = 'only-a';
    else if (sameList(a, b)) relation = 'identical';
    else if (sameList(a, [...b].reverse())) relation = 'reversed';
    else if (sameList(sortedNum(a), sortedNum(b))) relation = 'reordered';
    else relation = 'different-stops';

    return { ...pair, slot, shared, onlyA, onlyB, relation };
  });

  const totalStops = stopsA.reduce((n, r) => n + r.length, 0);
  const count = (rel) => detailed.filter(p => p.relation === rel).length;
  return {
    pairs: detailed,
    totalStops,
    // stops that sit on a different truck in B than in A, after pairing trucks by overlap
    stopsOnDifferentTruck: shiftedTotal,
    identicalTrucks: count('identical') + count('reversed'),
    exactTrucks: count('identical'),      // same stops in the same order
    reversedTrucks: count('reversed'),    // same stops driven the other way round
    // true only when every truck has exactly the same stops in exactly the same order
    noChanges: detailed.length > 0 && detailed.every(p => p.relation === 'identical'),
    sameStopsTrucks: count('reordered'),
    differentTrucks: count('different-stops') + count('only-a') + count('only-b'),
  };
}

// Stat rows for the comparison table. better: 'lower' marks metrics where lower wins.
export const COMPARE_STATS = [
  { key: 'total_distance_mi', label: 'Total distance', unit: 'mi', digits: 2, better: 'lower' },
  { key: 'total_drive_time_min', label: 'Total drive time', unit: 'min', digits: 1, better: 'lower' },
  { key: 'total_energy_kwh', label: 'Total energy', unit: 'kWh', digits: 1, better: 'lower' },
  { key: 'makespan_min', label: 'Longest truck (makespan)', unit: 'min', digits: 1, better: 'lower' },
  { key: 'vehicles_used', label: 'Trucks used', unit: '', digits: 0 },
  { key: 'max_stops_per_truck', label: 'Most stops on one truck', unit: '', digits: 0 },
  { key: 'solve_time_ms', label: 'Solve time', unit: 'ms', digits: 0 },
];

export function statDiff(metricsA, metricsB, stat) {
  const a = metricsA[stat.key];
  const b = metricsB[stat.key];
  const delta = b - a;
  const pct = a ? (delta / a) * 100 : null;
  let winner = null;
  if (stat.better === 'lower' && Math.abs(delta) > 1e-9) winner = delta < 0 ? 'b' : 'a';
  return { a, b, delta, pct, winner };
}
