"""Driver defaults and explicit, per-run vehicle overrides."""
from copy import deepcopy
import re
from energy import validate_style


def driver_key(name):
    return re.sub(r'\s+', '', str(name or '')).lower()


def resolve_vehicles(vehicles, profiles=None):
    profiles = profiles or {}
    out, assigned, ids = [], set(), set()
    for i, source in enumerate(vehicles):
        v = dict(source)
        vid = v.setdefault('id', i + 1)
        if not isinstance(vid, int) or isinstance(vid, bool) or vid <= 0 or vid in ids:
            raise ValueError('Vehicle IDs must be unique positive integers')
        ids.add(vid)
        key = v.get('driver_id') or None
        snapshot = v.get('driver_snapshot')
        if key:
            if key in assigned:
                raise ValueError('A driver can only be assigned to one truck per run')
            assigned.add(key)
            p = snapshot if isinstance(snapshot, dict) and snapshot.get('id') == key else profiles.get(key)
            if not p:
                raise ValueError(f'Unknown driver: {key}')
            default = validate_style(p.get('driving_style', 'normal'))
            v.update(driver=p['name'], driver_snapshot={'id': key, 'name': p['name'], 'driving_style': default})
        else:
            default = 'normal'
            v.update(driver='', driver_snapshot=None)
        override = v.get('driving_style_override')
        if override is not None:
            validate_style(override)
        v.update(driver_id=key, driver_default_style=default, driving_style_override=override,
                 effective_driving_style=override or default,
                 style_source='run_override' if override else 'driver' if key else 'default')
        out.append(v)
    return out


def assign_driver(vehicles, vehicle_id, key, profiles):
    """Move a driver without replacing truck slots or any vehicle specifications."""
    if key is not None and key not in profiles:
        raise ValueError(f'Unknown driver: {key}')
    if vehicle_id not in [v.get('id') for v in vehicles]:
        raise ValueError('Unknown truck')
    out = deepcopy(vehicles)
    for v in out:
        if v['id'] == vehicle_id or (key and v.get('driver_id') == key):
            v.update(driver_id=key if v['id'] == vehicle_id else None,
                     driver_snapshot=None, driving_style_override=None)
    return resolve_vehicles(out, profiles)


def apply_overrides(vehicles, changes, profiles=None):
    if not isinstance(changes, list) or not changes:
        raise ValueError('At least one vehicle override is required')
    out = deepcopy(vehicles)
    ids = {v['id'] for v in out}
    seen = set()
    for item in changes:
        vid, style = item.get('vehicle_id'), item.get('driving_style_override')
        if vid not in ids or vid in seen:
            raise ValueError('Each override must name one existing truck')
        seen.add(vid)
        if style is not None:
            validate_style(style)
        next(v for v in out if v['id'] == vid)['driving_style_override'] = style
    return resolve_vehicles(out, profiles)
