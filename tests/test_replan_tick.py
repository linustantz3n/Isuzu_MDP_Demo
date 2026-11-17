"""Rolling replan for the drive simulation (/api/replan_tick, replan_tick)."""
import pytest

import route_optimizer_backend as be

# Depot and five stops on a straight road: x km from the depot.
X = [0, 1, 2, 3, 4, 5]
LOCS = [{'address': f'S{i}', 'lat': 33.8, 'lng': -118 + x / 100, 'x': x} for i, x in enumerate(X)]
TIME_GOALS = {**be.DEFAULT_GOALS, 'distance': 'off', 'time': 'high', 'battery_reserve_pct': None}
VEHICLES = [{'id': 1, 'battery_packs': 4, 'weight_lbs': 13000}, {'id': 2, 'battery_packs': 4, 'weight_lbs': 13000}]
PARAMS = {'temperature_c': 20, 'unload_time_min': 10}


@pytest.fixture
def traffic(monkeypatch):
    """Traffic data stand-in: 1 km and 60 s per km of road; measured legs 65 s."""
    calls = []

    def matrices(locations, source, issued_at):
        calls.append((len(locations), source, issued_at))
        xs = [loc['x'] for loc in locations]
        n = len(xs)
        return {'distance': [[abs(a - b) * 1000 for b in xs] for a in xs],
                'time': [[abs(a - b) * 60 for b in xs] for a in xs],
                'paths': {(i, j): [f'{i}-{j}'] for i in range(n) for j in range(n) if i != j}}

    def drive(paths, order, departure_time, unload_s, closes_itself):
        calls.append(('drive', departure_time))
        return [65 for _ in order[1:]]
    monkeypatch.setattr(be.traffic_data, 'travel_matrices', matrices)
    monkeypatch.setattr(be.traffic_data, 'drive_on_ground_truth', drive)
    monkeypatch.setattr(be.traffic_data, 'path_coordinates', lambda edges: [{'lat': 33.8, 'lng': -118}])
    return calls


def tick(trucks, goals=TIME_GOALS, now_s=900):
    return be.replan_tick(LOCS, [0.0] * len(LOCS), VEHICLES, trucks, be.normalize_goals(goals), PARAMS,
                          '2025-11-12T07:30', now_s)


def state(remaining, from_node=1, used_kwh=0.0, cps=0.0, v=0):
    return {'vehicle_index': v, 'locked_leg': 0, 'from_node': from_node, 'start_s': 1200,
            'remaining': remaining, 'used_kwh': used_kwh, 'cargo_per_stop_lbs': cps}


def test_reorders_remaining_stops_and_keeps_the_locked_stop_first(traffic):
    out = tick([state([4, 2, 3], cps=500)])
    t = out['trucks'][0]
    assert t['changed'] and t['alert'] is None
    assert t['order'][0] == 1 and t['order'][-1] == 0 and sorted(t['order'][1:-1]) == [2, 3, 4]
    # 1→4→2→3→0 drives 9 km; any order sweeping out and back drives 7 km
    assert t['saved_time_s'] == 120 and t['saved_distance_m'] == 2000
    assert [l['from_node'] for l in t['legs']] == t['order'][:-1]
    assert t['legs'][-1]['to_label'] == 'Depot'
    assert all(l['path'] and l['planned_s'] >= 0 and l['measured_s'] > 0 and l['energy_kwh'] > 0 for l in t['legs'])
    # Forecast issued at the tick time (07:30 + 15 min); the truck leaves its locked stop at +20 min
    assert out['issued_at'] == '2025-11-12T07:45'
    assert ('drive', '2025-11-12T07:50:00') in traffic


def test_keeps_an_order_that_is_already_best(traffic):
    t = tick([state([2, 3, 4])])['trucks'][0]
    assert not t['changed'] and t['order'] == [1, 2, 3, 4, 0] and t['legs'] == []


def test_ignores_gains_below_the_threshold(traffic, monkeypatch):
    monkeypatch.setattr(be, 'REPLAN_MIN_GAIN', 0.5)    # 9 km → 7 km is only 22% better
    assert not tick([state([4, 2, 3])])['trucks'][0]['changed']


def test_only_nodes_still_in_play_are_timed(traffic):
    tick([state([3]), state([5], from_node=4, v=1)])
    assert traffic[0] == (5, 'forecast', '2025-11-12T07:45')    # depot, 1, 3, 4, 5 (stop 2 is done)


def _bounds(remaining, from_node=1):
    """Upper-bound Wh of the full route and of driving straight home, as replan_tick prices them."""
    xs = [0, *X[1:]]
    D = [[abs(a - b) * 1000 for b in xs] for a in xs]
    T = [[abs(a - b) * 60 for b in xs] for a in xs]
    base = be.empty_weight_lbs(4)
    e = be.energy_upper_bound_matrix_wh(D, T, [0.0] * len(xs), base, base, 20, True, 'normal')
    route = [from_node, *remaining, 0]
    return sum(e[a][b] for a, b in zip(route, route[1:])), e[from_node][0]


def test_stops_that_no_longer_fit_the_battery_are_an_alert(traffic):
    full, home = _bounds([5])
    cap = be.battery_limit_wh(80, 20)
    used = (cap - (home + full) / 2) / 1000         # room to go home, not for the far stop
    goals = {**TIME_GOALS, 'battery_reserve_pct': 20}
    t = tick([state([5], used_kwh=used)], goals=goals)['trucks'][0]
    assert t['alert'] == 'unserved' and t['dropped'] == [5] and t['order'] == [1, 0] and t['changed']
    assert 'S5' in t['message']


def test_no_way_home_above_reserve_is_a_battery_alert(traffic):
    _, home = _bounds([5])
    used = (be.battery_limit_wh(80, 20) - home / 2) / 1000
    goals = {**TIME_GOALS, 'battery_reserve_pct': 20}
    t = tick([state([5], used_kwh=used)], goals=goals)['trucks'][0]
    assert t['alert'] == 'battery' and not t['changed'] and 'reserve' in t['message']


def test_endpoint_validates_the_truck_state(traffic):
    client = be.app.test_client()
    body = {'locations': LOCS, 'vehicles': VEHICLES, 'departure_time': '2025-11-12T07:30', 'now_s': 600,
            'goals': TIME_GOALS, 'energy_params': PARAMS, 'elevations': [0.0] * len(LOCS),
            'trucks': [state([4, 2, 3])]}
    ok = client.post('/api/replan_tick', json=body).get_json()
    assert ok['status'] == 'ok' and ok['trucks'][0]['changed']
    for bad in ({**state([4]), 'from_node': 0}, {**state([4]), 'remaining': [99]}, {**state([4]), 'vehicle_index': 5}):
        r = client.post('/api/replan_tick', json={**body, 'trucks': [bad]})
        assert r.status_code == 400
    assert client.post('/api/replan_tick', json={**body, 'now_s': 'soon'}).status_code == 400
