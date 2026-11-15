"""
Time-dependent travel times from the POC traffic data package.

The data package (poc_route_planning_v1/) has the major-road network of a
15 x 14 km area of LA / Orange County and, for four days, ground-truth speeds
every 5 minutes plus 2-hour speed forecasts issued every 5 minutes. See its
README.md for details.

Travel times follow the package's rules: an edge takes the speed of its
corridor in the 5-minute interval in which it is entered; each step to the next
edge adds the stretch through the intersection (at the next edge's speed) plus
the traffic-light delay.

The data is licensed for this project only, so the package is kept out of git.
"""
import heapq
import json
import math
import os
from functools import lru_cache
from types import SimpleNamespace

import numpy as np
import pandas as pd

DATA_DIR = os.environ.get('POC_TRAFFIC_DIR', os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 'poc_route_planning_v1'))

# Each day of data, and the day whose ground truth stands in for its history
HISTORY_DAY = {
    '2025-11-12': '2025-11-18',  # Wed <- Tue
    '2025-11-18': '2025-11-12',  # Tue <- Wed
    '2025-11-22': '2025-11-23',  # Sat <- Sun
    '2025-11-23': '2025-11-22',  # Sun <- Sat
}
SOURCES = ('forecast', 'ground_truth', 'historical')

MIN_SPEED_KMH = 5.0            # as in the package's example.py (avoids near-zero speeds)
TRUCK_MAX_SPEED_KMH = 88.0     # California truck limit (55 mph); the data has car speeds
FREEWAY_SPEED_LIMIT_KMH = 88.0  # stops are never placed on edges this fast
SNAP_TOLERANCE_M = 30          # also use edges this much farther than the nearest (other direction)
MAX_SNAP_DISTANCE_M = 1000     # a stop farther than this from the network is outside the data


def _secs(ts) -> float:
    """Seconds on one consistent clock (local time, as in the data)."""
    return pd.Timestamp(ts).value / 1e9


@lru_cache(maxsize=1)
def load_network() -> SimpleNamespace:
    """Edges, connections and edge geometry, loaded once."""
    edges = pd.read_csv(os.path.join(DATA_DIR, 'network', 'edges.csv'), dtype={'edge_id': str})
    connections = pd.read_csv(os.path.join(DATA_DIR, 'network', 'connections.csv'),
                              dtype={'from_edge': str, 'to_edge': str}, keep_default_na=False)
    with open(os.path.join(DATA_DIR, 'network', 'corridors.geojson')) as f:
        corridors = json.load(f)['features']

    next_edges = {}  # edge -> [(next edge, via_length_m, signal_delay_s)]
    for row in connections.itertuples():
        next_edges.setdefault(row.from_edge, []).append(
            (row.to_edge, row.via_length_m, row.signal_delay_s))

    # The i-th line of a corridor's geometry is its i-th edge in edges.csv
    edges_by_corridor = edges.groupby('corridor_id', sort=False).edge_id.apply(list)
    geometry = {}
    for feature in corridors:
        corridor_edges = edges_by_corridor[feature['properties']['corridor_id']]
        for edge_id, line in zip(corridor_edges, feature['geometry']['coordinates']):
            geometry[edge_id] = [{"lat": lat, "lng": lng} for lng, lat in line]

    snappable = edges[~edges.is_virtual & (edges.speed_limit_kmh < FREEWAY_SPEED_LIMIT_KMH)].edge_id
    return SimpleNamespace(
        length=dict(zip(edges.edge_id, edges.length_m)),
        corridor=dict(zip(edges.edge_id, edges.corridor_id)),
        next_edges=next_edges,
        geometry=geometry,
        snappable=[e for e in snappable if e in geometry],
        num_corridor_ids=int(edges.corridor_id.max()) + 1,
    )


