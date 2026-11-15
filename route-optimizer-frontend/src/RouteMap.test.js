import { stopSequence } from './RouteMap';

jest.mock('@react-google-maps/api', () => ({}));

test('stops are numbered by delivery order on each truck, not by form position', () => {
  const results = { ortools_fleet: { routes: [
    { vehicle_id: 1, vehicle_name: 'Vehicle 1', order: [0, 5, 4, 0], stop_kinds: ['depot', 'stop', 'stop', 'depot'] },
    { vehicle_id: 2, vehicle_name: 'Vehicle 2', order: [0, 3, 1, 2, 0], stop_kinds: ['depot', 'stop', 'stop', 'stop', 'depot'] },
  ] } };
  const seq = stopSequence(results);
  expect(seq[3]).toMatchObject({ n: 1, of: 3, truck: 'Vehicle 2' });   // Cerritos, the priority stop
  expect(seq[5]).toMatchObject({ n: 1, of: 2, truck: 'Vehicle 1' });
  expect(seq[2].n).toBe(3);
  expect(seq[0]).toBeUndefined();
});

test('after a breakdown a cargo pickup is not a delivery, and unserved stops have no number', () => {
  const results = { ortools_fleet: { routes: [
    { vehicle_id: 1, order: [0, 5, 4, 3, 1, 0], stop_kinds: ['depot', 'done', 'stop', 'pickup', 'rescue', 'depot'] },
    { vehicle_id: 2, order: [0, 3], stop_kinds: ['depot', 'done'] },
  ] } };
  const seq = stopSequence(results);
  expect(seq[1]).toMatchObject({ n: 3, of: 3, truck: 'Vehicle 1' });  // rescued after its pickup at stop 3
  expect(seq[3]).toMatchObject({ n: 1, truck: 'Vehicle 2' });         // delivered before the breakdown
  expect(seq[2]).toBeUndefined();
});

test('a single truck follows the optimal order', () => {
  const seq = stopSequence({ optimal: { order: [0, 2, 3, 1, 5, 4] } });
  expect([2, 3, 1, 5, 4].map(i => seq[i].n)).toEqual([1, 2, 3, 4, 5]);
});
