"""
Algorithm registry for the benchmark.

Every algorithm is a function solve(problem) -> list of closed routes
([0, ..., 0]). The runner handles timing and scoring, so algorithms only
decide which stops go on which truck and in what order.

To add an algorithm (e.g. a future BEV-aware router), write a solve function
and register it with ALGORITHMS.append(Algorithm(...)).
"""

import math
import random
from dataclasses import dataclass
from typing import Callable

from solvers import (
    solve_tsp_held_karp,
    solve_tsp_nearest_neighbor,
    solve_tsp_2opt,
    solve_vrp_ortools,
)

HELD_KARP_MAX_NODES = 13  # depot + 12 stops; beyond this runtime/memory blow up


# =============================================================================
# Helpers
# =============================================================================

def route_subset(problem, stop_ids: list, tsp_fn: Callable) -> list:
    """Run a single-vehicle TSP solver on depot + stop_ids; return a closed route."""
    if not stop_ids:
        return None
    nodes = [0] + list(stop_ids)
    sub = [[problem.dist[a][b] for b in nodes] for a in nodes]
    result = tsp_fn(sub, return_to_start=True)
    return [nodes[i] for i in result["order"]] + [0]


def _projected_xy(problem) -> list:
    """Equirectangular projection of lat/lng, good enough for clustering a metro area."""
    lat0 = math.radians(problem.locations[0]["lat"])
    return [
        (loc["lng"] * math.cos(lat0), loc["lat"])
        for loc in problem.locations
    ]


def _route_distance(problem, route: list) -> float:
    return sum(problem.dist[route[i]][route[i + 1]] for i in range(len(route) - 1))


def _even_chunks(items: list, k: int) -> list:
    """Split items into k contiguous chunks whose sizes differ by at most 1."""
    base, extra = divmod(len(items), k)
    chunks, start = [], 0
    for c in range(k):
        size = base + (1 if c < extra else 0)
        chunks.append(items[start:start + size])
        start += size
    return chunks


# =============================================================================
# Single-vehicle TSP (K = 1)
# =============================================================================

def solve_nn(problem) -> list:
    return [route_subset(problem, range(1, problem.n), solve_tsp_nearest_neighbor)]


def solve_2opt(problem) -> list:
    return [route_subset(problem, range(1, problem.n), solve_tsp_2opt)]


def solve_held_karp(problem) -> list:
    return [route_subset(problem, range(1, problem.n), solve_tsp_held_karp)]


# =============================================================================
# Naive fleet split: round-robin, no geography
# =============================================================================

def _round_robin(problem, tsp_fn) -> list:
    k = problem.num_vehicles
    buckets = [[s for s in range(1, problem.n) if (s - 1) % k == v] for v in range(k)]
    return [r for r in (route_subset(problem, b, tsp_fn) for b in buckets) if r]


def solve_rr_nn(problem) -> list:
    return _round_robin(problem, solve_tsp_nearest_neighbor)


def solve_rr_2opt(problem) -> list:
    return _round_robin(problem, solve_tsp_2opt)


# =============================================================================
# Capacity-balanced k-means + 2-opt per cluster
# =============================================================================

def _balanced_assign(points: dict, centroids: list, cap: int) -> dict:
    """Greedy capacity-constrained assignment: closest (stop, centroid) pairs first."""
    pairs = sorted(
        (math.dist(p, c), s, ci)
        for s, p in points.items()
        for ci, c in enumerate(centroids)
    )
    assignment, sizes = {}, [0] * len(centroids)
    for _, s, ci in pairs:
        if s not in assignment and sizes[ci] < cap:
            assignment[s] = ci
            sizes[ci] += 1
    return assignment


def _kmeans_pp_init(points: dict, k: int, rng: random.Random) -> list:
    ids = sorted(points)
    centroids = [points[rng.choice(ids)]]
    while len(centroids) < k:
        weights = [min(math.dist(points[s], c) ** 2 for c in centroids) for s in ids]
        if sum(weights) == 0:
            centroids.append(points[rng.choice(ids)])
        else:
            centroids.append(points[rng.choices(ids, weights=weights)[0]])
    return centroids


