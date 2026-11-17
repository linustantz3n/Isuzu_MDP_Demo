import { API_BASE } from './api';

// Share road requests across the planner, comparison maps and simulation.
// Keep a short, bounded cache; failed calls can always be retried.
const cache = new Map();
const TTL_MS = 5 * 60 * 1000;
const MAX_ROUTES = 100;
const MAX_POINTS = 27; // Routes API: origin, up to 25 intermediates, destination.
const samePoint = (a, b) => a.lat === b.lat && a.lng === b.lng;

async function fetchPart(points) {
  const response = await fetch(`${API_BASE}/directions`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ locations: points, order: points.map((_, i) => i) }),
  });
  const data = await response.json();
  if (!response.ok || data.status !== 'ok') throw new Error(data.message || 'Road routes could not be loaded');
  const route = data.route;
  if (!route?.path?.length || route.legs?.length !== points.length - 1
      || route.legs.some(leg => !leg.path?.length)) throw new Error('The road route has incomplete leg geometry');
  return route;
}

export function fetchRoadRoute(locations) {
  const points = locations.map(({ lat, lng }) => ({ lat, lng }));
  if (points.length < 2) return Promise.reject(new Error('A road route needs at least two points'));
  if (points.every(p => samePoint(p, points[0]))) {
    return Promise.resolve({ path: points, distance_m: 0, time_s: 0,
      legs: points.slice(1).map(p => ({ path: [points[0], p], distance_m: 0, time_s: 0 })) });
  }
  const key = JSON.stringify(points);
  const cached = cache.get(key);
  if (cached && Date.now() - cached.created < TTL_MS) return cached.promise;
  const entry = { created: Date.now() };
  entry.promise = (async () => {
    // Consecutive chunks share their boundary stop, preserving every delivery
    // and one geometry leg per planned leg even for long benchmark routes.
    const parts = [];
    for (let start = 0; start < points.length - 1; start += MAX_POINTS - 1) {
      parts.push(await fetchPart(points.slice(start, start + MAX_POINTS)));
    }
    return { path: parts.flatMap((p, i) => i && samePoint(parts[i - 1].path.at(-1), p.path[0]) ? p.path.slice(1) : p.path),
      legs: parts.flatMap(p => p.legs), distance_m: parts.reduce((s, p) => s + p.distance_m, 0),
      time_s: parts.reduce((s, p) => s + p.time_s, 0) };
  })().catch(error => { if (cache.get(key) === entry) cache.delete(key); throw error; });
  cache.delete(key);
  cache.set(key, entry);
  if (cache.size > MAX_ROUTES) cache.delete(cache.keys().next().value);
  return entry.promise;
}

function metres(a, b) {
  const rad = Math.PI / 180;
  const h = Math.sin((b.lat - a.lat) * rad / 2) ** 2
    + Math.cos(a.lat * rad) * Math.cos(b.lat * rad) * Math.sin((b.lng - a.lng) * rad / 2) ** 2;
  return 6371000 * 2 * Math.asin(Math.sqrt(Math.min(1, h)));
}

export function pointAlongLeg(route, legIndex, fraction) {
  const path = route?.legs?.[legIndex]?.path || [];
  if (!path.length) return null;
  const lengths = path.slice(1).map((p, i) => metres(path[i], p));
  let left = Math.max(0, Math.min(1, fraction)) * lengths.reduce((a, b) => a + b, 0);
  for (let i = 0; i < lengths.length; i++) {
    if (left <= lengths[i]) {
      const w = lengths[i] ? left / lengths[i] : 0;
      return { lat: path[i].lat + w * (path[i + 1].lat - path[i].lat),
        lng: path[i].lng + w * (path[i + 1].lng - path[i].lng) };
    }
    left -= lengths[i];
  }
  return path[path.length - 1];
}
