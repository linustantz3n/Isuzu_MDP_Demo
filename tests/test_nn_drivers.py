"""City NN, persistent driver defaults, run overrides and verified suggestions."""
from copy import deepcopy
import json

import numpy as np
import pytest

import energy
from energy_model import inference
from energy_model.aggregate import aggregate_trace
from energy_model.features import feature_vector
from energy_model.traces import simulate_trace_physics
from drivers import assign_driver, apply_overrides, resolve_vehicles
from driver_chat import parse_command
import route_optimizer_backend as be
from solvers import solve_vrp_constrained


@pytest.fixture
def profiles(monkeypatch, tmp_path):
    monkeypatch.setattr(be, 'MEMORY_PATH', str(tmp_path / 'drivers.json'))
    data = {'wang': {'id': 'wang', 'name': 'Wang', 'driving_style': 'aggressive', 'battery_packs': 9}}
    be.save_memory({'drivers': data})
    return data


def trucks():
    return [{'id': i, 'name': f'Truck {i}', 'battery_packs': 4, 'battery_kwh': 80,
             'weight_lbs': 13000} for i in (1, 2)]


@pytest.mark.parametrize('style', energy.DRIVING_STYLES)
def test_nn_matches_direct_trace_features_and_forward(style):
    leg = dict(distance_m=7000, duration_seconds=600, elevation_from_m=200,
               elevation_to_m=240, driving_style=style)
    t, v, h = aggregate_trace(leg)
    assert t[-1] == pytest.approx(600)
    assert np.sum(.5 * (v[1:] + v[:-1]) * np.diff(t)) * 1609.344 / 3600 == pytest.approx(7000)
    trace = simulate_trace_physics(t, v, h, 86, 13000, hvac_mode='off').features
    direct = np.r_[feature_vector(trace, 86, 13000, 200 / .3048), style == 'eco', style == 'normal']
    np.testing.assert_allclose(inference.inputs(7000, 600, 200, 240, 13000, 30, style), direct)
    total, parts = energy.predict_leg(7000, 600, 200, 240, 13000, 30, style)
    assert parts['propulsion'] == pytest.approx(max(0, inference.forward(direct, inference.model()[0])[0]))
    assert total == pytest.approx(parts['propulsion'] + 14 * 600 / 3600)
    assert energy.predict_leg(7000, 600, 200, 240, 13000, 30, style)[0] == total


@pytest.mark.parametrize('style', energy.DRIVING_STYLES)
def test_load_bound_covers_actual_predictions_at_intermediate_weights(style):
    bound, _ = energy.predict_leg(4000, 400, 200, 150, 7000, 20, style, upper_weight=19500)
    for weight in np.linspace(7000, 19500, 31):
        actual, _ = energy.predict_leg(4000, 400, 200, 150, float(weight), 20, style)
        assert actual <= bound + 1e-10


def test_load_bound_finds_interior_nn_peak(monkeypatch):
    w0, w1, b0 = np.zeros((12, 32)), np.zeros((32, 1)), np.zeros(32)
    w0[0, :3], w1[:3, 0], b0[:3] = [1, 1, 1], [1, -2, 1], [0, -2, -4]
    member = (np.zeros(12), np.ones(12), [w0, w1], [b0, np.zeros(1)], 0, 1)
    monkeypatch.setattr(inference, 'inputs', lambda d, t, a, b, weight, temp, style: np.r_[weight, np.zeros(11)])
    model = ([member], {})
    low = inference.predict(1, 3600, 0, 0, 1, 20, 'normal', False, loaded_model=model)[0]
    high = inference.predict(1, 3600, 0, 0, 3, 20, 'normal', False, loaded_model=model)[0]
    bounded = inference.predict(1, 3600, 0, 0, 1, 20, 'normal', False, upper_weight=3, loaded_model=model)[0]
    assert low == high == 11
    assert bounded == 12


def test_missing_or_invalid_weights_fail_without_another_model(monkeypatch, tmp_path):
    path = tmp_path / 'missing.json'
    monkeypatch.setenv('ENERGY_NN_PATH', str(path))
    with pytest.raises(inference.ModelUnavailable):
        energy.predict_leg(1000, 100, 0, 0, 7000, 20)
    path.write_text(json.dumps({'model_version': 'energy_nn_v1', 'input_names': []}))
    response = be.app.test_client().post('/api/optimize', json={})
    assert response.status_code == 503
    assert response.json['code'] == 'model_unavailable'
    response = be.app.test_client().post('/api/optimize', json={'energy_params': {'model': 'model_e'}})
    assert response.status_code == 400


