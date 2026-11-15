"""
Isuzu N-Series EV energy model (Model E regression).

Standalone so both the Flask backend and the benchmark package can use it
without importing each other.
"""

import math

# Driving style is assumed, not fitted: it scales a leg's energy for gentler or
# harder acceleration than the fitted average (see the backend for how a style
# also changes cruising speed).
STYLE_ENERGY_FACTOR = {'eco': 0.95, 'normal': 1.0, 'aggressive': 1.10}

# =============================================================================
# Energy Consumption Model — Model E regression
# =============================================================================

def calculate_energy_consumption(
    distance_km: float,
    avg_speed_kmh: float,
    temperature_c: float,
    vehicle_weight_tonnes: float,
    altitude_diff_m: float,
    altitude_m: float,
    has_climate_control: bool = True,
    driving_style: str = 'normal'
) -> dict:
    """
    Calculate energy consumption using Model E polynomial regression.

    Model E extends Model D with two air-density interaction terms that capture
    how aerodynamic drag varies with temperature and absolute altitude:
      F_aero ∝ ρ(T, alt) · v²   where ρ decreases with higher T and higher alt.

    Formula (returns kWh per leg):
      grade = alt_diff_m / dist_km   (m/km)
      e_per_km = b0 + b1*v + b2*T + b3*W + b4*Δh + b5*v²
               + b6*(W·grade) + b7*(v·grade)
               + b8*(v²·T)    + b9*(v²·alt_m)
      total_kwh = e_per_km * dist_km

    Target range: 0.85–1.3 kWh/mile (Isuzu N-Series EV)
    R²=0.9997, MAPE=3.46%, test RMSE=0.38 kWh

    Args:
        distance_km:           Leg distance in km
        avg_speed_kmh:         Average speed in km/h
        temperature_c:         Ambient temperature in °C
        vehicle_weight_tonnes: Gross vehicle weight in tonnes
        altitude_diff_m:       Elevation change over leg in m (+ = uphill)
        altitude_m:            Absolute elevation at leg start in m (above sea level)
        has_climate_control:   HVAC active (additive overhead, not in regression)
        driving_style:         'eco' / 'normal' / 'aggressive': scales the energy for
                               how hard the driver accelerates (assumed factor,
                               STYLE_ENERGY_FACTOR; not in the regression)

    Returns:
        Dict with energy_kwh and breakdown
    """
    # Model E fitted coefficients (Isuzu N-Series EV, R²=0.9997, MAPE=3.46%)
    # Units: dist=km, speed=km/h, temp=°C, weight=tonnes, alt=metres, grade=m/km
    b0 =  5.7142142481e-01   # intercept
    b1 = -1.0081493644e-02   # speed (v)
    b2 =  1.4087882356e-04   # temperature (T)
    b3 =  1.8176062830e-02   # weight (W)
    b4 =  1.7585256876e-05   # altitude change (Δh)
    b5 =  1.2923132040e-04   # speed squared (v²)
    b6 =  2.8886974460e-03   # weight × road grade (W·Δh/d)
    b7 = -9.9890433238e-06   # speed × road grade (v·Δh/d)
    b8 = -2.6120414024e-07   # speed²×temp  (ρ–temperature coupling)
    b9 = -8.6411314209e-09   # speed²×alt_m (ρ–altitude coupling)

    # Avoid division by zero on zero-length legs
    if distance_km <= 0:
        return {"energy_kwh": 0.0, "energy_per_km": 0.0, "energy_per_mile": 0.0,
                "miles_per_kwh": 0.0, "kwh_per_mile": 0.0, "breakdown": {}}

    dist  = distance_km      # km
    v     = avg_speed_kmh    # km/h
    T     = temperature_c
    W     = vehicle_weight_tonnes
    dh    = altitude_diff_m
    h     = altitude_m

    # grade = alt_diff_m / dist_km  →  m/km (as model expects)
    grade = dh / dist

    energy_per_km = (
        b0
        + b1 * v
        + b2 * T
        + b3 * W
        + b4 * dh
        + b5 * v**2
        + b6 * (W * grade)
        + b7 * (v * grade)
        + b8 * (v**2 * T)
        + b9 * (v**2 * h)
    )

    # HVAC additive overhead (0.092 kWh/km)
    hvac_per_km = 0.092 if has_climate_control else 0.0
    energy_per_km += hvac_per_km

    # Driving style: harder or gentler acceleration than the fitted average.
    # Only on a leg that uses energy; regen on a steep downhill is left alone.
    style_per_km = (energy_per_km * (STYLE_ENERGY_FACTOR.get(driving_style, 1.0) - 1)
                    if energy_per_km > 0 else 0.0)
    energy_per_km += style_per_km

    total_energy_kwh = energy_per_km * dist

    dist_miles = distance_km * 0.621371
    energy_per_mile = total_energy_kwh / dist_miles if dist_miles > 0 else 0
    miles_per_kwh = dist_miles / total_energy_kwh if total_energy_kwh > 0 else 0

    return {
        "energy_kwh": total_energy_kwh,
        "energy_per_km": energy_per_km,
        "energy_per_mile": energy_per_mile,
        "miles_per_kwh": miles_per_kwh,
        "kwh_per_mile": 1 / miles_per_kwh if miles_per_kwh > 0 else 0,
        "breakdown": {
            "intercept_kwh":       b0             * dist,
            "speed_kwh":           b1 * v         * dist,
            "temperature_kwh":     b2 * T         * dist,
            "weight_kwh":          b3 * W         * dist,
            "altitude_diff_kwh":   b4 * dh        * dist,
            "speed_sq_kwh":        b5 * v**2      * dist,
            "weight_grade_kwh":    b6 * W * grade * dist,
            "speed_grade_kwh":     b7 * v * grade * dist,
            "aero_temp_kwh":       b8 * v**2 * T  * dist,
            "aero_alt_kwh":        b9 * v**2 * h  * dist,
            "climate_control_kwh": hvac_per_km    * dist,
            "driving_style_kwh":   style_per_km   * dist,
        }
    }


