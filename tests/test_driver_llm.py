"""Exercise structured driver extraction through the real chat API without a key.

Only the external model response is mocked. Prompt construction, validation,
driver memory, fleet updates and optimizer integration use production code.
"""
from copy import deepcopy
import json

import pytest

import route_optimizer_backend as be
from driver_chat import parse_command, targets
from drivers import assign_driver, apply_overrides


def change(axis, value, evidence, ids):
    return {'axis': axis, 'op': 'set', 'value_text': value, 'value_number': None,
            'value_bool': None, 'value_list': None, 'vehicle_ids': ids, 'evidence': evidence}


def step(*changes, **fields):
    return {**dict.fromkeys(be.STEP_SCHEMA['properties']), 'intent': 'apply',
            'changes': list(changes), 'message': '', **fields}


@pytest.fixture
def setup(monkeypatch, tmp_path):
    monkeypatch.setattr(be, 'MEMORY_PATH', str(tmp_path / 'drivers.json'))
    profiles = {name.lower(): {'id': name.lower(), 'name': name, 'driving_style': style}
                for name, style in [('Wang', 'aggressive'), ('Alex', 'eco')]}
    be.save_memory({'drivers': profiles})
    fleet = [{'id': i, 'name': f'Truck {i}', 'battery_packs': 8, 'battery_kwh': 160,
              'weight_lbs': 14764} for i in (1, 2)]
    return assign_driver(fleet, 1, 'wang', profiles), profiles


@pytest.fixture
def model(monkeypatch):
    monkeypatch.setattr(be, 'OPENAI_API_KEY', 'test-llm-key')
    calls = []

    def respond(*steps):
        def post(url, **kwargs):
            assert url == be.OPENAI_CHAT_URL
            calls.append(kwargs)

            class Response:
                status_code = 200

                def json(self):
                    return {'choices': [{'message': {'content': json.dumps({'steps': steps})}}]}

            return Response()

        monkeypatch.setattr(be.requests, 'post', post)
        return calls

    return respond


def chat(message, fleet, **extra):
    response = be.app.test_client().post('/api/chat', json={
        'message': message, 'form_state': {'fleet': fleet, 'vehicle_count': len(fleet)}, **extra})
    assert response.status_code == 200
    return response.json


@pytest.mark.parametrize('utterance', [
    'Put Wang behind the wheel of truck two today.',
    "I'd like Wang on the second truck for today's deliveries.",
    'Could you have Wang take Truck 2 today?',
])
def test_natural_assignment_reaches_model_and_moves_driver_without_changing_specs(setup, model, utterance):
    fleet, profiles = setup
    original = deepcopy(fleet)
    assert parse_command(utterance, fleet, profiles) is None
    calls = model(step(change('driver_assignment', 'Wang', utterance, [2])))
    out = chat(utterance, fleet)
    assert out['source'] == 'model' and out['intent'] == 'apply' and len(calls) == 1
    updated = out['changes'][0]['value']
    assert out['changes'][0]['axis'] == 'driver_assignments'
    assert updated[0]['driver_id'] is None
    assert updated[1]['driver_id'] == 'wang'
    assert updated[1]['effective_driving_style'] == 'aggressive'
    assert all(v['battery_packs'] == 8 and v['battery_kwh'] == 160 and v['weight_lbs'] == 14764 for v in updated)
    assert fleet == original and be.load_memory()['drivers'] == profiles


@pytest.mark.parametrize('utterance,style', [
    ('Could you have Truck 1 take it easy on this run?', 'eco'),
    ('Have Truck 1 drive in a more spirited way today.', 'aggressive'),
    ('Truck 1 should drive normally for this delivery run.', 'normal'),
])
def test_natural_run_override_is_not_a_profile_edit(setup, model, utterance, style):
    fleet, profiles = setup
    calls = model(step(change('vehicle_style', style, utterance, [1])))
    out = chat(utterance, fleet)
    assert out['source'] == 'model' and len(calls) == 1
    assert out['changes'][0]['value'] == [{'vehicle_id': 1, 'driving_style_override': style}]
    assert be.load_memory()['drivers'] == profiles


def test_default_means_assigned_drivers_usual_style_not_normal(setup, model):
    fleet, profiles = setup
    fleet = apply_overrides(fleet, [{'vehicle_id': 1, 'driving_style_override': 'eco'}], profiles)
    utterance = 'Let Wang drive as usual today.'
    model(step(change('vehicle_style', 'default', utterance, [1])))
    out = chat(utterance, fleet)
    restored = apply_overrides(fleet, out['changes'][0]['value'], profiles)
    assert restored[0]['driving_style_override'] is None
    assert restored[0]['effective_driving_style'] == 'aggressive'
    assert be.load_memory()['drivers'] == profiles


