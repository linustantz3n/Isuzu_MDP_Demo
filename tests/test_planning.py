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


def test_depletion_marks_the_leg_and_the_share_of_it_driven():
    legs = [{'energy_kwh': e, 'distance_m': 10000, 'time_s': 600, 'from': {}, 'to': {}} for e in (30, -2, 60)]
    assert be.battery_depletion(legs, 100) is None              # 88 kWh in all
    d = be.battery_depletion(legs, 58, depart_s=[0, 2400, 4800])
    assert d['leg_index'] == 2 and d['fraction'] == pytest.approx(0.5)   # 28 used, 30 of 60 left
    assert d['distance_into_leg_m'] == 5000 and d['at_s'] == 4800 + 300
    assert d['from_depot'] is False and d['route_energy_kwh'] == 88


def test_a_fleet_route_says_where_its_battery_runs_out(ctx):
    fleet = be.solve_fleet(ctx, [truck(0, 3), truck(1, 3)], be.DEFAULT_GOALS, time_limit_s=1)
    for r in fleet['routes']:
        assert (r['battery_depletion'] is not None) == r['battery_depleted']
        if r['battery_depletion']:
            leg = r['legs'][r['battery_depletion']['leg_index']]
            assert r['battery_depletion']['to'] == leg['to']


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


# A goal request the model filed as a form change: no form value survives, so
# the goal update (or a reserve said outright) is the request.
def padding(axis, evidence):
    return {'axis': axis, 'op': 'set', 'value_text': None, 'value_number': None,
            'value_bool': None, 'value_list': None, 'evidence': evidence}


def run_turn(raw, utterance):
    return be.run_steps([raw], utterance, None, 'model', FORM)


def test_a_reserve_filed_as_empty_pack_changes_still_sets_the_reserve():
    u = 'keep at least 20% battery in each truck'
    raw = {'intent': 'apply', 'changes': [padding('battery_packs', u)] * 3,
           'goal_update': goal_raw(energy='high', battery_reserve_pct=20, evidence=u)}
    out = run_turn(raw, u)
    assert out['intent'] == 'goals' and out['changes'][0]['value']['battery_reserve_pct'] == 20
    assert out['changes'][0]['value']['energy'] == 'high'
    # No goal update at all: the words carry the reserve on their own.
    out = run_turn({'intent': 'apply', 'changes': [padding('battery_packs', u)]}, u)
    assert out['intent'] == 'goals' and out['changes'][0]['value']['battery_reserve_pct'] == 20


def test_running_late_filed_as_unnamed_priority_stops_moves_the_goals():
    u = "We're running late, get everyone back sooner"
    stop = dict(padding('priority_stops', 'get everyone back sooner'), op='add', value_list=['Stanton'])
    raw = {'intent': 'apply', 'changes': [stop],
           'goal_update': goal_raw(time='high', balance='medium', evidence="We're running late")}
    out = run_turn(raw, u)
    assert out['intent'] == 'goals' and out['changes'][0]['value']['time'] == 'high'


def test_a_real_form_change_that_fails_is_still_a_question():
    u = 'add a stop at Fooville'
    stop = dict(padding('addresses', 'Fooville'), op='add', value_list=['Springfield'])
    assert run_turn({'intent': 'apply', 'changes': [stop]}, u)['intent'] == 'ask'


@pytest.mark.parametrize('utterance, pct', [
    ('keep at least 20% battery in each truck', 20), ('a 15% reserve on every truck', 15),
    ('never go below 10 percent charge', 10), ('每辆车至少保留20%电量', 20),
    ('cut energy use by 20%', None), ('leave 20% of the stops for tomorrow', None), ('use 3 trucks', None)])
def test_reserve_read_from_the_words(utterance, pct):
    said = be.reserve_from_words(utterance)
    assert (said and said['battery_reserve_pct']) == pct


def test_a_reserve_quoted_from_earlier_in_the_chat_uses_this_messages_words():
    u = 'keep 20% battery in each truck'
    raw = {'intent': 'goals', 'changes': [],
           'goal_update': goal_raw(energy='high', battery_reserve_pct=20, evidence='Battery is tight today')}
    out = run_turn(raw, u)
    assert out['changes'] and out['changes'][0]['value']['battery_reserve_pct'] == 20


def test_a_question_about_a_reserve_sets_nothing():
    u = 'what does a 20% reserve do to each truck?'
    out = run_turn({'intent': 'goals', 'changes': [], 'goal_update': goal_raw()}, u)
    assert out['intent'] == 'goals' and not out['changes']
    out = run_turn({'intent': 'out_of_scope', 'changes': []}, 'could you keep 20% battery in each truck?')
    assert out['changes'][0]['value']['battery_reserve_pct'] == 20


