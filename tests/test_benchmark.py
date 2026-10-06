import inspect
import math
import random

import pytest

from benchmark import ALGORITHMS, ALGORITHMS_BY_ID, list_scenarios, run_benchmark
from benchmark.algorithms import balanced_kmeans
from benchmark.metrics import check_routes, score
from benchmark.problem import Problem
from solvers import solve_vrp_ortools


def make_problem(n_stops=8, k=1, seed=0, asymmetric=True):
    """Random points around a depot; distances in meters with optional one-way noise."""
    rng = random.Random(seed)
    locations = [{"address": "depot", "lat": 33.8, "lng": -117.9}] + [
        {"address": f"s{i}", "lat": 33.8 + rng.uniform(-0.2, 0.2), "lng": -117.9 + rng.uniform(-0.2, 0.2)}
        for i in range(1, n_stops + 1)
    ]

    def meters(a, b):
        dy = (a["lat"] - b["lat"]) * 111_000
        dx = (a["lng"] - b["lng"]) * 111_000 * math.cos(math.radians(33.8))
        return math.hypot(dx, dy)

    dist = [[0 if i == j else int(meters(a, b) * (1 + (rng.uniform(0, 0.15) if asymmetric else 0)))
             for j, b in enumerate(locations)] for i, a in enumerate(locations)]
    time = [[int(d / 12) for d in row] for row in dist]  # ~43 km/h
    return Problem("synthetic", locations, dist, time, num_vehicles=k, ortools_time_limit_s=1)


@pytest.mark.parametrize("k", [1, 2, 3])
@pytest.mark.parametrize("seed", [0, 1])
def test_every_algorithm_returns_valid_capped_routes(k, seed):
    problem = make_problem(n_stops=8, k=k, seed=seed)
    for algo in ALGORITHMS:
        if not algo.supports(problem):
            continue
        routes = algo.solve(problem)
        assert check_routes(problem, routes) == [], algo.id
        assert all(len(r) - 2 <= problem.max_stops_per_vehicle for r in routes), algo.id
        if algo.id != "clarke_wright":  # CW may legitimately need more than K trucks
            assert len(routes) <= k, algo.id


def test_held_karp_is_optimal_at_k1():
    problem = make_problem(n_stops=9, k=1, seed=3)
    costs = {a.id: score(problem, a.solve(problem), 0)["total_distance_m"]
             for a in ALGORITHMS if a.supports(problem)}
    assert costs["held_karp"] == min(costs.values())


def test_supports_matches_vehicle_scope():
    one, fleet = make_problem(k=1), make_problem(k=2)
    assert ALGORITHMS_BY_ID["nn"].supports(one) and not ALGORITHMS_BY_ID["nn"].supports(fleet)
    assert ALGORITHMS_BY_ID["rr_nn"].supports(fleet) and not ALGORITHMS_BY_ID["rr_nn"].supports(one)
    assert ALGORITHMS_BY_ID["ortools"].supports(one) and ALGORITHMS_BY_ID["ortools"].supports(fleet)
    assert not ALGORITHMS_BY_ID["held_karp"].supports(make_problem(n_stops=13, k=1))


def test_balanced_kmeans_respects_cap():
    problem = make_problem(n_stops=10, k=3, seed=5)
    clusters = balanced_kmeans(problem)
    assert sorted(s for c in clusters for s in c) == list(range(1, 11))
    assert max(len(c) for c in clusters) <= problem.max_stops_per_vehicle


def test_score_recomputes_from_matrices():
    problem = make_problem(n_stops=4, k=2)
    routes = [[0, 1, 2, 0], [0, 3, 4, 0]]
    d = problem.dist
    expected = d[0][1] + d[1][2] + d[2][0] + d[0][3] + d[3][4] + d[4][0]
    m = score(problem, routes, 1.0)
    assert m["total_distance_m"] == expected
    assert m["feasible"] and m["vehicles_used"] == 2 and m["max_stops_per_truck"] == 2


def test_score_flags_invalid_and_infeasible():
    problem = make_problem(n_stops=4, k=2)
    assert not score(problem, [[0, 1, 2, 0]], 0)["valid"]          # stops 3, 4 missing
    over_cap = score(problem, [[0, 1, 2, 3, 0], [0, 4, 0]], 0)      # cap is 2
    assert over_cap["valid"] and not over_cap["feasible"]


def test_ortools_defaults_are_production_config():
    params = inspect.signature(solve_vrp_ortools).parameters
    assert params["first_solution_strategy"].default == "PATH_CHEAPEST_ARC"
    assert params["metaheuristic"].default == "GUIDED_LOCAL_SEARCH"
    assert params["time_limit_s"].default is None
    assert params["solution_limit"].default is None


def test_runner_on_demo_scenario():
    assert "demo_anaheim" in {s["id"] for s in list_scenarios()}
    result = run_benchmark("demo_anaheim", max_vehicles=2, ortools_time_limit_s=1)
    assert all("error" not in r for r in result["rows"])
    k1 = {r["algorithm"]: r["metrics"] for r in result["rows"] if r["num_vehicles"] == 1}
    assert k1["held_karp"]["gap_to_optimal_distance_pct"] == 0
    assert all(m["gap_to_optimal_distance_pct"] >= 0 for m in k1.values())
    assert [h["num_vehicles"] for h in result["headline"]] == [1, 2]


def test_runner_rejects_unknown_scenario():
    with pytest.raises(ValueError):
        run_benchmark("../../etc/passwd")
