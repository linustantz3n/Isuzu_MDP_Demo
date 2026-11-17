"""Shared City NN energy accounting for planning, charging and simulation."""
import math
from contextvars import ContextVar
from functools import wraps
from energy_model import inference

DRIVING_STYLES = ('eco', 'normal', 'aggressive')
STYLE_CRUISE_FACTOR = {'eco': .90, 'normal': 1., 'aggressive': 1.10}
_session = ContextVar('energy_model', default=None)


def energy_metadata():
    return dict((_session.get() or inference.model())[1])


def energy_session(fn):
    """Pin the weights for a whole planning operation, including nested calls."""
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if _session.get() is not None:
            return fn(*args, **kwargs)
        token = _session.set(inference.model())
        try:
            return fn(*args, **kwargs)
        finally:
            _session.reset(token)
    return wrapped


def validate_energy_params(params):
    if (params or {}).get('model', 'city_nn') != 'city_nn':
        raise ValueError('Only City NN is supported; remove the legacy energy model selection')


def validate_style(style):
    if style not in DRIVING_STYLES:
        raise ValueError('Driving style must be eco, normal or aggressive')
    return style


def styled_matrices(matrices, style):
    factor = STYLE_CRUISE_FACTOR[validate_style(style)]
    if factor == 1:
        return matrices
    def adjusted(d, t):
        if finite(d) and finite(t) and t > 0 and d / t * 3.6 > 50:
            return int(round(t / factor))
        return t
    return {**matrices, 'time': [[adjusted(d, t) for d, t in zip(dr, tr)]
                                for dr, tr in zip(matrices['distance'], matrices['time'])]}


def predict_leg(distance_m, duration_s, start_m, end_m, weight_lbs, temperature_c,
                style='normal', hvac=True, upper_weight=None):
    validate_style(style)
    values = [distance_m, duration_s, start_m, end_m, weight_lbs, temperature_c]
    if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in values):
        raise ValueError('Energy inputs must be finite numbers')
    if distance_m < 0 or duration_s < 0 or (distance_m > 0 and duration_s <= 0) or weight_lbs <= 0:
        raise ValueError('Energy prediction requires nonnegative distance/time and positive driving duration and weight')
    if distance_m > 1000000 or duration_s > 86400 or temperature_c <= -273.15:
        raise ValueError('Energy inputs exceed the supported leg domain')
    return inference.predict(distance_m, duration_s, start_m, end_m, weight_lbs, temperature_c,
                             style, hvac, upper_weight=upper_weight, loaded_model=_session.get())


def calculate_energy_consumption(distance_km, avg_speed_kmh, temperature_c, vehicle_weight_tonnes,
                                 altitude_diff_m, altitude_m, has_climate_control=True, driving_style='normal'):
    duration = distance_km / avg_speed_kmh * 3600 if avg_speed_kmh > 0 else 0.
    total, parts = predict_leg(distance_km * 1000, duration, altitude_m, altitude_m + altitude_diff_m,
                               vehicle_weight_tonnes * LBS_PER_TONNE, temperature_c, driving_style,
                               has_climate_control)
    miles = distance_km / 1.609344
    return {'energy_kwh': total, 'energy_per_km': total / distance_km if distance_km else 0.,
            'energy_per_mile': total / miles if miles else 0.,
            'kwh_per_mile': total / miles if miles else 0.,
            'miles_per_kwh': miles / total if total else 0., 'breakdown': parts}


def estimate_average_speed(distance_m, time_s):
    return distance_m / time_s * 3.6 if time_s else 0.


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
    """City NN energy (kWh) for every ordered pair of locations at one truck
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


def energy_upper_bound_matrix_wh(dist, time, elevation_m, loaded_weight_lbs, base_weight_lbs,
                                  temperature_c, has_climate_control=True, driving_style='normal',
                                  empty_return=True):
    """Exact maximum of the frozen NN over each arc's feasible weight interval.

    This bounds model predictions, not measured battery use. Returning cargo
    requires empty_return=False. Round up with a small floating-point margin.
    """
    elev = elevation_m or [0.] * len(dist)
    out = [[0] * len(dist) for _ in dist]
    for i in range(len(dist)):
        for j in range(len(dist)):
            if i == j:
                continue
            d, t = dist[i][j], time[i][j]
            if not finite(d) or not finite(t):
                out[i][j] = UNROUTABLE_WH
                continue
            upper = base_weight_lbs if empty_return and j == 0 else max(base_weight_lbs, loaded_weight_lbs)
            energy, _ = predict_leg(d, t, elev[i], elev[j], base_weight_lbs, temperature_c,
                                    driving_style, has_climate_control, upper_weight=upper)
            out[i][j] = math.ceil(energy * 1000 + 1e-7) if energy else 0
    return out