def estimate_average_speed(distance_m: float, time_s: float) -> float:
    """
    Estimate average speed from distance and time.

    Args:
        distance_m: Distance in meters
        time_s: Time in seconds

    Returns:
        Average speed in km/h
    """
    if time_s == 0:
        return 0.0

    distance_km = distance_m / 1000
    time_h = time_s / 3600
    return distance_km / time_h


# =============================================================================
# Battery limit
# =============================================================================

LBS_PER_TONNE = 2204.62
DEFAULT_BATTERY_RESERVE_PCT = 20.0


def battery_limit_wh(battery_kwh: float, reserve_pct: float) -> int:
    """Energy (Wh) a truck may use before it reaches its reserve."""
    usable = max(0.0, 1 - reserve_pct / 100) * battery_kwh
    return int(math.floor(usable * 1000))


def finite(x) -> bool:
    return x is not None and isinstance(x, (int, float)) and math.isfinite(x)


def energy_matrix_kwh(
    dist: list,
    time: list,
    elevation_m: list,
    weight_lbs: float,
    temperature_c: float,
    has_climate_control: bool = True,
    driving_style: str = 'normal',
) -> list:
    """Model E energy (kWh) for every ordered pair of locations at one truck
    weight. An unroutable pair (no finite distance or time) is inf."""
    n = len(dist)
    elev = elevation_m or [0.0] * n
    out = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            d, t = dist[i][j], time[i][j]
            if not (finite(d) and finite(t)):
                out[i][j] = float('inf')
                continue
            out[i][j] = calculate_energy_consumption(
                distance_km=d / 1000,
                avg_speed_kmh=estimate_average_speed(d, t),
                temperature_c=temperature_c,
                vehicle_weight_tonnes=weight_lbs / LBS_PER_TONNE,
                altitude_diff_m=elev[j] - elev[i],
                altitude_m=elev[i],
                has_climate_control=has_climate_control,
                driving_style=driving_style,
            )["energy_kwh"]
    return out


UNROUTABLE_WH = 10 ** 9   # energy of an arc that cannot be driven: more than any battery


def energy_upper_bound_matrix_wh(
    dist: list,
    time: list,
    elevation_m: list,
    loaded_weight_lbs: float,
    base_weight_lbs: float,
    temperature_c: float,
    has_climate_control: bool = True,
    driving_style: str = 'normal',
    empty_return: bool = True,
) -> list:
    """
    Directed arc energy (whole Wh) that never undershoots the weight-aware
    energy the solvers report for that arc. Used for every battery limit.

    A truck weighs somewhere between base and loaded on any arc. Energy is
    linear in weight, so the larger of the two endpoints bounds every weight in
    between (the heavier end is not always the costlier one: on steep
    downhills more weight means more regen). With empty_return, arcs into the
    depot (node 0) are priced at base weight only, for plans where a truck is
    always empty by the time it heads home. Negative arcs are clamped to 0 and
    values rounded up, so a route that fits under the limit with these numbers
    fits in reality. Unroutable arcs get UNROUTABLE_WH.
    """
    n = len(dist)
    loaded = energy_matrix_kwh(dist, time, elevation_m, loaded_weight_lbs, temperature_c,
                               has_climate_control, driving_style)
    base = energy_matrix_kwh(dist, time, elevation_m, base_weight_lbs, temperature_c,
                             has_climate_control, driving_style)
    out = [[0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            kwh = base[i][j] if (empty_return and j == 0) else max(loaded[i][j], base[i][j])
            out[i][j] = int(math.ceil(max(0.0, kwh) * 1000)) if finite(kwh) else UNROUTABLE_WH
    return out
