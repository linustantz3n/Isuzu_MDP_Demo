"""R4 test set: 50 bilingual requests for objective weights, live traffic and
driving style, 10 of them adversarial.

For each case it records the intent the chatbot returned, the optimizer
parameters it extracted (applied and queued changes), whether every value is
traceable to the operator's words, and, for the non-adversarial cases, whether
the route metrics move the requested way on a fixed scenario (Piedmont SC,
15 stops, 2 trucks; saved matrices, so no Google calls).

Traffic changes need Google to price them: `--live` fetches the Anaheim demo's
matrices for each requested departure and checks that the times come from
Google's traffic prediction (they differ from the typical ones). Without it,
traffic cases are checked for parameters and traceability only.

Run with the backend up:  python r4_test.py [--live]
Pass (R4): >=95% correct, zero untraceable values, correct metric direction
on every non-adversarial case.
"""
import contextlib
import io
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

from prompt_test import ask

with contextlib.redirect_stdout(io.StringIO()):
    import route_optimizer_backend as be

HERE = os.path.dirname(os.path.abspath(__file__))
LIVE = '--live' in sys.argv

# The form the requests are made against: room to raise every goal, no reserve,
# normal driving, no traffic.
BASE_GOALS = {'distance': 'medium', 'time': 'low', 'energy': 'low', 'balance': 'off', 'battery_reserve_pct': None}
BASE = {'vehicle_count': 2, 'battery_packs': 6, 'goals': BASE_GOALS, 'driving_style': 'normal',
        'traffic': {'departure': None}}
LEVEL = {lvl: i for i, lvl in enumerate(be.GOAL_LEVELS)}

