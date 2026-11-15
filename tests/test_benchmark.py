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


# =============================================================================
# Energy scoring and the optimization objective
# =============================================================================

from benchmark.energy import (
    DEFAULT_ENERGY_PARAMS, LBS_PER_TONNE, leg_energy_kwh, route_energy_kwh, static_energy_matrix_kwh,
)
from benchmark.objective import build_cost_matrix, parse_objective
from energy import calculate_energy_consumption


def hilly_problem(n_stops=8, k=1, seed=0, objective="energy", weights=None):
    """make_problem plus random elevations and the cost matrix for an objective."""
    base = make_problem(n_stops=n_stops, k=k, seed=seed)
    rng = random.Random(seed + 100)
    elevation = [rng.uniform(0, 250) for _ in range(base.n)]
    params = dict(DEFAULT_ENERGY_PARAMS)
    obj = parse_objective({"mode": objective, "weights": weights})
    energy = static_energy_matrix_kwh(base.dist, base.time, elevation, params)
    cost = build_cost_matrix(obj, base.dist, base.time, energy)
    return Problem("synthetic", base.locations, base.dist, base.time, num_vehicles=k,
                   ortools_time_limit_s=1, cost=cost, elevation_m=elevation,
                   energy_params=params, objective=obj)


def test_cost_defaults_to_distance():
    problem = make_problem()
    assert problem.cost is problem.dist


def test_distance_objective_changes_nothing():
    obj = parse_objective({"mode": "distance"})
    problem = make_problem(n_stops=6)
    assert build_cost_matrix(obj, problem.dist, problem.time, [[0] * problem.n] * problem.n) is problem.dist


def test_objective_validation():
    assert parse_objective(None)["mode"] == "distance"
    with pytest.raises(ValueError):
        parse_objective({"mode": "carbon"})
    with pytest.raises(ValueError):
        parse_objective({"mode": "blend", "weights": {"distance": 0, "time": 0, "energy": 0}})
    with pytest.raises(ValueError):
        parse_objective({"mode": "blend", "weights": {"distance": -1, "time": 2}})
    w = parse_objective({"mode": "blend", "weights": {"distance": 50, "time": 25, "energy": 25}})["weights"]
    assert w == {"distance": 0.5, "time": 0.25, "energy": 0.25}


def test_blend_with_one_weight_is_proportional_to_that_metric():
    problem = make_problem(n_stops=6)
    energy = static_energy_matrix_kwh(problem.dist, problem.time, None, dict(DEFAULT_ENERGY_PARAMS))
    cost = build_cost_matrix(parse_objective({"mode": "blend", "weights": {"time": 1}}),
                             problem.dist, problem.time, energy)
    ratios = {cost[i][j] / problem.time[i][j] for i in range(problem.n) for j in range(problem.n) if i != j}
    assert max(ratios) - min(ratios) < 1e-9


def test_energy_matches_model_and_is_direction_dependent():
    problem = hilly_problem()
    params = problem.energy_params
    i, j = 1, 2
    w = 10000.0
    up = leg_energy_kwh(problem.dist, problem.time, problem.elevation_m, i, j, w, params)
    expected = calculate_energy_consumption(
        distance_km=problem.dist[i][j] / 1000,
        avg_speed_kmh=problem.dist[i][j] / 1000 / (problem.time[i][j] / 3600),
        temperature_c=params["temperature_c"], vehicle_weight_tonnes=w / LBS_PER_TONNE,
        altitude_diff_m=problem.elevation_m[j] - problem.elevation_m[i],
        altitude_m=problem.elevation_m[i], has_climate_control=True)["energy_kwh"]
    assert up == pytest.approx(expected)
    # Model E has a positive weight x grade term, so climbing costs more than the same arc descending
    elev = list(problem.elevation_m)
    elev[j] = elev[i] + 100
    climb = leg_energy_kwh(problem.dist, problem.time, elev, i, j, w, params)
    elev[j] = elev[i] - 100
    descend = leg_energy_kwh(problem.dist, problem.time, elev, i, j, w, params)
    assert climb > descend


def test_route_energy_follows_production_unloading_convention():
    problem = hilly_problem()
    params = problem.energy_params
    route = [0, 3, 1, 2, 0]
    cargo = params["loaded_weight_lbs"] - params["base_weight_lbs"]
    weights = [params["loaded_weight_lbs"] - p * cargo / 3 for p in range(3)] + [params["base_weight_lbs"]]
    expected = sum(leg_energy_kwh(problem.dist, problem.time, problem.elevation_m,
                                  route[p], route[p + 1], weights[p], params) for p in range(4))
    assert route_energy_kwh(route, problem.dist, problem.time, problem.elevation_m, params) == pytest.approx(expected)


