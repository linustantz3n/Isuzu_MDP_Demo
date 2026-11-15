import { compareRoutes, hungarian, statDiff, COMPARE_STATS } from './compareRoutes';

function permutations(arr) {
  if (arr.length <= 1) return [arr];
  return arr.flatMap((x, i) => permutations([...arr.slice(0, i), ...arr.slice(i + 1)]).map(p => [x, ...p]));
}

test('hungarian matches brute force on random matrices', () => {
  let seed = 7;
  const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
  for (let trial = 0; trial < 40; trial++) {
    const n = 2 + Math.floor(rnd() * 5);
    const cost = Array.from({ length: n }, () => Array.from({ length: n }, () => Math.floor(rnd() * 20) - 10));
    const got = hungarian(cost).reduce((sum, c, r) => sum + cost[r][c], 0);
    const best = Math.min(...permutations([...Array(n).keys()]).map(p => p.reduce((s, c, r) => s + cost[r][c], 0)));
    expect(got).toBe(best);
  }
});

test('identical solutions with shuffled truck order pair up and show no differences', () => {
  const a = [[0, 1, 2, 0], [0, 3, 4, 0], [0, 5, 6, 0]];
  const b = [[0, 5, 6, 0], [0, 1, 2, 0], [0, 3, 4, 0]];
  const c = compareRoutes(a, b);
  expect(c.stopsOnDifferentTruck).toBe(0);
  expect(c.identicalTrucks).toBe(3);
  expect(c.pairs.map(p => [p.aIdx, p.bIdx])).toEqual([[0, 1], [1, 2], [2, 0]]);
});

test('classifies reversed, reordered and moved stops', () => {
  const a = [[0, 1, 2, 3, 0], [0, 4, 5, 6, 0], [0, 7, 8, 9, 0]];
  // truck 1 reversed, 6 and 9 swap trucks between trucks 2 and 3
  const c = compareRoutes(a, [[0, 3, 2, 1, 0], [0, 5, 4, 9, 0], [0, 7, 8, 6, 0]]);
  const rel = Object.fromEntries(c.pairs.map(p => [p.aIdx, p.relation]));
  expect(rel[0]).toBe('reversed');
  expect(rel[1]).toBe('different-stops');
  expect(rel[2]).toBe('different-stops');
  expect(c.stopsOnDifferentTruck).toBe(2);
  expect(c.identicalTrucks).toBe(1);
  const pair1 = c.pairs.find(p => p.aIdx === 1);
  expect(pair1.onlyA).toEqual([6]);
  expect(pair1.onlyB).toEqual([9]);
});

test('noChanges is strict: a reversed route counts as a change', () => {
  const same = compareRoutes([[0, 1, 2, 0], [0, 3, 4, 0]], [[0, 3, 4, 0], [0, 1, 2, 0]]);
  expect(same.noChanges).toBe(true);
  expect(same.exactTrucks).toBe(2);
  const flipped = compareRoutes([[0, 1, 2, 0], [0, 3, 4, 0]], [[0, 2, 1, 0], [0, 3, 4, 0]]);
  expect(flipped.noChanges).toBe(false);
  expect(flipped.reversedTrucks).toBe(1);
  expect(flipped.stopsOnDifferentTruck).toBe(0);
  expect(compareRoutes([[0, 1, 2, 0]], [[0, 1, 2, 0], [0, 3, 0]]).noChanges).toBe(false);
});

test('reordered stops on the same truck are reported separately', () => {
  const c = compareRoutes([[0, 1, 2, 3, 0]], [[0, 2, 1, 3, 0]]);
  expect(c.pairs[0].relation).toBe('reordered');
  expect(c.stopsOnDifferentTruck).toBe(0);
  expect(c.sameStopsTrucks).toBe(1);
});

test('different truck counts leave unmatched trucks', () => {
  const c = compareRoutes([[0, 1, 2, 0], [0, 3, 4, 0]], [[0, 1, 2, 3, 4, 0]]);
  expect(c.pairs).toHaveLength(2);
  const matched = c.pairs.filter(p => p.aIdx !== null && p.bIdx !== null);
  const orphan = c.pairs.filter(p => p.bIdx === null);
  expect(matched).toHaveLength(1);
  expect(orphan).toHaveLength(1);
  expect(orphan[0].relation).toBe('only-a');
  // pairing maximises overlap, so exactly one A truck's stops are split from B's truck
  expect(c.stopsOnDifferentTruck).toBe(2);
});

test('statDiff reports delta, percent and which side is better', () => {
  const stat = COMPARE_STATS.find(s => s.key === 'total_distance_mi');
  const d = statDiff({ total_distance_mi: 100 }, { total_distance_mi: 110 }, stat);
  expect(d.delta).toBeCloseTo(10);
  expect(d.pct).toBeCloseTo(10);
  expect(d.winner).toBe('a');
  expect(statDiff({ total_distance_mi: 5 }, { total_distance_mi: 5 }, stat).winner).toBeNull();
  const neutral = COMPARE_STATS.find(s => s.key === 'vehicles_used');
  expect(statDiff({ vehicles_used: 2 }, { vehicles_used: 3 }, neutral).winner).toBeNull();
});