ASK_OR_OOS = {'ask', 'out_of_scope'}
# (message, language, adversarial, expected intent(s), expected parameters)
#   raise: goals whose level must go up · reserve: battery reserve % ·
#   traffic: departure ('now', 'HH:MM', or None = off) · style: driving style
CASES = [
    # English: objective weights
    ("battery is tight today", 'en', False, 'goals', {'raise': ['energy']}),
    ("we're running late, get everyone back sooner", 'en', False, 'goals', {'raise': ['time']}),
    ("save as much electricity as possible", 'en', False, 'goals', {'raise': ['energy']}),
    ("fewest miles please, tyres are expensive", 'en', False, 'goals', {'raise': ['distance']}),
    ("make the routes fair for the drivers", 'en', False, 'goals', {'raise': ['balance']}),
    ("time matters more than distance today", 'en', False, 'goals', {'raise': ['time']}),
    ("keep 20% battery in every truck", 'en', False, 'goals', {'reserve': 20}),
    ("customers are waiting, finish the day sooner", 'en', False, 'goals', {'raise': ['time']}),
    # English: traffic
    ("we leave at 7:30", 'en', False, 'apply', {'traffic': '07:30'}),
    ("use live traffic", 'en', False, 'apply', {'traffic': 'now'}),
    ("trucks go out at 6pm, plan with traffic", 'en', False, 'apply', {'traffic': '18:00'}),
    ("plan for a 5 am departure with traffic", 'en', False, 'apply', {'traffic': '05:00'}),
    ("ignore traffic", 'en', False, 'apply', {'traffic': None}),
    # English: driving style
    ("drive in eco mode", 'en', False, 'apply', {'style': 'eco'}),
    ("the drivers should take it easy", 'en', False, 'apply', {'style': 'eco'}),
    ("aggressive driving today", 'en', False, 'apply', {'style': 'aggressive'}),
    ("back to normal driving", 'en', False, 'apply', {'style': 'normal'}),
    ("drive gently", 'en', False, 'apply', {'style': 'eco'}),
    # English: combined
    ("use live traffic and eco driving", 'en', False, 'apply', {'traffic': 'now', 'style': 'eco'}),
    ("leave at 8:00 and drive aggressively", 'en', False, 'apply', {'traffic': '08:00', 'style': 'aggressive'}),
    # Chinese: objective weights
    ("今天电量紧张", 'zh', False, 'goals', {'raise': ['energy']}),
    ("我们赶时间，让大家早点回来", 'zh', False, 'goals', {'raise': ['time']}),
    ("尽量省电", 'zh', False, 'goals', {'raise': ['energy']}),
    ("路程越短越好", 'zh', False, 'goals', {'raise': ['distance']}),
    ("让司机的工作量平均一点", 'zh', False, 'goals', {'raise': ['balance']}),
    ("今天时间比距离重要", 'zh', False, 'goals', {'raise': ['time']}),
    ("每辆车留20%电量", 'zh', False, 'goals', {'reserve': 20}),
    ("客户在等，早点送完", 'zh', False, 'goals', {'raise': ['time']}),
    # Chinese: traffic
    ("早上7点半出发", 'zh', False, 'apply', {'traffic': '07:30'}),
    ("用实时路况", 'zh', False, 'apply', {'traffic': 'now'}),
    ("下午6点出发，考虑路况", 'zh', False, 'apply', {'traffic': '18:00'}),
    ("凌晨5点出发", 'zh', False, 'apply', {'traffic': '05:00'}),
    ("不考虑路况", 'zh', False, 'apply', {'traffic': None}),
    # Chinese: driving style
    ("节能驾驶", 'zh', False, 'apply', {'style': 'eco'}),
    ("开得温和一点", 'zh', False, 'apply', {'style': 'eco'}),
    ("激进驾驶", 'zh', False, 'apply', {'style': 'aggressive'}),
    ("正常驾驶", 'zh', False, 'apply', {'style': 'normal'}),
    ("开慢一点", 'zh', False, 'apply', {'style': 'eco'}),
    # Chinese: combined
    ("用实时路况，节能驾驶", 'zh', False, 'apply', {'traffic': 'now', 'style': 'eco'}),
    ("8点出发，开得激进一点", 'zh', False, 'apply', {'traffic': '08:00', 'style': 'aggressive'}),
    # Adversarial: traffic with no departure time, a style outside the three,
    # an impossible time, conflicting weights, traffic the app cannot have
    ("plan with traffic", 'en', True, 'ask', {}),
    ("drive in ludicrous mode", 'en', True, 'ask', {}),
    ("leave at 25:00 with live traffic", 'en', True, 'ask', {}),
    ("make it the shortest, the fastest and the most energy-efficient route", 'en', True, 'goals', {}),
    ("use last Tuesday's traffic", 'en', True, ASK_OR_OOS, {}),
    ("考虑路况", 'zh', True, 'ask', {}),
    ("用赛车模式开", 'zh', True, 'ask', {}),
    ("25点出发", 'zh', True, 'ask', {}),
    ("又要最短又要最快还要最省电", 'zh', True, 'goals', {}),
    ("用上周二的路况", 'zh', True, ASK_OR_OOS, {}),
]


def extracted(d):
    """The optimizer parameters a reply sets: applied changes plus queued ones."""
    changes = list(d.get('changes') or []) + list((d.get('pending') or {}).get('queued') or [])
    params = {}
    for c in changes:
        if c['axis'] == 'goals':
            params['goals'] = c['value']
        elif c['axis'] == 'traffic':
            params['traffic'] = c['value'].get('departure')
        elif c['axis'] == 'driving_style':
            params['style'] = c['value']
        else:
            params[c['axis']] = c['value']
    return changes, params


def traceable(c, msg):
    """Is this change's value tied to the operator's own words?"""
    ev = be._strip_edges(c.get('evidence'))
    if not ev or be._norm(ev) not in be._norm(msg):
        return False
    v = c['value']
    if c['axis'] == 'traffic':
        dep = v.get('departure')
        return be.TRAFFIC_OFF_RE.search(msg) is not None if dep is None else dep == be.departure_in(msg)
    if c['axis'] == 'driving_style':
        return bool(be.STYLE_WORD_RE[v].search(ev))
    if c['axis'] == 'goals':
        levels_ok = all(v[k] in be.GOAL_LEVELS for k in be.GOAL_KEYS)
        r = v.get('battery_reserve_pct')
        return levels_ok and (r in (None, 0) or str(r) in msg)
    return True


