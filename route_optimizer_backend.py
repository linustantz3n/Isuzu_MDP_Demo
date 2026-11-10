"""
Route Optimization Backend for Isuzu BEV Fleet Project
======================================================

This server provides:
1. Address geocoding via Google Geocoding API
2. Distance/time matrix computation via Google Distance Matrix API
3. TSP solving using multiple algorithms for comparison

Run with: python route_optimizer_backend.py
API will be available at http://localhost:5000
"""

import os
import csv
import gzip
import json
import math
import time
import requests
from bisect import bisect_right
from concurrent.futures import ThreadPoolExecutor
from itertools import combinations, permutations
from functools import lru_cache
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
from solvers import (
    solve_tsp_brute_force,
    solve_tsp_held_karp,
    solve_tsp_nearest_neighbor,
    solve_tsp_2opt,
    solve_vrp_ortools,
    BatteryLimitError,
)
from energy import (  # noqa: F401 (used below)
    calculate_energy_consumption,
    estimate_average_speed,
    battery_limit_wh,
    energy_upper_bound_matrix_wh,
    DEFAULT_BATTERY_RESERVE_PCT,
)
from benchmark import ALGORITHMS as BENCHMARK_ALGORITHMS, list_scenarios, run_benchmark, simulate
from benchmark.runner import fixed_plan_view

import traffic_data

# Load environment variables from .env file
load_dotenv()

app = Flask(__name__)
CORS(app)

# Get API key from environment or set directly
GOOGLE_API_KEY = os.environ.get('GOOGLE_MAPS_API_KEY', '')


# =============================================================================
# Google Maps API Functions
# =============================================================================

def geocode_address(address: str) -> dict:
    """Convert address to lat/lng coordinates."""
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {
        "address": address,
        "key": GOOGLE_API_KEY
    }
    
    response = requests.get(url, params=params)
    data = response.json()
    
    if data["status"] != "OK":
        detail = f" ({data['error_message']})" if data.get("error_message") else ""
        raise Exception(f"Geocoding failed for '{address}': {data['status']}{detail}")
    
    result = data["results"][0]
    location = result["geometry"]["location"]
    
    return {
        "address": address,
        "formatted_address": result["formatted_address"],
        "lat": location["lat"],
        "lng": location["lng"]
    }


def get_distance_matrix(locations: list) -> dict:
    """
    Get distance and time matrices between all locations.

    Uses the Routes API computeRouteMatrix endpoint (the legacy Distance Matrix
    API cannot be enabled on new projects). Batches requests in 25x25 chunks to
    stay within the limit of 625 elements per request.

    Args:
        locations: List of dicts with 'lat' and 'lng' keys

    Returns:
        Dict with 'distance' and 'time' matrices (values in meters and seconds)
    """
    n = len(locations)
    waypoints = [_routes_waypoint(loc) for loc in locations]

    distance_matrix = [[0] * n for _ in range(n)]
    time_matrix = [[0] * n for _ in range(n)]

    url = "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix"
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": GOOGLE_API_KEY,
        "X-Goog-FieldMask": "originIndex,destinationIndex,status,condition,distanceMeters,duration",
    }
    chunk_size = 25  # 25x25 = 625 elements, exactly at the API limit

    for i_start in range(0, n, chunk_size):
        i_end = min(i_start + chunk_size, n)

        for j_start in range(0, n, chunk_size):
            j_end = min(j_start + chunk_size, n)

            body = {
                "origins": [{"waypoint": w} for w in waypoints[i_start:i_end]],
                "destinations": [{"waypoint": w} for w in waypoints[j_start:j_end]],
                "travelMode": "DRIVE",
            }

            response = requests.post(url, json=body, headers=headers)
            data = response.json()
            # Errors come back as {"error": ...}, sometimes wrapped in a list
            error = data.get("error") if isinstance(data, dict) else (
                data[0].get("error") if data and "error" in data[0] else None)
            if response.status_code != 200 or error:
                message = (error or {}).get("message", response.text)
                raise Exception(f"Routes API (route matrix) error: {message}")

            for element in data:
                # Zero-valued fields (index 0, 0 meters) are omitted from the JSON
                gi = i_start + element.get("originIndex", 0)
                gj = j_start + element.get("destinationIndex", 0)
                if element.get("condition") == "ROUTE_EXISTS":
                    distance_matrix[gi][gj] = element.get("distanceMeters", 0)
                    time_matrix[gi][gj] = round(float(element.get("duration", "0s").rstrip("s")))
                else:
                    distance_matrix[gi][gj] = float('inf')
                    time_matrix[gi][gj] = float('inf')

    return {
        "distance": distance_matrix,
        "time": time_matrix
    }


def get_directions(locations: list, order: list) -> dict:
    """
    Get turn-by-turn directions for the optimized route.
    
    Args:
        locations: List of location dicts
        order: Optimized order indices
    
    Returns:
        Routes API computeRoutes response with route details
    """
    ordered_locs = [locations[i] for i in order]

    url = "https://routes.googleapis.com/directions/v2:computeRoutes"
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": GOOGLE_API_KEY,
        "X-Goog-FieldMask": "routes.distanceMeters,routes.duration,"
                            "routes.polyline.encodedPolyline,routes.legs",
    }
    body = {
        "origin": _routes_waypoint(ordered_locs[0]),
        "destination": _routes_waypoint(ordered_locs[-1]),
        "intermediates": [_routes_waypoint(loc) for loc in ordered_locs[1:-1]],
        "travelMode": "DRIVE",
    }

    response = requests.post(url, json=body, headers=headers)
    data = response.json()
    if response.status_code != 200:
        message = data.get("error", {}).get("message", response.text)
        raise Exception(f"Routes API (directions) error: {message}")
    return data


def _routes_waypoint(loc: dict) -> dict:
    """Build a Routes API waypoint from a dict with 'lat' and 'lng' keys."""
    return {"location": {"latLng": {"latitude": loc['lat'], "longitude": loc['lng']}}}


# =============================================================================
# Incident Replanning (demo: accidents at historical crash hotspots)
# =============================================================================

ACCIDENT_AVOID_RADIUS_M = 500    # a route is blocked if it passes this close to an accident
DETOUR_OFFSETS_M = [1500, 3000]  # how far a forced detour swings out around an accident

# Historical crashes: NHTSA FARS fatal crashes, built by scripts/build_crash_history.py
CRASH_DATA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               'data', 'crash_history.csv.gz')
CRASH_GRID_DEG = 0.01              # spatial index cell size (~1.1 km north-south)
HOTSPOT_MATCH_RADIUS_M = 150       # a crash belongs to a route if it is this close to it
HOTSPOT_WINDOW_M = 1000            # crashes within this stretch of road form one hotspot
HOTSPOT_MIN_SEPARATION_M = 3000    # chosen hotspots are at least this far apart
HOTSPOT_STOP_CLEARANCE_M = 1000    # no hotspots this close to a stop (no way around them)
MAX_HOTSPOT_REJECTIONS = 6         # hotspots without a detour skipped before giving up

_crash_grid = None


def _grid_cell(lat: float, lng: float) -> tuple:
    return (math.floor(lat / CRASH_GRID_DEG), math.floor(lng / CRASH_GRID_DEG))


def load_crash_grid() -> dict:
    """Load historical crashes once, as {grid cell: [(lat, lng, year, fatals, road), ...]}."""
    global _crash_grid
    if _crash_grid is None:
        grid = {}
        with gzip.open(CRASH_DATA_PATH, 'rt', newline='') as f:
            for row in csv.DictReader(f):
                lat, lng = float(row['lat']), float(row['lng'])
                grid.setdefault(_grid_cell(lat, lng), []).append(
                    (lat, lng, int(row['year']), int(row['fatals']), row['road']))
        _crash_grid = grid
    return _crash_grid


def compute_leg_routes(origin: dict, destination: dict, vias: list = None,
                       alternatives: bool = False) -> list:
    """
    Get driving routes for one leg via the Routes API computeRoutes endpoint.

    Args:
        origin, destination: Dicts with 'lat' and 'lng' keys
        vias: Optional pass-through points (no stops) used to force a detour
        alternatives: Also return Google's alternative routes (ignored with vias)

    Returns:
        List of dicts with 'distance_m', 'time_s' and 'path' ([{lat, lng}, ...]),
        Google's default route first
    """
    url = "https://routes.googleapis.com/directions/v2:computeRoutes"
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": GOOGLE_API_KEY,
        "X-Goog-FieldMask": "routes.distanceMeters,routes.duration,routes.polyline.encodedPolyline",
    }
    body = {
        "origin": _routes_waypoint(origin),
        "destination": _routes_waypoint(destination),
        "travelMode": "DRIVE",
    }
    if vias:
        body["intermediates"] = [{**_routes_waypoint(v), "via": True} for v in vias]
    elif alternatives:
        body["computeAlternativeRoutes"] = True

    response = requests.post(url, json=body, headers=headers)
    data = response.json()
    if response.status_code != 200:
        message = data.get("error", {}).get("message", response.text)
        raise Exception(f"Routes API (leg routes) error: {message}")

    return [
        {
            "distance_m": r.get("distanceMeters", 0),
            "time_s": round(float(r.get("duration", "0s").rstrip("s"))),
            "path": _decode_polyline(r["polyline"]["encodedPolyline"]),
        }
        for r in data.get("routes", [])
    ]


