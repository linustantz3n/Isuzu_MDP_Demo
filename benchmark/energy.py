"""
Energy for benchmark routes, using the production Model E regression.

Two views of the same model:

* route_energy_kwh: the faithful number used for scoring. It follows the
  production fleet solver: each truck leaves the depot at its loaded weight,
  sheds an equal share of cargo at every stop, and drives home at its empty
  weight.
* static_energy_matrix_kwh: one kWh value per directed arc at a single fixed
  weight, so any matrix-based algorithm can minimize energy. This ignores the
  fact that a truck gets lighter as it delivers, so it is an approximation of
  what route_energy_kwh measures.

Speed comes from each arc's distance / time, and the uphill/downhill term from
the scenario's elevations (flat terrain if a scenario has none).
"""

from energy import (
    DEFAULT_BATTERY_RESERVE_PCT,
    battery_limit_wh,
    calculate_energy_consumption,
    energy_upper_bound_matrix_wh,
    estimate_average_speed,
)

LBS_PER_TONNE = 2204.62

# Same defaults as the production /api/optimize fleet path
DEFAULT_ENERGY_PARAMS = {
    "temperature_c": 20.0,
    "loaded_weight_lbs": 13000.0,
    "base_weight_lbs": 7000.0,
    "has_climate_control": True,
    # Battery limit: with battery_kwh > 0, every truck must keep
    # battery_reserve_pct of it at every point of its route. Off by default:
    # with the 80 kWh production default each saved scenario has a stop that
    # is out of range, so the limit is opted into per run.
    "battery_kwh": 0.0,
    "battery_reserve_pct": DEFAULT_BATTERY_RESERVE_PCT,
}


def resolve_params(params: dict = None) -> dict:
    merged = dict(DEFAULT_ENERGY_PARAMS)
    merged.update({k: v for k, v in (params or {}).items() if k in DEFAULT_ENERGY_PARAMS and v is not None})
    return merged


def static_weight_lbs(params: dict) -> float:
    """Average truck weight over a delivery run: halfway between loaded and empty."""
    return (params["loaded_weight_lbs"] + params["base_weight_lbs"]) / 2


def _elev(elevation_m, i):
    return elevation_m[i] if elevation_m else 0.0


def leg_energy_kwh(dist, time, elevation_m, i, j, weight_lbs, params) -> float:
    d = dist[i][j]
    if i == j or d <= 0:
        return 0.0
    return calculate_energy_consumption(
        distance_km=d / 1000,
        avg_speed_kmh=estimate_average_speed(d, time[i][j]),
        temperature_c=params["temperature_c"],
        vehicle_weight_tonnes=weight_lbs / LBS_PER_TONNE,
        altitude_diff_m=_elev(elevation_m, j) - _elev(elevation_m, i),
        altitude_m=_elev(elevation_m, i),
        has_climate_control=params["has_climate_control"],
    )["energy_kwh"]


def static_energy_matrix_kwh(dist, time, elevation_m, params: dict) -> list:
    """Directed arc energy in kWh at the fixed average weight."""
    w = static_weight_lbs(params)
    n = len(dist)
    return [[leg_energy_kwh(dist, time, elevation_m, i, j, w, params) for j in range(n)] for i in range(n)]


def battery_limit(dist, time, elevation_m, params: dict):
    """(per-arc upper-bound Wh matrix, usable Wh per truck), or (None, None) if the limit is off."""
    if not params["battery_kwh"]:
        return None, None
    bound = energy_upper_bound_matrix_wh(
        dist, time, elevation_m,
        loaded_weight_lbs=params["loaded_weight_lbs"],
        base_weight_lbs=params["base_weight_lbs"],
        temperature_c=params["temperature_c"],
        has_climate_control=params["has_climate_control"],
    )
    return bound, battery_limit_wh(params["battery_kwh"], params["battery_reserve_pct"])


def route_energy_kwh(route: list, dist, time, elevation_m, params: dict) -> float:
    """Weight-aware energy for one closed route [0, s1, ..., sn, 0]."""
    n_stops = len(route) - 2
    cargo = max(0.0, params["loaded_weight_lbs"] - params["base_weight_lbs"])
    per_stop = cargo / n_stops if n_stops > 0 else 0.0
    total = 0.0
    for p in range(len(route) - 1):
        is_return = p == len(route) - 2
        weight = params["base_weight_lbs"] if is_return else params["loaded_weight_lbs"] - p * per_stop
        total += leg_energy_kwh(dist, time, elevation_m, route[p], route[p + 1], weight, params)
    return total
