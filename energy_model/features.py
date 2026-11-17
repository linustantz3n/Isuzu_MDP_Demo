"""The ten physical features used by the frozen City NN."""
import math
import numpy as np
from .traces import TraceFeatures
REFERENCE_TEMPERATURE_F = 68.0

FEATURE_NAMES = (
    "distance_miles",
    "average_speed_mph_distance_miles",
    "temperature_delta_f_distance_miles",
    "weight_klb_distance_miles",
    "aero_speed_squared_mph2_distance_miles",
    "aero_speed_squared_temperature_delta_distance",
    "aero_speed_squared_altitude_kft_distance",
    "weight_klb_elevation_gain_kft",
    "weight_klb_elevation_loss_kft",
    "positive_acceleration_work_kwh",
)




def _finite(name: str, value: float, *, minimum: float | None = None) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    if minimum is not None and result < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return result


def feature_values(
    trace: TraceFeatures,
    temperature_f: float,
    gross_weight_lb: float,
    baseline_altitude_ft: float,
) -> dict[str, float]:
    """Build the ten City NN route features.

    ``aerodynamic_speed_squared_mph2`` is calculated from the trace as
    ``sum(v^3 dt) / sum(v dt)``.  It therefore replaces every historical
    occurrence of average-speed squared rather than being added beside it.
    """
    distance = _finite("distance_miles", trace.distance_miles, minimum=0.0)
    average_speed = _finite("average_speed_mph", trace.average_speed_mph, minimum=0.0)
    aero_speed_squared = _finite(
        "aerodynamic_speed_squared_mph2",
        trace.aerodynamic_speed_squared_mph2,
        minimum=0.0,
    )
    temperature_delta = _finite("temperature_f", temperature_f) - REFERENCE_TEMPERATURE_F
    weight_klb = _finite("gross_weight_lb", gross_weight_lb, minimum=1.0) / 1000.0
    altitude_kft = _finite("baseline_altitude_ft", baseline_altitude_ft) / 1000.0
    gain_kft = _finite("elevation_gain_ft", trace.elevation_gain_ft, minimum=0.0) / 1000.0
    loss_kft = _finite("elevation_loss_ft", trace.elevation_loss_ft, minimum=0.0) / 1000.0

    return {
        "distance_miles": distance,
        "average_speed_mph_distance_miles": average_speed * distance,
        "temperature_delta_f_distance_miles": temperature_delta * distance,
        "weight_klb_distance_miles": weight_klb * distance,
        "aero_speed_squared_mph2_distance_miles": aero_speed_squared * distance,
        "aero_speed_squared_temperature_delta_distance": (
            aero_speed_squared * temperature_delta * distance
        ),
        "aero_speed_squared_altitude_kft_distance": (
            aero_speed_squared * altitude_kft * distance
        ),
        "weight_klb_elevation_gain_kft": weight_klb * gain_kft,
        "weight_klb_elevation_loss_kft": weight_klb * loss_kft,
        "positive_acceleration_work_kwh": _finite(
            "acceleration_work_kwh", trace.acceleration_work_kwh, minimum=0.0
        ),
    }


def feature_vector(
    trace: TraceFeatures,
    temperature_f: float,
    gross_weight_lb: float,
    baseline_altitude_ft: float,
) -> np.ndarray:
    values = feature_values(trace, temperature_f, gross_weight_lb, baseline_altitude_ft)
    return np.asarray([values[name] for name in FEATURE_NAMES], dtype=float)