# ---- traffic and driving style (R4) -------------------------------------------

@pytest.mark.parametrize('text, want', [
    ('we leave at 7:30', '07:30'), ('trucks go out at 6pm', '18:00'), ('leave at 7', '07:00'),
    ('depart 5 a.m.', '05:00'), ('早上7点半出发', '07:30'), ('下午3点出发', '15:00'),
    ('use live traffic', 'now'), ('live traffic, we leave at 8:15', '08:15'), ('at noon', '12:00'),
    ('use 2 trucks', None), ('unload 20 min', None), ('set temperature to 35 degrees', None),
    ('keep 20% battery', None), ('at 25:00', None), ('leave at 25:00 with live traffic', None), ('25点出发', None)])
def test_departure_is_read_from_the_words(text, want):
    assert be.departure_in(text) == want


def test_departure_is_the_next_time_the_depot_clock_reads_it(monkeypatch):
    monkeypatch.setattr(be, 'get_utc_offset', lambda lat, lng, ts: (-8 * 3600, 'America/Los_Angeles'))
    now = 1_762_000_000                                  # 12:26:40 UTC = 04:26:40 at UTC-8
    soon = be.departure_info('07:30', {'lat': 0, 'lng': 0}, now)
    assert soon['departure_ts'] - now == 3 * 3600 + 3 * 60 + 20 and soon['time_zone'] == 'America/Los_Angeles'
    gone = be.departure_info('04:00', {'lat': 0, 'lng': 0}, now)  # passed: tomorrow
    assert 0 < gone['departure_ts'] - now < 24 * 3600 and gone['departure_ts'] - now > 23 * 3600
    assert be.departure_info('now', {'lat': 0, 'lng': 0}, now)['departure_ts'] == now


def test_traffic_asks_google_for_the_departure_and_uses_the_traffic_time(monkeypatch):
    seen = {}

    class Resp:
        def json(self):
            el = {'status': 'OK', 'distance': {'value': 1000}, 'duration': {'value': 100},
                  'duration_in_traffic': {'value': 160}}
            return {'status': 'OK', 'rows': [{'elements': [el, el]}, {'elements': [el, el]}]}
    monkeypatch.setattr(be.requests, 'get', lambda url, params=None, **kw: seen.update(params) or Resp())
    out = be.get_distance_matrix([{'lat': 0, 'lng': 0}, {'lat': 1, 'lng': 1}], departure_ts=1_800_000_000)
    assert seen['departure_time'] == 1_800_000_000 and out['time'][0][1] == 160


def test_driving_style_slows_only_cruising_legs_and_orders_the_energy():
    m = {'distance': [[0, 10000, 2000], [10000, 0, 1], [2000, 1, 0]],
         'time': [[0, 400, 400], [400, 0, 1], [400, 1, 0]]}       # 90 km/h, and an 18 km/h city leg
    eco = be.styled_matrices(m, 'eco')
    assert eco['time'][0][1] == round(400 / 0.9) and eco['time'][0][2] == 400
    assert be.styled_matrices(m, 'normal') is m
    for v in (25, 90):
        e = {st: be.calculate_energy_consumption(10, v * (be.STYLE_CRUISE_FACTOR[st] if v > be.CRUISE_KMH else 1),
                                                 20, 5.9, 0, 50, True, st)['energy_kwh'] for st in be.DRIVING_STYLES}
        assert e['eco'] < e['normal'] < e['aggressive']
    r = {st: be.truck_limits(4, driving_style=st)['rows'][1]['range_empty_mi'] for st in be.DRIVING_STYLES}
    assert r['eco'] > r['normal'] > r['aggressive']


