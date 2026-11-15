from itertools import combinations, permutations

import pytest

import route_optimizer_backend as backend
from benchmark import simulate
from benchmark.simulate import parse_drain
from energy import energy_upper_bound_matrix_wh
from solvers import BatteryLimitError, replan_route
from tests.test_benchmark import make_problem

BATTERY = {"battery_kwh": 110}


def _energy(matrix, route):
    return sum(matrix[route[i]][route[i + 1]] for i in range(len(route) - 1))


@pytest.fixture(scope="module")
def drained():
    """case1, 4 trucks, 30% more drain than planned: truck 1 has to drop its Detroit stops."""
    return simulate("case1_plymouth_mi", 4, energy_params=BATTERY, drain={"factor": 1.3}, ortools_time_limit_s=2)


# ── replan_route ─────────────────────────────────────────────────────────────

def _replan_case(seed=0):
    problem = make_problem(n_stops=7, seed=seed)
    energy = energy_upper_bound_matrix_wh(problem.dist, problem.time, None, 13000, 7000, 20.0)
    return problem, energy, 1, [2, 3, 4, 5, 6, 7]


def test_replan_keeps_every_stop_when_energy_allows():
    problem, energy, start, stops = _replan_case()
    result = replan_route(problem.dist, start, 0, stops, energy, 10**9)
    assert result["dropped"] == []
    assert result["route"][0] == start and result["route"][-1] == 0
    assert sorted(result["route"][1:-1]) == stops


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_replan_drops_the_fewest_stops_and_fits(seed):
    problem, energy, start, stops = _replan_case(seed)
    full = replan_route(problem.dist, start, 0, stops, energy, 10**9)["route"]
    limit = int(_energy(energy, full) * 0.6)
    result = replan_route(problem.dist, start, 0, stops, energy, limit, time_limit_s=2)
    assert _energy(energy, result["route"]) <= limit
    assert result["route"][0] == start and result["route"][-1] == 0
    assert sorted(result["route"][1:-1] + result["dropped"]) == stops

    # Brute force: keeping even one more stop, in any order, would not fit
    keep = len(stops) - len(result["dropped"])
    if keep < len(stops):
        best = min(_energy(energy, [start, *order, 0])
                   for subset in combinations(stops, keep + 1) for order in permutations(subset))
        assert best > limit


def test_replan_raises_when_even_going_home_does_not_fit():
    problem, energy, start, stops = _replan_case()
    with pytest.raises(BatteryLimitError):
        replan_route(problem.dist, start, 0, stops, energy, energy[start][0] - 1)


# ── simulate ─────────────────────────────────────────────────────────────────

def test_no_extra_drain_drives_the_plan_exactly():
    result = simulate("case1_plymouth_mi", 4, energy_params=BATTERY, drain={"factor": 1.0}, ortools_time_limit_s=2)
    assert result["summary"]["replans"] == 0 and result["summary"]["dropped"] == 0
    for t in result["trucks"]:
        assert t["actual_route"] == t["planned_route"]
        assert t["soc_trace"] == t["planned"]["soc_trace"]
        assert all(e["action"] == "continue" for e in t["events"])


def test_drain_replans_drops_and_stays_above_reserve(drained):
    s = drained["summary"]
    assert s["replans"] >= 1 and s["dropped"] >= 1
    assert s["blind_trucks_below_reserve"] >= 1
    for t in drained["trucks"]:
        planned_stops = t["planned_route"][1:-1]
        assert sorted(t["served"] + t["dropped"]) == sorted(planned_stops)
        assert len(set(t["served"])) == len(t["served"])
        gave_up = any(e["action"] == "return_to_depot" for e in t["events"])
        assert gave_up or t["min_soc_pct"] >= drained["energy"]["battery_reserve_pct"]
        assert t["blind"]["final_soc_pct"] <= t["final_soc_pct"]


def test_event_changes_drain_after_nth_stop():
    result = simulate("case3_piedmont_sc", 3, energy_params={"battery_kwh": 180},
                      drain={"factor": 1.1, "event": {"after_stop": 2, "factor": 1.8}}, ortools_time_limit_s=2)
    for t in result["trucks"]:
        estimates = [e["drain_estimate"] for e in t["events"]]
        assert estimates[:2] == [1.1, 1.1]          # first two legs at the normal drain
        assert all(x == 1.8 for x in estimates[2:])  # third leg onwards: the event's drain
        assert all(e["action"] == "continue" for e in t["events"][:2])
    assert result["summary"]["dropped"] >= 1


def test_parse_drain_validates():
    assert parse_drain(None) == {"factor": 1.3, "event": None}
    with pytest.raises(ValueError):
        parse_drain({"factor": 0})
    with pytest.raises(ValueError):
        parse_drain({"factor": 1.2, "event": {"after_stop": 2}})


def test_simulate_needs_a_battery():
    with pytest.raises(ValueError):
        simulate("demo_anaheim", 2)


# ── /api/benchmark/simulate ──────────────────────────────────────────────────

@pytest.fixture
def client():
    return backend.app.test_client()


def test_api_simulate(client):
    res = client.post("/api/benchmark/simulate", json={
        "scenario_id": "case2_charlotte_lansing_mi", "num_vehicles": 2, "ortools_time_limit_s": 1,
        "energy_params": {"battery_kwh": 120}, "drain": {"factor": 1.2},
    })
    assert res.status_code == 200
    body = res.get_json()
    assert body["status"] == "ok" and len(body["trucks"]) == 2
    t = body["trucks"][0]
    assert {"planned_route", "actual_route", "events", "soc_trace", "blind", "planned"} <= set(t)


def test_api_simulate_initial_plan_too_big_is_422(client):
    res = client.post("/api/benchmark/simulate", json={
        "scenario_id": "demo_anaheim", "num_vehicles": 2, "energy_params": {"battery_kwh": 80},
    })
    assert res.status_code == 422 and res.get_json()["error_type"] == "battery_limit"


def test_api_simulate_bad_input_is_400(client):
    assert client.post("/api/benchmark/simulate", json={"scenario_id": "demo_anaheim"}).status_code == 400
    assert client.post("/api/benchmark/simulate", json={
        "scenario_id": "demo_anaheim", "energy_params": {"battery_kwh": 180}, "drain": {"factor": -1},
    }).status_code == 400