class SpeedTable:
    """Speeds (m/s) per 5-minute interval and corridor."""

    def __init__(self, frame: pd.DataFrame, time_col: str, shift=pd.Timedelta(0)):
        table = frame.pivot(index=time_col, columns='corridor_id', values='speed_kmh').sort_index()
        # Corridor ids have gaps, so reindex: column position == corridor id
        table = table.reindex(columns=range(load_network().num_corridor_ids))
        self.start = _secs(table.index[0] + shift)
        self.speeds = np.clip(table.to_numpy(dtype=float), MIN_SPEED_KMH, TRUCK_MAX_SPEED_KMH) / 3.6

    def speed(self, corridor: int, t: float) -> float:
        """Speed of a corridor at time t; past either end of the table, the nearest interval."""
        i = min(max(int((t - self.start) // 300), 0), len(self.speeds) - 1)
        return self.speeds[i, corridor]


@lru_cache(maxsize=8)
def _ground_truth(day: str) -> SpeedTable:
    return SpeedTable(pd.read_parquet(os.path.join(DATA_DIR, 'ground_truth', f'{day}.parquet')), 'time')


@lru_cache(maxsize=8)
def _historical(day: str) -> SpeedTable:
    """Another day's ground truth, moved onto this day's clock (same time of day)."""
    other = HISTORY_DAY[day]
    frame = pd.read_parquet(os.path.join(DATA_DIR, 'ground_truth', f'{other}.parquet'))
    return SpeedTable(frame, 'time', shift=pd.Timestamp(day) - pd.Timestamp(other))


@lru_cache(maxsize=32)
def _forecast(day: str, issue_time: pd.Timestamp) -> SpeedTable:
    frame = pd.read_parquet(os.path.join(DATA_DIR, 'forecasts', f'{day}.parquet'),
                            filters=[('issue_time', '==', issue_time)])
    if frame.empty:
        raise ValueError(f"No forecast issued at {issue_time}")
    return SpeedTable(frame, 'target_start')


def speed_table(source: str, departure: pd.Timestamp) -> SpeedTable:
    """
    Speeds known for a vehicle leaving at `departure`:
      forecast:     the forecast issued at departure (covers 2 hours, then its last value)
      ground_truth: the measured speeds of that day (perfect knowledge)
      historical:   the measured speeds of a comparable other day at the same times
    """
    day = departure.strftime('%Y-%m-%d')
    if day not in HISTORY_DAY:
        raise ValueError(f"No traffic data for {day}; available days: {', '.join(HISTORY_DAY)}")
    if source == 'forecast':
        return _forecast(day, departure.floor('5min'))
    if source == 'ground_truth':
        return _ground_truth(day)
    if source == 'historical':
        return _historical(day)
    raise ValueError(f"Unknown traffic source '{source}'; use one of {', '.join(SOURCES)}")


def snap_to_network(location: dict) -> list:
    """
    Edges a stop can leave from or arrive on: the nearest non-freeway edge plus
    any within SNAP_TOLERANCE_M of it (usually the other direction of that road).
    """
    net = load_network()
    distances = [(_distance_to_line_m(location, net.geometry[e]), e) for e in net.snappable]
    nearest = min(d for d, _ in distances)
    if nearest > MAX_SNAP_DISTANCE_M:
        raise ValueError(f"{location.get('address', location)} is outside the traffic data area")
    return [e for d, e in distances if d <= nearest + SNAP_TOLERANCE_M]


def _fastest_routes(table: SpeedTable, sources: list, departure: float, targets: set) -> dict:
    """
    Time-dependent Dijkstra from the start of any source edge, until every
    target edge is reached.

    Returns:
        {edge: (arrival at the edge's end, metres driven, previous edge)}
    """
    net = load_network()
    queue = []
    for edge in sources:
        arrival = departure + net.length[edge] / table.speed(net.corridor[edge], departure)
        heapq.heappush(queue, (arrival, edge, net.length[edge], None))

    reached, best, remaining = {}, {}, set(targets)
    while queue and remaining:
        t, edge, metres, previous = heapq.heappop(queue)
        if edge in reached:
            continue
        reached[edge] = (t, metres, previous)
        remaining.discard(edge)
        for next_edge, via_m, delay_s in net.next_edges.get(edge, ()):
            if next_edge in reached:
                continue
            corridor = net.corridor[next_edge]
            t_next = t + via_m / table.speed(corridor, t) + delay_s
            t_next += net.length[next_edge] / table.speed(corridor, t_next)
            if t_next < best.get(next_edge, math.inf):
                best[next_edge] = t_next
                heapq.heappush(queue, (t_next, next_edge, metres + via_m + net.length[next_edge], edge))
    return reached


def travel_matrices(locations: list, source: str, departure_time: str) -> dict:
    """
    Distance (m) and travel time (s) between all locations from the traffic data.

    Every pair is timed as if it started at the departure time (a snapshot), so
    the existing solvers can use the matrices unchanged.

    Returns:
        Dict with 'distance' and 'time' matrices, and 'paths': {(i, j): [edge, ...]}
    """
    departure = pd.Timestamp(departure_time)
    table = speed_table(source, departure)
    snapped = [snap_to_network(loc) for loc in locations]
    all_targets = {e for edges in snapped for e in edges}

    n = len(locations)
    distance = [[0] * n for _ in range(n)]
    duration = [[0] * n for _ in range(n)]
    paths = {}
    start = _secs(departure)
    for i in range(n):
        reached = _fastest_routes(table, snapped[i], start, all_targets)
        for j in range(n):
            if i == j:
                continue
            ends = [e for e in snapped[j] if e in reached]
            if not ends:
                raise ValueError(f"No route in the traffic network from location {i} to {j}")
            end = min(ends, key=lambda e: reached[e][0])
            arrival, metres, _ = reached[end]
            distance[i][j] = round(metres)
            duration[i][j] = round(arrival - start)
            path, edge = [], end
            while edge is not None:
                path.append(edge)
                edge = reached[edge][2]
            paths[(i, j)] = path[::-1]
    return {"distance": distance, "time": duration, "paths": paths}


def drive_on_ground_truth(paths: dict, order: list, departure_time: str,
                          unload_s: float, return_to_start: bool) -> list:
    """
    Drive a planned route on the measured speeds, with the clock running
    (unload_s at each stop before leaving it), as the package README suggests:
    plan with what was known, then check how long it actually takes.

    Returns:
        Actual travel seconds of each leg, in route order
    """
    net = load_network()
    departure = pd.Timestamp(departure_time)
    table = speed_table('ground_truth', departure)
    stops = order + ([order[0]] if return_to_start else [])

    t = _secs(departure)
    leg_times = []
    for k, (a, b) in enumerate(zip(stops, stops[1:])):
        if k > 0:
            t += unload_s
        leg_start = t
        path = paths[(a, b)]
        t += net.length[path[0]] / table.speed(net.corridor[path[0]], t)
        for prev, edge in zip(path, path[1:]):
            via_m, delay_s = next((v, d) for e, v, d in net.next_edges[prev] if e == edge)
            corridor = net.corridor[edge]
            t += via_m / table.speed(corridor, t) + delay_s
            t += net.length[edge] / table.speed(corridor, t)
        leg_times.append(round(t - leg_start))
    return leg_times


def _distance_to_line_m(point: dict, line: list) -> float:
    """Shortest distance in metres from a point to a polyline (local flat projection)."""
    def xy(p):
        return ((p['lng'] - point['lng']) * 111320 * math.cos(math.radians(point['lat'])),
                (p['lat'] - point['lat']) * 110540)
    best = math.inf
    for a, b in zip(line, line[1:]):
        (ax, ay), (bx, by) = xy(a), xy(b)
        dx, dy = bx - ax, by - ay
        seg_sq = dx * dx + dy * dy
        t = 0.0 if seg_sq == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / seg_sq))
        best = min(best, math.hypot(ax + t * dx, ay + t * dy))
    return best
