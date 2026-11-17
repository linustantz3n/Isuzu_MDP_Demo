import { replanSchedule, REPLAN_INTERVAL_S, tickTruckState, spliceReplan, placeAccidents } from './DriveSimulation';

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
    remaining: [1, 4, 2], used_kwh: 1, cargo_per_stop_lbs: 900,
  });
  // Unloading at stop 1 (end of leg 1): it leaves when the unloading is done
  const unloading = tickTruckState(truck({ phase: 'unloading', leg: 1, unloadLeft: 120 }), 2000, meta);
  expect(unloading).toMatchObject({ locked_leg: 1, from_node: 1, start_s: 2120, remaining: [4, 2], used_kwh: 3 });
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
