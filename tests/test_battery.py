import random

import pytest

import route_optimizer_backend as backend
from benchmark import run_benchmark
from benchmark.energy import battery_limit, resolve_params, route_energy_kwh
from benchmark.metrics import score
from energy import battery_limit_wh, energy_upper_bound_matrix_wh
from solvers import BatteryLimitError, solve_vrp_ortools
from tests.test_benchmark import make_problem

HILLY = [100.0, 400.0, 20.0, 650.0, 300.0, 50.0, 500.0, 250.0, 700.0]


def _route_bound_wh(bound, route):
    return sum(bound[route[p]][route[p + 1]] for p in range(len(route) - 1))


def _route_peak_wh(bound, route):
    """Most energy used so far at any node, as the solver's Energy dimension sees it."""
    used, peak = 0, 0
    for p in range(len(route) - 1):
        used += bound[route[p]][route[p + 1]]
        peak = max(peak, used)
    return peak


def test_battery_limit_wh():
    assert battery_limit_wh(80, 20) == 64_000
    assert battery_limit_wh(80, 0) == 80_000


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_upper_bound_never_undershoots_weight_aware_energy(seed):
    problem = make_problem(n_stops=8, seed=seed)
    params = resolve_params({"battery_kwh": 80})
    bound, _ = battery_limit(problem.dist, problem.time, HILLY, params)
    rng = random.Random(seed)
    for _ in range(50):
        stops = rng.sample(range(1, problem.n), rng.randint(1, problem.num_stops))
        route = [0, *stops, 0]
        real_wh = route_energy_kwh(route, problem.dist, problem.time, HILLY, params) * 1000
        assert _route_bound_wh(bound, route) >= real_wh - 1e-6


def _tight_fleet(seed=0, k=3, margin=0.97):
    """A fleet whose limit is just below what the unconstrained plan's hungriest truck uses."""
    problem = make_problem(n_stops=9, k=k, seed=seed)
    bound = energy_upper_bound_matrix_wh(problem.dist, problem.time, None, 13000, 7000, 20.0)
    free = solve_vrp_ortools(problem.dist, k, time_limit_s=1)
    limit = int(max(_route_peak_wh(bound, r) for r in free) * margin)
    return problem, bound, free, limit


def test_ortools_keeps_every_truck_under_the_energy_limit():
    problem, bound, free, limit = _tight_fleet()
    assert any(_route_peak_wh(bound, r) > limit for r in free)  # the limit really binds
    routes = solve_vrp_ortools(problem.dist, problem.num_vehicles, time_limit_s=1,
                               energy_matrices=[bound] * problem.num_vehicles,
                               energy_limits=[limit] * problem.num_vehicles)
    assert sorted(s for r in routes for s in r[1:-1]) == list(range(1, problem.n))
    assert all(_route_peak_wh(bound, r) <= limit for r in routes)


def test_unreachable_stop_is_named():
    problem = make_problem(n_stops=5, k=2)
    bound = energy_upper_bound_matrix_wh(problem.dist, problem.time, None, 13000, 7000, 20.0)
    far = max(range(1, problem.n), key=lambda j: bound[0][j] + bound[j][0])
    limit = bound[0][far] + bound[far][0] - 1
    with pytest.raises(BatteryLimitError) as err:
        solve_vrp_ortools(problem.dist, 2, time_limit_s=1, energy_matrices=[bound] * 2, energy_limits=[limit] * 2)
    assert far in err.value.unreachable_stops


def test_keep_empty_returns_one_route_per_vehicle():
    problem = make_problem(n_stops=5, k=4)  # stop cap 2: at most 3 trucks need to drive
    routes = solve_vrp_ortools(problem.dist, 4, time_limit_s=1, keep_empty=True)
    assert len(routes) == 4
    assert sorted(s for r in routes for s in r[1:-1]) == list(range(1, problem.n))


def test_score_flags_a_truck_below_reserve():
    problem = make_problem(n_stops=4, k=1)
    used = route_energy_kwh([0, 1, 2, 3, 4, 0], problem.dist, problem.time, None, resolve_params())
    problem.energy_params = {"battery_kwh": used / 0.9}  # ends at 10% left
    m = score(problem, [[0, 1, 2, 3, 4, 0]], 0)
    assert m["min_battery_remaining_pct"] == pytest.approx(10, abs=0.1)
    assert m["valid"] and not m["feasible"]
    assert any("reserve 20%" in i for i in m["issues"])


def test_benchmark_battery_limit_is_off_by_default():
    result = run_benchmark("demo_anaheim", fleet_sizes=[2], ortools_time_limit_s=1, algorithm_ids=["ortools"])
    row = result["rows"][0]
    assert "error" not in row and row["metrics"]["min_battery_remaining_pct"] is None


def test_benchmark_ortools_respects_battery_limit():
    result = run_benchmark("case1_plymouth_mi", fleet_sizes=[4], ortools_time_limit_s=2,
                           algorithm_ids=["ortools"], energy_params={"battery_kwh": 110})
    m = result["rows"][0]["metrics"]
    assert m["feasible"] and m["min_battery_remaining_pct"] >= 20


# ── /api/optimize ────────────────────────────────────────────────────────────

@pytest.fixture
def api(monkeypatch):
    problem = make_problem(n_stops=6, seed=3)
    monkeypatch.setattr(backend, "get_distance_matrix",
                        lambda locs: {"distance": problem.dist, "time": problem.time})
    monkeypatch.setattr(backend, "get_elevation_data", lambda locs: [0.0] * len(locs))
    client = backend.app.test_client()

    def optimize(vehicles, **energy_params):
        return client.post("/api/optimize", json={
            "locations": problem.locations, "algorithms": [], "vehicles": vehicles,
            "energy_params": energy_params,
        })
    return optimize


def _vehicle(i, packs=4):
    return {"id": i, "name": f"V{i}", "battery_packs": packs, "battery_kwh": packs * 20,
            "weight_lbs": 13000 + (packs - 4) * 440.9}


def test_api_fleet_routes_stay_above_reserve(api):
    res = api([_vehicle(1), _vehicle(2)])
    assert res.status_code == 200
    fleet = res.get_json()["results"]["ortools_fleet"]
    assert fleet["battery_reserve_pct"] == 20
    for r in fleet["routes"]:
        assert r["battery_remaining_pct"] >= 20 and not r["below_battery_reserve"]


def test_api_reports_battery_limit_as_422(api):
    res = api([_vehicle(1), _vehicle(2)], battery_reserve_pct=99.5)
    assert res.status_code == 422
    body = res.get_json()
    assert body["error_type"] == "battery_limit" and body["unreachable_stops"]


def test_api_rejects_bad_reserve(api):
    assert api([_vehicle(1), _vehicle(2)], battery_reserve_pct=100).status_code == 400
