"""
Benchmark runner: every algorithm x every fleet size on one saved scenario.
"""

import json
import os
import time

from .algorithms import ALGORITHMS, ALGORITHMS_BY_ID
from .metrics import add_gaps, headline, score
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


def run_benchmark(
    scenario_id: str,
    max_vehicles: int = 3,
    algorithm_ids: list = None,
    ortools_time_limit_s: int = None,
) -> dict:
    scenario = load_scenario(scenario_id)
    num_stops = len(scenario["locations"]) - 1
    max_vehicles = max(1, min(int(max_vehicles), num_stops))

    if algorithm_ids:
        unknown = [a for a in algorithm_ids if a not in ALGORITHMS_BY_ID]
        if unknown:
            raise ValueError(f"Unknown algorithms: {unknown}")
        algorithms = [a for a in ALGORITHMS if a.id in algorithm_ids]
    else:
        algorithms = ALGORITHMS

    rows = []
    for k in range(1, max_vehicles + 1):
        problem = Problem(
            scenario_id=scenario_id,
            locations=scenario["locations"],
            dist=scenario["distance_m"],
            time=scenario["time_s"],
            num_vehicles=k,
            ortools_time_limit_s=ortools_time_limit_s,
        )
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
        "max_vehicles": max_vehicles,
        "algorithms": [a.to_dict() for a in algorithms],
        "rows": rows,
        "headline": headline(rows),
    }