def test_assignment_moves_driver_preserves_specs_and_override_is_temporary(profiles):
    fleet = assign_driver(trucks(), 1, 'wang', profiles)
    fleet = apply_overrides(fleet, [{'vehicle_id': 1, 'driving_style_override': 'eco'}])
    assert fleet[0]['effective_driving_style'] == 'eco'
    assert profiles['wang']['driving_style'] == 'aggressive'
    restored = apply_overrides(fleet, [{'vehicle_id': 1, 'driving_style_override': None}])
    assert restored[0]['effective_driving_style'] == 'aggressive'
    moved = assign_driver(fleet, 2, 'wang', profiles)
    assert [v['effective_driving_style'] for v in moved] == ['normal', 'aggressive']
    assert moved[0]['driver_id'] is None
    assert all(v['battery_packs'] == 4 and v['weight_lbs'] == 13000 for v in moved)
    profiles['wang']['driving_style'] = 'eco'
    assert resolve_vehicles(moved, profiles)[1]['effective_driving_style'] == 'aggressive'


def test_chat_remembers_assigns_then_overrides_without_changing_memory(profiles):
    client = be.app.test_client()
    def chat(message, fleet=None, **extra):
        return client.post('/api/chat', json={'message': message, 'form_state': {'fleet': fleet or trucks()}, **extra}).json
    saved = chat('Remember Alex as an eco driver.')
    assert saved['intent'] == 'memory'
    out = chat('Assign Wang to Truck 2.')
    assert out['intent'] == 'apply'
    fleet = out['changes'][0]['value']
    assert fleet[1]['effective_driving_style'] == 'aggressive'
    out = chat('Use Eco for Truck 2 on this run.', fleet)
    assert out['changes'][0]['value'] == [{'vehicle_id': 2, 'driving_style_override': 'eco'}]
    assert be.load_memory()['drivers']['wang']['driving_style'] == 'aggressive'
    out = chat('Assign Wang to Truck 1 and use Eco for Truck 1', fleet)
    updated = out['changes'][0]['value']
    assert updated[0]['effective_driving_style'] == 'eco' and updated[1]['driver_id'] is None
    assert chat('Assign Wang to Truck 9')['intent'] == 'ask'
    assert chat('Use Eco')['intent'] == 'ask'
    assert parse_command('Would Eco help Truck 2?', fleet, profiles) is None
    assert parse_command('Should Truck 2 use Eco?', fleet, profiles) is None
    assert parse_command("Don't use Eco for Truck 2", fleet, profiles) is None
    assert chat('Use Eco or Normal for Truck 2')['intent'] == 'ask'
    fabricated = {'axis': 'vehicle_style', 'evidence': 'Use Eco for Truck 1', 'value_text': 'eco'}
    assert be.validate_change(fabricated, 'What is Eco?', {'fleet': fleet})[0] is None
    assigned = parse_command('Wang is assigned to the second truck', trucks(), profiles)
    assert assigned['value'][1]['driver_id'] == 'wang'


def test_named_truck_limits_use_its_driver_default(profiles):
    fleet = assign_driver(trucks(), 2, 'wang', profiles)
    out = be.outcome_limits({}, 'How far can Truck 2 go?', {'fleet': fleet}, 'model')
    assert out['limits']['driving_style'] == 'aggressive'
    assert out['limits']['packs'] == 4
    assert out['limits']['vehicle_id'] == 2


def test_old_profiles_default_to_normal_without_rewriting_storage(profiles):
    old = {'drivers': {'alex': {'name': 'Alex', 'battery_packs': 6, 'weight_lbs': 14000}}}
    be.save_memory(old)
    loaded = be.load_memory()['drivers']['alex']
    assert loaded['id'] == 'alex' and loaded['driving_style'] == 'normal'
    with open(be.MEMORY_PATH) as source:
        assert json.load(source) == old


def test_pending_plan_rejects_a_different_model():
    metadata = {**energy.energy_metadata(), 'sha256': 'old-model'}
    response = be.app.test_client().post('/api/plan_charging', json={'expected_energy_model': metadata})
    assert response.status_code == 409


def test_simulation_uses_each_trucks_style():
    from benchmark.simulate import simulate
    result = simulate('demo_anaheim', 2, energy_params={'battery_kwh': 400},
                      vehicle_styles=['eco', 'aggressive'], drain={'factor': 1}, ortools_time_limit_s=1)
    assert result['energy_model'] == energy.energy_metadata()
    assert [t['effective_driving_style'] for t in result['trucks']] == ['eco', 'aggressive']
    assert result['summary']['dropped'] == 0
    for t in result['trucks']:
        assert t['soc_trace'] == t['planned']['soc_trace']


