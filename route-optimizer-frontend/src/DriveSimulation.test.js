import {
  replanSchedule, REPLAN_INTERVAL_S, tickTruckState, spliceReplan, placeAccidents,
  drainAt, drainActive, drainedKwh, drainEstimate, socTrace, NO_DRAIN,
} from './DriveSimulation';

jest.mock('@react-google-maps/api', () => ({ MarkerF: () => null, PolylineF: () => null }));

test('replans every 5 simulated minutes from departure', () => {
  expect(REPLAN_INTERVAL_S).toBe(300);
  expect(replanSchedule(0)).toEqual({ nextS: 300, leftS: 300, count: 0 });
  expect(replanSchedule(120)).toEqual({ nextS: 300, leftS: 180, count: 0 });
});

test('exactly on a replan moves the countdown to the following one', () => {
  expect(replanSchedule(300)).toEqual({ nextS: 600, leftS: 300, count: 1 });
  expect(replanSchedule(1799.5)).toEqual({ nextS: 1800, leftS: 0.5, count: 5 });
});

// A truck on route 0 → 3 → 1 → 4 → 2 → 0, on its first leg (to stop 3), 400 m of 1000 m done at 10 m/s
const leg = (energyKwh, path = [{ lat: 0, lng: 0 }, { lat: 0, lng: 0.01 }]) => ({ path, energyKwh, durationS: 100, plannedS: 90 });
const truck = (over = {}) => ({
  phase: 'driving', leg: 0, order: [0, 3, 1, 4, 2, 0],
  legs: [leg(1), leg(2), leg(3), leg(4), leg(5)],
  line: { length: 1000 }, along: 400, mps: 10, unloadLeft: 0, ...over,
});
const meta = { vehicleIndex: 1, unloadS: 600, cargoPerStopLbs: 900 };

test('a tick locks the stop the truck is driving to and sends what is left', () => {
  expect(tickTruckState(truck(), 1000, meta)).toEqual({
    vehicle_index: 1, locked_leg: 0, from_node: 3, start_s: 1000 + 60 + 600,
    remaining: [1, 4, 2], used_kwh: 1, cargo_per_stop_lbs: 900, drain_estimate: 1,
  });
  // Unloading at stop 1 (end of leg 1): it leaves when the unloading is done
  const unloading = tickTruckState(truck({ phase: 'unloading', leg: 1, unloadLeft: 120 }), 2000, meta);
  expect(unloading).toMatchObject({ locked_leg: 1, from_node: 1, start_s: 2120, remaining: [4, 2], used_kwh: 3 });
});

const drain = { factor: 1.5, event: { after_stop: 2, factor: 2 } };

test('drain factor jumps after the Nth delivery', () => {
  expect(drainAt(NO_DRAIN, 5)).toBe(1);
  expect(drainAt(undefined, 0)).toBe(1);
  expect([0, 1, 2, 3].map(k => drainAt(drain, k))).toEqual([1.5, 1.5, 2, 2]);
  expect(drainActive(NO_DRAIN)).toBe(false);
  expect(drainActive({ factor: 1, event: { after_stop: 1, factor: 1.2 } })).toBe(true);
  expect(drainActive({ factor: 1.3, event: null })).toBe(true);
});

test('a tick sends drained energy and the drain seen on finished legs', () => {
  const legs = truck().legs;                                          // 1, 2, 3, 4, 5 kWh
  expect(drainedKwh(legs, 2, drain)).toBeCloseTo(1 * 1.5 + 2 * 1.5 + 3 * 2);
  expect(drainEstimate(legs, -1, drain)).toBe(1);                     // nothing driven yet
  expect(drainEstimate(legs, 1, drain)).toBeCloseTo(1.5);
  expect(drainEstimate(legs, 2, drain)).toBe(2);                      // the last leg's jump counts at once
  // Driving leg 2 (to stop 4): its energy is counted in full, the drain seen covers legs 0-1
  expect(tickTruckState(truck({ leg: 1 }), 0, meta, drain))
    .toMatchObject({ used_kwh: 1 * 1.5 + 2 * 1.5, drain_estimate: 1.5 });
  expect(tickTruckState(truck({ phase: 'unloading', leg: 1, unloadLeft: 60 }), 0, meta, drain))
    .toMatchObject({ used_kwh: 4.5, drain_estimate: 1.5 });
});

test('state-of-charge trace drives the legs at the drain with no replanning', () => {
  const legs = [1, 2, 3].map((e, k) => ({ energyKwh: e * 10, toLabel: k === 2 ? 'Depot' : `S${k + 1}` }));
  const planned = socTrace(legs, 100, NO_DRAIN);
  expect(planned.soc_trace.map(p => p.soc_pct)).toEqual([100, 90, 70, 40]);
  expect(planned).toMatchObject({ final_soc_pct: 40, min_soc_pct: 40, ran_out_at: null });
  const drained = socTrace(legs, 100, { factor: 2, event: { after_stop: 2, factor: 3 } });
  expect(drained.soc_trace.map(p => p.soc_pct)).toEqual([100, 80, 40, -50]);
  expect(drained.ran_out_at).toBe('Depot');
});

test('nothing to send when only one stop is left after the locked one, or the truck is heading home', () => {
  expect(tickTruckState(truck({ leg: 2 }), 0, meta)).toBeNull();        // locked 4, then only 2
  expect(tickTruckState(truck({ leg: 4 }), 0, meta)).toBeNull();        // driving home
  expect(tickTruckState(truck({ phase: 'done' }), 0, meta)).toBeNull();
});

const result = {
  locked_leg: 0, order: [3, 4, 1, 2, 0],
  legs: [4, 1, 2, 0].map(n => ({ to_node: n, to_label: `S${n}`, path: [{ lat: 0, lng: n }], measured_s: 70, planned_s: 60, energy_kwh: 2, to: { lat: 0, lng: n } })),
};

test('a replan is swapped in after the locked stop while the truck has not left it', () => {
  const next = spliceReplan(truck(), result);
  expect(next.order).toEqual([0, 3, 4, 1, 2, 0]);
  expect(next.legs).toHaveLength(5);
  expect(next.legs[0].energyKwh).toBe(1);                              // the locked leg is untouched
  expect(next.legs.slice(1).map(l => l.toLabel)).toEqual(['S4', 'S1', 'S2', 'S0']);
  expect(next.legs[1]).toMatchObject({ durationS: 70, plannedS: 60, replanned: true, reroute: null });
  // Still unloading at the locked stop counts as not having left it
  expect(spliceReplan(truck({ phase: 'unloading' }), result)).not.toBeNull();
});

test('a replan that arrives after the truck left its locked stop is dropped', () => {
  expect(spliceReplan(truck({ leg: 1 }), result)).toBeNull();
  expect(spliceReplan(truck({ phase: 'done' }), result)).toBeNull();
});

test('accidents are placed on whichever replanned leg passes them', () => {
  const legs = [leg(1), leg(1, [{ lat: 0, lng: 1 }, { lat: 0, lng: 1.01 }]), leg(1, [{ lat: 0, lng: 2 }, { lat: 0, lng: 2.01 }])];
  const accidents = [
    { id: 1, lat: 0, lng: 0.005, leg_index: 0 },          // on the leg being driven: stays
    { id: 2, lat: 0, lng: 2.005, leg_index: Infinity },   // on the new third leg
    { id: 3, lat: 0.5, lng: 5, leg_index: Infinity },     // on no leg any more
  ];
  expect(placeAccidents(accidents, legs, 0).map(a => [a.id, a.leg_index])).toEqual([[1, 0], [2, 2]]);
});
