"""Goals, constrained fleet routing, truck limits, breakdown re-planning, and
the chat gates in front of them. No API keys: matrices come from the saved
Anaheim scenario and the chat tests call the validators directly."""
import json
import os

import pytest

import route_optimizer_backend as be
from solvers import solve_tsp_held_karp, solve_vrp_constrained

SCENARIO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        'benchmark', 'scenarios', 'demo_anaheim.json')


@pytest.fixture(scope='module')
def ctx():
    sc = json.load(open(SCENARIO))
    n = len(sc['locations'])
    return {'matrices': {'distance': sc['distance_m'], 'time': sc['time_s']},
            'elevations': [0.0] * n, 'locations': sc['locations'],
            'temperature_c': 20.0, 'has_climate_control': True, 'unload_s': 1800.0}


def truck(i, packs=4):
    return {'id': i + 1, 'name': f'Vehicle {i + 1}', 'battery_packs': packs, 'battery_kwh': packs * 20,
            'weight_lbs': round(13000 - 4 * be.PACK_WEIGHT_LBS + packs * be.PACK_WEIGHT_LBS)}


# ---- goals → weights → cost matrix -------------------------------------------

def test_goal_weights_follow_the_level_table():
    assert be.goal_weights(be.DEFAULT_GOALS) == {'distance': 1.0, 'time': 0.0, 'energy': 0.0, 'balance': 0.0}
    w = be.goal_weights(be.normalize_goals({'distance': 'low', 'energy': 'high'}))
    assert w['energy'] == pytest.approx(0.8) and w['distance'] == pytest.approx(0.2)


def test_normalize_goals_drops_junk_and_never_turns_everything_off():
    g = be.normalize_goals({'distance': 'off', 'time': 'max', 'battery_reserve_pct': 99})
    assert g['distance'] == 'high'           # all off → back to distance
    assert g['time'] == 'off'                # unknown level ignored
    assert g['battery_reserve_pct'] == be.MAX_RESERVE_PCT


def test_default_goals_minimise_plain_distance(ctx):
    obj = be.objective_matrix(ctx['matrices'], None, be.DEFAULT_GOALS)
    assert obj['cost'] == ctx['matrices']['distance']
    assert obj['span_coef'] == 0


def test_priority_stop_is_served_first(ctx):
    obj = be.objective_matrix(ctx['matrices'], None, be.DEFAULT_GOALS, priority=[3])
    order = solve_tsp_held_karp(obj['cost'], return_to_start=True)['order']
    assert order[1] == 3
    plain = solve_tsp_held_karp(ctx['matrices']['distance'], return_to_start=True)['order']
    assert plain[1] != 3                     # it really moved


def test_balance_only_prices_the_finish_with_a_fleet(ctx):
    goals = be.normalize_goals({'distance': 'low', 'balance': 'high'})
    assert be.objective_matrix(ctx['matrices'], None, goals, num_vehicles=1)['span_coef'] == 0
    assert be.objective_matrix(ctx['matrices'], None, goals, num_vehicles=2)['span_coef'] > 0


# ---- constrained OR-Tools model ----------------------------------------------

def line(n):
    """Stops on a line, 1 km apart; depot at 0."""
    return [[abs(i - j) * 1000 for j in range(n)] for i in range(n)]


def test_energy_cap_drops_what_no_truck_can_reach():
    m = line(5)
    sol = solve_vrp_constrained(m, 1, energy=[m], energy_caps=[6000], optional=True, time_limit_s=1)
    assert sol['dropped'] == [4]             # 0→1→2→3→0 is 6 km; reaching 4 needs 8


def test_pickup_comes_before_its_delivery_on_the_same_truck():
    m = line(6)
    sol = solve_vrp_constrained(m, 2, time_matrix=m, pairs=[(5, 1)], optional=True, time_limit_s=1)
    route = next(r for r in sol['routes'] if 1 in r)
    assert 5 in route and route.index(5) < route.index(1)


def test_allowed_vehicles_keep_cargo_on_its_truck():
    m = line(5)
    sol = solve_vrp_constrained(m, 2, allowed={4: [1]}, time_limit_s=1)
    assert 4 in sol['routes'][1]


# ---- fleet routing with goals --------------------------------------------------

