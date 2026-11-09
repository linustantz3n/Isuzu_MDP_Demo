"""
Offline tests for incident replanning (/api/replan_incidents).

Checks that a second run on "live" data (accidents) respects the plan the
trucks were loaded for: the stop order never changes, and only the leg an
accident sits on is rerouted. Google's Routes API and the crash history are
replaced with synthetic data, so no API key or network is needed.

Run from the repo root:
    python -m unittest discover tests
"""
import math
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import route_optimizer_backend as backend  # noqa: E402

# A square route, ~18 km per side: Depot -> A -> B -> C -> Depot
DEPOT = {"lat": 33.80, "lng": -118.00, "address": "Depot"}
A = {"lat": 33.80, "lng": -117.80, "address": "A"}
B = {"lat": 33.95, "lng": -117.80, "address": "B"}
C = {"lat": 33.95, "lng": -118.00, "address": "C"}
LEGS = [{"from": f, "to": t} for f, t in [(DEPOT, A), (A, B), (B, C), (C, DEPOT)]]

# Historical crashes: 5 near the middle of A -> B, 3 near the middle of B -> C
CRASHES = ([(33.875 + k * 0.001, -117.80, 2021, 1, "NORTH RD") for k in range(-2, 3)]
           + [(33.95, -117.90 + k * 0.001, 2022, 1, "WEST RD") for k in range(-1, 2)])


def fake_crash_grid(crashes=CRASHES):
    grid = {}
    for crash in crashes:
        grid.setdefault(backend._grid_cell(crash[0], crash[1]), []).append(crash)
    return grid


def _route(points):
    length = sum(backend._distance_m(a, b) for a, b in zip(points, points[1:]))
    return {"distance_m": round(length), "time_s": round(length / 15), "path": points}


def fake_compute_leg_routes(origin, destination, vias=None, alternatives=False):
    """Straight line by default; the alternative bows 3 km to the side mid-leg."""
    points = [origin, *(vias or []), destination]
    routes = [_route(points)]
    if alternatives and not vias:
        mid = {"lat": (origin['lat'] + destination['lat']) / 2,
               "lng": (origin['lng'] + destination['lng']) / 2}
        dx, dy = backend._to_local_m(destination, origin)
        norm = math.hypot(dx, dy)
        routes.append(_route([origin, backend._offset_point(mid, -dy / norm * 3000, dx / norm * 3000),
                              destination]))
    return routes


def run_replan(legs, crashes, num_accidents):
    with mock.patch.object(backend, 'compute_leg_routes', fake_compute_leg_routes), \
         mock.patch.object(backend, '_crash_grid', fake_crash_grid(crashes)):
        response = backend.app.test_client().post(
            '/api/replan_incidents', json={"legs": legs, "num_accidents": num_accidents})
    result = response.get_json()
    assert result['status'] == 'ok', result.get('message')
    return result


class ReplanningTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.result = run_replan(LEGS, CRASHES, 2)

    def test_hotspots_ranked_by_crash_count(self):
        accidents = self.result['accidents']
        self.assertEqual([(a['id'], a['leg_index'], a['crash_count']) for a in accidents],
                         [(1, 1, 5), (2, 2, 3)])

    def test_scenario_for_every_combination(self):
        self.assertEqual(sorted(self.result['scenarios']), ["1", "1,2", "2"])

    def test_stop_order_unchanged(self):
        """Every rerouted leg still runs between the same two stops, in plan order."""
        for key, legs in self.result['scenarios'].items():
            indices = [leg['leg_index'] for leg in legs]
            self.assertEqual(indices, sorted(set(indices)), key)
            for leg in legs:
                planned = LEGS[leg['leg_index']]
                path = leg['rerouted']['path']
                self.assertEqual((path[0]['lat'], path[0]['lng']), (planned['from']['lat'], planned['from']['lng']))
                self.assertEqual((path[-1]['lat'], path[-1]['lng']), (planned['to']['lat'], planned['to']['lng']))

    def test_only_the_accidents_own_leg_is_rerouted(self):
        owner = {a['id']: a['leg_index'] for a in self.result['accidents']}
        for key, legs in self.result['scenarios'].items():
            active = [int(i) for i in key.split(',')]
            self.assertEqual([leg['leg_index'] for leg in legs], sorted({owner[i] for i in active}), key)

    def test_reroutes_avoid_their_accidents(self):
        location = {a['id']: {"lat": a['lat'], "lng": a['lng']} for a in self.result['accidents']}
        for key, legs in self.result['scenarios'].items():
            for leg in legs:
                for accident_id in leg['accident_ids']:
                    clearance = backend._distance_to_path_m(location[accident_id], leg['rerouted']['path'])
                    self.assertGreater(clearance, backend.ACCIDENT_AVOID_RADIUS_M, key)


class FleetReplanningTest(unittest.TestCase):
    """Fleet routes are sent as all vehicles' legs together, tagged with vehicle_id."""

    # Vehicle 1: Depot -> A -> B -> Depot. Vehicle 2: Depot -> C -> Depot.
    FLEET_LEGS = ([{"from": f, "to": t, "vehicle_id": 1} for f, t in [(DEPOT, A), (A, B), (B, DEPOT)]]
                  + [{"from": f, "to": t, "vehicle_id": 2} for f, t in [(DEPOT, C), (C, DEPOT)]])
    # Vehicle 1's legs hold the two worst hotspots (5 crashes on A -> B, 4 on Depot -> A);
    # vehicle 2's road Depot <-> C has 3
    FLEET_CRASHES = ([(33.875 + k * 0.001, -117.80, 2021, 1, "NORTH RD") for k in range(-2, 3)]
                     + [(33.80, -117.90 + k * 0.001, 2022, 1, "SOUTH RD") for k in range(-2, 2)]
                     + [(33.875 + k * 0.001, -118.00, 2023, 1, "WEST RD") for k in range(-1, 2)])

    @classmethod
    def setUpClass(cls):
        cls.result = run_replan(cls.FLEET_LEGS, cls.FLEET_CRASHES, 2)

    def test_accidents_spread_over_vehicles(self):
        """The second accident goes to vehicle 2, not vehicle 1's 4-crash hotspot."""
        accidents = self.result['accidents']
        self.assertEqual([(a['vehicle_id'], a['crash_count']) for a in accidents], [(1, 5), (2, 3)])

    def test_rerouted_legs_belong_to_the_accidents_vehicle(self):
        accidents = {a['id']: a for a in self.result['accidents']}
        for key, legs in self.result['scenarios'].items():
            for leg in legs:
                self.assertEqual(leg['vehicle_id'], self.FLEET_LEGS[leg['leg_index']]['vehicle_id'], key)
                for accident_id in leg['accident_ids']:
                    self.assertEqual(accidents[accident_id]['leg_index'], leg['leg_index'], key)


if __name__ == '__main__':
    unittest.main()
