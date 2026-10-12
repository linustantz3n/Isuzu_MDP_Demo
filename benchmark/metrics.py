"""
Scoring for benchmark solutions.

Metrics are always recomputed from the scenario's matrices, never taken from
what an algorithm reports, so every algorithm is scored identically.
"""

METERS_PER_MILE = 1609.344


def _pct_gap(value, reference):
    if value is None or not reference:
        return None
    return round((value - reference) / reference * 100, 2)


def _saved_pct(value, reference):
    """Percent reduction of value relative to reference (positive = value is better)."""
    gap = _pct_gap(value, reference)
    return None if gap is None else 0.0 - gap


def check_routes(problem, routes: list) -> list:
    """Return a list of validity problems (empty list = valid)."""
    issues = []
    seen = []
    for r in routes:
        if len(r) < 3 or r[0] != 0 or r[-1] != 0:
            issues.append(f"route {r} must start and end at the depot and visit a stop")
        seen.extend(s for s in r[1:-1])
    expected = set(range(1, problem.n))
    if 0 in seen:
        issues.append("depot visited mid-route")
    if len(seen) != len(set(seen)):
        issues.append("a stop is visited more than once")
    missing = expected - set(seen)
    if missing:
        issues.append(f"stops never visited: {sorted(missing)}")
    extra = set(seen) - expected - {0}
    if extra:
        issues.append(f"unknown stop indices: {sorted(extra)}")
    return issues


def score(problem, routes: list, solve_time_ms: float) -> dict:
    """Metrics for one algorithm on one (scenario, K) problem."""
    issues = check_routes(problem, routes)
    per_route = []
    for r in routes:
        per_route.append({
            "route": r,
            "stops": len(r) - 2,
            "distance_m": sum(problem.dist[r[i]][r[i + 1]] for i in range(len(r) - 1)),
            "time_s": sum(problem.time[r[i]][r[i + 1]] for i in range(len(r) - 1)),
        })

    total_distance_m = sum(p["distance_m"] for p in per_route)
    total_time_s = sum(p["time_s"] for p in per_route)
    makespan_s = max((p["time_s"] for p in per_route), default=0)
    max_stops = max((p["stops"] for p in per_route), default=0)
    vehicles_used = len(routes)

    within_cap = max_stops <= problem.max_stops_per_vehicle
    within_fleet = vehicles_used <= problem.num_vehicles
    if not within_cap:
        issues.append(f"a truck has {max_stops} stops (cap {problem.max_stops_per_vehicle})")
    if not within_fleet:
        issues.append(f"uses {vehicles_used} trucks (only {problem.num_vehicles} available)")

    return {
        "total_distance_m": total_distance_m,
        "total_distance_mi": round(total_distance_m / METERS_PER_MILE, 2),
        "total_drive_time_s": total_time_s,
        "total_drive_time_min": round(total_time_s / 60, 1),
        "makespan_s": makespan_s,
        "makespan_min": round(makespan_s / 60, 1),
        "vehicles_used": vehicles_used,
        "max_stops_per_truck": max_stops,
        "stop_cap": problem.max_stops_per_vehicle,
        "solve_time_ms": round(solve_time_ms, 1),
        "valid": not check_routes(problem, routes),
        "feasible": not issues,
        "issues": issues,
        "routes": per_route,
    }


def add_gaps(rows: list) -> None:
    """
    Add comparison metrics in place. Rows are grouped by num_vehicles; within
    a group every gap is relative to a reference in that same group:
      gap_to_best_*     best feasible result
      gap_vs_ortools_*  production OR-Tools
      gap_to_optimal_*  Held-Karp (K = 1 only)
    """
    for k in sorted({r["num_vehicles"] for r in rows}):
        group = [r for r in rows if r["num_vehicles"] == k and r.get("metrics")]
        feasible = [r for r in group if r["metrics"]["feasible"]]
        by_id = {r["algorithm"]: r for r in group}

        best_d = min((r["metrics"]["total_distance_m"] for r in feasible), default=None)
        best_t = min((r["metrics"]["total_drive_time_s"] for r in feasible), default=None)
        ort = by_id.get("ortools")
        hk = by_id.get("held_karp")

        for r in group:
            m = r["metrics"]
            d, t = m["total_distance_m"], m["total_drive_time_s"]
            m["gap_to_best_distance_pct"] = _pct_gap(d, best_d)
            m["gap_to_best_time_pct"] = _pct_gap(t, best_t)
            m["gap_vs_ortools_distance_pct"] = _pct_gap(d, ort and ort["metrics"]["total_distance_m"])
            m["gap_vs_ortools_time_pct"] = _pct_gap(t, ort and ort["metrics"]["total_drive_time_s"])
            m["gap_to_optimal_distance_pct"] = _pct_gap(d, hk and hk["metrics"]["total_distance_m"])
            m["is_best_distance"] = m["feasible"] and d == best_d


def headline(rows: list) -> list:
    """
    Per K: how much less distance / drive time production OR-Tools needs
    compared with the naive baseline (NN for one truck, round-robin + NN for
    a fleet) and with the best non-OR-Tools heuristic.
    """
    out = []
    for k in sorted({r["num_vehicles"] for r in rows}):
        group = {r["algorithm"]: r for r in rows if r["num_vehicles"] == k and r.get("metrics")}
        ort = group.get("ortools")
        if not ort:
            continue
        naive_id = "nn" if k == 1 else "rr_nn"
        naive = group.get(naive_id)
        heuristics = [
            r for r in group.values()
            if r["family"] not in ("ortools", "exact") and r["metrics"]["feasible"]
        ]
        best_h = min(heuristics, key=lambda r: r["metrics"]["total_distance_m"], default=None)

        def savings(ref):
            if not ref:
                return None
            rm, om = ref["metrics"], ort["metrics"]
            return {
                "algorithm": ref["algorithm"],
                "label": ref["label"],
                "distance_saved_pct": _saved_pct(om["total_distance_m"], rm["total_distance_m"]),
                "time_saved_pct": _saved_pct(om["total_drive_time_s"], rm["total_drive_time_s"]),
                "distance_saved_mi": round(rm["total_distance_mi"] - om["total_distance_mi"], 2),
                "time_saved_min": round(rm["total_drive_time_min"] - om["total_drive_time_min"], 1),
            }

        out.append({
            "num_vehicles": k,
            "vs_naive": savings(naive),
            "vs_best_heuristic": savings(best_h),
        })
    return out