def balanced_kmeans(problem, restarts: int = 10, max_iter: int = 50, seed: int = 42) -> list:
    """Return k clusters of stop ids, each no larger than the stop cap."""
    xy = _projected_xy(problem)
    points = {s: xy[s] for s in range(1, problem.n)}
    k, cap = problem.num_vehicles, problem.max_stops_per_vehicle
    rng = random.Random(seed)

    best_clusters, best_sse = None, float("inf")
    for _ in range(restarts):
        centroids = _kmeans_pp_init(points, k, rng)
        assignment = None
        for _ in range(max_iter):
            new_assignment = _balanced_assign(points, centroids, cap)
            if new_assignment == assignment:
                break
            assignment = new_assignment
            for ci in range(k):
                members = [points[s] for s, c in assignment.items() if c == ci]
                if members:
                    centroids[ci] = (
                        sum(p[0] for p in members) / len(members),
                        sum(p[1] for p in members) / len(members),
                    )
        sse = sum(math.dist(points[s], centroids[c]) ** 2 for s, c in assignment.items())
        if sse < best_sse:
            best_sse = sse
            best_clusters = [sorted(s for s, c in assignment.items() if c == ci) for ci in range(k)]
    return best_clusters


def solve_kmeans_2opt(problem) -> list:
    clusters = balanced_kmeans(problem)
    return [r for r in (route_subset(problem, c, solve_tsp_2opt) for c in clusters) if r]


# =============================================================================
# Sweep algorithm (Gillett & Miller, 1974) + 2-opt per sector
# =============================================================================

def solve_sweep_2opt(problem) -> list:
    """
    Sort stops by polar angle around the depot, cut into K contiguous sectors
    of balanced size (<= stop cap), 2-opt each sector. Every rotation of the
    sweep start angle is tried and the lowest total distance is kept.
    """
    xy = _projected_xy(problem)
    dx, dy = xy[0]
    by_angle = sorted(range(1, problem.n), key=lambda s: math.atan2(xy[s][1] - dy, xy[s][0] - dx))
    k = problem.num_vehicles
    rotations = range(len(by_angle)) if k > 1 else [0]

    best_routes, best_cost = None, float("inf")
    for start in rotations:
        order = by_angle[start:] + by_angle[:start]
        routes = [r for r in (route_subset(problem, chunk, solve_tsp_2opt)
                              for chunk in _even_chunks(order, k)) if r]
        cost = sum(_route_distance(problem, r) for r in routes)
        if cost < best_cost:
            best_routes, best_cost = routes, cost
    return best_routes


# =============================================================================
# Clarke-Wright savings (parallel version)
# =============================================================================

def solve_clarke_wright(problem) -> list:
    """
    Start with one out-and-back route per stop, then merge route endpoints in
    decreasing order of savings s(i,j) = d(0,i) + d(0,j) - d(i,j) while the
    merged route stays within the stop cap. Savings use the symmetrized
    distance; each final route is driven in whichever direction is shorter.

    Classic Clarke-Wright does not fix the number of vehicles, so it can end
    with more than K routes. That is reported (and scored infeasible), not hidden.
    """
    d = problem.dist
    cap = problem.max_stops_per_vehicle

    def sym(a, b):
        return (d[a][b] + d[b][a]) / 2

    stops = range(1, problem.n)
    routes = {s: [s] for s in stops}      # route id -> ordered stops
    route_of = {s: s for s in stops}      # stop -> route id

    savings = sorted(
        ((sym(0, i) + sym(0, j) - sym(i, j), i, j)
         for i in stops for j in stops if i < j),
        reverse=True,
    )
    for _, i, j in savings:
        ri, rj = route_of[i], route_of[j]
        if ri == rj or len(routes[ri]) + len(routes[rj]) > cap:
            continue
        a, b = routes[ri], routes[rj]
        # Orient so that i ends route a and j starts route b
        if a[-1] != i:
            if a[0] != i:
                continue
            a = a[::-1]
        if b[0] != j:
            if b[-1] != j:
                continue
            b = b[::-1]
        merged = a + b
        routes[ri] = merged
        del routes[rj]
        for s in b:
            route_of[s] = ri

    result = []
    for r in routes.values():
        fwd, rev = [0] + r + [0], [0] + r[::-1] + [0]
        result.append(fwd if _route_distance(problem, fwd) <= _route_distance(problem, rev) else rev)
    return result


