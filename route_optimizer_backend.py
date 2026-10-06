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
import json
import math
import time
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
from solvers import (
    solve_tsp_brute_force,
    solve_tsp_held_karp,
    solve_tsp_nearest_neighbor,
    solve_tsp_2opt,
    solve_vrp_ortools,
)
from benchmark import ALGORITHMS as BENCHMARK_ALGORITHMS, list_scenarios, run_benchmark

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
        raise Exception(f"Geocoding failed for '{address}': {data['status']}")
    
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

    Batches requests in 10x10 chunks to stay within the Google Distance Matrix
    API limit of 100 elements per request (25 origins, 25 destinations).

    Args:
        locations: List of dicts with 'lat' and 'lng' keys

    Returns:
        Dict with 'distance' and 'time' matrices (values in meters and seconds)
    """
    n = len(locations)
    coords = [f"{loc['lat']},{loc['lng']}" for loc in locations]

    distance_matrix = [[0] * n for _ in range(n)]
    time_matrix = [[0] * n for _ in range(n)]

    url = "https://maps.googleapis.com/maps/api/distancematrix/json"
    chunk_size = 10  # 10x10 = 100 elements, exactly at the API limit

    for i_start in range(0, n, chunk_size):
        i_end = min(i_start + chunk_size, n)
        origin_coords = coords[i_start:i_end]

        for j_start in range(0, n, chunk_size):
            j_end = min(j_start + chunk_size, n)
            dest_coords = coords[j_start:j_end]

            params = {
                "origins": "|".join(origin_coords),
                "destinations": "|".join(dest_coords),
                "key": GOOGLE_API_KEY,
                "units": "imperial"
            }

            response = requests.get(url, params=params)
            data = response.json()

            if data["status"] != "OK":
                raise Exception(f"Distance Matrix API error: {data['status']}")

            for ri, row in enumerate(data["rows"]):
                for rj, element in enumerate(row["elements"]):
                    gi = i_start + ri
                    gj = j_start + rj
                    if element["status"] == "OK":
                        distance_matrix[gi][gj] = element["distance"]["value"]
                        time_matrix[gi][gj] = element["duration"]["value"]
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
        Directions API response with route details
    """
    ordered_locs = [locations[i] for i in order]
    
    origin = f"{ordered_locs[0]['lat']},{ordered_locs[0]['lng']}"
    destination = f"{ordered_locs[-1]['lat']},{ordered_locs[-1]['lng']}"
    
    waypoints = []
    for loc in ordered_locs[1:-1]:
        waypoints.append(f"{loc['lat']},{loc['lng']}")
    
    url = "https://maps.googleapis.com/maps/api/directions/json"
    params = {
        "origin": origin,
        "destination": destination,
        "key": GOOGLE_API_KEY
    }
    
    if waypoints:
        params["waypoints"] = "|".join(waypoints)
    
    response = requests.get(url, params=params)
    return response.json()


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
# Energy Consumption Model — Model E regression
# =============================================================================

def calculate_energy_consumption(
    distance_km: float,
    avg_speed_kmh: float,
    temperature_c: float,
    vehicle_weight_tonnes: float,
    altitude_diff_m: float,
    altitude_m: float,
    has_climate_control: bool = True
) -> dict:
    """
    Calculate energy consumption using Model E polynomial regression.

    Model E extends Model D with two air-density interaction terms that capture
    how aerodynamic drag varies with temperature and absolute altitude:
      F_aero ∝ ρ(T, alt) · v²   where ρ decreases with higher T and higher alt.

    Formula (returns kWh per leg):
      grade = alt_diff_m / dist_km   (m/km)
      e_per_km = b0 + b1*v + b2*T + b3*W + b4*Δh + b5*v²
               + b6*(W·grade) + b7*(v·grade)
               + b8*(v²·T)    + b9*(v²·alt_m)
      total_kwh = e_per_km * dist_km

    Target range: 0.85–1.3 kWh/mile (Isuzu N-Series EV)
    R²=0.9997, MAPE=3.46%, test RMSE=0.38 kWh

    Args:
        distance_km:           Leg distance in km
        avg_speed_kmh:         Average speed in km/h
        temperature_c:         Ambient temperature in °C
        vehicle_weight_tonnes: Gross vehicle weight in tonnes
        altitude_diff_m:       Elevation change over leg in m (+ = uphill)
        altitude_m:            Absolute elevation at leg start in m (above sea level)
        has_climate_control:   HVAC active (additive overhead, not in regression)

    Returns:
        Dict with energy_kwh and breakdown
    """
    # Model E fitted coefficients (Isuzu N-Series EV, R²=0.9997, MAPE=3.46%)
    # Units: dist=km, speed=km/h, temp=°C, weight=tonnes, alt=metres, grade=m/km
    b0 =  5.7142142481e-01   # intercept
    b1 = -1.0081493644e-02   # speed (v)
    b2 =  1.4087882356e-04   # temperature (T)
    b3 =  1.8176062830e-02   # weight (W)
    b4 =  1.7585256876e-05   # altitude change (Δh)
    b5 =  1.2923132040e-04   # speed squared (v²)
    b6 =  2.8886974460e-03   # weight × road grade (W·Δh/d)
    b7 = -9.9890433238e-06   # speed × road grade (v·Δh/d)
    b8 = -2.6120414024e-07   # speed²×temp  (ρ–temperature coupling)
    b9 = -8.6411314209e-09   # speed²×alt_m (ρ–altitude coupling)

    # Avoid division by zero on zero-length legs
    if distance_km <= 0:
        return {"energy_kwh": 0.0, "energy_per_km": 0.0, "energy_per_mile": 0.0,
                "miles_per_kwh": 0.0, "kwh_per_mile": 0.0, "breakdown": {}}

    dist  = distance_km      # km
    v     = avg_speed_kmh    # km/h
    T     = temperature_c
    W     = vehicle_weight_tonnes
    dh    = altitude_diff_m
    h     = altitude_m

    # grade = alt_diff_m / dist_km  →  m/km (as model expects)
    grade = dh / dist

    energy_per_km = (
        b0
        + b1 * v
        + b2 * T
        + b3 * W
        + b4 * dh
        + b5 * v**2
        + b6 * (W * grade)
        + b7 * (v * grade)
        + b8 * (v**2 * T)
        + b9 * (v**2 * h)
    )

    # HVAC additive overhead (0.092 kWh/km)
    hvac_per_km = 0.092 if has_climate_control else 0.0
    energy_per_km += hvac_per_km

    total_energy_kwh = energy_per_km * dist

    dist_miles = distance_km * 0.621371
    energy_per_mile = total_energy_kwh / dist_miles if dist_miles > 0 else 0
    miles_per_kwh = dist_miles / total_energy_kwh if total_energy_kwh > 0 else 0

    return {
        "energy_kwh": total_energy_kwh,
        "energy_per_km": energy_per_km,
        "energy_per_mile": energy_per_mile,
        "miles_per_kwh": miles_per_kwh,
        "kwh_per_mile": 1 / miles_per_kwh if miles_per_kwh > 0 else 0,
        "breakdown": {
            "intercept_kwh":       b0             * dist,
            "speed_kwh":           b1 * v         * dist,
            "temperature_kwh":     b2 * T         * dist,
            "weight_kwh":          b3 * W         * dist,
            "altitude_diff_kwh":   b4 * dh        * dist,
            "speed_sq_kwh":        b5 * v**2      * dist,
            "weight_grade_kwh":    b6 * W * grade * dist,
            "speed_grade_kwh":     b7 * v * grade * dist,
            "aero_temp_kwh":       b8 * v**2 * T  * dist,
            "aero_alt_kwh":        b9 * v**2 * h  * dist,
            "climate_control_kwh": hvac_per_km    * dist,
        }
    }