def test_solver_prices_the_same_stop_differently_for_each_vehicle():
    a = [[0, 1, 100], [1, 0, 100], [100, 100, 0]]
    b = [[0, 100, 1], [100, 0, 100], [1, 100, 0]]
    result = solve_vrp_constrained(a, 2, vehicle_costs=[a, b], max_stops=[1, 1], time_limit_s=1)
    assert result['routes'] == [[0, 1, 0], [0, 2, 0]]
    result = solve_vrp_constrained(a, 2, vehicle_costs=[b, a], max_stops=[1, 1], time_limit_s=1)
    assert result['routes'] == [[0, 2, 0], [0, 1, 0]]


def request_data():
    return {'locations': [{'address': str(i), 'lat': i, 'lng': 0} for i in range(3)],
            'vehicles': trucks(), 'return_to_start': True, 'fleet_time_limit_s': 1,
            'goals': {**be.DEFAULT_GOALS, 'battery_reserve_pct': None, 'time': 'high'},
            'precomputed': {'distance': [[0, 10000, 15000], [10000, 0, 5000], [15000, 5000, 0]],
                            'time': [[0, 400, 600], [400, 0, 200], [600, 200, 0]], 'elevations': [0, 10, 20]}}


def test_fleet_legs_use_each_driver_style_and_original_traffic_snapshot(profiles):
    data = request_data()
    data['vehicles'] = assign_driver(data['vehicles'], 2, 'wang', profiles)
    out = be.optimize_payload(data)
    assert out['energy_model']['name'] == 'city_nn'
    assert out['matrices']['time'] == data['precomputed']['time']
    routes = out['results']['ortools_fleet']['routes']
    assert len(routes) == 2
    for route in routes:
        style = 'aggressive' if route['vehicle_id'] == 2 else 'normal'
        assert route['effective_driving_style'] == style
        for leg in route['legs']:
            assert leg['effective_driving_style'] == style
            assert leg['energy_kwh'] == pytest.approx(energy.predict_leg(
                leg['distance_m'], leg['time_s'], leg['elevation_from_m'], leg['elevation_to_m'],
                leg['vehicle_weight_lbs'], 20, style)[0], rel=1e-4)


@pytest.mark.parametrize('served, battery, accepted', [([0, 1, 2, 0], 25, True), ([0, 1, 0], 25, False), ([0, 1, 2, 0], 19, False)])
def test_eco_suggestion_requires_same_work_and_original_reserve(monkeypatch, profiles, served, battery, accepted):
    data = request_data()
    data.update(goals=be.DEFAULT_GOALS, plan_context='plan-v1', expected_energy_model=energy.energy_metadata())
    before = {'order': [0, 1, 2, 0], 'total_energy_kwh': 90, 'battery_capacity_kwh': 80,
              'battery_depleted': True, 'battery_reserve_ok': False, 'battery_remaining_pct': -12.5, 'total_time_s': 1000}
    data['results'] = {'optimal': before}
    original = deepcopy(data)
    patch = [{'vehicle_id': 1, 'driving_style_override': 'eco'}]
    monkeypatch.setattr(be, 'fix_candidates', lambda *a: [('vehicle_style_overrides', patch)])
    def solve(candidate):
        assert candidate['goals']['battery_reserve_pct'] == 20
        assert candidate['precomputed'] == data['precomputed']
        assert candidate['vehicles'][0]['effective_driving_style'] == 'eco'
        return {'results': {'optimal': {**before, 'order': served, 'total_energy_kwh': 60,
            'battery_depleted': False, 'battery_reserve_ok': battery >= 20, 'battery_remaining_pct': battery}}}
    monkeypatch.setattr(be, 'optimize_payload', solve)
    suggestion = be.suggest_fix(data)
    assert bool(suggestion['fix']) is accepted
    assert data == original
    if accepted:
        pending = suggestion['pending']
        client = be.app.test_client()
        current = client.post('/api/chat', json={'message': 'yes', 'pending': pending,
                                               'form_state': {'plan_context': 'plan-v1'}}).json
        assert current['changes'][0]['value'] == patch
        stale = client.post('/api/chat', json={'message': 'yes', 'pending': pending,
                                             'form_state': {'plan_context': 'plan-v2'}}).json
        assert not stale['changes'] and 'out of date' in stale['message']