def params_ok(expect, params):
    g = params.get('goals') or {}
    for k in expect.get('raise', []):
        if LEVEL[g.get(k, BASE_GOALS[k])] <= LEVEL[BASE_GOALS[k]]:
            return False
    if 'reserve' in expect and g.get('battery_reserve_pct') != expect['reserve']:
        return False
    if 'traffic' in expect and ('traffic' not in params or params['traffic'] != expect['traffic']):
        return False
    if 'style' in expect and params.get('style') != expect['style']:
        return False
    return True


# ---- metric direction on a fixed scenario -------------------------------------

SCENARIO = json.load(open(os.path.join(HERE, 'benchmark', 'scenarios', 'case3_piedmont_sc.json')))
CTX = {'matrices': {'distance': SCENARIO['distance_m'], 'time': SCENARIO['time_s']},
       'elevations': [0.0] * len(SCENARIO['locations']), 'locations': SCENARIO['locations'],
       'temperature_c': 20.0, 'has_climate_control': True, 'unload_s': 1800.0}
TRUCKS = [{'id': i + 1, 'name': f'Vehicle {i + 1}', 'battery_packs': 6, 'battery_kwh': 120,
           'weight_lbs': round(13000 + 2 * be.PACK_WEIGHT_LBS)} for i in range(2)]
_solves = {}


def solve(goals, style):
    key = (json.dumps(goals, sort_keys=True), style)
    if key not in _solves:
        ctx = dict(CTX, matrices=be.styled_matrices(CTX['matrices'], style), driving_style=style)
        f = be.solve_fleet(ctx, TRUCKS, be.normalize_goals(goals), time_limit_s=4)
        _solves[key] = {'mi': f['total_distance_mi'], 'kwh': f['total_energy_kwh'],
                        'drive_h': f['total_travel_time_s'] / 3600, 'back_h': f['makespan_s'] / 3600,
                        'reserve_ok': all(r.get('battery_reserve_ok', True) for r in f['routes']),
                        'unserved': len(f['unserved'])}
    return _solves[key]


def direction(expect, params):
    """(ok, note) for whether the metrics moved the requested way, or None if
    this case has nothing the offline scenario can check."""
    goals = dict(BASE_GOALS, **(params.get('goals') or {}))
    style = params.get('style', 'normal')
    base = solve(BASE_GOALS, 'normal')
    checks = []
    if expect.get('raise') or 'reserve' in expect or 'style' in expect:
        new = solve(goals, style)
        for k in expect.get('raise', []):
            metric = {'energy': 'kwh', 'time': 'drive_h', 'distance': 'mi', 'balance': 'back_h'}[k]
            checks.append((new[metric] <= base[metric] + 1e-9, f"{metric} {base[metric]:.2f}→{new[metric]:.2f}"))
        if 'reserve' in expect:
            checks.append((new['reserve_ok'], f"reserve kept on every route ({new['unserved']} unserved)"))
        if 'style' in expect:
            # The style alone, on the same goals: eco below normal, aggressive above.
            plain = solve(goals, 'normal')
            want = {'eco': new['kwh'] < plain['kwh'], 'aggressive': new['kwh'] > plain['kwh'],
                    'normal': abs(new['kwh'] - plain['kwh']) < 1e-6}[style]
            checks.append((want, f"kWh normal {plain['kwh']:.1f} → {style} {new['kwh']:.1f}"))
    if 'traffic' in expect:
        checks.append(live_traffic(params.get('traffic')))
    if not checks:
        return None
    return all(ok for ok, _ in checks if ok is not None), '; '.join(n for _, n in checks)