def find_path_hotspots(path: list) -> list:
    """
    Find stretches of a path with the most historical crashes.

    Each crash within HOTSPOT_MATCH_RADIUS_M of the path is placed at its
    distance along the path. Every HOTSPOT_WINDOW_M stretch starting at a crash
    is a candidate hotspot, scored by its crash count. The accident marker goes
    on the path at the stretch's median crash.

    Returns:
        Candidate hotspots as dicts with 'location' ({lat, lng}),
        'direction' ((ux, uy), unit direction of travel there) and 'crashes'
    """
    grid = load_crash_grid()
    dense = _densify_path(path)

    # Index the dense path points by grid cell, then match crashes in nearby cells
    points_by_cell = {}
    for idx, (point, _) in enumerate(dense):
        points_by_cell.setdefault(_grid_cell(point['lat'], point['lng']), []).append(idx)

    def neighbours(cell):
        return [(cell[0] + dr, cell[1] + dc) for dr in (-1, 0, 1) for dc in (-1, 0, 1)]

    near_cells = {c for cell in points_by_cell for c in neighbours(cell)}
    matches = []  # (distance along path, dense point index, crash)
    for cell in near_cells:
        for crash in grid.get(cell, []):
            crash_point = {"lat": crash[0], "lng": crash[1]}
            best_idx, best_m = None, HOTSPOT_MATCH_RADIUS_M
            for c in neighbours(cell):
                for idx in points_by_cell.get(c, []):
                    d = _distance_m(dense[idx][0], crash_point)
                    if d <= best_m:
                        best_idx, best_m = idx, d
            if best_idx is not None:
                matches.append((dense[best_idx][1], best_idx, crash))
    matches.sort(key=lambda m: m[0])
    along = [m[0] for m in matches]

    candidates = []
    for i, s_start in enumerate(along):
        window = matches[i:bisect_right(along, s_start + HOTSPOT_WINDOW_M)]
        _, idx, _ = window[len(window) // 2]
        before = dense[max(idx - 1, 0)][0]
        after = dense[min(idx + 1, len(dense) - 1)][0]
        dx, dy = _to_local_m(after, before)
        norm = math.hypot(dx, dy) or 1.0
        candidates.append({
            "location": dense[idx][0],
            "direction": (dx / norm, dy / norm),
            "crashes": [m[2] for m in window],
        })
    return candidates


def pick_accident_hotspots(leg_paths: list, stops: list, count: int,
                           is_avoidable=lambda chosen: True, leg_vehicles: list = None) -> list:
    """
    Pick the `count` worst historical crash hotspots along a route.

    Spreads the accidents over different vehicles (if leg_vehicles gives each
    leg's vehicle) and then different legs first, and keeps chosen hotspots
    at least HOTSPOT_MIN_SEPARATION_M apart so one stretch of road (e.g. shared
    by the outbound and return legs) is not picked twice. Hotspots next to a
    stop are skipped, as are those for which is_avoidable(chosen + [hotspot])
    returns False.
    """
    candidates = [
        {**hotspot, "leg_index": leg_index}
        for leg_index, path in enumerate(leg_paths)
        for hotspot in find_path_hotspots(path)
        if all(_distance_m(hotspot['location'], stop) >= HOTSPOT_STOP_CLEARANCE_M
               for stop in stops)
    ]
    # Most crashes first; total fatalities breaks ties
    candidates.sort(key=lambda h: (len(h['crashes']), sum(c[3] for c in h['crashes'])),
                    reverse=True)

    # Passes from most to least spread out: at most one hotspot per vehicle, per leg, then any
    spread_keys = [lambda h: h['leg_index'], None]
    if leg_vehicles:
        spread_keys.insert(0, lambda h: leg_vehicles[h['leg_index']])

    chosen, rejected = [], []
    for spread_key in spread_keys:
        for hotspot in candidates:
            if len(chosen) == count or len(rejected) > MAX_HOTSPOT_REJECTIONS:
                return chosen
            if hotspot in rejected:
                continue
            if spread_key and any(spread_key(c) == spread_key(hotspot) for c in chosen):
                continue
            if any(_distance_m(hotspot['location'], c['location']) < HOTSPOT_MIN_SEPARATION_M
                   for c in chosen):
                continue
            if not is_avoidable(chosen + [hotspot]):
                rejected.append(hotspot)
                continue
            chosen.append(hotspot)
    return chosen


def _blocks(accident: dict, path: list) -> bool:
    return _distance_to_path_m(accident['location'], path) <= ACCIDENT_AVOID_RADIUS_M


def reroute_leg_around_accidents(origin: dict, destination: dict,
                                 routes: list, accidents: list):
    """
    Find the fastest route for a leg that avoids every given accident.

    Google's alternative routes are tried first. If all of them are blocked, a
    bypass is forced around each accident on the default route through two
    pass-through points: one diagonally behind it and one diagonally ahead of
    it, on the same side of the road.

    Args:
        routes: The leg's routes from compute_leg_routes, default first
        accidents: Hotspots (see find_path_hotspots) to avoid

    Returns:
        (rerouted route, method) where method is 'alternative' or 'detour',
        or None if no route avoids the accidents
    """
    def avoids_accidents(route):
        return not any(_blocks(a, route['path']) for a in accidents)

    candidates = [r for r in routes[1:] if avoids_accidents(r)]
    if candidates:
        return min(candidates, key=lambda r: r['time_s']), "alternative"

    blocking = [a for a in accidents if _blocks(a, routes[0]['path'])]
    for offset in DETOUR_OFFSETS_M:
        for side in (1, -1):
            vias = []
            for accident in blocking:
                ux, uy = accident['direction']
                for forward in (-1, 1):
                    vias.append(_offset_point(accident['location'],
                                              (ux * forward - uy * side) * offset,
                                              (uy * forward + ux * side) * offset))
            # Visit the pass-through points in the order the leg reaches them
            vias.sort(key=lambda v: _distance_m(v, origin))
            candidates += [r for r in compute_leg_routes(origin, destination, vias=vias)
                           if avoids_accidents(r)]
        if candidates:
            return min(candidates, key=lambda r: r['time_s']), "detour"
    return None


def replan_route_around_hotspots(legs: list, count: int) -> dict:
    """
    Place accidents at the route's worst historical crash hotspots and reroute
    around them, for every combination of accidents that could be active. The
    delivery order stays fixed.

    Each accident only affects the leg it was found on (stop i -> stop i+1):
    that leg is rerouted around it, even if other legs use the same road.

    Hotspots that leave their leg without a detour are skipped in favour of the
    next worst, so every returned accident can be driven around.

    Args:
        legs: Dicts with 'from' and 'to' locations, in route order. Fleet routes
              are passed as all vehicles' legs together, each with 'vehicle_id'.
        count: Number of accidents to place

    Returns:
        Dict with 'accidents' (hotspots with 'id' and 'leg_index') and
        'scenarios', mapping each non-empty set of active accident ids (sorted,
        comma-joined, e.g. "1,3") to its rerouted legs, each with 'leg_index',
        'accident_ids', 'original', 'rerouted' and 'method'
    """
    with ThreadPoolExecutor(max_workers=8) as pool:
        leg_routes = list(pool.map(
            lambda leg: compute_leg_routes(leg['from'], leg['to'], alternatives=True), legs))
    if not all(leg_routes):
        raise Exception("No route found for one of the legs")

    def reroute(leg_index, accidents_on_leg):
        leg = legs[leg_index]
        return reroute_leg_around_accidents(leg['from'], leg['to'], leg_routes[leg_index],
                                            accidents_on_leg)

    reroutes = {}  # leg index -> (rerouted route, method) avoiding all its chosen accidents

    def is_avoidable(chosen):
        """Reroute the newest hotspot's leg around its accidents; False if it can't be."""
        leg_index = chosen[-1]['leg_index']
        rerouted = reroute(leg_index, [a for a in chosen if a['leg_index'] == leg_index])
        if rerouted is None:
            return False
        reroutes[leg_index] = rerouted
        return True

    stops = [leg['from'] for leg in legs] + [leg['to'] for leg in legs]
    leg_vehicles = [leg.get('vehicle_id') for leg in legs]
    accidents = pick_accident_hotspots([routes[0]['path'] for routes in leg_routes],
                                       stops, count, is_avoidable,
                                       leg_vehicles if len(set(leg_vehicles)) > 1 else None)
    for i, accident in enumerate(accidents):
        accident['id'] = i + 1

    # Reroute each leg for every combination of its own accidents. reroutes already
    # avoids all of them, so it covers the full set and is the fallback for a subset.
    def reroute_subset(job):
        leg_index, subset = job
        on_leg = [a for a in accidents if a['leg_index'] == leg_index]
        if len(subset) == len(on_leg):
            return reroutes[leg_index]
        return reroute(leg_index, list(subset)) or reroutes[leg_index]

    jobs = [(leg_index, subset)
            for leg_index in sorted({a['leg_index'] for a in accidents})
            for size in range(1, len(accidents) + 1)
            for subset in combinations([a for a in accidents if a['leg_index'] == leg_index], size)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        leg_reroutes = dict(zip(((i, tuple(a['id'] for a in s)) for i, s in jobs),
                                pool.map(reroute_subset, jobs)))

    scenarios = {}
    for size in range(1, len(accidents) + 1):
        for active in combinations(accidents, size):
            replanned = []
            for leg_index in sorted({a['leg_index'] for a in active}):
                accident_ids = tuple(a['id'] for a in active if a['leg_index'] == leg_index)
                rerouted, method = leg_reroutes[(leg_index, accident_ids)]
                replanned.append({
                    "leg_index": leg_index,
                    "vehicle_id": legs[leg_index].get('vehicle_id'),
                    "accident_ids": list(accident_ids),
                    "original": leg_routes[leg_index][0],
                    "rerouted": rerouted,
                    "method": method,
                })
            scenarios[",".join(str(a['id']) for a in active)] = replanned

    return {"accidents": accidents, "scenarios": scenarios}


# =============================================================================
# Charging Stops (keep a state-of-charge reserve all the way back to the depot)
# =============================================================================

SOC_RESERVE_FRACTION = 0.20     # share of the battery a vehicle must never drop below
VEHICLE_MAX_CHARGE_KW = 50      # assumed max DC charging power of the truck (check Isuzu spec)
FAST_CHARGER_MIN_KW = 50        # chargers searched first: DC fast with a CCS1 connector
FAST_CHARGER_CONNECTOR = "EV_CONNECTOR_TYPE_CCS_COMBO_1"
LEVEL2_CHARGER_KW = 7.2         # charging power assumed when a station doesn't report one


def search_chargers_along_path(path: list, fast_only: bool = True) -> list:
    """
    Find EV charging stations along a path via Places API (New) Text Search
    with searchAlongRouteParameters.

    Args:
        path: [{lat, lng}, ...], e.g. one leg's route
        fast_only: Only DC fast chargers (FAST_CHARGER_MIN_KW+, CCS1)

    Returns:
        List of dicts with 'name', 'address', 'lat', 'lng', 'max_kw' (None if
        unknown), and 'to_charger' / 'from_charger' ({distance_m, time_s}:
        path start -> charger and charger -> path end)
    """
    url = "https://places.googleapis.com/v1/places:searchText"
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": GOOGLE_API_KEY,
        "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.location,"
                            "places.evChargeOptions,routingSummaries",
    }
    body = {
        "textQuery": "EV charging station",
        "includedType": "electric_vehicle_charging_station",
        "strictTypeFiltering": True,
        "searchAlongRouteParameters": {"polyline": {"encodedPolyline": _encode_polyline(path)}},
        "routingParameters": {"origin": {"latitude": path[0]['lat'], "longitude": path[0]['lng']}},
        "pageSize": 20,
    }
    if fast_only:
        body["evOptions"] = {"minimumChargingRateKw": FAST_CHARGER_MIN_KW,
                             "connectorTypes": [FAST_CHARGER_CONNECTOR]}

    response = requests.post(url, json=body, headers=headers)
    data = response.json()
    if response.status_code != 200:
        message = data.get("error", {}).get("message", response.text)
        raise Exception(f"Places API (charger search) error: {message}")

    def trip(leg):
        return {"distance_m": leg.get("distanceMeters", 0),
                "time_s": round(float(leg.get("duration", "0s").rstrip("s")))}

    chargers = []
    for place, summary in zip(data.get("places", []), data.get("routingSummaries", [])):
        legs = summary.get("legs", [])
        if len(legs) != 2:
            continue
        rates = [c.get("maxChargeRateKw", 0)
                 for c in place.get("evChargeOptions", {}).get("connectorAggregation", [])]
        chargers.append({
            "name": place.get("displayName", {}).get("text", "EV charging station"),
            "address": place.get("formattedAddress"),
            "lat": place["location"]["latitude"],
            "lng": place["location"]["longitude"],
            "max_kw": max(rates) if rates and max(rates) > 0 else None,
            "to_charger": trip(legs[0]),
            "from_charger": trip(legs[1]),
        })
    return chargers


def route_energy_kwh(route: dict, leg: dict, energy_params: dict,
                     altitude_diff_m: float = None, altitude_m: float = None) -> float:
    """
    Energy for driving a route ({distance_m, time_s}) in place of (part of) a
    planned leg, with the leg's vehicle weight. Same model as /api/optimize;
    elevation defaults to the leg's.
    """
    return calculate_energy_consumption(
        distance_km=route['distance_m'] / 1000,
        avg_speed_kmh=estimate_average_speed(route['distance_m'], route['time_s']),
        temperature_c=energy_params.get('temperature_c', 20.0),
        vehicle_weight_tonnes=leg.get('vehicle_weight_tonnes', 13000 / 2204.62),
        altitude_diff_m=leg.get('elevation_gain_m', 0.0) if altitude_diff_m is None else altitude_diff_m,
        altitude_m=leg.get('elevation_from_m', 0.0) if altitude_m is None else altitude_m,
        has_climate_control=energy_params.get('has_climate_control', True)
    )['energy_kwh']


def plan_charging_stop(legs: list, battery_kwh: float, energy_params: dict) -> dict:
    """
    Plan one charging stop so a vehicle never drops below the SoC reserve
    (SOC_RESERVE_FRACTION) and is back at the depot with at least that much.

    The vehicle starts full, and each leg's energy (leg['energy_kwh']) is
    spread evenly over its distance. Chargers are searched along every leg up
    to the point where the battery would hit the reserve. Each charger is
    inserted into its leg (leg start -> charger -> leg end); it qualifies if
    the vehicle reaches it above the reserve and one charge covers the rest of
    the route plus the reserve. The vehicle charges just that much, and the
    charger adding the least time (detour + charging) is chosen.

    Args:
        legs: The vehicle's legs in route order, as in /api/optimize fleet routes
        battery_kwh: Battery capacity

    Returns:
        Dict with 'needed', 'reserve_kwh' and 'final_soc_kwh' (without a stop).
        If a stop is needed, also 'reserve_point' ({lat, lng, leg_index}: where
        the battery would hit the reserve), 'stop' (the chosen stop, or None if
        no charger works) and 'alternatives' (other qualifying chargers)
    """
    reserve_kwh = battery_kwh * SOC_RESERVE_FRACTION
    energies = [leg['energy_kwh'] for leg in legs]
    used_before = [sum(energies[:i]) for i in range(len(legs))]
    plan = {"reserve_kwh": reserve_kwh, "final_soc_kwh": battery_kwh - sum(energies)}
    if plan['final_soc_kwh'] >= reserve_kwh:
        return {**plan, "needed": False}

    # Leg where the battery would hit the reserve, and how far into it
    usable_kwh = battery_kwh - reserve_kwh
    k = next(i for i in range(len(legs)) if used_before[i] + energies[i] > usable_kwh)
    fraction = (usable_kwh - used_before[k]) / energies[k]

    with ThreadPoolExecutor(max_workers=8) as pool:
        routes = list(pool.map(lambda leg: compute_leg_routes(leg['from'], leg['to'])[0],
                               legs[:k + 1]))
    dense = _densify_path(routes[k]['path'])
    reserve_point = min(dense, key=lambda d: abs(d[1] - fraction * dense[-1][1]))[0]
    plan.update(needed=True, reserve_point={**reserve_point, "leg_index": k})

    def search(fast_only):
        with ThreadPoolExecutor(max_workers=8) as pool:
            found = list(pool.map(lambda r: search_chargers_along_path(r['path'], fast_only), routes))
        return [{**c, "leg_index": i} for i, chargers in enumerate(found) for c in chargers]

    candidates = search(fast_only=True) or search(fast_only=False)
    if not candidates:
        return {**plan, "stop": None, "alternatives": []}
    elevations = get_elevation_data(candidates)

    options = []
    for charger, elevation in zip(candidates, elevations):
        i = charger['leg_index']
        leg = legs[i]
        start_elev = leg.get('elevation_from_m', 0.0)
        end_elev = start_elev + leg.get('elevation_gain_m', 0.0)
        to_kwh = route_energy_kwh(charger['to_charger'], leg, energy_params,
                                  elevation - start_elev, start_elev)
        from_kwh = route_energy_kwh(charger['from_charger'], leg, energy_params,
                                    end_elev - elevation, elevation)
        arrival_kwh = battery_kwh - used_before[i] - to_kwh
        needed_kwh = from_kwh + sum(energies[i + 1:]) + reserve_kwh  # SoC needed leaving the charger
        if arrival_kwh < reserve_kwh or needed_kwh > battery_kwh:
            continue
        charge_kwh = max(0.0, needed_kwh - arrival_kwh)
        charge_kw = min(charger['max_kw'] or LEVEL2_CHARGER_KW, VEHICLE_MAX_CHARGE_KW)
        charge_time_s = round(charge_kwh / charge_kw * 3600)
        detour_time_s = (charger['to_charger']['time_s'] + charger['from_charger']['time_s']
                         - routes[i]['time_s'])
        options.append({
            "charger": {key: charger[key] for key in ('name', 'address', 'lat', 'lng', 'max_kw')},
            "leg_index": i,
            "arrival_soc_kwh": arrival_kwh,
            "charge_kwh": charge_kwh,
            "charge_kw": charge_kw,
            "charge_time_s": charge_time_s,
            "depart_soc_kwh": arrival_kwh + charge_kwh,
            "final_soc_kwh": arrival_kwh + charge_kwh - (needed_kwh - reserve_kwh),
            "detour_distance_m": (charger['to_charger']['distance_m']
                                  + charger['from_charger']['distance_m'] - routes[i]['distance_m']),
            "detour_time_s": detour_time_s,
            "detour_energy_kwh": to_kwh + from_kwh - route_energy_kwh(routes[i], leg, energy_params),
            "added_time_s": detour_time_s + charge_time_s,
        })
    options.sort(key=lambda o: o['added_time_s'])
    if not options:
        return {**plan, "stop": None, "alternatives": []}

    # Roads to and from the chosen charger, for the map
    stop = options[0]
    leg, charger = legs[stop['leg_index']], stop['charger']
    stop['to_charger_path'] = compute_leg_routes(leg['from'], charger)[0]['path']
    stop['from_charger_path'] = compute_leg_routes(charger, leg['to'])[0]['path']
    return {**plan, "stop": stop, "alternatives": [o['charger'] for o in options[1:5]]}


def _decode_polyline(encoded: str) -> list:
    """Decode a Google encoded polyline into [{lat, lng}, ...]."""
    points, index, lat, lng = [], 0, 0, 0
    while index < len(encoded):
        deltas = []
        for _ in range(2):
            shift, result = 0, 0
            while True:
                b = ord(encoded[index]) - 63
                index += 1
                result |= (b & 0x1f) << shift
                shift += 5
                if b < 0x20:
                    break
            deltas.append(~(result >> 1) if result & 1 else result >> 1)
        lat += deltas[0]
        lng += deltas[1]
        points.append({"lat": lat / 1e5, "lng": lng / 1e5})
    return points


def _encode_polyline(path: list) -> str:
    """Encode [{lat, lng}, ...] as a Google encoded polyline (inverse of _decode_polyline)."""
    chunks, prev_lat, prev_lng = [], 0, 0
    for point in path:
        lat, lng = round(point['lat'] * 1e5), round(point['lng'] * 1e5)
        for delta in (lat - prev_lat, lng - prev_lng):
            value = ~(delta << 1) if delta < 0 else delta << 1
            while value >= 0x20:
                chunks.append(chr((0x20 | (value & 0x1f)) + 63))
                value >>= 5
            chunks.append(chr(value + 63))
        prev_lat, prev_lng = lat, lng
    return "".join(chunks)


def _to_local_m(point: dict, ref: dict) -> tuple:
    """Project a point to (x, y) meters east/north of ref (fine over tens of km)."""
    return ((point['lng'] - ref['lng']) * 111320 * math.cos(math.radians(ref['lat'])),
            (point['lat'] - ref['lat']) * 110540)


def _distance_m(a: dict, b: dict) -> float:
    """Straight-line distance in meters between two nearby points."""
    return math.hypot(*_to_local_m(a, b))


def _offset_point(ref: dict, x_m: float, y_m: float) -> dict:
    """Inverse of _to_local_m: the point x_m east and y_m north of ref."""
    return {"lat": ref['lat'] + y_m / 110540,
            "lng": ref['lng'] + x_m / (111320 * math.cos(math.radians(ref['lat'])))}


def _densify_path(path: list, step_m: float = 50) -> list:
    """Points at most step_m apart along a path, as [({lat, lng}, meters along path), ...]."""
    dense = [(path[0], 0.0)]
    along = 0.0
    for a, b in zip(path, path[1:]):
        bx, by = _to_local_m(b, a)
        length = math.hypot(bx, by)
        steps = max(1, math.ceil(length / step_m))
        for k in range(1, steps + 1):
            dense.append((_offset_point(a, bx * k / steps, by * k / steps),
                          along + length * k / steps))
        along += length
    return dense


def _distance_to_path_m(point: dict, path: list) -> float:
    """Shortest distance in meters from a point to any segment of a path."""
    best = float('inf')
    for a, b in zip(path, path[1:]):
        ax, ay = _to_local_m(a, point)  # point itself sits at the origin
        bx, by = _to_local_m(b, point)
        dx, dy = bx - ax, by - ay
        seg_sq = dx * dx + dy * dy
        t = 0.0 if seg_sq == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / seg_sq))
        best = min(best, math.hypot(ax + t * dx, ay + t * dy))
    return best