def test_fleet_default_serves_everything_and_maps_vehicles(ctx):
    fleet = be.solve_fleet(ctx, [truck(0), truck(1)], be.DEFAULT_GOALS, time_limit_s=1)
    served = sorted(i for r in fleet['routes'] for i in r['order'] if i)
    assert served == [1, 2, 3, 4, 5] and fleet['unserved'] == []
    assert [r['vehicle_index'] for r in fleet['routes']] == sorted(r['vehicle_index'] for r in fleet['routes'])


def test_battery_reserve_is_enforced_and_unreachable_stops_reported(ctx):
    goals = be.normalize_goals({'distance': 'high', 'battery_reserve_pct': 20})
    fleet = be.solve_fleet(ctx, [truck(0, 3), truck(1, 3)], goals, time_limit_s=2)
    assert all(r['battery_reserve_ok'] for r in fleet['routes'])
    assert [u['index'] for u in fleet['unserved']] == [1]    # Marina Del Rey: ~40 mi out


# ---- breakdown re-plan ---------------------------------------------------------

PLAN = [{'vehicle_index': 0, 'order': [0, 2, 1, 3, 0]}, {'vehicle_index': 1, 'order': [0, 5, 4, 0]}]


def test_breakdown_moves_stranded_stops_with_a_pickup_first(ctx):
    out = be.replan_breakdown(ctx, [truck(0, 9), truck(1, 9)], PLAN,
                              {'vehicle_index': 1, 'after_stop': 1}, be.DEFAULT_GOALS, time_limit_s=2)
    routes = {r['vehicle_index']: r for r in out['fleet']['routes']}
    broken = routes[1]
    assert broken['status'] == 'broken_down' and broken['order'] == [0, 5]
    rescuer = routes[0]
    kinds = dict(zip(rescuer['order'], rescuer['stop_kinds']))
    assert kinds[4] == 'rescue'
    assert rescuer['order'].index(5) < rescuer['order'].index(4)   # collect at the breakdown site first
    assert rescuer['stop_kinds'][rescuer['order'].index(5)] == 'pickup'
    assert rescuer['eta_s'] == sorted(rescuer['eta_s'])
    assert out['fleet']['unserved'] == []


def test_breakdown_keeps_stops_that_were_already_unserved(ctx):
    plan = [{'vehicle_index': 0, 'order': [0, 2, 3, 0]}, {'vehicle_index': 1, 'order': [0, 5, 4, 0]}]
    out = be.replan_breakdown(ctx, [truck(0, 9), truck(1, 9)], plan,
                              {'vehicle_index': 1, 'after_stop': 1}, be.DEFAULT_GOALS, time_limit_s=2)
    assert [u['index'] for u in out['fleet']['unserved'] if u.get('before_breakdown')] == [1]


def test_breakdown_after_the_last_stop_needs_no_replan(ctx):
    out = be.replan_breakdown(ctx, [truck(0), truck(1)], PLAN,
                              {'vehicle_index': 1, 'after_stop': 2}, be.DEFAULT_GOALS)
    assert out['nothing']


def test_single_truck_breakdown_sends_a_spare(ctx):
    plan = [{'vehicle_index': 0, 'order': [0, 4, 5, 3, 2, 0]}]
    out = be.replan_breakdown(ctx, [truck(0, 9)], plan, {'vehicle_index': 0, 'after_stop': 2},
                              be.DEFAULT_GOALS, time_limit_s=2)
    spare = [r for r in out['fleet']['routes'] if r['status'] == 'spare']
    assert spare and set(spare[0]['order']) >= {3, 2}


# ---- truck limits --------------------------------------------------------------

def test_three_pack_limits():
    lim = be.truck_limits(3)
    assert lim['battery_kwh'] == 60 and lim['empty_weight_lbs'] == 6559 and lim['max_payload_lbs'] == 12941
    for row in lim['rows']:
        assert 0 < row['range_loaded_mi'] < row['range_empty_mi']


def test_limits_check_todays_longest_route():
    plan = {'vehicles': [{'number': 1, 'name': 'Vehicle 1', 'energy_kwh': 102.7}]}
    lim = be.truck_limits(3, plan=plan)
    assert not lim['plan_check']['fits'] and lim['plan_check']['min_packs'] == 6


