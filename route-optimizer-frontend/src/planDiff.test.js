import { planOf, metricRows, movedStops, addedStops, impactText, settingChanges } from './planDiff';
import { DEFAULT_GOALS, goalWeights } from './goals';

const loc = (name, lat) => ({ formatted_address: `1 Main St, ${name}, CA 90000, USA`, lat, lng: -118 });
const D = loc('Depot', 33.0), A = loc('Anaheim', 33.1), B = loc('Brea', 33.2), C = loc('Cerritos', 33.3);

const route = (vehicle_index, stops, extra = {}) => ({
  vehicle_index, vehicle_id: vehicle_index + 1, vehicle_name: `Vehicle ${vehicle_index + 1}`,
  ordered_locations: [D, ...stops, D], battery_remaining_pct: 50, battery_depleted: false, ...extra,
});
const fleet = (routes, extra = {}) => ({
  ortools_fleet: {
    routes, total_distance_m: 160934, makespan_s: 7200, total_energy_kwh: 100, unserved: [], ...extra,
  },
});

test('a stop that changes truck or position counts as moved', () => {
  const before = planOf(fleet([route(0, [A, B]), route(1, [C])]));
  const after = planOf(fleet([route(0, [B]), route(1, [C, A])]));
  const moved = movedStops(before, after);
  expect(moved.size).toBe(2);                  // A changed truck, B moved up
  expect(addedStops(before, after).size).toBe(0);
});

test('metric rows: lower is better, battery is in points, unchanged reads "same"', () => {
  const before = planOf(fleet([route(0, [A])]));
  const after = planOf(fleet([route(0, [A], { battery_remaining_pct: 70 })], { total_energy_kwh: 90 }));
  const rows = Object.fromEntries(metricRows(before, after).map(r => [r.id, r]));
  expect(rows.energy).toMatchObject({ better: true, delta: '-10.0%' });
  expect(rows.battery).toMatchObject({ better: true, delta: '+20 pts' });
  expect(rows.distance.same).toBe(true);
});

test('impact text names what changed, and says so when nothing did', () => {
  const before = planOf(fleet([route(0, [A, B])]));
  const same = impactText(before, before);
  expect(same).toMatch(/came out the same/);
  const after = planOf(fleet([route(0, [B, A])], { total_energy_kwh: 80, unserved: [{ location: C }] }));
  const text = impactText(before, after, { versionLabel: 'v2' });
  expect(text).toMatch(/^Re-solved \(v2\)\. Energy 100\.0 kWh → 80\.0 kWh/);
  expect(text).toMatch(/Stop order changed for Vehicle 1/);
  expect(text).toMatch(/Can't be served: Cerritos/);
});

test('setting changes read as chips', () => {
  const s = { goals: DEFAULT_GOALS, priorityStops: [], vehicles: [{ name: 'V1', battery_packs: 4, weight_lbs: 13000 }],
              energyParams: { temperature_c: 20, unload_time_min: 30, has_climate_control: true }, solver: 'auto',
              depot: 'x', stops: ['a', 'b'] };
  const t = { ...s, goals: { ...DEFAULT_GOALS, energy: 'high', battery_reserve_pct: 20 },
              vehicles: [{ name: 'V1', battery_packs: 3, weight_lbs: 12559 }] };
  expect(settingChanges(s, t)).toEqual([
    'Energy off → high', 'Battery reserve: not enforced → keep 20%', 'V1: 4 → 3 packs',
  ]);
});

test('weights mirror the backend table', () => {
  expect(goalWeights({ ...DEFAULT_GOALS, distance: 'low', energy: 'high' })).toEqual(
    { distance: 0.2, time: 0, energy: 0.8, balance: 0 });
  expect(goalWeights({ distance: 'off', time: 'off', energy: 'off', balance: 'off' }).distance).toBe(1);
});
