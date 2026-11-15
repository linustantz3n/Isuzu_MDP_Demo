"""
Benchmark runner: every algorithm x every fleet size on one saved scenario.
"""

import json
import os
import time

from .algorithms import ALGORITHMS, ALGORITHMS_BY_ID
from .energy import battery_limit, resolve_params, static_energy_matrix_kwh
from .fixed_plan import fixed_stop_plan
from .metrics import BASELINE_FAMILY, add_gaps, headline, score
from .objective import build_cost_matrix, describe, parse_objective
from .problem import Problem

SCENARIO_DIR = os.path.join(os.path.dirname(__file__), "scenarios")


def list_scenarios() -> list:
    out = []
    for fname in sorted(os.listdir(SCENARIO_DIR)):
        if fname.endswith(".json"):
            with open(os.path.join(SCENARIO_DIR, fname)) as f:
                s = json.load(f)
            out.append({
                "id": s["id"],
                "name": s["name"],
                "description": s.get("description", ""),
                "num_stops": len(s["locations"]) - 1,
                "depot": s["locations"][0]["address"],
                "has_elevation": bool(s.get("elevation_m")),
                "total_demand": sum(loc.get("demand", 0) for loc in s["locations"]) or None,
            })
    return out


def load_scenario(scenario_id: str) -> dict:
    # Only accept ids of files that actually exist in SCENARIO_DIR
    known = {s["id"] for s in list_scenarios()}
    if scenario_id not in known:
        raise ValueError(f"Unknown scenario '{scenario_id}'")
    with open(os.path.join(SCENARIO_DIR, f"{scenario_id}.json")) as f:
        return json.load(f)


def fixed_plan_view(scenario_id: str, num_vehicles: int) -> dict:
    """The fixed stop order for a scenario and fleet size, with stop names, for display."""
    scenario = load_scenario(scenario_id)
    num_stops = len(scenario["locations"]) - 1
    if not 1 <= num_vehicles <= num_stops:
        raise ValueError(f"num_vehicles must be between 1 and {num_stops}")
    locs = scenario["locations"]
    routes = fixed_stop_plan(num_stops, num_vehicles)
    return {
        "num_vehicles": num_vehicles,
        "routes": routes,
        "trucks": [
            {
                "truck": i + 1,
                "stops": [{"index": s, "name": locs[s].get("name") or locs[s]["address"],
                           "address": locs[s]["address"]} for s in r[1:-1]],
            }
            for i, r in enumerate(routes)
        ],
    }


def run_benchmark(
    scenario_id: str,
    max_vehicles: int = 3,
    algorithm_ids: list = None,
    ortools_time_limit_s: int = None,
    objective: dict = None,
    energy_params: dict = None,
    fleet_sizes: list = None,
    preserve_stop_order: bool = False,
) -> dict:
    """
    Run every selected algorithm on a saved scenario.

    Fleet sizes: by default K = 1..max_vehicles. Pass fleet_sizes (e.g. [5]) to
    run exactly those sizes instead.

    preserve_stop_order: lock every truck to the fixed plan (see fixed_plan.py).
    Each algorithm's row is the plan itself: same trucks, same stops, same order,
    nothing added or moved, and the solvers are not run. The plan is also scored
    as a "Fixed stop order" row, so before/after comparisons report "no changes".
    """
    objective = parse_objective(objective)
    energy_params = resolve_params(energy_params)
    scenario = load_scenario(scenario_id)
    elevation_m = scenario.get("elevation_m")
    # Arc costs do not depend on K, so build the matrix once for the whole run
    energy_kwh = static_energy_matrix_kwh(scenario["distance_m"], scenario["time_s"], elevation_m, energy_params)
    cost = build_cost_matrix(objective, scenario["distance_m"], scenario["time_s"], energy_kwh)
    energy_bound_wh, energy_limit_wh = battery_limit(scenario["distance_m"], scenario["time_s"], elevation_m,
                                                      energy_params)
    num_stops = len(scenario["locations"]) - 1
    if fleet_sizes:
        sizes = sorted({int(k) for k in fleet_sizes})
        bad = [k for k in sizes if not 1 <= k <= num_stops]
        if bad:
            raise ValueError(f"Fleet size must be between 1 and {num_stops} (got {bad})")
    else:
        sizes = list(range(1, max(1, min(int(max_vehicles), num_stops)) + 1))

    if algorithm_ids:
        unknown = [a for a in algorithm_ids if a not in ALGORITHMS_BY_ID]
        if unknown:
            raise ValueError(f"Unknown algorithms: {unknown}")
        algorithms = [a for a in ALGORITHMS if a.id in algorithm_ids]
    else:
        algorithms = ALGORITHMS

    rows = []
    for k in sizes:
        plan = fixed_stop_plan(num_stops, k) if preserve_stop_order else None
        problem = Problem(
            scenario_id=scenario_id,
            locations=scenario["locations"],
            dist=scenario["distance_m"],
            time=scenario["time_s"],
            num_vehicles=k,
            ortools_time_limit_s=ortools_time_limit_s,
            cost=cost,
            elevation_m=elevation_m,
            energy_params=energy_params,
            objective=objective,
            fixed_plan=plan,
            energy_bound_wh=energy_bound_wh,
            energy_limit_wh=energy_limit_wh,
        )
        if plan:
            baseline = {
                "num_vehicles": k,
                "algorithm": "fixed_order",
                "label": "Fixed stop order (baseline)",
                "family": BASELINE_FAMILY,
            }
            try:
                baseline["metrics"] = score(problem, plan, 0)
            except Exception as e:
                baseline["error"] = str(e)
            rows.append(baseline)
        for algo in algorithms:
            if not algo.supports(problem):
                continue
            row = {
                "num_vehicles": k,
                "algorithm": algo.id,
                "label": algo.label,
                "family": algo.family,
            }
            try:
                if plan:
                    routes, elapsed_ms = [list(r) for r in plan], 0.0
                    row["locked"] = True
                else:
                    start = time.perf_counter()
                    routes = algo.solve(problem)
                    elapsed_ms = (time.perf_counter() - start) * 1000
                row["metrics"] = score(problem, routes, elapsed_ms)
            except Exception as e:  # one failing algorithm must not sink the run
                row["error"] = str(e)
            rows.append(row)

    add_gaps(rows)
    return {
        "scenario": {
            "id": scenario["id"],
            "name": scenario["name"],
            "num_stops": num_stops,
            "locations": scenario["locations"],
        },
        "fleet_sizes": sizes,
        "max_vehicles": max(sizes),
        "preserve_stop_order": bool(preserve_stop_order),
        "objective": {**objective, "label": describe(objective)},
        "energy": {**energy_params, "has_elevation": bool(elevation_m)},
        "algorithms": [a.to_dict() for a in algorithms],
        "rows": rows,
        "headline": headline(rows),
    }