def estimate_average_speed(distance_m: float, time_s: float) -> float:
    """
    Estimate average speed from distance and time.

    Args:
        distance_m: Distance in meters
        time_s: Time in seconds

    Returns:
        Average speed in km/h
    """
    if time_s == 0:
        return 0.0

    distance_km = distance_m / 1000
    time_h = time_s / 3600
    return distance_km / time_h


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
        "algorithms": ["held_karp", "nearest_neighbor", "2opt"]  // optional
    }
    
    Returns optimized route with comparison between algorithms.
    """
    data = request.json
    locations = data.get('locations', [])
    mode = data.get('mode', 'distance')
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
        # Get distance matrix from Google
        matrices = get_distance_matrix(locations)
        matrix = matrices['distance'] if mode == 'distance' else matrices['time']

        # Get elevation data for energy calculations
        elevations = get_elevation_data(locations)

        # Extract energy model parameters from request (with defaults)
        energy_params = data.get('energy_params', {})
        temperature_c       = energy_params.get('temperature_c', 20.0)
        total_weight_lbs    = float(energy_params.get('total_weight_lbs', 19500.0))
        base_weight_lbs     = float(energy_params.get('base_weight_lbs', 7000.0))
        unload_time_min     = float(energy_params.get('unload_time_min', 30.0))
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
            start = time.time()
            vrp_routes = solve_vrp_ortools(matrix, num_vehicles)
            solve_ms = (time.time() - start) * 1000

            fleet_routes = []
            fleet_total_dist = 0
            fleet_total_energy = 0

            for v_idx, route in enumerate(vrp_routes):
                # route = [0, a, b, 0] — depot at both ends
                v_dist = sum(matrices['distance'][route[i]][route[i+1]] for i in range(len(route)-1))
                v_time = sum(matrices['time'][route[i]][route[i+1]] for i in range(len(route)-1))

                # Per-vehicle config (weight, battery)
                vcfg = vehicles_config[v_idx] if v_idx < len(vehicles_config) else vehicles_config[-1]
                if 'weight_lbs' in vcfg:
                    v_total_weight_lbs = float(vcfg['weight_lbs'])
                elif 'weight_tonnes' in vcfg:
                    v_total_weight_lbs = float(vcfg['weight_tonnes']) * 2204.62
                else:
                    v_total_weight_lbs = DEFAULT_WEIGHT_LBS
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


@app.route('/api/benchmark/run', methods=['POST'])
def benchmark_run():
    """
    Run every selected algorithm for K = 1..max_vehicles on a saved scenario.

    Expected JSON:
    {
        "scenario_id": "demo_anaheim",
        "max_vehicles": 3,
        "algorithms": ["nn", "ortools", ...],   // optional, default all
        "ortools_time_limit_s": 5                // optional, default production
    }
    """
    data = request.json or {}
    scenario_id = data.get('scenario_id')
    if not scenario_id:
        return jsonify({"status": "error", "message": "scenario_id is required"}), 400

    time_limit = data.get('ortools_time_limit_s')
    try:
        result = run_benchmark(
            scenario_id,
            max_vehicles=int(data.get('max_vehicles', 3)),
            algorithm_ids=data.get('algorithms') or None,
            ortools_time_limit_s=int(time_limit) if time_limit else None,
        )
    except ValueError as e:
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
    print("  GET  /api/health         - Health check")
    print("  GET  /api/benchmark/scenarios  - List benchmark scenarios")
    print("  GET  /api/benchmark/algorithms - List benchmark algorithms")
    print("  POST /api/benchmark/run        - Run algorithm comparison")
    print()
    print("Starting server on http://localhost:5001")
    print("=" * 60)

    app.run(host='0.0.0.0', port=5001, debug=True)
