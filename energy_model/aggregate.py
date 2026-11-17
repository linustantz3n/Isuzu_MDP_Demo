"""Deterministic traces matching supplied distance and driving duration."""
import numpy as np
from .traces import DRIVER_PROFILES, DriverBehaviorParams, MILE_TO_M, synthesize_speed_trace

def aggregate_trace(leg):
    """Match the supplied (already style-adjusted) duration and distance exactly."""
    p = DRIVER_PROFILES[leg["driving_style"]]
    behavior = DriverBehaviorParams(p.speed_ratio_mean, p.acceleration_mphps_mean,
        p.deceleration_mphps_mean, p.coast_lead_seconds_mean, p.speed_noise_mph)
    distance = leg["distance_m"] / MILE_TO_M
    times, speeds, _ = synthesize_speed_trace(
        distance, max(.1, distance * 3600 / leg["duration_seconds"]), behavior,
        traffic_state="free_flow", wobble="per_second", rng=np.random.default_rng(42))
    scale = leg["duration_seconds"] / times[-1]
    times, speeds = times * scale, speeds / scale
    # Preserve phase boundaries and the piecewise-linear integral when subdividing.
    grid = np.unique(np.r_[times, np.arange(0, times[-1], 1.)])
    speeds = np.interp(grid, times, speeds)
    travelled = np.r_[0., np.cumsum(.5 * (speeds[:-1] + speeds[1:]) * np.diff(grid))] * MILE_TO_M / 3600
    elevations = np.interp(travelled, [0., leg["distance_m"]],
                           [leg["elevation_from_m"], leg["elevation_to_m"]]) / .3048
    return grid, speeds, elevations