# =============================================================================
# OR-Tools (production engine) and a construction-only variant
# =============================================================================

def _int_matrix(matrix: list) -> list:
    return [[int(round(v)) for v in row] for row in matrix]


def solve_ortools(problem) -> list:
    return solve_vrp_ortools(
        _int_matrix(problem.dist),
        problem.num_vehicles,
        time_limit_s=problem.ortools_time_limit_s,
    )


def solve_ortools_cheap(problem) -> list:
    return solve_vrp_ortools(
        _int_matrix(problem.dist),
        problem.num_vehicles,
        metaheuristic=None,
        solution_limit=1,
        time_limit_s=problem.ortools_time_limit_s,
    )


# =============================================================================
# Registry
# =============================================================================

@dataclass
class Algorithm:
    id: str
    label: str
    family: str          # naive | tsp_heuristic | exact | cluster_first | vrp_heuristic | ortools
    vehicles: str        # "single" (K=1 only) | "fleet" (K>=2 only) | "any"
    description: str
    solve: Callable

    def supports(self, problem) -> bool:
        k = problem.num_vehicles
        if self.vehicles == "single" and k != 1:
            return False
        if self.vehicles == "fleet" and k < 2:
            return False
        if self.id == "held_karp" and problem.n > HELD_KARP_MAX_NODES:
            return False
        return True

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "family": self.family,
            "vehicles": self.vehicles,
            "description": self.description,
        }


ALGORITHMS = [
    Algorithm("nn", "Nearest Neighbor", "naive", "single",
              "Greedy: always drive to the closest unvisited stop.", solve_nn),
    Algorithm("2opt", "2-opt", "tsp_heuristic", "single",
              "Nearest neighbor, then reverse route segments while it helps.", solve_2opt),
    Algorithm("held_karp", "Held-Karp (optimal)", "exact", "single",
              "Exact dynamic-programming TSP; the true optimum for one truck.", solve_held_karp),
    Algorithm("rr_nn", "Round-robin + NN", "naive", "fleet",
              "Deal stops to trucks in list order, then nearest neighbor per truck.", solve_rr_nn),
    Algorithm("rr_2opt", "Round-robin + 2-opt", "naive", "fleet",
              "Deal stops to trucks in list order, then 2-opt per truck.", solve_rr_2opt),
    Algorithm("kmeans_2opt", "K-means + 2-opt", "cluster_first", "fleet",
              "Capacity-balanced k-means on lat/lng, then 2-opt per cluster.", solve_kmeans_2opt),
    Algorithm("sweep_2opt", "Sweep + 2-opt", "cluster_first", "any",
              "Radial sectors around the depot (Gillett-Miller), then 2-opt per sector.", solve_sweep_2opt),
    Algorithm("clarke_wright", "Clarke-Wright Savings", "vrp_heuristic", "any",
              "Merge out-and-back routes by largest distance savings, within the stop cap.", solve_clarke_wright),
    Algorithm("ortools_cheap", "OR-Tools (construction only)", "ortools", "any",
              "PATH_CHEAPEST_ARC first solution, no local search.", solve_ortools_cheap),
    Algorithm("ortools", "OR-Tools (production)", "ortools", "any",
              "PATH_CHEAPEST_ARC + Guided Local Search, production time limit.", solve_ortools),
]

ALGORITHMS_BY_ID = {a.id: a for a in ALGORITHMS}
