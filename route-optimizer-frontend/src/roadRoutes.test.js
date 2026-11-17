let fetchRoadRoute, pointAlongLeg;
const points = [{ lat: 33, lng: -118 }, { lat: 34, lng: -118 }, { lat: 35, lng: -117 }];
const geometry = pts => ({ path: pts, distance_m: pts.length * 100, time_s: pts.length * 10,
  legs: pts.slice(1).map((p, i) => ({ path: [pts[i], p], distance_m: 100, time_s: 10 })) });
const success = pts => ({ ok: true, json: async () => ({ status: 'ok', route: geometry(pts) }) });

beforeEach(() => {
  jest.resetModules();
  ({ fetchRoadRoute, pointAlongLeg } = require('./roadRoutes'));
  global.fetch = jest.fn();
});
afterEach(() => { delete global.fetch; jest.restoreAllMocks(); });

test('uses the backend in fixed order and shares an in-flight request', async () => {
  const closed = [...points, points[0]];
  fetch.mockResolvedValue(success(closed));
  const a = fetchRoadRoute(closed), b = fetchRoadRoute(closed);
  expect(a).toBe(b);
  expect(await a).toEqual(geometry(closed));
  expect(fetch).toHaveBeenCalledTimes(1);
  const [url, options] = fetch.mock.calls[0];
  expect(url).toMatch(/\/api\/directions$/);
  expect(JSON.parse(options.body)).toEqual({ locations: closed, order: [0, 1, 2, 3] });
  expect(options.headers).toEqual({ 'Content-Type': 'application/json' });
});

test('breakdown routes stay open and opposite orders do not share cached geometry', async () => {
  fetch.mockImplementation(async (url, options) => success(JSON.parse(options.body).locations));
  expect((await fetchRoadRoute(points)).path).toEqual(points);
  const reversed = [...points].reverse();
  expect((await fetchRoadRoute(reversed)).path).toEqual(reversed);
  expect(fetch).toHaveBeenCalledTimes(2);
});

test('failed calls can be retried and successful geometry expires', async () => {
  fetch.mockResolvedValueOnce({ ok: false, json: async () => ({ status: 'error', message: 'Routes API is not enabled' }) })
    .mockResolvedValue(success(points));
  await expect(fetchRoadRoute(points)).rejects.toThrow('Routes API is not enabled');
  await fetchRoadRoute(points);
  const now = Date.now();
  jest.spyOn(Date, 'now').mockReturnValue(now + 6 * 60 * 1000);
  await fetchRoadRoute(points);
  expect(fetch).toHaveBeenCalledTimes(3);
});

test('long benchmark routes are chunked without losing or duplicating delivery legs', async () => {
  const long = Array.from({ length: 31 }, (_, i) => ({ lat: 33 + i / 1000, lng: -118 }));
  fetch.mockImplementation(async (url, options) => success(JSON.parse(options.body).locations));
  const route = await fetchRoadRoute(long);
  expect(fetch.mock.calls.map(([, options]) => JSON.parse(options.body).locations.length)).toEqual([27, 5]);
  expect(route.path).toEqual(long);
  expect(route.legs).toHaveLength(30);
  expect(route.legs[26].path).toEqual([long[26], long[27]]);
});

test('a truck staying at the depot needs no route API call', async () => {
  const result = await fetchRoadRoute([points[0], points[0]]);
  expect(result.legs).toHaveLength(1);
  expect(result.distance_m).toBe(0);
  expect(fetch).not.toHaveBeenCalled();
});

test('depletion interpolation follows per-leg road geometry rather than a line between stops', () => {
  const bent = { legs: [{ path: [{ lat: 0, lng: 0 }, { lat: 0, lng: 1 }, { lat: 1, lng: 1 }] }] };
  expect(pointAlongLeg(bent, 0, .5)).toEqual({ lat: 0, lng: 1 });
  expect(pointAlongLeg(bent, 0, 1)).toEqual({ lat: 1, lng: 1 });
  expect(pointAlongLeg(bent, 3, .5)).toBeNull();
});