# ---- chat gates ----------------------------------------------------------------

FORM = {'addresses': ['3301 E Imperial Hwy, Brea, CA', '11232 Knott Ave, Stanton, CA'], 'vehicle_count': 2,
        'battery_packs': 4, 'temperature_c': 20, 'has_climate_control': True, 'solver': 'auto',
        'goals': dict(be.DEFAULT_GOALS),
        'plan': {'vehicles': [{'number': 1, 'vehicle_index': 0, 'name': 'Vehicle 1', 'driver': 'Wang', 'stops': 3},
                              {'number': 2, 'vehicle_index': 1, 'name': 'Vehicle 2', 'driver': '', 'stops': 2}]}}


def goal_raw(**kw):
    raw = {k: None for k in be.GOAL_KEYS}
    raw.update(battery_reserve_pct=None, reset=False, evidence='')
    raw.update(kw)
    return raw


def test_goals_change_needs_the_operators_words():
    u = 'battery is tight today, keep 20% in every truck'
    out = be.outcome_goals(goal_raw(energy='high', battery_reserve_pct=20, evidence='battery is tight today'),
                           u, FORM, 'model')
    assert out['changes'][0]['value']['energy'] == 'high'
    assert out['changes'][0]['value']['battery_reserve_pct'] == 20
    assert be.outcome_goals(goal_raw(energy='high', evidence='save power'), u, FORM, 'model')['intent'] == 'ask'


def test_reserve_number_must_be_in_the_message():
    out = be.outcome_goals(goal_raw(battery_reserve_pct=30, evidence='battery is tight'),
                           'battery is tight', FORM, 'model')
    assert not out['changes'] and out['rejected']


def test_raise_nudges_one_level_and_a_question_explains():
    out = be.outcome_goals(goal_raw(time='raise', evidence='a bit faster'), 'a bit faster please', FORM, 'model')
    assert out['changes'][0]['value']['time'] == 'low'
    assert 'weights' in be.outcome_goals(None, 'how do the weights work?', FORM, 'model')['message']


def disruption_raw(**kw):
    raw = {'vehicle_number': None, 'driver': None, 'after_stop': None, 'at_minute': None,
           'spare_truck': False, 'evidence': ''}
    raw.update(kw)
    return raw


def test_disruption_never_guesses_the_truck():
    out = be.outcome_disruption(disruption_raw(vehicle_number=1, after_stop=1, evidence='A truck'),
                                'A truck broke down', FORM, 'model')
    assert out['intent'] == 'ask' and out.get('action') is None


def test_disruption_by_driver_and_minutes():
    u = "Wang's truck crashed an hour in"
    out = be.outcome_disruption(disruption_raw(driver='Wang', after_stop=0, at_minute=60, evidence='an hour in'),
                                u, FORM, 'model')
    assert out['action'] == {**out['action'], 'vehicle_index': 0, 'at_minute': 60, 'after_stop': None,
                             'spare_truck': False}


def test_disruption_after_the_last_stop_is_not_a_breakdown():
    u = 'truck 2 broke down after 2 stops'
    out = be.outcome_disruption(disruption_raw(vehicle_number=2, after_stop=2, evidence=u), u, FORM, 'model')
    assert out['action'] is None and 'nothing to re-plan' in out['message']


@pytest.mark.parametrize('utterance', ['truck 2 broke down after 2 stops', 'leave the battery at 20%',
                                       'what are the limits of 3 packs'])
def test_lexicon_leaves_goals_limits_and_breakdowns_to_the_model(utterance):
    assert be.lexicon_fast_path(utterance, None, FORM) is None


def test_resolve_phrases_run_the_optimizer():
    for u in ('re-optimize', 'run the optimization for me', '重新优化'):
        assert be.lexicon_fast_path(u, None, FORM)['action']['type'] == 'resolve'


def test_solver_switch_needs_a_solver_word():
    raw = {'axis': 'solver', 'op': 'set', 'value_text': 'nearest_neighbor', 'value_number': None,
           'value_bool': None, 'value_list': None, 'evidence': 'get everyone back sooner'}
    change, reason = be.validate_change(raw, "we're late, get everyone back sooner", FORM)
    assert change is None and reason