def get_elevation_data(locations: list) -> list:
    """
    Get elevation data for a list of locations using Google Elevation API.

    Args:
        locations: List of dicts with 'lat' and 'lng' keys

    Returns:
        List of elevations in meters (same order as input)
    """
    coords = "|".join([f"{loc['lat']},{loc['lng']}" for loc in locations])

    url = "https://maps.googleapis.com/maps/api/elevation/json"
    params = {
        "locations": coords,
        "key": GOOGLE_API_KEY
    }

    response = requests.get(url, params=params)
    data = response.json()

    if data["status"] != "OK":
        # If elevation API fails, return zeros (graceful degradation)
        return [0.0] * len(locations)

    return [result["elevation"] for result in data["results"]]


# =============================================================================
# API Endpoints
# =============================================================================

@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint."""
    return jsonify({
        "status": "ok",
        "api_key_set": bool(GOOGLE_API_KEY)
    })


@app.route('/api/set_key', methods=['POST'])
def set_api_key():
    """Set the Google Maps API key."""
    global GOOGLE_API_KEY
    data = request.json
    GOOGLE_API_KEY = data.get('api_key', '')
    return jsonify({"status": "ok", "api_key_set": bool(GOOGLE_API_KEY)})


@app.route('/api/geocode', methods=['POST'])
def geocode():
    """Geocode a single address."""
    data = request.json
    address = data.get('address', '')
    
    try:
        result = geocode_address(address)
        return jsonify({"status": "ok", "result": result})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400


@app.route('/api/geocode_batch', methods=['POST'])
def geocode_batch():
    """Geocode multiple addresses."""
    data = request.json
    addresses = data.get('addresses', [])
    
    results = []
    errors = []
    
    for addr in addresses:
        try:
            result = geocode_address(addr)
            results.append(result)
        except Exception as e:
            errors.append({"address": addr, "error": str(e)})
            results.append(None)
    
    return jsonify({
        "status": "ok" if not errors else "partial",
        "results": results,
        "errors": errors
    })


@app.route('/api/optimize', methods=['POST'])
def optimize_route():
    """
    Main optimization endpoint.
    
    Request body:
    {
        "locations": [
            {"address": "...", "lat": ..., "lng": ...},
            ...
        ],
        "mode": "distance" | "time",
        "algorithms": ["held_karp", "nearest_neighbor", "2opt"],  // optional
        "traffic_source": "google" | "forecast" | "ground_truth" | "historical",  // optional
        "departure_time": "2025-11-12T07:30"  // needed unless traffic_source is google
    }

    traffic_source other than google takes travel times from the traffic data
    package (see traffic_data.py); the Held-Karp result then also gets 'traffic' with
    its travel time when actually driven on the measured speeds.

    Returns optimized route with comparison between algorithms.
    """
    data = request.json
    locations = data.get('locations', [])
    mode = data.get('mode', 'distance')
    traffic_source = data.get('traffic_source', 'google')
    departure_time = data.get('departure_time')
    algorithms = data.get('algorithms', ['held_karp', 'nearest_neighbor', '2opt'])
    # Accept either a `vehicles` array (new) or legacy `num_vehicles` int.
    vehicles_config = data.get('vehicles', None)
    num_vehicles = data.get('num_vehicles', 1)
    if vehicles_config:
        num_vehicles = len(vehicles_config)
    # Fleet mode always needs closed routes (vehicles must return to depot).
    return_to_start = data.get('return_to_start', False) or (num_vehicles > 1)

    if len(locations) < 2:
        return jsonify({"status": "error", "message": "Need at least 2 locations"}), 400
    
    if len(locations) > 25:
        return jsonify({"status": "error", "message": "Maximum 25 locations supported"}), 400
    
    try:
        # Distance and time matrices: Google, or the traffic data package
        if traffic_source == 'google':
            matrices = get_distance_matrix(locations)
        else:
            if not departure_time:
                raise ValueError("departure_time is needed for traffic data")
            matrices = traffic_data.travel_matrices(locations, traffic_source, departure_time)
        matrix = matrices['distance'] if mode == 'distance' else matrices['time']

        # Get elevation data for energy calculations
        elevations = get_elevation_data(locations)

        # Extract energy model parameters from request (with defaults)
        energy_params = data.get('energy_params', {})
        temperature_c       = energy_params.get('temperature_c', 20.0)
        total_weight_lbs    = float(energy_params.get('total_weight_lbs', 19500.0))
        base_weight_lbs     = float(energy_params.get('base_weight_lbs', 7000.0))
        unload_time_min     = float(energy_params.get('unload_time_min', 30.0))
        # Share of the battery each truck must still hold at every point of its route
        battery_reserve_pct = float(energy_params.get('battery_reserve_pct', DEFAULT_BATTERY_RESERVE_PCT))
        if not 0 <= battery_reserve_pct < 100:
            return jsonify({"status": "error", "message": "battery_reserve_pct must be between 0 and 100"}), 400
        has_climate_control = energy_params.get('has_climate_control', True)

        base_weight_tonnes  = base_weight_lbs / 2204.62

        # Build per-vehicle config. If caller sent a `vehicles` array, use it;
        # otherwise synthesise one uniform config per vehicle from energy_params.
        DEFAULT_BATTERY_KWH  = 80.0
        DEFAULT_WEIGHT_LBS   = 13000.0
        PACK_WEIGHT_LBS      = 200 * 2.20462   # ≈ 440.9 lbs per pack (200 kg)
        BASE_TRUCK_4PACKS    = 7000.0           # empty truck weight with 4 packs installed
        DEFAULT_PACKS        = 4

        def vehicle_base_weight_lbs(vcfg):
            """Return the empty-truck weight for this vehicle based on its pack count."""
            packs = int(vcfg.get('battery_packs', DEFAULT_PACKS))
            return BASE_TRUCK_4PACKS + (packs - DEFAULT_PACKS) * PACK_WEIGHT_LBS

        def vehicle_total_weight_lbs(vcfg):
            """Return the loaded-truck weight; weight_lbs wins over legacy weight_tonnes."""
            if 'weight_lbs' in vcfg:
                return float(vcfg['weight_lbs'])
            if 'weight_tonnes' in vcfg:
                return float(vcfg['weight_tonnes']) * 2204.62
            return DEFAULT_WEIGHT_LBS
        if not vehicles_config:
            vehicles_config = [
                {
                    'id': i + 1,
                    'name': f'Vehicle {i + 1}',
                    'weight_lbs': DEFAULT_WEIGHT_LBS,
                    'battery_kwh': DEFAULT_BATTERY_KWH,
                }
                for i in range(num_vehicles)
            ]

        # Held-Karp uses first vehicle's weight (single vehicle).
        # weight_lbs takes precedence; fall back to legacy weight_tonnes if present.
        hk_cfg = vehicles_config[0]
        if 'weight_lbs' in hk_cfg:
            total_weight_lbs = float(hk_cfg['weight_lbs'])
        elif 'weight_tonnes' in hk_cfg:
            total_weight_lbs = float(hk_cfg['weight_tonnes']) * 2204.62
        hk_base_weight_lbs  = vehicle_base_weight_lbs(hk_cfg)
        hk_base_weight_tonnes = hk_base_weight_lbs / 2204.62
        cargo_weight_lbs    = max(0.0, total_weight_lbs - hk_base_weight_lbs)
        total_weight_tonnes = total_weight_lbs / 2204.62

        # Run requested algorithms
        results = {}
        
        if 'held_karp' in algorithms or 'optimal' in algorithms:
            start = time.time()
            results['optimal'] = solve_tsp_held_karp(matrix, return_to_start)
            results['optimal']['solve_time_ms'] = (time.time() - start) * 1000
        
        if 'nearest_neighbor' in algorithms or 'greedy' in algorithms:
            start = time.time()
            results['greedy'] = solve_tsp_nearest_neighbor(matrix, return_to_start)
            results['greedy']['solve_time_ms'] = (time.time() - start) * 1000

        if '2opt' in algorithms:
            start = time.time()
            results['2opt'] = solve_tsp_2opt(matrix, return_to_start=return_to_start)
            results['2opt']['solve_time_ms'] = (time.time() - start) * 1000
        
        # Calculate full metrics for each result
        for key, result in results.items():
            order = result['order']

            # Calculate both distance and time for the route
            total_distance = sum(
                matrices['distance'][order[i]][order[i+1]]
                for i in range(len(order)-1)
            )
            total_time = sum(
                matrices['time'][order[i]][order[i+1]]
                for i in range(len(order)-1)
            )

            # Add return to start if in depot mode
            if return_to_start:
                total_distance += matrices['distance'][order[-1]][order[0]]
                total_time += matrices['time'][order[-1]][order[0]]

            result['total_distance_m'] = total_distance
            result['total_distance_mi'] = total_distance / 1609.34
            result['total_time_s'] = total_time
            result['total_time_min'] = total_time / 60
            result['return_to_start'] = return_to_start

            # Build ordered locations list
            result['ordered_locations'] = [locations[i] for i in order]

            # Build leg-by-leg breakdown with energy calculations
            legs = []
            total_energy_kwh = 0

            # num_delivery_stops = all stops except the starting depot
            num_delivery_stops = len(order) - 1
            weight_per_stop_lbs = cargo_weight_lbs / num_delivery_stops if num_delivery_stops > 0 else 0

            for i in range(len(order) - 1):
                from_idx = order[i]
                to_idx = order[i+1]

                distance_m = matrices['distance'][from_idx][to_idx]
                time_s = matrices['time'][from_idx][to_idx]
                distance_km = distance_m / 1000

                avg_speed_kmh = estimate_average_speed(distance_m, time_s)
                altitude_diff_m = elevations[to_idx] - elevations[from_idx]

                # Weight decreases after each stop is visited (leg i departs after i stops delivered)
                current_weight_lbs = total_weight_lbs - i * weight_per_stop_lbs
                current_weight_tonnes = current_weight_lbs / 2204.62

                energy_data = calculate_energy_consumption(
                    distance_km=distance_km,
                    avg_speed_kmh=avg_speed_kmh,
                    temperature_c=temperature_c,
                    vehicle_weight_tonnes=current_weight_tonnes,
                    altitude_diff_m=altitude_diff_m,
                    altitude_m=elevations[from_idx],
                    has_climate_control=has_climate_control
                )

                total_energy_kwh += energy_data['energy_kwh']

                legs.append({
                    "from": locations[from_idx],
                    "to": locations[to_idx],
                    "distance_m": distance_m,
                    "distance_mi": distance_m / 1609.34,
                    "distance_km": distance_km,
                    "time_s": time_s,
                    "time_min": time_s / 60,
                    "avg_speed_kmh": avg_speed_kmh,
                    "avg_speed_mph": avg_speed_kmh * 0.621371,
                    "elevation_from_m": elevations[from_idx],
                    "elevation_to_m": elevations[to_idx],
                    "elevation_gain_m": altitude_diff_m,
                    "vehicle_weight_lbs": round(current_weight_lbs, 1),
                    "vehicle_weight_tonnes": round(current_weight_tonnes, 3),
                    "energy_kwh": energy_data['energy_kwh'],
                    "energy_per_km": energy_data['energy_per_km'],
                    "energy_per_mile": energy_data['energy_per_mile'],
                    "energy_breakdown": energy_data['breakdown']
                })

            # Return leg — truck is empty (only base weight)
            if return_to_start:
                from_idx = order[-1]
                to_idx = order[0]

                distance_m = matrices['distance'][from_idx][to_idx]
                time_s = matrices['time'][from_idx][to_idx]
                distance_km = distance_m / 1000

                avg_speed_kmh = estimate_average_speed(distance_m, time_s)
                altitude_diff_m = elevations[to_idx] - elevations[from_idx]

                energy_data = calculate_energy_consumption(
                    distance_km=distance_km,
                    avg_speed_kmh=avg_speed_kmh,
                    temperature_c=temperature_c,
                    vehicle_weight_tonnes=hk_base_weight_tonnes,
                    altitude_diff_m=altitude_diff_m,
                    altitude_m=elevations[from_idx],
                    has_climate_control=has_climate_control
                )

                total_energy_kwh += energy_data['energy_kwh']

                legs.append({
                    "from": locations[from_idx],
                    "to": locations[to_idx],
                    "distance_m": distance_m,
                    "distance_mi": distance_m / 1609.34,
                    "distance_km": distance_km,
                    "time_s": time_s,
                    "time_min": time_s / 60,
                    "avg_speed_kmh": avg_speed_kmh,
                    "avg_speed_mph": avg_speed_kmh * 0.621371,
                    "elevation_from_m": elevations[from_idx],
                    "elevation_to_m": elevations[to_idx],
                    "elevation_gain_m": altitude_diff_m,
                    "vehicle_weight_lbs": round(base_weight_lbs, 1),
                    "vehicle_weight_tonnes": round(base_weight_tonnes, 3),
                    "energy_kwh": energy_data['energy_kwh'],
                    "energy_per_km": energy_data['energy_per_km'],
                    "energy_per_mile": energy_data['energy_per_mile'],
                    "energy_breakdown": energy_data['breakdown'],
                    "return_leg": True
                })

            # Time breakdown: travel time + unload time (30 min default per stop)
            travel_time_s  = result['total_time_s']
            unload_time_s  = int(unload_time_min * 60 * num_delivery_stops)
            result['travel_time_s']  = travel_time_s
            result['unload_time_s']  = unload_time_s
            result['total_time_s']   = travel_time_s + unload_time_s
            result['total_time_min'] = result['total_time_s'] / 60

            result['legs'] = legs
            result['total_energy_kwh'] = total_energy_kwh
            result['avg_energy_per_km'] = total_energy_kwh / (result['total_distance_m'] / 1000) if result['total_distance_m'] > 0 else 0
            result['avg_energy_per_mile'] = total_energy_kwh / result['total_distance_mi'] if result['total_distance_mi'] > 0 else 0
            result['weight_params'] = {
                'total_weight_lbs': total_weight_lbs,
                'base_weight_lbs': hk_base_weight_lbs,
                'cargo_weight_lbs': cargo_weight_lbs,
                'weight_per_stop_lbs': round(weight_per_stop_lbs, 1),
                'num_delivery_stops': num_delivery_stops,
            }
        
        # Plan vs reality: drive the Held-Karp route on the measured speeds
        if traffic_source != 'google' and 'optimal' in results:
            hk = results['optimal']
            actual = traffic_data.drive_on_ground_truth(
                matrices['paths'], hk['order'], departure_time, unload_time_min * 60, return_to_start)
            hk['traffic'] = {
                "source": traffic_source,
                "departure_time": departure_time,
                "planned_travel_time_s": hk['travel_time_s'],
                "actual_travel_time_s": sum(actual),
                "actual_leg_times_s": actual,
            }

        # Calculate savings if we have both optimal and greedy
        comparison = {}
        if 'optimal' in results and 'greedy' in results:
            comparison = {
                "distance_saved_m": results['greedy']['total_distance_m'] - results['optimal']['total_distance_m'],
                "distance_saved_mi": results['greedy']['total_distance_mi'] - results['optimal']['total_distance_mi'],
                "distance_saved_percent": (
                    (results['greedy']['total_distance_m'] - results['optimal']['total_distance_m']) /
                    results['greedy']['total_distance_m'] * 100
                ) if results['greedy']['total_distance_m'] > 0 else 0,
                "time_saved_s": results['greedy']['total_time_s'] - results['optimal']['total_time_s'],
                "time_saved_min": results['greedy']['total_time_min'] - results['optimal']['total_time_min'],
                "time_saved_percent": (
                    (results['greedy']['total_time_s'] - results['optimal']['total_time_s']) /
                    results['greedy']['total_time_s'] * 100
                ) if results['greedy']['total_time_s'] > 0 else 0,
                "energy_saved_kwh": results['greedy']['total_energy_kwh'] - results['optimal']['total_energy_kwh'],
                "energy_saved_percent": (
                    (results['greedy']['total_energy_kwh'] - results['optimal']['total_energy_kwh']) /
                    results['greedy']['total_energy_kwh'] * 100
                ) if results['greedy']['total_energy_kwh'] > 0 else 0,
            }
        
        # Generate Google Maps URLs for all single-vehicle routes
        for key in results:
            ordered = results[key]['ordered_locations']
            origin = f"{ordered[0]['lat']},{ordered[0]['lng']}"

            if return_to_start:
                dest = origin
                waypoints = "|".join(f"{loc['lat']},{loc['lng']}" for loc in ordered[1:])
            else:
                dest = f"{ordered[-1]['lat']},{ordered[-1]['lng']}"
                waypoints = "|".join(f"{loc['lat']},{loc['lng']}" for loc in ordered[1:-1])

            maps_url = f"https://www.google.com/maps/dir/?api=1&origin={origin}&destination={dest}"
            if waypoints:
                maps_url += f"&waypoints={waypoints}"

            results[key]['google_maps_url'] = maps_url

        # ── OR-Tools fleet routing (only when num_vehicles > 1) ──────────────
        if num_vehicles > 1:
            fleet_cfgs = [vehicles_config[v] if v < len(vehicles_config) else vehicles_config[-1]
                          for v in range(num_vehicles)]
            # Upper-bound energy per arc for each distinct truck build (weight, packs)
            bound_cache = {}
            energy_matrices = []
            for vcfg in fleet_cfgs:
                weights = (vehicle_total_weight_lbs(vcfg), vehicle_base_weight_lbs(vcfg))
                if weights not in bound_cache:
                    bound_cache[weights] = energy_upper_bound_matrix_wh(
                        matrices['distance'], matrices['time'], elevations,
                        loaded_weight_lbs=weights[0], base_weight_lbs=weights[1],
                        temperature_c=temperature_c, has_climate_control=has_climate_control,
                    )
                energy_matrices.append(bound_cache[weights])
            energy_limits = [battery_limit_wh(float(vcfg.get('battery_kwh', DEFAULT_BATTERY_KWH)), battery_reserve_pct)
                             for vcfg in fleet_cfgs]

            start = time.time()
            try:
                vrp_routes = solve_vrp_ortools(matrix, num_vehicles,
                                               energy_matrices=energy_matrices, energy_limits=energy_limits,
                                               keep_empty=True)
            except BatteryLimitError as e:
                return jsonify({
                    "status": "error",
                    "error_type": "battery_limit",
                    "message": str(e),
                    "battery_reserve_pct": battery_reserve_pct,
                    "unreachable_stops": [locations[i] for i in e.unreachable_stops],
                }), 422
            solve_ms = (time.time() - start) * 1000

            fleet_routes = []
            fleet_total_dist = 0
            fleet_total_energy = 0

            for v_idx, route in enumerate(vrp_routes):
                if len(route) <= 2:
                    continue  # this vehicle got no stops
                # route = [0, a, b, 0] — depot at both ends
                v_dist = sum(matrices['distance'][route[i]][route[i+1]] for i in range(len(route)-1))
                v_time = sum(matrices['time'][route[i]][route[i+1]] for i in range(len(route)-1))

                # Per-vehicle config (weight, battery)
                vcfg = fleet_cfgs[v_idx]
                v_total_weight_lbs = vehicle_total_weight_lbs(vcfg)
                v_base_weight_lbs = vehicle_base_weight_lbs(vcfg)
                v_cargo_weight_lbs = max(0.0, v_total_weight_lbs - v_base_weight_lbs)
                v_weight = vcfg.get('weight_tonnes', v_total_weight_lbs / 2204.62)
                v_battery = vcfg.get('battery_kwh', DEFAULT_BATTERY_KWH)

                # Build legs with energy for this vehicle (progressive weight unloading)
                v_num_delivery_stops = len(route) - 2  # excludes depot at both ends
                v_weight_per_stop_lbs = v_cargo_weight_lbs / v_num_delivery_stops if v_num_delivery_stops > 0 else 0

                v_legs = []
                v_energy = 0
                for i in range(len(route) - 1):
                    from_idx = route[i]
                    to_idx = route[i+1]
                    distance_m = matrices['distance'][from_idx][to_idx]
                    time_s = matrices['time'][from_idx][to_idx]
                    distance_km = distance_m / 1000
                    avg_speed_kmh = estimate_average_speed(distance_m, time_s)
                    altitude_diff_m = elevations[to_idx] - elevations[from_idx]

                    is_return = (i == len(route) - 2)
                    if is_return:
                        leg_weight_lbs = v_base_weight_lbs
                    else:
                        leg_weight_lbs = v_total_weight_lbs - i * v_weight_per_stop_lbs
                    leg_weight_tonnes = leg_weight_lbs / 2204.62

                    energy_data = calculate_energy_consumption(
                        distance_km=distance_km,
                        avg_speed_kmh=avg_speed_kmh,
                        temperature_c=temperature_c,
                        vehicle_weight_tonnes=leg_weight_tonnes,
                        altitude_diff_m=altitude_diff_m,
                        altitude_m=elevations[from_idx],
                        has_climate_control=has_climate_control
                    )
                    v_energy += energy_data['energy_kwh']
                    v_legs.append({
                        "from": locations[from_idx],
                        "to": locations[to_idx],
                        "distance_m": distance_m,
                        "distance_mi": distance_m / 1609.34,
                        "time_s": time_s,
                        "time_min": time_s / 60,
                        "elevation_from_m": elevations[from_idx],
                        "elevation_to_m": elevations[to_idx],
                        "elevation_gain_m": altitude_diff_m,
                        "vehicle_weight_lbs": round(leg_weight_lbs, 1),
                        "vehicle_weight_tonnes": round(leg_weight_tonnes, 3),
                        "energy_kwh": energy_data['energy_kwh'],
                        "return_leg": is_return
                    })

                # Time breakdown
                v_travel_time_s = v_time
                v_unload_time_s = int(unload_time_min * 60 * v_num_delivery_stops)
                v_total_time_s  = v_travel_time_s + v_unload_time_s

                # Battery state
                battery_remaining = max(0.0, v_battery - v_energy)
                battery_pct = (battery_remaining / v_battery * 100) if v_battery > 0 else 0

                # Google Maps URL for this vehicle (closed — origin == destination)
                depot_loc = locations[route[0]]
                origin_str = f"{depot_loc['lat']},{depot_loc['lng']}"
                waypoints_str = "|".join(
                    f"{locations[route[i]]['lat']},{locations[route[i]]['lng']}"
                    for i in range(1, len(route) - 1)
                )
                v_url = f"https://www.google.com/maps/dir/?api=1&origin={origin_str}&destination={origin_str}"
                if waypoints_str:
                    v_url += f"&waypoints={waypoints_str}"

                fleet_total_dist += v_dist
                fleet_total_energy += v_energy
                fleet_routes.append({
                    "vehicle_id": vcfg.get('id', v_idx + 1),
                    "vehicle_name": vcfg.get('name', f'Vehicle {v_idx + 1}'),
                    "vehicle_config": {
                        "total_weight_lbs": v_total_weight_lbs,
                        "cargo_weight_lbs": v_cargo_weight_lbs,
                        "weight_per_stop_lbs": round(v_weight_per_stop_lbs, 1),
                        "battery_kwh": v_battery,
                    },
                    "order": route,
                    "ordered_locations": [locations[i] for i in route],
                    "total_distance_m": v_dist,
                    "total_distance_mi": v_dist / 1609.34,
                    "travel_time_s": v_travel_time_s,
                    "unload_time_s": v_unload_time_s,
                    "total_time_s": v_total_time_s,
                    "total_time_min": v_total_time_s / 60,
                    "num_delivery_stops": v_num_delivery_stops,
                    "total_energy_kwh": v_energy,
                    "battery_capacity_kwh": v_battery,
                    "battery_remaining_kwh": round(battery_remaining, 2),
                    "battery_remaining_pct": round(battery_pct, 1),
                    "battery_depleted": v_energy > v_battery,
                    "battery_reserve_pct": battery_reserve_pct,
                    "below_battery_reserve": battery_pct < battery_reserve_pct,
                    # same flag under the key the frontend reads (single-accident branch)
                    "battery_below_reserve": battery_pct < battery_reserve_pct,
                    "google_maps_url": v_url,
                    "legs": v_legs
                })

            makespan_s = max(r["total_time_s"] for r in fleet_routes)
            travel_makespan_s = max(r["travel_time_s"] for r in fleet_routes)
            results['ortools_fleet'] = {
                "num_vehicles_requested": num_vehicles,
                "num_vehicles_used": len(fleet_routes),
                "routes": fleet_routes,
                "total_distance_m": fleet_total_dist,
                "total_distance_mi": fleet_total_dist / 1609.34,
                "makespan_s": makespan_s,
                "makespan_min": makespan_s / 60,
                "travel_makespan_s": travel_makespan_s,
                "total_travel_time_s": sum(r["travel_time_s"] for r in fleet_routes),
                "total_unload_time_s": sum(r["unload_time_s"] for r in fleet_routes),
                "total_driver_time_s": sum(r["total_time_s"] for r in fleet_routes),
                "total_energy_kwh": fleet_total_energy,
                "avg_energy_per_mile": fleet_total_energy / (fleet_total_dist / 1609.34) if fleet_total_dist > 0 else 0,
                "battery_reserve_pct": battery_reserve_pct,
                "solve_time_ms": solve_ms
            }

        return jsonify({
            "status": "ok",
            "mode": mode,
            "num_locations": len(locations),
            "results": results,
            "comparison": comparison,
            "energy_params": {
                "temperature_c": temperature_c,
                "temperature_f": temperature_c * 9/5 + 32,
                "vehicle_weight_tonnes": total_weight_tonnes,
                "vehicle_weight_lbs": total_weight_lbs,
                "has_climate_control": has_climate_control,
                "model": "Model E polynomial regression (speed, temp, weight, grade, air-density interactions)"
            },
            "matrices": {
                "distance": matrices['distance'],
                "time": matrices['time']
            }
        })
        
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/directions', methods=['POST'])
def get_route_directions():
    """
    Get detailed directions for an optimized route.
    Useful for getting polylines and turn-by-turn instructions.
    """
    data = request.json
    locations = data.get('locations', [])
    order = data.get('order', list(range(len(locations))))
    
    try:
        directions = get_directions(locations, order)
        return jsonify({
            "status": "ok",
            "directions": directions
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/replan_incidents', methods=['POST'])
def replan_incidents():
    """
    Demo replanning: place accidents at the route's historical crash hotspots
    (NHTSA FARS fatal crashes) and reroute around them, for every combination
    of accidents, so the frontend can toggle each one on and off.

    The delivery order stays fixed. Each accident only affects the leg it sits
    on (stop i -> stop i+1); only that leg's from -> to route changes.

    Works for one vehicle or a fleet: for a fleet, send every vehicle's legs
    together, each tagged with its vehicle_id. The accidents are then spread
    over the vehicles first.

    Request body:
    {
        "legs": [...],         // results.optimal.legs, or every fleet route's legs
                               // in route order, each with "vehicle_id"
        "num_accidents": 3,    // 1-4 (each extra accident doubles the scenarios)
        "energy_params": {"temperature_c": 20, "has_climate_control": true}
    }

    Returns:
        accidents: [{id, lat, lng, leg_index, vehicle_id, crash_count, fatalities,
                     years: [first, last], road, crashes: [{lat, lng, year}]}]
        scenarios: {"1": legs, "1,3": legs, ...} keyed by the active accident
                   ids (sorted, comma-joined), where legs is
                   [{leg_index, vehicle_id, accident_ids, method, original, rerouted}] and
                   original/rerouted have distance_m, time_s, energy_kwh and path
    """
    data = request.json
    legs = data.get('legs') or []
    num_accidents = max(1, min(int(data.get('num_accidents', 3)), 4))
    energy_params = data.get('energy_params', {})

    if not legs or any('from' not in leg or 'to' not in leg for leg in legs):
        return jsonify({"status": "error", "message": "Need legs with 'from' and 'to'"}), 400

    try:
        replan = replan_route_around_hotspots(legs, num_accidents)
        if not replan['accidents']:
            raise Exception("No historical crash hotspots found along this route")

        accidents = []
        for a in replan['accidents']:
            roads = [c[4] for c in a['crashes'] if c[4]]
            years = [c[2] for c in a['crashes']]
            accidents.append({
                "id": a['id'],
                **a['location'],
                "leg_index": a['leg_index'],
                "vehicle_id": legs[a['leg_index']].get('vehicle_id'),
                "crash_count": len(a['crashes']),
                "fatalities": sum(c[3] for c in a['crashes']),
                "years": [min(years), max(years)],
                "road": max(set(roads), key=roads.count) if roads else None,
                "crashes": [{"lat": c[0], "lng": c[1], "year": c[2]} for c in a['crashes']],
            })

        # Same energy model as /api/optimize, with the leg's weight and elevation
        for replanned in (l for s in replan['scenarios'].values() for l in s):
            leg = legs[replanned['leg_index']]
            for key in ('original', 'rerouted'):
                replanned[key]['energy_kwh'] = route_energy_kwh(replanned[key], leg, energy_params)

        return jsonify({
            "status": "ok",
            "num_requested": num_accidents,
            "data_source": "NHTSA FARS fatal crashes",
            "accidents": accidents,
            "scenarios": replan['scenarios'],
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/plan_charging', methods=['POST'])
def plan_charging():
    """
    Find a charging stop for each vehicle that would otherwise drop below the
    SoC reserve (SOC_RESERVE_FRACTION of its battery) before it is back at the
    depot. Stops are not reordered; the charger is inserted into one leg.

    Request body:
    {
        "vehicles": [{"vehicle_id": 1, "battery_kwh": 80, "legs": [...]}],  // fleet route legs
        "energy_params": {"temperature_c": 20, "has_climate_control": true}
    }

    Returns:
        reserve_pct, and plans: [{vehicle_id, needed, reserve_kwh, final_soc_kwh,
        reserve_point, stop, alternatives}] (see plan_charging_stop), where stop has
        charger {name, address, lat, lng, max_kw}, leg_index, arrival/depart/final
        SoC in kWh, charge_kwh, charge_kw, charge_time_s, detour_distance_m,
        detour_time_s, detour_energy_kwh, added_time_s and the to/from charger paths
    """
    data = request.json
    vehicles = data.get('vehicles') or []
    energy_params = data.get('energy_params', {})

    if not vehicles or any(not v.get('legs') or not v.get('battery_kwh') for v in vehicles):
        return jsonify({"status": "error", "message": "Need vehicles with 'battery_kwh' and 'legs'"}), 400

    try:
        plans = [{"vehicle_id": v.get('vehicle_id'),
                  **plan_charging_stop(v['legs'], float(v['battery_kwh']), energy_params)}
                 for v in vehicles]
        return jsonify({"status": "ok", "reserve_pct": SOC_RESERVE_FRACTION * 100, "plans": plans})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# =============================================================================
# Benchmark Endpoints
# =============================================================================

@app.route('/api/benchmark/scenarios', methods=['GET'])
def benchmark_scenarios():
    """List saved benchmark scenarios."""
    return jsonify({"status": "ok", "scenarios": list_scenarios()})


@app.route('/api/benchmark/algorithms', methods=['GET'])
def benchmark_algorithms():
    """List registered benchmark algorithms."""
    return jsonify({"status": "ok", "algorithms": [a.to_dict() for a in BENCHMARK_ALGORITHMS]})


@app.route('/api/benchmark/fixed_plan', methods=['GET'])
def benchmark_fixed_plan():
    """The fixed stop order (which truck visits which stops, in order) for a scenario and fleet size."""
    scenario_id = request.args.get('scenario_id')
    if not scenario_id:
        return jsonify({"status": "error", "message": "scenario_id is required"}), 400
    try:
        plan = fixed_plan_view(scenario_id, int(request.args.get('num_vehicles', 1)))
    except ValueError as e:
        return jsonify({"status": "error", "message": str(e)}), 400
    return jsonify({"status": "ok", **plan})


@app.route('/api/benchmark/run', methods=['POST'])
def benchmark_run():
    """
    Run every selected algorithm on a saved scenario.

    Fleet size: send "num_vehicles": 5 to run K = 5 only (add
    "include_smaller_fleets": true to run K = 1..5). The legacy "max_vehicles"
    runs K = 1..max_vehicles.

    Expected JSON:
    {
        "scenario_id": "demo_anaheim",
        "num_vehicles": 5,
        "include_smaller_fleets": false,
        "preserve_stop_order": false,            // lock every truck to the fixed plan; all algorithms return it unchanged
        "algorithms": ["nn", "ortools", ...],   // optional, default all
        "ortools_time_limit_s": 5,               // optional, default production
        "objective": {                           // optional, default distance
            "mode": "distance" | "time" | "energy" | "blend",
            "weights": {"distance": 50, "time": 25, "energy": 25}   // blend only
        },
        "energy_params": {"temperature_c": 20, "loaded_weight_lbs": 13000,
                          "base_weight_lbs": 7000, "has_climate_control": true}   // optional
    }
    """
    data = request.json or {}
    scenario_id = data.get('scenario_id')
    if not scenario_id:
        return jsonify({"status": "error", "message": "scenario_id is required"}), 400

    time_limit = data.get('ortools_time_limit_s')
    fleet_sizes = None
    try:
        if data.get('num_vehicles') is not None:
            k = int(data['num_vehicles'])
            fleet_sizes = list(range(1, k + 1)) if data.get('include_smaller_fleets') else [k]
        result = run_benchmark(
            scenario_id,
            max_vehicles=int(data.get('max_vehicles', 3)),
            fleet_sizes=fleet_sizes,
            preserve_stop_order=bool(data.get('preserve_stop_order')),
            algorithm_ids=data.get('algorithms') or None,
            ortools_time_limit_s=int(time_limit) if time_limit else None,
            objective=data.get('objective'),
            energy_params=data.get('energy_params'),
        )
    except ValueError as e:
        return jsonify({"status": "error", "message": str(e)}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

    return jsonify({"status": "ok", **result})


@app.route('/api/benchmark/simulate', methods=['POST'])
def benchmark_simulate():
    """
    Plan a battery-limited fleet on a saved scenario, then simulate driving it
    with extra battery drain, replanning (and dropping stops if needed) at
    every stop. See benchmark/simulate.py.

    Expected JSON:
    {
        "scenario_id": "case1_plymouth_mi",
        "num_vehicles": 4,
        "energy_params": {"battery_kwh": 110, "battery_reserve_pct": 20, ...},   // battery_kwh required
        "drain": {"factor": 1.3, "event": {"after_stop": 3, "factor": 1.6}},     // event optional
        "objective": {...},                     // optional, as in /api/benchmark/run
        "ortools_time_limit_s": 5               // optional, initial plan only
    }
    """
    data = request.json or {}
    scenario_id = data.get('scenario_id')
    if not scenario_id:
        return jsonify({"status": "error", "message": "scenario_id is required"}), 400

    time_limit = data.get('ortools_time_limit_s')
    try:
        result = simulate(
            scenario_id,
            int(data.get('num_vehicles', 2)),
            energy_params=data.get('energy_params'),
            drain=data.get('drain'),
            objective=data.get('objective'),
            ortools_time_limit_s=int(time_limit) if time_limit else None,
        )
    except BatteryLimitError as e:
        return jsonify({"status": "error", "error_type": "battery_limit", "message": str(e)}), 422
    except (ValueError, TypeError) as e:
        return jsonify({"status": "error", "message": str(e)}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

    return jsonify({"status": "ok", **result})


# =============================================================================
# Main
# =============================================================================

if __name__ == '__main__':
    print("=" * 60)
    print("Route Optimization Server")
    print("=" * 60)
    print(f"API Key Set: {'Yes' if GOOGLE_API_KEY else 'No - set GOOGLE_MAPS_API_KEY env var'}")
    print()
    print("Endpoints:")
    print("  POST /api/set_key        - Set Google Maps API key")
    print("  POST /api/geocode        - Geocode single address")
    print("  POST /api/geocode_batch  - Geocode multiple addresses")
    print("  POST /api/optimize       - Optimize route (main endpoint)")
    print("  POST /api/directions     - Get turn-by-turn directions")
    print("  POST /api/replan_incidents - Reroute around historical crash hotspots")
    print("  POST /api/plan_charging  - Charging stops to keep a 20% SoC reserve")
    print("  GET  /api/health         - Health check")
    print("  GET  /api/benchmark/scenarios  - List benchmark scenarios")
    print("  GET  /api/benchmark/algorithms - List benchmark algorithms")
    print("  POST /api/benchmark/run        - Run algorithm comparison")
    print("  POST /api/benchmark/simulate   - Simulate battery drain + replanning")
    print()
    print("Starting server on http://localhost:5001")
    print("=" * 60)

    app.run(host='0.0.0.0', port=5001, debug=True)
