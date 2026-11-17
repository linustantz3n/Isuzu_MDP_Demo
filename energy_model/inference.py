"""Frozen City NN inference, with exact load-interval maxima for battery limits."""
from functools import lru_cache
from pathlib import Path
import hashlib
import json
import os

import numpy as np

from .aggregate import aggregate_trace
from .features import FEATURE_NAMES, feature_vector
from .traces import simulate_trace_physics

INPUT_NAMES = (*FEATURE_NAMES, 'profile_eco', 'profile_normal')
ADAPTER_VERSION = 'aggregate_leg_v2_load_bounds'


class ModelUnavailable(RuntimeError):
    pass


@lru_cache(maxsize=4)
def _load(path, modified, size):
    try:
        raw = Path(path).read_bytes()
        artifact = json.loads(raw)
        if artifact.get('model_version') != 'energy_nn_v1' or tuple(artifact['input_names']) != INPUT_NAMES:
            raise ValueError('Unsupported NN schema')
        members = []
        for m in artifact['members']:
            w, b = [np.asarray(v, dtype=float) for v in m['weights']], [np.asarray(v, dtype=float) for v in m['biases']]
            mean, scale = np.asarray(m['x_mean']), np.asarray(m['x_scale'])
            if (len(w) != 2 or len(b) != 2 or w[0].shape != (12, 32) or w[1].shape != (32, 1)
                    or b[0].shape != (32,) or b[1].shape not in ((1,), ())
                    or mean.shape != (12,) or scale.shape != (12,)):
                raise ValueError('Expected the frozen 12-32-1 network')
            arrays = [*w, *b, mean, scale, np.asarray(m['y_mean']), np.asarray(m['y_scale'])]
            if not all(np.isfinite(x).all() for x in arrays) or np.any(scale < 0):
                raise ValueError('Non-finite NN parameters')
            members.append((mean, np.where(scale == 0, 1., scale), w, b, float(m['y_mean']), float(m['y_scale'])))
        if not members:
            raise ValueError('Missing NN members')
        return members, {'name': 'city_nn', 'model_version': 'energy_nn_v1',
                         'sha256': hashlib.sha256(raw).hexdigest(), 'adapter_version': ADAPTER_VERSION}
    except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
        raise ModelUnavailable('City NN weights are missing or invalid') from exc


def model():
    path = Path(os.environ.get('ENERGY_NN_PATH') or Path(__file__).resolve().parents[1] / 'energy_nn_results_city.json')
    try:
        stat = path.stat()
    except OSError as exc:
        raise ModelUnavailable('City NN weights are missing or invalid') from exc
    return _load(str(path), stat.st_mtime_ns, stat.st_size)


def forward(inputs, members):
    x = np.atleast_2d(inputs)
    predictions = [((np.maximum(0, ((x - mean) / scale) @ w[0] + b[0]) @ w[1] + b[1]).reshape(-1)
                    * ys + ym) for mean, scale, w, b, ym, ys in members]
    result = np.mean(predictions, axis=0)
    if not np.isfinite(result).all():
        raise ModelUnavailable('City NN produced a non-finite prediction')
    return result


@lru_cache(maxsize=8192)
def _unit_features(distance_m, duration_s, start_m, end_m, temperature_c, style):
    """The trace is independent of weight; three weight features and acceleration work are linear."""
    leg = dict(distance_m=distance_m, duration_seconds=duration_s, elevation_from_m=start_m,
               elevation_to_m=end_m, driving_style=style)
    times, speeds, elevations = aggregate_trace(leg)
    trace = simulate_trace_physics(times, speeds, elevations, temperature_c * 9 / 5 + 32,
                                   1000., hvac_mode='off').features
    v = feature_vector(trace, temperature_c * 9 / 5 + 32, 1000., start_m / .3048)
    return np.r_[v, float(style == 'eco'), float(style == 'normal')]


def inputs(distance_m, duration_s, start_m, end_m, weight_lbs, temperature_c, style):
    v = _unit_features(distance_m, duration_s, start_m, end_m, temperature_c, style).copy()
    v[[3, 7, 8, 9]] *= weight_lbs / 1000.
    return v


def predict(distance_m, duration_s, start_m, end_m, weight_lbs, temperature_c, style, hvac, upper_weight=None, loaded_model=None):
    members, metadata = loaded_model or model()
    propulsion = 0.
    if distance_m > 0:
        low = inputs(distance_m, duration_s, start_m, end_m, weight_lbs, temperature_c, style)
        candidates = [low]
        if upper_weight is not None and upper_weight != weight_lbs:
            high = inputs(distance_m, duration_s, start_m, end_m, upper_weight, temperature_c, style)
            delta = high - low
            candidates.append(high)
            # All ensemble members are piecewise affine in load. Their union of
            # activation boundaries plus both endpoints contains the exact maximum.
            for mean, scale, w, b, _, _ in members:
                offset, slope = ((low - mean) / scale) @ w[0] + b[0], (delta / scale) @ w[0]
                active = np.abs(slope) > 0
                roots = -offset[active] / slope[active]
                candidates.extend(low + t * delta for t in roots if 0 < t < 1)
        propulsion = max(0., float(np.max(forward(candidates, members))))
    hours = duration_s / 3600.
    components = {'propulsion': propulsion, 'base_aux': 5 * hours, 'epto': 5 * hours,
                  'hvac': (4 if hvac else 0) * hours}
    return sum(components.values()), components