def test_score_reports_all_three_metrics_whatever_the_objective():
    for mode in ("distance", "time", "energy"):
        problem = hilly_problem(n_stops=6, k=2, objective=mode)
        m = score(problem, [[0, 1, 2, 3, 0], [0, 4, 5, 6, 0]], 0)
        assert m["total_distance_m"] > 0 and m["total_drive_time_s"] > 0 and m["total_energy_kwh"] > 0
        assert m["objective_cost"] == pytest.approx(sum(r["cost"] for r in m["routes"]))
        assert m["total_energy_kwh"] == pytest.approx(sum(r["energy_kwh"] for r in m["routes"]), abs=1e-3)


@pytest.mark.parametrize("mode", ["time", "energy", "blend"])
@pytest.mark.parametrize("k", [1, 2])
def test_every_algorithm_stays_valid_under_every_objective(mode, k):
    problem = hilly_problem(n_stops=8, k=k, objective=mode, weights={"distance": 1, "time": 1, "energy": 1})
    for algo in ALGORITHMS:
        if not algo.supports(problem):
            continue
        routes = algo.solve(problem)
        assert check_routes(problem, routes) == [], (algo.id, mode)
        assert all(len(r) - 2 <= problem.max_stops_per_vehicle for r in routes), (algo.id, mode)


def test_held_karp_minimizes_whatever_cost_it_is_given():
    by_energy = hilly_problem(n_stops=8, objective="energy", seed=4)
    by_distance = hilly_problem(n_stops=8, objective="distance", seed=4)
    hk = ALGORITHMS_BY_ID["held_karp"]
    route_e, route_d = hk.solve(by_energy), hk.solve(by_distance)

    def cost(problem, routes):
        return sum(problem.cost[r[i]][r[i + 1]] for r in routes for i in range(len(r) - 1))

    # Each is optimal for its own objective, so neither can lose on it
    assert cost(by_energy, route_e) <= cost(by_energy, route_d) + 1e-6
    assert cost(by_distance, route_d) <= cost(by_distance, route_e) + 1e-6


def test_runner_default_objective_matches_distance_gaps():
    result = run_benchmark("demo_anaheim", max_vehicles=2, ortools_time_limit_s=1)
    assert result["objective"]["mode"] == "distance"
    for r in result["rows"]:
        m = r["metrics"]
        assert m["gap_to_best_objective_pct"] == m["gap_to_best_distance_pct"]
        assert m["total_energy_kwh"] > 0


@pytest.mark.parametrize("objective", [
    {"mode": "energy"},
    {"mode": "time"},
    {"mode": "blend", "weights": {"distance": 50, "time": 25, "energy": 25}},
])
def test_runner_accepts_objectives(objective):
    result = run_benchmark("demo_anaheim", max_vehicles=2, ortools_time_limit_s=1, objective=objective)
    assert all("error" not in r for r in result["rows"])
    assert all(r["metrics"]["feasible"] for r in result["rows"] if r["algorithm"] != "clarke_wright")
    assert result["objective"]["mode"] == objective["mode"]
    assert result["energy"]["has_elevation"] in (True, False)
    # the best row on the minimized cost has a 0% gap to best
    k1 = [r["metrics"] for r in result["rows"] if r["num_vehicles"] == 1 and r["metrics"]["feasible"]]
    assert min(m["gap_to_best_objective_pct"] for m in k1) == 0


def test_runner_rejects_bad_objective():
    with pytest.raises(ValueError):
        run_benchmark("demo_anaheim", objective={"mode": "nope"})


# =============================================================================
# Single fleet size and the fixed stop order
# =============================================================================

from benchmark.fixed_plan import fixed_stop_plan
from benchmark.runner import fixed_plan_view
from benchmark import load_scenario


def plan_problem(n_stops=9, k=3, seed=0):
    """make_problem with the fixed plan attached."""
    base = make_problem(n_stops=n_stops, k=k, seed=seed)
    base.fixed_plan = fixed_stop_plan(n_stops, k)
    return base


def route_cost(problem, routes):
    return sum(problem.cost[r[i]][r[i + 1]] for r in routes for i in range(len(r) - 1))


