"""
Offline tests for charging stop planning (/api/plan_charging).

Checks the 20% state-of-charge reserve: the chosen charger is reached above
the reserve, the vehicle is back at the depot with at least the reserve, and
among qualifying chargers the one adding the least time wins. Google's Routes
and Places APIs and the energy model are replaced with synthetic data
(1 kWh per km), so no API key or network is needed.

Run from the repo root:
    python -m unittest discover tests
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import route_optimizer_backend as backend  # noqa: E402

DEPOT = {"lat": 33.80, "lng": -118.00}
A = {"lat": 33.80, "lng": -117.70}
B = {"lat": 34.00, "lng": -117.70}
# Three 30 km / 30 kWh legs on an 80 kWh battery: 90 kWh needed, reserve is 16 kWh.
# The battery would hit the reserve (64 kWh used) on the last leg.
LEGS = [{"from": f, "to": t, "energy_kwh": 30.0, "time_s": 1800}
        for f, t in [(DEPOT, A), (A, B), (B, DEPOT)]]


def fake_compute_leg_routes(origin, destination, vias=None, alternatives=False):
    return [{"distance_m": 30000, "time_s": 1800, "path": [origin, destination]}]


def charger(name, to_km, to_s, from_km, from_s, max_kw=150):
    return {"name": name, "address": f"{name} St", "lat": 33.9, "lng": -117.8, "max_kw": max_kw,
            "to_charger": {"distance_m": to_km * 1000, "time_s": to_s},
            "from_charger": {"distance_m": from_km * 1000, "time_s": from_s}}


# Chargers found along each leg, keyed by the leg's start
CHARGERS = {
    # Leg 0: reachable, but even a full battery there can't cover the rest of the route
    (DEPOT['lat'], DEPOT['lng']): [charger("Too Early", 10, 600, 21, 1300)],
    # Leg 1: both qualify; "Best" has the smaller detour (100 s vs 240 s)
    (A['lat'], A['lng']): [charger("Best", 15, 900, 16, 1000), charger("Longer Detour", 14, 840, 20, 1200)],
    # Leg 2: past the reserve point, reached with 0 kWh
    (B['lat'], B['lng']): [charger("Too Late", 20, 1200, 10, 600)],
}


def plan(legs, battery_kwh=80.0):
    def chargers_along(path, fast_only=True):
        return CHARGERS[(path[0]['lat'], path[0]['lng'])]

    with mock.patch.object(backend, 'compute_leg_routes', fake_compute_leg_routes), \
         mock.patch.object(backend, 'search_chargers_along_path', chargers_along), \
         mock.patch.object(backend, 'get_elevation_data', lambda locs: [0.0] * len(locs)), \
         mock.patch.object(backend, 'route_energy_kwh', lambda route, *a, **k: route['distance_m'] / 1000):
        return backend.plan_charging_stop(legs, battery_kwh, {})


class ChargingStopTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.plan = plan(LEGS)
        cls.stop = cls.plan['stop']

    def test_reserve_point_on_the_leg_where_soc_hits_20_percent(self):
        self.assertTrue(self.plan['needed'])
        self.assertEqual(self.plan['reserve_kwh'], 16.0)
        self.assertEqual(self.plan['reserve_point']['leg_index'], 2)

    def test_picks_the_qualifying_charger_with_least_added_time(self):
        self.assertEqual(self.stop['charger']['name'], "Best")
        self.assertEqual(self.stop['leg_index'], 1)
        self.assertEqual([c['name'] for c in self.plan['alternatives']], ["Longer Detour"])

    def test_soc_never_below_reserve(self):
        # Arrive with 80 - 30 - 15 = 35 kWh; need 16 + 30 + 16 = 62 kWh to finish with the reserve
        self.assertAlmostEqual(self.stop['arrival_soc_kwh'], 35.0)
        self.assertAlmostEqual(self.stop['charge_kwh'], 27.0)
        self.assertAlmostEqual(self.stop['depart_soc_kwh'], 62.0)
        self.assertAlmostEqual(self.stop['final_soc_kwh'], 16.0)

    def test_added_time_is_detour_plus_charging(self):
        self.assertEqual(self.stop['detour_time_s'], 100)
        self.assertEqual(self.stop['charge_kw'], backend.VEHICLE_MAX_CHARGE_KW)  # 150 kW charger, truck limit
        self.assertEqual(self.stop['charge_time_s'], round(27.0 / backend.VEHICLE_MAX_CHARGE_KW * 3600))
        self.assertEqual(self.stop['added_time_s'], 100 + self.stop['charge_time_s'])

    def test_no_stop_when_reserve_is_kept(self):
        light = [{**leg, "energy_kwh": 20.0} for leg in LEGS]  # 60 kWh used, 20 kWh left
        result = plan(light)
        self.assertFalse(result['needed'])
        self.assertNotIn('stop', result)


if __name__ == '__main__':
    unittest.main()