def test_priority_stop_must_be_on_the_form():
    raw = {'axis': 'priority_stops', 'op': 'add', 'value_text': None, 'value_number': None,
           'value_bool': None, 'value_list': ['Brea'], 'evidence': 'deliver to Brea first'}
    change, _ = be.validate_change(raw, 'deliver to Brea first', FORM)
    assert change['value'] == ['3301 E Imperial Hwy, Brea, CA']
    raw.update(value_list=['deliver to Brea first'])          # the model passing the whole phrase
    assert be.validate_change(raw, 'deliver to Brea first', FORM)[0]['value'] == ['3301 E Imperial Hwy, Brea, CA']
    raw.update(value_list=['Irvine'], evidence='Irvine first')
    assert be.validate_change(raw, 'Irvine first', FORM)[0] is None


def test_limits_numbers_sent_as_form_changes_still_answer_the_question():
    u = "how far can a 5-pack truck go when it's -5 C?"
    raw = {'intent': 'limits', 'limits_query': None, 'changes': [
        {'axis': 'battery_packs', 'op': 'set', 'value_text': '5', 'value_number': None, 'value_bool': None,
         'value_list': None, 'evidence': '5-pack truck'},
        {'axis': 'temperature_c', 'op': 'set', 'value_text': None, 'value_number': -5, 'value_bool': None,
         'value_list': None, 'evidence': '-5 C'}]}
    out = be.run_steps([raw], u, None, 'model', FORM)
    assert out['limits']['packs'] == 5 and out['limits']['temperature_c'] == -5
    assert out['changes'] == []              # a hypothetical truck, not a form change


def test_a_driver_named_in_the_message_picks_the_truck_even_if_the_model_missed_it():
    u = "Wang's truck crashed an hour in"
    out = be.outcome_disruption(disruption_raw(at_minute=60, evidence='an hour in'), u, FORM, 'model')
    assert out['action']['vehicle_index'] == 0


def test_reset_is_evidenced_by_the_message_itself():
    form = dict(FORM, goals={**be.DEFAULT_GOALS, 'energy': 'high'})
    out = be.outcome_goals(goal_raw(reset=True, evidence=''), 'reset the goals', form, 'model')
    assert out['changes'][0]['value'] == be.DEFAULT_GOALS


@pytest.mark.parametrize('raw, want', [('depot in Anaheim CA', 'Anaheim CA'), ('Depot: 500 S State St', '500 S State St'),
                                       ('Depot Rd 5, Anaheim, CA', 'Depot Rd 5, Anaheim, CA')])
def test_depot_value_drops_a_leading_depot_in(raw, want):
    assert be.check_value('depot', 'set', raw)[0] == want


def test_a_number_sent_as_text_is_still_a_number():
    raw = {'axis': 'battery_packs', 'op': 'set', 'value_text': '6', 'value_number': None,
           'value_bool': None, 'value_list': None, 'evidence': '6 battery packs'}
    change, _ = be.validate_change(raw, 'Give every truck 6 battery packs', FORM)
    assert change['value'] == 6


def test_a_priority_stop_the_operator_never_named_is_refused():
    raw = {'axis': 'priority_stops', 'op': 'add', 'value_text': None, 'value_number': None,
           'value_bool': None, 'value_list': ['Stanton'], 'evidence': 'get everyone back sooner'}
    change, reason = be.validate_change(raw, "We're running late, get everyone back sooner", FORM)
    assert change is None and reason


def test_a_packs_question_filed_as_solver_advice_is_answered_as_limits():
    raw = {'intent': 'recommend', 'changes': [], 'advice_topic': 'which'}
    out = be.run_steps([raw], "can 4 packs handle today's routes?", None, 'model', FORM)
    assert out['intent'] == 'limits' and out['limits']['packs'] == 4
    out = be.run_steps([raw], 'which solver handles 25 stops?', None, 'model', FORM)
    assert out['intent'] == 'recommend'


@pytest.mark.parametrize('extracted', [{'driver': "Wang's truck"}, {'vehicle_number': 1}, {}])
def test_a_named_driver_wins_over_a_bad_extraction(extracted):
    u = "Wang's truck crashed an hour in"
    raw = disruption_raw(at_minute=60, evidence='an hour in', **extracted)
    assert be.outcome_disruption(raw, u, FORM, 'model')['action']['vehicle_index'] == 0