def test_optimize_with_traffic_fetches_for_the_departure_and_styles_the_solve(monkeypatch, ctx):
    calls = []
    monkeypatch.setattr(be, 'get_distance_matrix', lambda locs, departure_ts=None: calls.append(departure_ts) or ctx['matrices'])
    monkeypatch.setattr(be, 'get_elevation_data', lambda locs: [0.0] * len(locs))
    monkeypatch.setattr(be, 'departure_info', lambda dep, depot, now=None: {
        'departure': dep, 'departure_ts': 1_800_000_000, 'departure_local': 'Mon 07:30', 'time_zone': 'X'})
    locs = [{'address': a, 'lat': 0, 'lng': 0} for a in ctx['locations']]
    client = be.app.test_client()

    def solve(style, traffic=None, pre=None):
        body = {'locations': locs, 'return_to_start': True, 'vehicles': [truck(0)], 'solver': 'held_karp',
                'energy_params': {'driving_style': style}, 'traffic': traffic, 'precomputed': pre}
        return client.post('/api/optimize', json=body).get_json()

    first = solve('normal', {'departure': '07:30'})
    assert calls == [1_800_000_000] and first['traffic']['departure'] == '07:30'
    pre = {**first['matrices'], 'elevations': first['elevations'], 'traffic': first['traffic']}
    again = solve('eco', {'departure': '07:30'}, pre)
    assert calls == [1_800_000_000]                                  # same traffic: no new Google call
    assert again['matrices']['time'] == first['matrices']['time']    # Google's times, not eco's
    assert again['results']['optimal']['total_energy_kwh'] < first['results']['optimal']['total_energy_kwh']
    solve('normal', None, pre)
    assert calls == [1_800_000_000, None]                            # traffic turned off: refetch


@pytest.mark.parametrize('value, utterance, want', [
    ('now', 'use live traffic', {'departure': 'now'}),
    ('07:30', 'we leave at 7:30', {'departure': '07:30'}),
    ('08:00', 'use live traffic', {'departure': 'now'}),            # code reads the words, not the model
    ('off', 'ignore traffic', {'departure': None})])
def test_traffic_change_takes_its_departure_from_the_message(value, utterance, want):
    raw = {'axis': 'traffic', 'op': 'set', 'value_text': value, 'value_number': None, 'value_bool': None,
           'value_list': None, 'evidence': utterance}
    change, reason = be.validate_change(raw, utterance, FORM)
    assert reason is None and change['value'] == want


def test_traffic_without_a_time_asks_when_the_trucks_leave():
    u = 'plan with traffic'
    raw = {'intent': 'apply', 'changes': [{'axis': 'traffic', 'op': 'set', 'value_text': 'on', 'value_number': None,
                                           'value_bool': None, 'value_list': None, 'evidence': u}]}
    out = run_turn(raw, u)
    assert out['intent'] == 'ask' and out['pending']['axis'] == 'traffic' and 'When do the trucks leave' in out['message']
    answer = be.lexicon_fast_path('7:30', out['pending'], FORM)
    assert answer['changes'][0]['value'] == {'departure': '07:30'}


@pytest.mark.parametrize('value, evidence, want', [
    ('eco', 'take it easy', 'eco'), ('aggressive', 'sporty mode', 'aggressive'), ('eco', '节能驾驶', 'eco'),
    ('aggressive', 'ludicrous mode', None), ('race', 'race mode', None)])
def test_a_driving_style_must_be_named_by_its_own_words(value, evidence, want):
    raw = {'axis': 'driving_style', 'op': 'set', 'value_text': value, 'value_number': None, 'value_bool': None,
           'value_list': None, 'evidence': evidence}
    change, reason = be.validate_change(raw, evidence, FORM)
    if want:
        assert change['value'] == want
    else:
        assert change is None and 'eco, normal, aggressive' in reason


@pytest.mark.parametrize('utterance, axis, want', [
    ('use live traffic', 'traffic', {'departure': 'now'}), ('ignore traffic', 'traffic', {'departure': None}),
    ('live traffic at 7:30', 'traffic', {'departure': '07:30'}), ('eco driving', 'driving_style', 'eco'),
    ('drive aggressively', 'driving_style', 'aggressive'), ('实时路况', 'traffic', {'departure': 'now'})])
def test_lexicon_sets_traffic_and_style(utterance, axis, want):
    out = be.lexicon_fast_path(utterance, None, FORM)
    assert out['source'] == 'lexicon' and out['changes'][0]['axis'] == axis and out['changes'][0]['value'] == want


def test_a_departure_time_next_to_another_value_goes_to_the_model():
    assert be.lexicon_fast_path('leave at 8:00 and drive aggressively', None, FORM) is None
    assert be.lexicon_fast_path('drive aggressively', None, FORM)['changes'][0]['value'] == 'aggressive'


def test_traffic_on_from_the_model_reads_the_departure_from_the_words():
    for value, u, want in (('on', 'use live traffic and eco driving', 'now'), ('on', 'we go at 6pm', '18:00')):
        raw = {'axis': 'traffic', 'op': 'set', 'value_text': value, 'value_number': None, 'value_bool': None,
               'value_list': None, 'evidence': u.split(' and ')[0]}
        change, reason = be.validate_change(raw, u, FORM)
        assert reason is None and change['value'] == {'departure': want}


