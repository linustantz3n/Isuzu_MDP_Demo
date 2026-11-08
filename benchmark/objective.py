"""
What the algorithms minimize.

Every algorithm minimizes problem.cost, one directed arc-cost matrix. The
objective decides how that matrix is built from the scenario's distance, time
and energy matrices. All three metrics are scored for every run regardless of
the objective (see metrics.score); the objective only steers the search.

  distance  raw meters
  time      raw seconds
  energy    Wh (kWh * 1000), so OR-Tools' integer costs keep resolution
  blend     weighted sum of the three, each divided by its own scenario-wide
            mean arc first so miles, minutes and kWh are comparable (a 10%
            change in any one moves the cost the same amount), then scaled
            by 1000 for integer resolution
"""

MODES = ("distance", "time", "energy", "blend")
METRICS = ("distance", "time", "energy")
MODE_LABELS = {"distance": "total distance", "time": "total drive time", "energy": "total energy",
               "blend": "blended cost"}


def parse_objective(spec: dict = None) -> dict:
    """Validate a request's objective and normalize blend weights to sum to 1."""
    spec = spec or {}
    mode = spec.get("mode", "distance")
    if mode not in MODES:
        raise ValueError(f"Unknown objective mode '{mode}' (expected one of {', '.join(MODES)})")
    if mode != "blend":
        return {"mode": mode, "weights": {m: 1.0 if m == mode else 0.0 for m in METRICS}}

    raw = spec.get("weights") or {}
    weights = {}
    for m in METRICS:
        try:
            weights[m] = float(raw.get(m, 0) or 0)
        except (TypeError, ValueError):
            raise ValueError(f"Blend weight for {m} must be a number")
        if weights[m] < 0:
            raise ValueError("Blend weights cannot be negative")
    total = sum(weights.values())
    if total <= 0:
        raise ValueError("Blend needs at least one weight above zero")
    return {"mode": "blend", "weights": {m: w / total for m, w in weights.items()}}


def describe(objective: dict) -> str:
    if objective["mode"] != "blend":
        return MODE_LABELS[objective["mode"]]
    parts = [f"{round(w * 100)}% {m}" for m, w in objective["weights"].items() if w > 0]
    return "blend of " + ", ".join(parts)


def _mean_arc(matrix: list) -> float:
    vals = [v for i, row in enumerate(matrix) for j, v in enumerate(row) if i != j and v > 0]
    return sum(vals) / len(vals) if vals else 1.0


def build_cost_matrix(objective: dict, dist: list, time: list, energy_kwh: list) -> list:
    mode = objective["mode"]
    if mode == "distance":
        return dist
    if mode == "time":
        return time
    if mode == "energy":
        return [[v * 1000 for v in row] for row in energy_kwh]

    mats = {"distance": dist, "time": time, "energy": energy_kwh}
    scales = {m: objective["weights"][m] / _mean_arc(mats[m]) * 1000 for m in METRICS if objective["weights"][m] > 0}
    n = len(dist)
    return [[sum(mats[m][i][j] * s for m, s in scales.items()) for j in range(n)] for i in range(n)]
