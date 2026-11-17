"""Synthetic speed traces and physical features for City NN inference.

Inputs use miles, mph, degF, pounds and feet; integration uses SI units.
Driver profiles and auxiliary loads are simulation assumptions.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import math
from typing import Mapping, Sequence

import numpy as np


MILE_TO_M = 1609.344
MPH_TO_MPS = 0.44704
LB_TO_KG = 0.45359237
FT_TO_M = 0.3048
JOULES_PER_KWH = 3_600_000.0
G = 9.81
R_AIR = 287.05
P_SEA = 101325.0


HVAC_MODES = {"off": 0.0, "eco": 2.0, "normal": 4.0, "max": 6.0}




@dataclass(frozen=True)
class VehicleConfig:
    """Vehicle and auxiliary assumptions for trace feature extraction."""

    drag_coefficient: float = 0.7
    frontal_area_m2: float = 7.0
    rolling_resistance: float = 0.006
    drivetrain_efficiency: float = 0.92
    base_aux_kw: float = 5.0
    epto_kw: float = 5.0
    regen_efficiency: float = 0.0


@dataclass(frozen=True)
class DriverProfileDistribution:
    speed_ratio_mean: float
    speed_ratio_sd: float
    acceleration_mphps_mean: float
    acceleration_mphps_sd: float
    deceleration_mphps_mean: float
    deceleration_mphps_sd: float
    coast_lead_seconds_mean: float
    coast_lead_seconds_sd: float
    speed_noise_mph: float


@dataclass(frozen=True)
class DriverBehaviorParams:
    """One concrete draw from a driver-profile distribution."""

    speed_ratio: float
    acceleration_mphps: float
    deceleration_mphps: float
    coast_lead_seconds: float
    speed_noise_mph: float


DRIVER_PROFILES: dict[str, DriverProfileDistribution] = {
    "eco": DriverProfileDistribution(0.90, 0.03, 1.23, 0.22, 1.34, 0.22, 8.0, 1.5, 0.3),
    "normal": DriverProfileDistribution(0.97, 0.02, 2.01, 0.34, 2.13, 0.34, 5.0, 1.0, 0.6),
    "aggressive": DriverProfileDistribution(1.00, 0.02, 2.91, 0.45, 3.24, 0.56, 2.0, 0.5, 1.2),
}


@dataclass(frozen=True)
class TraceFeatures:
    distance_miles: float
    duration_seconds: float
    average_speed_mph: float
    aerodynamic_speed_squared_mph2: float
    rms_speed_mph: float
    speed_std_mph: float
    idle_seconds: float
    idle_fraction: float
    stop_count: int
    stops_per_mile: float
    pke_mps2: float
    nke_mps2: float
    harsh_acceleration_count: int
    harsh_braking_count: int
    elevation_gain_ft: float
    elevation_loss_ft: float
    net_elevation_ft: float
    positive_tractive_work_kwh: float
    rolling_work_kwh: float
    aerodynamic_work_kwh: float
    uphill_work_kwh: float
    signed_downhill_relief_kwh: float
    acceleration_work_kwh: float



@dataclass(frozen=True)
class TracePhysicsResult:
    features: TraceFeatures
    propulsion_kwh: float
    base_aux_kwh: float
    epto_kwh: float
    hvac_kwh: float
    total_kwh: float
    warnings: tuple[str, ...]


def fahrenheit_to_celsius(value: float) -> float:
    return (float(value) - 32.0) * 5.0 / 9.0


def mph_to_mps(value):
    return np.asarray(value, dtype=float) * MPH_TO_MPS


def miles_to_m(value: float) -> float:
    return float(value) * MILE_TO_M


def lb_to_kg(value: float) -> float:
    return float(value) * LB_TO_KG


def ft_to_m(value):
    return np.asarray(value, dtype=float) * FT_TO_M


def air_density_kg_m3(temperature_f: float, altitude_ft) -> np.ndarray:
    temp_k = fahrenheit_to_celsius(temperature_f) + 273.15
    if temp_k <= 0:
        raise ValueError("temperature_f is below absolute zero")
    altitude_m = ft_to_m(altitude_ft)
    base = 1.0 - 2.25577e-5 * altitude_m
    if np.any(base <= 0):
        raise ValueError("altitude_ft is outside the supported atmosphere model")
    pressure = P_SEA * np.power(base, 5.25588)
    return pressure / (R_AIR * temp_k)


def _finite_scalar(name: str, value, *, minimum: float | None = None) -> float:
    if isinstance(value, (bool, str, bytes)):
        raise ValueError(f"{name} must be a finite number")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    if minimum is not None and result < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return result


def _finite_array(name: str, values: Sequence[float], *, minimum: float | None = None) -> np.ndarray:
    try:
        result = np.asarray(values, dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must contain numeric values") from exc
    if result.ndim != 1 or len(result) < 2:
        raise ValueError(f"{name} must be a one-dimensional sequence with at least 2 values")
    if not np.all(np.isfinite(result)):
        raise ValueError(f"{name} must contain only finite values")
    if minimum is not None and np.any(result < minimum):
        raise ValueError(f"{name} values must be >= {minimum}")
    return result


def resolve_hvac_kw(hvac_mode: str | float, modes: Mapping[str, float] | None = None) -> float:
    if isinstance(hvac_mode, str):
        lookup = HVAC_MODES if modes is None else modes
        key = hvac_mode.lower()
        if key not in lookup:
            raise ValueError(f"unknown HVAC mode {hvac_mode!r}; expected one of {sorted(lookup)}")
        return _finite_scalar("hvac_kw", lookup[key], minimum=0.0)
    return _finite_scalar("hvac_kw", hvac_mode, minimum=0.0)


def _truncated_normal(
    rng: np.random.Generator, mean: float, sd: float, low: float, high: float
) -> float:
    if sd <= 0:
        return float(np.clip(mean, low, high))
    for _ in range(64):
        value = float(rng.normal(mean, sd))
        if low <= value <= high:
            return value
    return float(np.clip(mean, low, high))


def sample_driver_behavior(
    profile: str,
    rng: np.random.Generator,
    overrides: DriverBehaviorParams | Mapping[str, float] | None = None,
) -> DriverBehaviorParams:
    """Draw one behavior parameter set, optionally replacing selected values."""
    key = profile.lower()
    if key not in DRIVER_PROFILES:
        raise ValueError(f"unknown driver profile {profile!r}; expected {sorted(DRIVER_PROFILES)}")
    p = DRIVER_PROFILES[key]
    result = DriverBehaviorParams(
        speed_ratio=_truncated_normal(rng, p.speed_ratio_mean, p.speed_ratio_sd, 0.75, 1.0),
        acceleration_mphps=_truncated_normal(
            rng, p.acceleration_mphps_mean, p.acceleration_mphps_sd, 0.35, 4.5
        ),
        deceleration_mphps=_truncated_normal(
            rng, p.deceleration_mphps_mean, p.deceleration_mphps_sd, 0.35, 5.5
        ),
        coast_lead_seconds=_truncated_normal(
            rng, p.coast_lead_seconds_mean, p.coast_lead_seconds_sd, 0.0, 15.0
        ),
        speed_noise_mph=max(0.0, float(p.speed_noise_mph)),
    )
    if overrides is None:
        return result
    if isinstance(overrides, DriverBehaviorParams):
        result = overrides
    else:
        allowed = set(asdict(result))
        unknown = set(overrides) - allowed
        if unknown:
            raise ValueError(f"unknown driver behavior overrides: {sorted(unknown)}")
        result = replace(result, **{name: float(value) for name, value in overrides.items()})
    bounds = {
        "speed_ratio": (0.75, 1.0),
        "acceleration_mphps": (0.35, 4.5),
        "deceleration_mphps": (0.35, 5.5),
        "coast_lead_seconds": (0.0, 15.0),
        "speed_noise_mph": (0.0, 10.0),
    }
    for name, (low, high) in bounds.items():
        value = _finite_scalar(name, getattr(result, name))
        if not low <= value <= high:
            raise ValueError(f"{name} must be in [{low}, {high}]")
    return result


def driver_profile_manifest() -> dict[str, dict[str, float]]:
    return {name: asdict(profile) for name, profile in DRIVER_PROFILES.items()}


def _count_stops(speeds_mph: np.ndarray, dt: np.ndarray) -> int:
    interval_speeds = 0.5 * (speeds_mph[:-1] + speeds_mph[1:])
    stopped = interval_speeds < 2.0
    count = 0
    start = None
    for i, is_stopped in enumerate(stopped):
        if is_stopped and start is None:
            start = i
        if start is not None and (not is_stopped or i == len(stopped) - 1):
            end = i if not is_stopped else i + 1
            touches_trace_boundary = start == 0 or end == len(stopped)
            if not touches_trace_boundary and float(np.sum(dt[start:end])) >= 3.0:
                count += 1
            start = None
    return count


def simulate_trace_physics(
    timestamps_seconds: Sequence[float],
    speeds_mph: Sequence[float],
    elevations_ft: Sequence[float],
    temperature_f: float,
    gross_weight_lb: float,
    *,
    hvac_mode: str | float = "off",
    base_aux_kw: float = 5.0,
    epto_kw: float = 5.0,
    vehicle: VehicleConfig | None = None,
    enforce_trace_resolution: bool = True,
) -> TracePhysicsResult:
    """Integrate battery energy and extract physical features from a speed trace."""
    cfg = VehicleConfig() if vehicle is None else vehicle
    if cfg.regen_efficiency != 0:
        raise ValueError("Trace feature extraction requires regen_efficiency=0")
    t = _finite_array("timestamps_seconds", timestamps_seconds, minimum=0.0)
    speed_mph = _finite_array("speeds_mph", speeds_mph, minimum=0.0)
    elevation_ft = _finite_array("elevations_ft", elevations_ft)
    if not (len(t) == len(speed_mph) == len(elevation_ft)):
        raise ValueError("timestamps_seconds, speeds_mph and elevations_ft must have equal length")
    dt = np.diff(t)
    if np.any(dt <= 0):
        raise ValueError("timestamps_seconds must be strictly increasing")
    max_dt = float(np.max(dt))
    warnings: list[str] = []
    if max_dt > 30.0 and enforce_trace_resolution:
        raise ValueError("trace interval exceeds 30 seconds; use aggregate/profile prediction")
    if max_dt > 5.0:
        warnings.append("very_low_resolution_trace")
    elif max_dt > 2.0:
        warnings.append("low_resolution_trace")

    temp_f = _finite_scalar("temperature_f", temperature_f)
    mass_kg = lb_to_kg(_finite_scalar("gross_weight_lb", gross_weight_lb, minimum=1.0))
    base_kw = _finite_scalar("base_aux_kw", base_aux_kw, minimum=0.0)
    epto = _finite_scalar("epto_kw", epto_kw, minimum=0.0)
    hvac_kw = resolve_hvac_kw(hvac_mode)

    speed_mps = mph_to_mps(speed_mph)
    v_mid = 0.5 * (speed_mps[:-1] + speed_mps[1:])
    acceleration = np.diff(speed_mps) / dt
    interval_distance_m = v_mid * dt
    distance_m = float(np.sum(interval_distance_m))
    duration_s = float(t[-1] - t[0])
    if duration_s <= 0:
        raise ValueError("trace duration must be positive")

    dh_m = np.diff(ft_to_m(elevation_ft))
    theta = np.arctan2(dh_m, np.maximum(interval_distance_m, 1e-9))
    altitude_mid_ft = 0.5 * (elevation_ft[:-1] + elevation_ft[1:])
    rho = air_density_kg_m3(temp_f, altitude_mid_ft)

    force_inertia = mass_kg * acceleration
    force_grade = mass_kg * G * np.sin(theta)
    force_rolling = mass_kg * G * cfg.rolling_resistance * np.cos(theta)
    force_aero = 0.5 * cfg.drag_coefficient * cfg.frontal_area_m2 * rho * v_mid**2
    tractive_power_w = (force_inertia + force_grade + force_rolling + force_aero) * v_mid
    # Apply the no-regeneration floor at each interval before aggregation.
    # This preserves positive propulsion on a route that is net downhill but
    # still contains starts, level sections, or local climbs.
    positive_tractive_work_kwh = float(
        np.sum(np.maximum(tractive_power_w, 0.0) * dt) / JOULES_PER_KWH
    )
    propulsion_kwh = positive_tractive_work_kwh / cfg.drivetrain_efficiency

    hours = duration_s / 3600.0
    base_aux_kwh = base_kw * hours
    epto_kwh = epto * hours
    hvac_kwh = hvac_kw * hours
    total_kwh = propulsion_kwh + base_aux_kwh + epto_kwh + hvac_kwh

    positive_dv2 = np.maximum(np.diff(speed_mps**2), 0.0)
    negative_dv2 = np.maximum(-np.diff(speed_mps**2), 0.0)
    pke = float(np.sum(positive_dv2) / distance_m) if distance_m > 0 else 0.0
    nke = float(np.sum(negative_dv2) / distance_m) if distance_m > 0 else 0.0
    gain_m = float(np.sum(np.maximum(dh_m, 0.0)))
    loss_m = float(np.sum(np.maximum(-dh_m, 0.0)))
    interval_speed_mph = 0.5 * (speed_mph[:-1] + speed_mph[1:])
    idle_seconds = float(np.sum(dt[interval_speed_mph < 0.5]))
    stop_count = _count_stops(speed_mph, dt)
    distance_miles = distance_m / MILE_TO_M
    moving_time = float(np.sum(dt[interval_speed_mph >= 0.5]))
    average_speed_mph = distance_miles / (duration_s / 3600.0) if duration_s else 0.0
    # Distance-weighted speed squared.  This is the route-summary speed term
    # that preserves the v^3 integral used by aerodynamic energy:
    #   v_aero^2 = sum(v^3 dt) / sum(v dt).
    # It is intentionally distinct from average_speed_mph**2 and from the
    # time-weighted RMS speed when the trace contains speed variation or idle.
    if distance_m > 0:
        aerodynamic_speed_squared_mps2 = float(
            np.sum(v_mid**3 * dt) / np.sum(v_mid * dt)
        )
        aerodynamic_speed_squared_mph2 = aerodynamic_speed_squared_mps2 / MPH_TO_MPS**2
    else:
        aerodynamic_speed_squared_mph2 = 0.0
    if moving_time > 0:
        rms_speed_mph = float(
            np.sqrt(np.sum((interval_speed_mph**2) * dt) / np.sum(dt))
        )
        speed_std_mph = float(
            np.sqrt(np.sum(((interval_speed_mph - average_speed_mph) ** 2) * dt) / np.sum(dt))
        )
    else:
        rms_speed_mph = speed_std_mph = 0.0

    rolling_work_kwh = float(np.sum(np.maximum(force_rolling, 0.0) * interval_distance_m) / JOULES_PER_KWH)
    aero_work_kwh = float(np.sum(force_aero * v_mid * dt) / JOULES_PER_KWH)
    uphill_work_kwh = mass_kg * G * gain_m / JOULES_PER_KWH
    downhill_relief_kwh = -mass_kg * G * loss_m / JOULES_PER_KWH
    acceleration_work_kwh = 0.5 * mass_kg * float(np.sum(positive_dv2)) / JOULES_PER_KWH

    accel_mphps = np.diff(speed_mph) / dt
    features = TraceFeatures(
        distance_miles=distance_miles,
        duration_seconds=duration_s,
        average_speed_mph=average_speed_mph,
        aerodynamic_speed_squared_mph2=aerodynamic_speed_squared_mph2,
        rms_speed_mph=rms_speed_mph,
        speed_std_mph=speed_std_mph,
        idle_seconds=idle_seconds,
        idle_fraction=idle_seconds / duration_s,
        stop_count=stop_count,
        stops_per_mile=stop_count / distance_miles if distance_miles > 0 else 0.0,
        pke_mps2=pke,
        nke_mps2=nke,
        harsh_acceleration_count=int(np.sum(accel_mphps >= 2.5)),
        harsh_braking_count=int(np.sum(accel_mphps <= -3.0)),
        elevation_gain_ft=gain_m / FT_TO_M,
        elevation_loss_ft=loss_m / FT_TO_M,
        net_elevation_ft=float(elevation_ft[-1] - elevation_ft[0]),
        positive_tractive_work_kwh=positive_tractive_work_kwh,
        rolling_work_kwh=rolling_work_kwh,
        aerodynamic_work_kwh=aero_work_kwh,
        uphill_work_kwh=uphill_work_kwh,
        signed_downhill_relief_kwh=downhill_relief_kwh,
        acceleration_work_kwh=acceleration_work_kwh,
    )
    return TracePhysicsResult(
        features=features,
        propulsion_kwh=max(0.0, propulsion_kwh),
        base_aux_kwh=base_aux_kwh,
        epto_kwh=epto_kwh,
        hvac_kwh=hvac_kwh,
        total_kwh=max(0.0, total_kwh),
        warnings=tuple(warnings),
    )


def generate_route_events(
    distance_miles: float,
    stops_per_mile: float,
    traffic_state: str,
    rng: np.random.Generator,
) -> tuple[np.ndarray, np.ndarray]:
    distance = _finite_scalar("distance_miles", distance_miles, minimum=1e-9)
    density = _finite_scalar("stops_per_mile", stops_per_mile, minimum=0.0)
    state = traffic_state.lower()
    dwell_ranges = {"free_flow": (4.0, 12.0), "moderate": (10.0, 30.0), "congested": (20.0, 60.0)}
    if state not in dwell_ranges:
        raise ValueError(f"unknown traffic_state {traffic_state!r}")
    count = int(rng.poisson(density * distance))
    count = min(count, max(0, int(distance * 4)))
    if count == 0:
        return np.empty(0, dtype=float), np.empty(0, dtype=float)
    margin = min(0.1, distance * 0.05)
    positions = np.sort(rng.uniform(margin, max(margin, distance - margin), count))
    # Collapse nearly coincident events so that all driver profiles share a stable route.
    kept = [float(positions[0])]
    for value in positions[1:]:
        if value - kept[-1] >= 0.05:
            kept.append(float(value))
    lo, hi = dwell_ranges[state]
    dwells = rng.uniform(lo, hi, len(kept))
    return np.asarray(kept), np.asarray(dwells)


def _piecewise_leg_trace(
    distance_m: float,
    target_mps: float,
    accel_mps2: float,
    decel_mps2: float,
    dt_seconds: float,
) -> tuple[np.ndarray, np.ndarray]:
    if distance_m <= 0:
        return np.asarray([0.0]), np.asarray([0.0])
    peak = max(target_mps, 1e-6)
    accel = max(accel_mps2, 1e-6)
    decel = max(decel_mps2, 1e-6)
    required = peak**2 / (2 * accel) + peak**2 / (2 * decel)
    if required > distance_m:
        peak = math.sqrt(2 * distance_m / (1 / accel + 1 / decel))
    ta = peak / accel
    td = peak / decel
    accel_dist = peak**2 / (2 * accel)
    decel_dist = peak**2 / (2 * decel)
    cruise_dist = max(0.0, distance_m - accel_dist - decel_dist)
    tc = cruise_dist / peak if peak > 0 else 0.0
    boundaries = np.asarray([0.0, ta, ta + tc, ta + tc + td])
    regular = np.arange(0.0, boundaries[-1], dt_seconds)
    times = np.unique(np.concatenate([regular, boundaries]))
    speeds = np.empty_like(times)
    accel_mask = times <= ta
    cruise_mask = (times > ta) & (times <= ta + tc)
    decel_mask = times > ta + tc
    speeds[accel_mask] = accel * times[accel_mask]
    speeds[cruise_mask] = peak
    speeds[decel_mask] = np.maximum(0.0, peak - decel * (times[decel_mask] - ta - tc))
    speeds[-1] = 0.0
    return times, speeds


WOBBLE_CORRELATION_PER_SECOND = 0.94  # assumed, not sourced; about 16 s memory
WOBBLE_FULL_SPEED_MPH = 10.0  # wobble fades to zero below this speed, so stops stay stops
WOBBLE_MODES = ("leg", "per_second")


def apply_speed_wobble(
    timestamps_seconds: Sequence[float],
    speeds_mph: Sequence[float],
    wobble_mph: float,
    rng: np.random.Generator,
    *,
    correlation: float = WOBBLE_CORRELATION_PER_SECOND,
) -> np.ndarray:
    """Add a driver's slow speed drift: AR(1) noise on a 1 s grid, SD ``wobble_mph``.

    Each second the drift keeps ``correlation`` of its old value and gets a new
    random nudge, so its long-run SD is ``wobble_mph``. It is tied to time, not
    distance, so the same driver wobbles the same way on any route builder.
    """
    t = np.asarray(timestamps_seconds, dtype=float)
    v = np.asarray(speeds_mph, dtype=float)
    if wobble_mph <= 0 or len(t) < 2:
        return v.copy()
    grid = np.arange(0.0, t[-1] + 1.0, 1.0)
    nudges = rng.normal(0.0, wobble_mph * math.sqrt(1.0 - correlation**2), len(grid))
    drift = np.empty(len(grid))
    level = rng.normal(0.0, wobble_mph)  # start at the long-run spread, not at zero
    for i, nudge in enumerate(nudges):
        level = correlation * level + nudge
        drift[i] = level
    fade = np.clip(v / WOBBLE_FULL_SPEED_MPH, 0.0, 1.0)
    return np.maximum(0.0, v + fade * np.interp(t, grid, drift))


def _rescale_to_distance(
    timestamps: np.ndarray, speeds_mph: np.ndarray, distance_miles: float
) -> tuple[np.ndarray, np.ndarray]:
    """Scale speeds so the trace covers exactly ``distance_miles``; return speeds and miles."""
    miles = np.concatenate(
        [[0.0], np.cumsum(0.5 * (speeds_mph[:-1] + speeds_mph[1:]) * np.diff(timestamps))]
    ) / 3600.0
    if miles[-1] > 0:
        factor = distance_miles / miles[-1]
        speeds_mph = speeds_mph * factor
        miles = miles * factor
    miles[-1] = distance_miles
    return speeds_mph, miles


def synthesize_speed_trace(
    distance_miles: float,
    free_flow_speed_mph: float,
    behavior: DriverBehaviorParams,
    *,
    traffic_state: str = "moderate",
    stop_positions_miles: Sequence[float] = (),
    stop_durations_seconds: Sequence[float] = (),
    dt_seconds: float = 1.0,
    rng: np.random.Generator | None = None,
    wobble: str = "leg",
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Create a stop-to-stop, piecewise-linear speed trace.

    ``wobble="leg"`` (the original) shifts each stop-to-stop target speed once by
    a random amount. ``wobble="per_second"`` keeps the targets exact and adds
    ``apply_speed_wobble`` drift instead, the same drift the HERE/TomTom trace uses.

    Returns ``(timestamps_seconds, speeds_mph, cumulative_distance_miles)``.
    Phase boundaries are retained, so intervals are at most ``dt_seconds`` and
    trapezoidal distance integration is exact for each linear phase.
    """
    distance = _finite_scalar("distance_miles", distance_miles, minimum=1e-6)
    free_flow = _finite_scalar("free_flow_speed_mph", free_flow_speed_mph, minimum=0.1)
    dt_value = _finite_scalar("dt_seconds", dt_seconds, minimum=0.05)
    positions = np.asarray(stop_positions_miles, dtype=float)
    dwells = np.asarray(stop_durations_seconds, dtype=float)
    if len(positions) != len(dwells):
        raise ValueError("stop positions and durations must have equal length")
    if len(positions) and (np.any(np.diff(positions) <= 0) or positions[0] <= 0 or positions[-1] >= distance):
        raise ValueError("stop positions must be strictly increasing inside the route")
    if np.any(dwells < 0):
        raise ValueError("stop durations must be nonnegative")
    state = traffic_state.lower()
    traffic_multiplier = {"free_flow": 1.0, "moderate": 0.82, "congested": 0.62}.get(state)
    if traffic_multiplier is None:
        raise ValueError(f"unknown traffic_state {traffic_state!r}")
    if wobble not in WOBBLE_MODES:
        raise ValueError(f"wobble must be one of {WOBBLE_MODES}")
    generator = np.random.default_rng(0) if rng is None else rng
    leg_noise_mph = behavior.speed_noise_mph if wobble == "leg" else 0.0

    endpoints = np.concatenate([[0.0], positions, [distance]])
    all_times = [0.0]
    all_speeds = [0.0]
    all_distances = [0.0]
    current_t = 0.0
    current_d = 0.0
    accel_mps2 = behavior.acceleration_mphps * MPH_TO_MPS

    for leg_index, leg_miles in enumerate(np.diff(endpoints)):
        target_mph = min(
            free_flow,
            max(1.0, free_flow * behavior.speed_ratio * traffic_multiplier
                + generator.normal(0.0, leg_noise_mph)),
        )
        target_mps = target_mph * MPH_TO_MPS
        braking_mps2 = behavior.deceleration_mphps * MPH_TO_MPS
        # Longer coast lead lowers the effective deceleration and creates a
        # smoother, earlier speed reduction without treating coasting as regen.
        effective_decel = target_mps / (
            behavior.coast_lead_seconds + target_mps / max(braking_mps2, 1e-6)
        )
        local_t, local_v = _piecewise_leg_trace(
            leg_miles * MILE_TO_M, target_mps, accel_mps2, effective_decel, dt_value
        )
        local_distance = np.concatenate(
            [[0.0], np.cumsum(0.5 * (local_v[:-1] + local_v[1:]) * np.diff(local_t))]
        )
        all_times.extend((current_t + local_t[1:]).tolist())
        all_speeds.extend((local_v[1:] / MPH_TO_MPS).tolist())
        all_distances.extend((current_d + local_distance[1:] / MILE_TO_M).tolist())
        current_t += float(local_t[-1])
        current_d += float(leg_miles)

        if leg_index < len(dwells) and dwells[leg_index] > 0:
            dwell_grid = np.unique(
                np.concatenate([
                    np.arange(dt_value, dwells[leg_index], dt_value),
                    [dwells[leg_index]],
                ])
            )
            all_times.extend((current_t + dwell_grid).tolist())
            all_speeds.extend([0.0] * len(dwell_grid))
            all_distances.extend([current_d] * len(dwell_grid))
            current_t += float(dwells[leg_index])

    times = np.asarray(all_times, dtype=float)
    speeds = np.asarray(all_speeds, dtype=float)
    distances = np.asarray(all_distances, dtype=float)
    # Floating-point phase boundaries can collapse to the same absolute time
    # after an offset is added.  Keep the later sample at such a boundary.
    keep = np.r_[np.diff(times) > 1e-9, True]
    times = times[keep]
    speeds = speeds[keep]
    distances = distances[keep]
    distances[-1] = distance
    if wobble == "per_second":
        speeds = apply_speed_wobble(times, speeds, behavior.speed_noise_mph, generator)
        speeds, distances = _rescale_to_distance(times, speeds, distance)
    return times, speeds, distances