# ---- proactive suggestions ----------------------------------------------------

def facts(**kw):
    route = {'vehicle_name': 'Vehicle 1', 'num_delivery_stops': 2, 'battery_depleted': False,
             'battery_remaining_pct': 50.0, 'total_energy_kwh': 40.0, 'battery_capacity_kwh': 80.0,
             'total_time_s': 3600, 'total_distance_mi': 30.0}
    routes = kw.pop('routes', [dict(route)])
    f = {'routes': routes, 'unserved': [], 'fleet': len(routes) > 1, 'min_left_pct': min(r['battery_remaining_pct'] for r in routes),
         'back_s': max(r['total_time_s'] for r in routes), 'mi': 0, 'kwh': 0,
         'short': [r for r in routes if r['battery_depleted']]}
    f.update(kw)
    return f, route


def test_the_most_pressing_problem_is_named():
    f, r = facts()
    assert be.plan_problem(f, be.DEFAULT_GOALS) is None
    dead = dict(r, battery_depleted=True, battery_remaining_pct=0.0, total_energy_kwh=103.0)
    f, _ = facts(routes=[dead])
    kind, text = be.plan_problem(f, be.DEFAULT_GOALS)
    assert kind == 'battery' and 'needs 103 of 80 kWh' in text
    f, _ = facts(routes=[dict(r, battery_remaining_pct=9.0)])
    assert be.plan_problem(f, be.DEFAULT_GOALS)[0] == 'thin'
    f, _ = facts(routes=[dict(r, total_time_s=2 * 3600), dict(r, vehicle_name='Vehicle 2', total_time_s=4 * 3600)])
    kind, text = be.plan_problem(f, be.DEFAULT_GOALS)
    assert kind == 'imbalance' and text.startswith('Vehicle 2 gets back 2h 00m after Vehicle 1')


def test_fixes_are_tried_weights_first_then_packs_then_a_truck():
    dead = {'total_energy_kwh': 103.0}
    f = {'short': [dead], 'routes': [dead]}
    data = {'vehicles': [truck(0), truck(1)], 'locations': [{}] * 6}
    cands = be.fix_candidates('battery', data, f, be.DEFAULT_GOALS)
    assert [a for a, _ in cands] == ['goals', 'battery_packs', 'battery_packs', 'vehicle_count']
    assert cands[0][1]['energy'] == 'high' and cands[0][1]['distance'] == 'low'
    assert [cands[1][1], cands[2][1]] == [7, 8]  # 103 kWh with a 15% margin: 103 / 17 → 7, and one up
    assert be.fix_candidates('imbalance', data, f, be.DEFAULT_GOALS) == [('goals', dict(be.DEFAULT_GOALS, balance='high'))]


def test_a_fix_counts_only_if_it_solves_the_problem():
    before, _ = facts(short=[{}], min_left_pct=0.0)
    fixed, _ = facts(short=[], min_left_pct=20.0)
    assert be.fixes_problem('battery', before, fixed) == ''
    assert be.fixes_problem('battery', before, dict(fixed, min_left_pct=14.0)) == 'still'   # too thin to count
    assert be.fixes_problem('battery', before, dict(fixed, unserved=[{}])) == 'more_unserved'
    late, _ = facts(back_s=4 * 3600)
    assert be.fixes_problem('imbalance', late, dict(late, back_s=4 * 3600 - 600)) == 'no_better'


def test_suggestion_on_the_demo_offers_a_tested_fix_as_a_yes_no_question(ctx):
    locs = [dict(loc, lat=0.0, lng=0.0) if isinstance(loc, dict) else {'address': loc, 'lat': 0.0, 'lng': 0.0}
            for loc in ctx['locations']]
    data = {'locations': locs, 'return_to_start': True, 'vehicles': [truck(0), truck(1)], 'solver': 'auto',
            'energy_params': {'temperature_c': 20}, 'goals': dict(be.DEFAULT_GOALS),
            'precomputed': {**ctx['matrices'], 'elevations': ctx['elevations']}}
    data['results'] = be.optimize_payload(dict(data, fleet_time_limit_s=2))['results']
    sug = be.suggest_fix(data)
    assert sug['kind'] == 'battery' and sug['fix']['axis'] == 'battery_packs'
    change = sug['pending']['queued'][0]
    assert change['axis'] == 'battery_packs' and change['evidence'] == sug['problem']
    assert sug['message'].endswith('Apply it? (yes / no)')
    # Nothing is suggested without the plan's matrices: no Google calls for a suggestion.
    assert be.suggest_fix(dict(data, precomputed=None)) is None