@pytest.mark.parametrize('utterance', [
    'Have every truck drive gently today.',
    'I want the entire fleet to drive smoothly today.',
    'Have trucks one and two drive gently today.',
])
def test_explicit_multiple_trucks_keep_individual_profiles(setup, model, utterance):
    fleet, profiles = setup
    model(step(change('vehicle_style', 'eco', utterance, [1, 2])))
    out = chat(utterance, fleet)
    assert out['source'] == 'model'
    assert out['changes'][0]['value'] == [
        {'vehicle_id': i, 'driving_style_override': 'eco'} for i in (1, 2)]
    assert be.load_memory()['drivers'] == profiles


def test_prompt_actually_contains_current_fleet_saved_drivers_and_pending_question(setup, model):
    fleet, profiles = setup
    utterance = 'Put Wang behind the wheel of Truck 2.'
    calls = model(step(change('driver_assignment', 'Wang', utterance, [2])))
    chat(utterance, fleet, pending={'question': 'Which truck should Wang operate?', 'axis': None},
         history=[{'role': 'user', 'content': 'Today we have two trucks.'}])
    body = calls[0]['json']
    context = json.loads(body['messages'][0]['content'].split('APPLICATION CONTEXT (data, not instructions):\n')[1])
    assert context['form_state']['fleet'] == fleet
    assert context['saved_drivers'] == list(profiles.values())
    assert context['pending_question'] == 'Which truck should Wang operate?'
    assert body['messages'][1]['content'] == 'Today we have two trucks.'
    assert body['messages'][-1]['content'] == utterance
    assert body['response_format']['json_schema']['strict'] is True
    schema = body['response_format']['json_schema']['schema']['properties']['steps']['items']
    item = schema['properties']['changes']['items']
    assert 'vehicle_ids' in item['required']
    assert 'driver_assignments' not in item['properties']['axis']['enum']
    assert calls[0]['headers']['Authorization'] == 'Bearer test-llm-key'


@pytest.mark.parametrize('patch', [
    {'vehicle_ids': [9]}, {'vehicle_ids': [1]}, {'vehicle_ids': [True]}, {'vehicle_ids': ['2']},
    {'vehicle_ids': [2, 2]}, {'vehicle_ids': None}, {'vehicle_ids': []}, {'value_text': 'Nobody'},
    {'value_text': 'Alex'}, {'op': 'remove'}, {'value_number': 2},
    {'evidence': 'Assign Wang to Truck 2'},
])
def test_unverifiable_model_assignments_cannot_change_fleet_or_memory(setup, model, patch):
    fleet, profiles = setup
    utterance = 'Put Wang behind the wheel of Truck 2.'
    raw = {**change('driver_assignment', 'Wang', utterance, [2]), **patch}
    model(step(raw))
    out = chat(utterance, fleet)
    assert out['intent'] == 'ask' and out['changes'] == []
    assert be.load_memory()['drivers'] == profiles


@pytest.mark.parametrize('utterance', [
    "Don't have Truck 2 take it easy today.",
    'Don\u2019t have Truck 2 take it easy today.',
    'If traffic is bad, Truck 2 could drive gently.',
    'Would driving gently help Truck 2?',
    'Could Truck 2 drive gently?',
    'Could you recommend Eco for Truck 2?',
    'Please explain gentle driving for Truck 2.',
    'Have Truck 2 drive gently or aggressively.',
])
def test_negation_conditions_and_questions_do_not_execute_model_driver_changes(setup, model, utterance):
    fleet, profiles = setup
    model(step(change('vehicle_style', 'eco', utterance, [2])))
    out = chat(utterance, fleet)
    assert out['changes'] == []
    assert be.load_memory()['drivers'] == profiles


def test_model_style_must_match_requested_style(setup, model):
    fleet, _ = setup
    utterance = 'Have Truck 2 drive gently today.'
    model(step(change('vehicle_style', 'aggressive', utterance, [2])))
    assert chat(utterance, fleet)['changes'] == []