@pytest.mark.parametrize("n,k", [(5, 1), (9, 3), (19, 5), (7, 7), (10, 4)])
def test_fixed_plan_is_a_deterministic_capped_permutation(n, k):
    plan = fixed_stop_plan(n, k)
    assert plan == fixed_stop_plan(n, k)
    assert len(plan) == k and all(r[0] == 0 and r[-1] == 0 for r in plan)
    flat = [s for r in plan for s in r[1:-1]]
    assert flat == list(range(1, n + 1))                      # listed order, nothing scrambled
    assert max(len(r) - 2 for r in plan) == -(-n // k)         # same cap the algorithms are held to
    assert max(len(r) for r in plan) - min(len(r) for r in plan) <= 1


def test_fixed_plan_view_names_every_stop():
    view = fixed_plan_view("case2_charlotte_lansing_mi", 3)
    assert [t["truck"] for t in view["trucks"]] == [1, 2, 3]
    assert [s["index"] for t in view["trucks"] for s in t["stops"]] == list(range(1, 8))
    assert view["trucks"][0]["stops"][0]["name"] == "Meijer Charlotte"
    with pytest.raises(ValueError):
        fixed_plan_view("case2_charlotte_lansing_mi", 8)


def test_single_fleet_size_runs_only_that_k():
    result = run_benchmark("case3_piedmont_sc", fleet_sizes=[5], ortools_time_limit_s=1,
                           algorithm_ids=["rr_nn", "sweep_2opt", "ortools"])
    assert result["fleet_sizes"] == [5] and result["max_vehicles"] == 5
    assert {r["num_vehicles"] for r in result["rows"]} == {5}
    assert [h["num_vehicles"] for h in result["headline"]] == [5]


def test_default_still_sweeps_from_one():
    result = run_benchmark("demo_anaheim", max_vehicles=3, ortools_time_limit_s=1, algorithm_ids=["nn", "rr_nn"])
    assert result["fleet_sizes"] == [1, 2, 3]


def test_fleet_size_is_validated():
    with pytest.raises(ValueError):
        run_benchmark("demo_anaheim", fleet_sizes=[6])   # 5 stops
    with pytest.raises(ValueError):
        run_benchmark("demo_anaheim", fleet_sizes=[0])


def test_baseline_row_is_the_fixed_plan_and_never_a_reference():
    result = run_benchmark("case1_plymouth_mi", fleet_sizes=[4], ortools_time_limit_s=1,
                           preserve_stop_order=True, algorithm_ids=["rr_nn", "kmeans_2opt", "sweep_2opt", "ortools"])
    rows = result["rows"]
    base = [r for r in rows if r["algorithm"] == "fixed_order"]
    assert len(base) == 1 and base[0]["family"] == "baseline"
    assert [r["route"] for r in base[0]["metrics"]["routes"]] == fixed_stop_plan(19, 4)
    assert base[0]["metrics"]["feasible"] and not base[0]["metrics"]["is_best_objective"]
    algos = [r["metrics"] for r in rows if r["algorithm"] != "fixed_order"]
    assert min(m["gap_to_best_objective_pct"] for m in algos) == 0      # best is always an algorithm
    assert result["preserve_stop_order"] is True


def test_baseline_absent_without_preserve():
    result = run_benchmark("demo_anaheim", fleet_sizes=[2], ortools_time_limit_s=1, algorithm_ids=["rr_nn"])
    assert all(r["algorithm"] != "fixed_order" for r in result["rows"])
    assert result["preserve_stop_order"] is False


@pytest.mark.parametrize("objective", [{"mode": "distance"}, {"mode": "energy"}, {"mode": "time"}])
@pytest.mark.parametrize("scenario,k", [("case1_plymouth_mi", 5), ("case2_charlotte_lansing_mi", 2), ("case3_piedmont_sc", 1)])
def test_locked_plan_every_algorithm_returns_it_unchanged(scenario, k, objective):
    n = len(load_scenario(scenario)["locations"]) - 1
    plan = fixed_stop_plan(n, k)
    result = run_benchmark(scenario, fleet_sizes=[k], preserve_stop_order=True, objective=objective)
    algos = [r for r in result["rows"] if r["algorithm"] != "fixed_order"]
    assert algos and all(r["locked"] for r in algos)
    for r in result["rows"]:
        assert [x["route"] for x in r["metrics"]["routes"]] == plan        # same trucks, stops and order
        assert r["metrics"]["feasible"]
    # nothing differs between rows, so every metric matches the baseline's
    base = next(r for r in result["rows"] if r["algorithm"] == "fixed_order")["metrics"]
    for r in algos:
        for key in ("total_distance_m", "total_drive_time_s", "total_energy_kwh", "objective_cost"):
            assert r["metrics"][key] == base[key]


def test_locked_plan_never_runs_the_solvers(monkeypatch):
    def boom(problem):
        raise AssertionError("a solver ran while the stop order was locked")
    for algo in ALGORITHMS:
        monkeypatch.setattr(algo, "solve", boom)
    result = run_benchmark("case1_plymouth_mi", fleet_sizes=[4], preserve_stop_order=True)
    assert all("error" not in r for r in result["rows"])
    assert result["preserve_stop_order"] is True


def test_locked_plan_is_identical_on_every_rerun():
    first = run_benchmark("case3_piedmont_sc", fleet_sizes=[5], preserve_stop_order=True)
    second = run_benchmark("case3_piedmont_sc", fleet_sizes=[5], preserve_stop_order=True)
    routes = lambda res: [(r["algorithm"], [x["route"] for x in r["metrics"]["routes"]]) for r in res["rows"]]
    assert routes(first) == routes(second)


def test_unlocked_run_is_unaffected_by_the_option():
    result = run_benchmark("demo_anaheim", fleet_sizes=[2], ortools_time_limit_s=1, algorithm_ids=["rr_nn", "ortools"])
    assert not any(r.get("locked") for r in result["rows"])