ANAHEIM = json.load(open(os.path.join(HERE, 'benchmark', 'scenarios', 'demo_anaheim.json')))
_typical = {}


def live_traffic(dep):
    if not LIVE:
        return None, "traffic: run with --live to price it"
    locs = ANAHEIM['locations']
    if 'typical' not in _typical:
        _typical['typical'] = be.get_distance_matrix(locs)['time']
    if dep is None:
        return True, "traffic off: typical times"
    if dep not in _typical:                      # one Google request per departure
        info = be.departure_info(dep, locs[0])
        _typical[dep] = (info, be.get_distance_matrix(locs, info['departure_ts'])['time'])
    info, t = _typical[dep]
    typ = _typical['typical']
    total, total_typ = sum(map(sum, t)), sum(map(sum, typ))
    return t != typ, f"traffic {dep} ({info['departure_local']} {info['time_zone']}): {total_typ / 60:.0f} → {total / 60:.0f} min over all legs"


# ---- run -----------------------------------------------------------------------

def run_case(case):
    msg, lang, adv, want_intent, expect = case
    d = ask(msg, BASE)
    return case, d


def main():
    with ThreadPoolExecutor(max_workers=4) as ex:
        results = list(ex.map(run_case, CASES))
    rows, n_correct, n_intent, untraceable, dir_bad, dir_checked = [], 0, 0, 0, 0, 0
    for (msg, lang, adv, want_intent, expect), d in results:
        intent = d.get('intent')
        intent_ok = intent in want_intent if isinstance(want_intent, set) else intent == want_intent
        changes, params = extracted(d)
        bad_trace = [c['label'] for c in changes if not traceable(c, msg)]
        untraceable += len(bad_trace)
        if adv:
            # No traffic or style may be set by a request that should only ask.
            p_ok = intent not in ('ask', 'out_of_scope') or not changes
        else:
            p_ok = params_ok(expect, params)
        ok = intent_ok and p_ok and not bad_trace
        n_intent += intent_ok
        n_correct += ok
        dir_note = ''
        if not adv and ok:
            dr = direction(expect, params)
            if dr is not None:
                good, note = dr
                if good is not None and any(k in expect for k in ('raise', 'reserve', 'style')) or (LIVE and 'traffic' in expect):
                    dir_checked += 1
                    dir_bad += not good
                dir_note = f"  [{'OK' if good else '!!'} direction: {note}]" if good is not None else f"  [{note}]"
        label = '; '.join(c['label'] for c in changes) or (d.get('message') or '')[:70]
        tag = 'ADV' if adv else '   '
        rows.append(f"{'OK' if ok else '!!'} {tag} [{lang}] {msg!r}\n      {intent}/{d.get('source')}: {label}"
                    + (f"\n      untraceable: {bad_trace}" if bad_trace else '') + dir_note)
    print('\n'.join(rows))
    n = len(CASES)
    print(f"\n{'=' * 90}")
    print(f"Cases: {n} ({sum(1 for c in CASES if c[1] == 'en')} English, {sum(1 for c in CASES if c[1] == 'zh')} Chinese,"
          f" {sum(1 for c in CASES if c[2])} adversarial)")
    print(f"Intent match:           {n_intent}/{n} ({100 * n_intent / n:.0f}%)")
    print(f"Correct (intent + parameters + traceable): {n_correct}/{n} ({100 * n_correct / n:.0f}%)")
    print(f"Untraceable values:     {untraceable}")
    print(f"Metric direction:       {dir_checked - dir_bad}/{dir_checked} correct"
          + ('' if LIVE else '  (traffic cases need --live)'))
    passed = n_correct / n >= 0.95 and untraceable == 0 and dir_bad == 0
    print(f"R4 pass criteria met:   {'YES' if passed else 'NO'}")


if __name__ == '__main__':
    main()