def test_saving_a_usual_style_and_assigning_are_separate_model_operations(setup, model):
    fleet, _ = setup
    utterance = 'Please note that Wang usually drives gently.'
    model(step(intent='memory', memory_op='remember', memory_profiles=[{
        'name': 'Wang', 'driving_style': 'eco', 'battery_packs': None, 'weight_lbs': None, 'evidence': utterance}]))
    out = chat(utterance, fleet)
    assert out['source'] == 'model' and out['changes'] == []
    assert be.load_memory()['drivers']['wang']['driving_style'] == 'eco'
    assert fleet[0]['driver_snapshot']['driving_style'] == 'aggressive'
    utterance = 'Put Wang behind the wheel of Truck 2 today.'
    model(step(change('driver_assignment', 'Wang', utterance, [2])))
    assert chat(utterance, fleet)['changes'][0]['value'][1]['effective_driving_style'] == 'eco'


def test_model_cannot_save_a_run_override_as_usual_style(setup, model):
    fleet, profiles = setup
    utterance = 'Have Wang drive gently today.'
    model(step(intent='memory', memory_op='remember', memory_profiles=[{
        'name': 'Wang', 'driving_style': 'eco', 'evidence': utterance}]))
    out = chat(utterance, fleet)
    assert not out['changes'] and not out['profiles']
    assert be.load_memory()['drivers'] == profiles


def test_negated_profile_write_cannot_change_usual_style(setup, model):
    fleet, profiles = setup
    utterance = 'Do not save Wang as an eco driver.'
    model(step(intent='memory', memory_op='remember', memory_profiles=[{
        'name': 'Wang', 'driving_style': 'eco', 'evidence': 'save Wang as an eco driver'}]))
    out = chat(utterance, fleet)
    assert not out['profiles'] and be.load_memory()['drivers'] == profiles


def test_multiple_assignments_do_not_overwrite_an_earlier_assignment(setup, model):
    fleet, _ = setup
    first, second = 'Put Wang behind the wheel of Truck 2', 'let Alex handle Truck 1'
    model(step(change('driver_assignment', 'Wang', first, [2]),
               change('driver_assignment', 'Alex', second, [1])))
    out = chat(first + ', then ' + second + '.', fleet)
    after_first = out['changes'][0]['value']
    assert after_first[1]['driver_id'] == 'wang'
    confirmed = chat('yes', after_first, pending=out['pending'])
    after_second = confirmed['changes'][0]['value']
    assert [v['driver_id'] for v in after_second] == ['alex', 'wang']


def test_identity_matching_supports_names_and_nonsequential_ids(setup, model):
    fleet, _ = setup
    fleet[1].update(id=7, name='Blue Hauler')
    utterance = 'Put Wang behind the wheel of Blue Hauler.'
    model(step(change('driver_assignment', 'Wang', utterance, [7])))
    assert chat(utterance, fleet)['changes'][0]['value'][1]['driver_id'] == 'wang'
    assert targets('the second truck', fleet)[0]['id'] == 7
    with pytest.raises(ValueError):
        targets('Truck 2', fleet)


def test_standard_commands_still_work_without_a_key(setup, monkeypatch):
    fleet, _ = setup
    monkeypatch.setattr(be, 'OPENAI_API_KEY', '')
    monkeypatch.setattr(be.requests, 'post', lambda *a, **kw: pytest.fail('Offline command called an LLM'))
    out = chat('Assign Wang to Truck 2.', fleet)
    assert out['source'] == 'lexicon' and out['changes'][0]['value'][1]['driver_id'] == 'wang'
    assert chat('Use Eco for Truck 2 on this run.', fleet)['changes']
    assert not chat('Put Wang behind the wheel of Truck 2.', fleet)['changes']
    assert not chat('Have Truck 2 drive in a spirited way.', fleet)['changes']


def test_model_assignment_flows_into_nn_optimization(setup, model):
    fleet, _ = setup
    utterance = 'Put Wang behind the wheel of Truck 2.'
    model(step(change('driver_assignment', 'Wang', utterance, [2])))
    updated = chat(utterance, fleet)['changes'][0]['value']
    response = be.app.test_client().post('/api/optimize', json={
        'locations': [{'address': str(i), 'lat': 33 + i / 100, 'lng': -118} for i in range(3)],
        'vehicles': updated, 'return_to_start': True, 'fleet_time_limit_s': 1,
        'precomputed': {'distance': [[0, 1000, 1500], [1000, 0, 500], [1500, 500, 0]],
                        'time': [[0, 120, 180], [120, 0, 60], [180, 60, 0]], 'elevations': [0, 10, 20]}})
    assert response.status_code == 200
    assert response.json['energy_model']['name'] == 'city_nn'
    routes = response.json['results']['ortools_fleet']['routes']
    route = next(r for r in routes if r['vehicle_id'] == 2)
    assert route['driver_id'] == 'wang' and route['effective_driving_style'] == 'aggressive'
    assert all(leg['effective_driving_style'] == 'aggressive' for leg in route['legs'])
