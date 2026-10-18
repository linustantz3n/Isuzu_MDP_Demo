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
import re
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
    solve_vrp_constrained,
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
# Solver registry and recommendation
# =============================================================================
#
# The chatbot must never invent claims about the solvers, so everything it says
# about them is computed here: feasibility, predicted runtime and the reason.
#
# Held-Karp is O(n^2 * 2^n). Measured on this machine (matrix size n, closed
# tour): 118 ms at n=15, 621 ms at n=17, 3222 ms at n=19 — a near-constant
# ~17 ns per unit of n^2 * 2^n. That lets us price the solve before paying for
# it, instead of letting a 25-stop request hang the server for minutes.

HK_SEC_PER_UNIT = 17e-9
HK_TIME_BUDGET_MS = 2000.0     # `auto` declines Held-Karp beyond this
HK_HARD_LIMIT_N = 22           # refuse outright; ~34 s even if asked for

# 2-opt landed on the optimal tour in every measured case except n=12 (+6.6%).
TWO_OPT_TYPICAL_GAP = "0–5%"
NN_TYPICAL_GAP = "10–25%"

SOLVERS = {
    'held_karp': {
        'label': 'Held-Karp',
        'kind': 'tsp',
        'quality': 'optimal (proven)',
        'complexity': 'O(n²·2ⁿ)',
        'multi_vehicle': False,
    },
    '2opt': {
        'label': '2-opt',
        'kind': 'tsp',
        'quality': f'within {TWO_OPT_TYPICAL_GAP} of optimal',
        'complexity': 'O(n²) per pass',
        'multi_vehicle': False,
    },
    'nearest_neighbor': {
        'label': 'Nearest neighbor',
        'kind': 'tsp',
        'quality': f'{NN_TYPICAL_GAP} worse than optimal',
        'complexity': 'O(n²)',
        'multi_vehicle': False,
    },
    'ortools_vrp': {
        'label': 'OR-Tools VRP',
        'kind': 'vrp',
        'quality': 'near-optimal (metaheuristic)',
        'complexity': 'guided local search',
        'multi_vehicle': True,
    },
}

SOLVER_CHOICES = ['auto'] + list(SOLVERS)


def estimate_held_karp_ms(matrix_size: int) -> float:
    """Predicted Held-Karp runtime in ms for an n x n matrix."""
    n = max(1, int(matrix_size))
    return (n ** 2) * (2 ** n) * HK_SEC_PER_UNIT * 1000.0


CJK_RE = re.compile(r'[一-鿿]')


def detect_lang(text: str) -> str:
    """The advice is written by code, so it has to pick the operator's language itself."""
    return 'zh' if CJK_RE.search(str(text or '')) else 'en'


# Duration units, per language.
_DUR = {
    'en': [(1, "<1 ms"), (1000, "{:.0f} ms"), (90_000, "{:.1f} s"), (3_600_000, "{:.0f} min"),
           (86_400_000, "{:.1f} h"), (3.15e10, "{:.0f} days"), (None, "{:.0f} years")],
    'zh': [(1, "<1 毫秒"), (1000, "{:.0f} 毫秒"), (90_000, "{:.1f} 秒"), (3_600_000, "{:.0f} 分钟"),
           (86_400_000, "{:.1f} 小时"), (3.15e10, "{:.0f} 天"), (None, "{:.0f} 年")],
}
_DUR_DIV = [1, 1, 1000, 60_000, 3_600_000, 86_400_000, 3.15e10]


def format_duration(ms: float, lang: str = 'en') -> str:
    rows = _DUR.get(lang, _DUR['en'])
    for i, (limit, tmpl) in enumerate(rows):
        if limit is None or ms < limit:
            return tmpl if i == 0 else tmpl.format(ms / _DUR_DIV[i])
    return rows[-1][1].format(ms / _DUR_DIV[-1])


# Reason templates. One entry per branch of recommend_solver, so the English and
# Chinese wordings cannot drift apart.
REASONS = {
'fleet': {
 'en': "{veh} vehicles means this is a VRP, not a TSP. OR-Tools is the only solver here that can split "
       "stops across vehicles — Held-Karp, 2-opt and nearest neighbor all assume a single vehicle.",
 'zh': "{veh} 辆车意味着这是 VRP 而不是 TSP。这里只有 OR-Tools 能把站点分配给多辆车——"
       "Held-Karp、2-opt 和最近邻都只能解单车路线。",
},
'fleet_no_guarantee': {
 'en': " No solver here can prove an optimum for multiple vehicles; Held-Karp only does that for one. "
       "Drop to a single vehicle if you need the guarantee.",
 'zh': "另外，多车情况下这里没有任何求解器能证明最优——Held-Karp 只对单车有这个保证。"
       "如果你必须要这个保证，请把车辆数降到 1。",
},
'trivial': {
 'en': "{stops} stops — any solver returns the same route.",
 'zh': "只有 {stops} 个站，用哪个求解器结果都一样。",
},
'affordable': {
 'en': "{stops} stops: Held-Karp finishes in about {hk} and its answer is provably optimal — worth it as "
       "the baseline every other route is measured against.",
 'zh': "{stops} 个站：Held-Karp 大约 {hk} 就能跑完，而且结果在数学上可证明是最优的——"
       "作为衡量其他路线的基线，这个代价值得付。",
},
'forced_optimal': {
 'en': "{stops} stops: Held-Karp will take about {hk} — slow, but you asked for the proven optimum, "
       "and it is still within reach.",
 'zh': "{stops} 个站：Held-Karp 大约要 {hk}——是慢，但你要的是可证明的最优解，而且这个规模还跑得动。",
},
'too_slow': {
 'en': "{stops} stops puts Held-Karp at roughly {hk} — every extra stop doubles that. 2-opt stays under "
       "a millisecond and typically lands on the same tour ({gap} off optimal). Say \"I need the proven "
       "optimum\" to force it.",
 'zh': "{stops} 个站会让 Held-Karp 跑大约 {hk}——每多一个站这个时间就翻一倍。"
       "2-opt 不到 1 毫秒，而且通常能找到同一条路线（与最优相差 {gap}）。"
       "说“我要可证明的最优解”可以强制使用 Held-Karp。",
},
'beyond_limit': {
 'en': "{stops} stops is past Held-Karp's practical limit (about {hk}, and it needs {mb} M states in "
       "memory). 2-opt handles any size in milliseconds at {gap} off optimal.",
 'zh': "{stops} 个站已经超出 Held-Karp 的实用上限（约 {hk}，还需要 {mb} M 个状态驻留内存）。"
       "2-opt 不论规模都是毫秒级，与最优相差 {gap}。",
},
}

# Sentences outcome_recommend assembles around the reason.
PHRASES = {
'scope_current': {'en': "At your current {stops} stops and {veh} vehicle{s}",
                  'zh': "按当前的 {stops} 个站、{veh} 辆车"},
'scope_hypo':    {'en': "For {stops} stops and {veh} vehicle{s}",
                  'zh': "如果是 {stops} 个站、{veh} 辆车"},
'use':           {'en': "{scope}: use {label}. {reason}", 'zh': "{scope}：用 {label}。{reason}"},
'already':       {'en': " That is already what you're running.", 'zh': "这也正是你现在在用的。"},
'switch_q':      {'en': " Switch to {label}? (yes / no)", 'zh': "要切换到 {label} 吗？（是 / 否）"},
'switch_pend':   {'en': "Switch solver to {label}? (yes / no)", 'zh': "要把求解器切换到 {label} 吗？（是 / 否）"},
'current_auto':  {'en': "Solver is set to {cur}, which resolves to {eff} here. {reason}",
                  'zh': "求解器设置为 {cur}，在这里会解析成 {eff}。{reason}"},
'current_pin':   {'en': "Solver is set to {cur}. {reason}", 'zh': "求解器设置为 {cur}。{reason}"},
'why':           {'en': "Held-Karp is exponential: O(n²·2ⁿ). Measured here, it takes 118 ms at 14 stops, "
                        "621 ms at 16 and 3.2 s at 18 — every stop you add roughly doubles it, so 24 stops is "
                        "already about 13 minutes. 2-opt is O(n²) per pass, stays under a millisecond at any "
                        "size in this tool, and lands within {gap} of optimal — usually exactly on it. "
                        "{scope}, Held-Karp is priced at {hk}, so the answer is: it is worth paying while it "
                        "is cheap, and not once it is not.",
                  'zh': "Held-Karp 是指数复杂度 O(n²·2ⁿ)。在这台机器上实测：14 个站 118 毫秒，16 个站 621 毫秒，"
                        "18 个站 3.2 秒——每多一个站大约翻一倍，所以 24 个站就已经要 13 分钟左右。"
                        "2-opt 每轮是 O(n²)，在本工具的任何规模下都不到 1 毫秒，且与最优相差 {gap}——通常就是最优解。"
                        "{scope}，Held-Karp 的代价是 {hk}。所以答案是：便宜的时候值得付，不便宜的时候就不值。"},
'about_ok':      {'en': "{label} is the right pick here. {reason}", 'zh': "{label} 在这里正是合适的选择。{reason}"},
'about_workable':{'en': "{label} would work here, but {pick} is the better pick. {reason}",
                  'zh': "{label} 在这里能用，但 {pick} 更合适。{reason}"},
'about_no':      {'en': "{label} is not the one to use here — {why} {reason}",
                  'zh': "{label} 在这里不合适——{why}{reason}"},
'why_multi':     {'en': "it only handles a single vehicle.", 'zh': "它只能处理单车。"},
'why_slow':      {'en': "at {stops} stops it would take about {hk}.", 'zh': "{stops} 个站要跑大约 {hk}。"},
'why_worse':     {'en': "it is {gap} worse than optimal and costs no less than 2-opt.",
                  'zh': "它比最优差 {gap}，而代价并不比 2-opt 低。"},
'why_single':    {'en': "it needs 2 or more vehicles.", 'zh': "它需要 2 辆或更多车。"},
}


def _t(key: str, lang: str, **kw) -> str:
    return PHRASES[key].get(lang, PHRASES[key]['en']).format(**kw)


def recommend_solver(num_stops: int, num_vehicles: int, need_optimal: bool = False,
                     lang: str = 'en') -> dict:
    """Deterministic solver choice for a problem shape. No model involved.

    num_stops excludes the depot; the matrix Held-Karp sees is num_stops + 1.
    """
    num_stops = max(0, int(num_stops))
    num_vehicles = max(1, int(num_vehicles))
    n = num_stops + 1
    hk_ms = estimate_held_karp_ms(n)

    def out(branch, solver, **kw):
        text = REASONS[branch].get(lang, REASONS[branch]['en']).format(
            stops=num_stops, veh=num_vehicles, hk=format_duration(hk_ms, lang),
            gap=TWO_OPT_TYPICAL_GAP, mb=2 ** n // 1_000_000, **kw)
        return {'solver': solver, 'reason': text, 'hk_estimate_ms': hk_ms, 'branch': branch}

    if num_vehicles > 1:
        r = out('fleet', 'ortools_vrp')
        if need_optimal:
            r['reason'] += REASONS['fleet_no_guarantee'].get(lang, REASONS['fleet_no_guarantee']['en'])
        return r
    if num_stops < 2:
        return out('trivial', 'held_karp')
    if hk_ms <= HK_TIME_BUDGET_MS:
        return out('affordable', 'held_karp')
    if n <= HK_HARD_LIMIT_N:
        return out('forced_optimal', 'held_karp') if need_optimal else out('too_slow', '2opt')
    return out('beyond_limit', '2opt')


TABLE_NOTES = {
    'single_only': {'en': 'single vehicle only', 'zh': '仅支持单车'},
    'needs_multi': {'en': 'needs 2+ vehicles', 'zh': '需要 2 辆及以上'},
    'too_slow':    {'en': 'too slow ({hk})', 'zh': '太慢（{hk}）'},
    'instant':     {'en': '<1 ms', 'zh': '<1 毫秒'},
}
QUALITY_ZH = {
    'held_karp': '最优（可证明）',
    '2opt': f'与最优相差 {TWO_OPT_TYPICAL_GAP}',
    'nearest_neighbor': f'比最优差 {NN_TYPICAL_GAP}',
    'ortools_vrp': '接近最优（元启发式）',
}


def solver_table(num_stops: int, num_vehicles: int, lang: str = 'en') -> list:
    """Per-solver feasibility and cost at this problem size — the evidence behind the advice."""
    n = max(0, int(num_stops)) + 1
    multi = int(num_vehicles) > 1
    hk_ms = estimate_held_karp_ms(n)

    def note(key, **kw):
        return TABLE_NOTES[key].get(lang, TABLE_NOTES[key]['en']).format(**kw)

    rows = []
    for key, spec in SOLVERS.items():
        row = {'solver': key, 'label': spec['label'], 'complexity': spec['complexity'],
               'quality': QUALITY_ZH[key] if lang == 'zh' else spec['quality'],
               'feasible': True, 'note': ''}
        if multi and not spec['multi_vehicle']:
            row['feasible'], row['note'] = False, note('single_only')
        elif not multi and spec['multi_vehicle']:
            row['feasible'], row['note'] = False, note('needs_multi')
        elif key == 'held_karp':
            row['estimate_ms'] = hk_ms
            row['note'] = format_duration(hk_ms, lang)
            if n > HK_HARD_LIMIT_N:
                row['feasible'], row['note'] = False, note('too_slow', hk=format_duration(hk_ms, lang))
        else:
            row['note'] = note('instant')
        rows.append(row)
    return rows


def resolve_solver(requested: str, num_stops: int, num_vehicles: int) -> dict:
    """Turn a solver setting into the algorithm list /api/optimize should actually run.

    The cheap heuristics always run so the comparison panel keeps working; the
    setting really decides whether the exponential one is worth paying for.
    """
    requested = requested if requested in SOLVER_CHOICES else 'auto'
    n = max(0, int(num_stops)) + 1
    hk_ms = estimate_held_karp_ms(n)
    algorithms = ['nearest_neighbor', '2opt']
    skipped = None

    if requested == 'auto':
        # Held-Karp is the single-vehicle exact baseline the results panel is built
        # around, so affordability decides it — in fleet mode too, where it serves
        # as the "one truck does everything" reference.
        run_hk = recommend_solver(num_stops, 1)['solver'] == 'held_karp'
        if not run_hk:
            skipped = (f"Held-Karp skipped: about {format_duration(hk_ms)} at {num_stops} stops. "
                       f"Ask for the proven optimum to run it anyway.")
    elif requested == 'held_karp':
        run_hk = n <= HK_HARD_LIMIT_N
        if not run_hk:
            skipped = (f"Held-Karp refused: {num_stops} stops would take about {format_duration(hk_ms)}. "
                       f"Showing 2-opt instead.")
    elif requested == 'ortools_vrp':
        run_hk = recommend_solver(num_stops, 1)['solver'] == 'held_karp'
    else:
        run_hk = False
        skipped = f"Held-Karp skipped: solver is set to {SOLVERS[requested]['label']}."

    if run_hk:
        algorithms.insert(0, 'held_karp')

    # Which route the single-vehicle baseline panel shows when Held-Karp did not run.
    fallback = requested if requested in ('2opt', 'nearest_neighbor') else '2opt'

    return {
        'requested': requested,
        'effective': 'held_karp' if run_hk else (
            'ortools_vrp' if int(num_vehicles) > 1 else fallback),
        'algorithms': algorithms,
        'fallback_baseline': fallback,
        'held_karp_estimate_ms': hk_ms,
        'note': skipped,
    }


# =============================================================================
# Optimization goals, truck limits and breakdown re-planning
# =============================================================================
#
# The solvers minimise whatever cost matrix they are handed. The goals decide
# what goes into it: a leg's cost is a weighted mix of its distance, its driving
# time and its Model E energy, each divided by the average leg so the weights
# are comparable. "Balance" prices the latest truck's finish time (OR-Tools
# global span), so with 2+ trucks the work spreads out and everyone is back
# sooner.
#
# Operators are not expected to know weights. The chat assistant picks a level
# per goal from what they say ("battery is tight today" → energy high), and the
# table below turns levels into numbers — the model never writes a weight.

MAX_GROSS_LBS = 19500.0          # Isuzu N-Series EV GVWR; also the form's weight limit
PACK_KWH = 20.0
PACK_WEIGHT_LBS = 200 * 2.20462  # one pack is 200 kg
BASE_TRUCK_4PACKS_LBS = 7000.0   # empty truck with 4 packs
DEFAULT_PACKS = 4
LBS_PER_TONNE = 2204.62

GOAL_KEYS = ('distance', 'time', 'energy', 'balance')
GOAL_LEVELS = ('off', 'low', 'medium', 'high')
GOAL_LEVEL_WEIGHT = {'off': 0, 'low': 1, 'medium': 2, 'high': 4}
# Distance only, nothing enforced: exactly what the app did before goals existed.
DEFAULT_GOALS = {'distance': 'high', 'time': 'off', 'energy': 'off', 'balance': 'off',
                 'battery_reserve_pct': None}
MAX_RESERVE_PCT = 50
OBJ_SCALE = 100_000              # cost of one average leg at weight 1 (integer solvers need room)
DROP_PENALTY = 10 ** 9           # leaving a stop unserved costs more than any route
TRANSFER_MIN_PER_ORDER = 5       # moving one stop's cargo from a broken-down truck

# Speeds the limits answer prices range at (km/h), flat road.
SPEED_PROFILES = (('city', 40), ('mixed', 60), ('highway', 90))


GOAL_LABEL = {'distance': {'en': 'Distance', 'zh': '距离'}, 'time': {'en': 'Time', 'zh': '时间'},
              'energy': {'en': 'Energy', 'zh': '电量'}, 'balance': {'en': 'Balance', 'zh': '均衡'}}
LEVEL_LABEL = {'off': {'en': 'off', 'zh': '关'}, 'low': {'en': 'low', 'zh': '低'},
               'medium': {'en': 'medium', 'zh': '中'}, 'high': {'en': 'high', 'zh': '高'}}


def goals_summary(goals: dict, lang: str = 'en') -> str:
    """"Energy high · Distance low · reserve 20%": the goals that are on, strongest first."""
    order = sorted((k for k in GOAL_KEYS if goals.get(k, 'off') != 'off'),
                   key=lambda k: -GOAL_LEVEL_WEIGHT[goals[k]])
    parts = [f"{GOAL_LABEL[k][lang if lang == 'zh' else 'en']} {LEVEL_LABEL[goals[k]][lang if lang == 'zh' else 'en']}"
             for k in order]
    r = goals.get('battery_reserve_pct')
    if r is not None:
        parts.append(f"保留 {r}% 电量" if lang == 'zh' else f"reserve {r}%")
    return " · ".join(parts)


def weights_summary(goals: dict, lang: str = 'en') -> str:
    """"Energy 67% · Distance 33%"."""
    w = goal_weights(goals)
    key = 'zh' if lang == 'zh' else 'en'
    return " · ".join(f"{GOAL_LABEL[k][key]} {round(w[k] * 100)}%"
                      for k in sorted(GOAL_KEYS, key=lambda k: -w[k]) if w[k] > 0)


def short_stop(address: str) -> str:
    """"Brea" from "3301 E Imperial Hwy, Brea, CA 92823": the city when there is one."""
    parts = [p.strip() for p in str(address).split(',') if p.strip()]
    return parts[1] if len(parts) >= 3 else parts[0] if parts else str(address)


def empty_weight_lbs(packs) -> float:
    """Truck + batteries, no cargo. Same rule as vehicle_base_weight_lbs in optimize_route."""
    return BASE_TRUCK_4PACKS_LBS + (int(packs) - DEFAULT_PACKS) * PACK_WEIGHT_LBS


def normalize_goals(raw) -> dict:
    """Fill in defaults and drop anything that is not a known level."""
    goals = dict(DEFAULT_GOALS)
    if isinstance(raw, dict):
        for k in GOAL_KEYS:
            if raw.get(k) in GOAL_LEVELS:
                goals[k] = raw[k]
        r = raw.get('battery_reserve_pct')
        if r is not None and r != '':
            try:
                goals['battery_reserve_pct'] = int(max(0, min(MAX_RESERVE_PCT, round(float(r)))))
            except (TypeError, ValueError):
                pass
    if all(goals[k] == 'off' for k in GOAL_KEYS):
        goals['distance'] = 'high'
    return goals


def goal_weights(goals: dict) -> dict:
    """Levels → weights that add up to 1 (off 0, low 1, medium 2, high 4)."""
    raw = {k: GOAL_LEVEL_WEIGHT[goals.get(k, 'off')] for k in GOAL_KEYS}
    total = sum(raw.values()) or 1
    return {k: raw[k] / total for k in GOAL_KEYS}


def _finite(x) -> bool:
    return x is not None and isinstance(x, (int, float)) and math.isfinite(x)


def mean_offdiag(m: list) -> float:
    vals = [x for i, row in enumerate(m) for j, x in enumerate(row) if i != j and _finite(x) and x > 0]
    return sum(vals) / len(vals) if vals else 1.0


def energy_matrix_kwh(matrices: dict, elevations: list, weight_lbs: float,
                      temperature_c: float, has_climate_control: bool) -> list:
    """Model E energy for every ordered pair of locations at one truck weight."""
    dist, tim = matrices['distance'], matrices['time']
    n = len(dist)
    elev = elevations or [0.0] * n
    out = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            d, t = dist[i][j], tim[i][j]
            if not (_finite(d) and _finite(t)):
                out[i][j] = float('inf')
                continue
            out[i][j] = calculate_energy_consumption(
                distance_km=d / 1000, avg_speed_kmh=estimate_average_speed(d, t),
                temperature_c=temperature_c, vehicle_weight_tonnes=weight_lbs / LBS_PER_TONNE,
                altitude_diff_m=elev[j] - elev[i], altitude_m=elev[i],
                has_climate_control=has_climate_control)['energy_kwh']
    return out


def energy_matrix_wh(kwh: list) -> list:
    """Integer Wh for OR-Tools, rounded up and floored at 0: downhill regen is not
    banked against the battery limit, so the limit errs on the safe side."""
    return [[int(math.ceil(max(0.0, x) * 1000)) if _finite(x) else 10 ** 9 for x in row] for row in kwh]


def objective_matrix(matrices: dict, energy_kwh, goals: dict, num_vehicles: int = 1,
                     priority=()) -> dict:
    """The cost matrix the solvers minimise, built from the goals.

    cost[i][j] = OBJ_SCALE · (w_d·d/d̄ + w_t·t/t̄ + w_e·e/ē), where d̄, t̄, ē are the
    average leg. With one truck, balance folds into time (finishing that one
    route sooner is the same thing). With only balance on, driving time breaks
    ties. Pure distance with no priorities returns the distance matrix itself,
    so the default goals reproduce the original distance-only routes exactly.

    Priority stops: every arc from a regular stop into a priority stop costs an
    extra M larger than any whole route, so each truck serves its priority
    stops before its other stops.
    """
    w = goal_weights(goals)
    route_w = {'distance': w['distance'], 'time': w['time'], 'energy': w['energy']}
    if num_vehicles <= 1:
        route_w['time'] += w['balance']
    if sum(route_w.values()) == 0:
        route_w['time'] = 0.05
    norms = {'distance': mean_offdiag(matrices['distance']), 'time': mean_offdiag(matrices['time'])}
    n = len(matrices['distance'])

    plain = route_w['distance'] > 0 and route_w['time'] == 0 and route_w['energy'] == 0
    if plain and not priority:
        cost = [[x if _finite(x) else 10 ** 9 for x in row] for row in matrices['distance']]
    else:
        sources = {'distance': matrices['distance'], 'time': matrices['time']}
        if route_w['energy'] > 0:
            clamped = [[max(0.0, x) if _finite(x) else float('inf') for x in row] for row in energy_kwh]
            sources['energy'] = clamped
            norms['energy'] = mean_offdiag(clamped)
        cost = [[0] * n for _ in range(n)]
        for i in range(n):
            for j in range(n):
                if i == j:
                    continue
                c = 0.0
                for k, wk in route_w.items():
                    if wk:
                        c += wk * sources[k][i][j] / norms[k]
                cost[i][j] = int(round(c * OBJ_SCALE)) if math.isfinite(c) else 10 ** 9

    if priority:
        prio = set(priority)
        big = max((x for row in cost for x in row if x < 10 ** 9), default=1) * (n + 1) + 1
        cost = [[c + (big if (i not in prio and i != 0 and j in prio) else 0) for j, c in enumerate(row)]
                for i, row in enumerate(cost)]

    # Balance: OR-Tools prices the latest finish (seconds). Scaled so one average
    # leg of time costs about what one average leg costs in the routing term,
    # times the fleet size, so "high" balance competes with the routing cost.
    span_coef = 0
    if num_vehicles > 1 and w['balance'] > 0:
        span_coef = max(1, int(round(w['balance'] * OBJ_SCALE * num_vehicles / norms['time'])))
    return {'cost': cost, 'weights': w, 'route_weights': route_w, 'span_coef': span_coef}


def precomputed_inputs(raw, n: int):
    """Matrices and elevations the client sends back from an earlier solve of the
    same locations, so a re-solve needs no Google calls. None if anything is off."""
    if not isinstance(raw, dict):
        return None

    def square(m):
        if not isinstance(m, list) or len(m) != n:
            return None
        out = []
        for row in m:
            if not isinstance(row, list) or len(row) != n:
                return None
            # JSON has no infinity; an unroutable pair comes back as null.
            if any(x is not None and not isinstance(x, (int, float)) for x in row):
                return None
            out.append([float('inf') if x is None else x for x in row])
        return out

    dist, tim = square(raw.get('distance')), square(raw.get('time'))
    elev = raw.get('elevations')
    if dist is None or tim is None or not isinstance(elev, list) or len(elev) != n \
            or not all(isinstance(x, (int, float)) for x in elev):
        return None
    return {'matrices': {'distance': dist, 'time': tim}, 'elevations': [float(x) for x in elev]}


def stop_service_s(kind: str, unload_s: float) -> float:
    if kind in ('stop', 'done', 'rescue'):
        return unload_s
    if kind == 'pickup':
        return TRANSFER_MIN_PER_ORDER * 60
    return 0.0


def evaluate_route(nodes: list, kinds: list, cargo_delta: list, ctx: dict,
                   start_weight_lbs: float, start_time_s: float = 0.0) -> dict:
    """Walk a route leg by leg: distance, driving time, energy at the weight the
    truck carries on that leg, and the arrival / departure time at each node.

    kinds[i] says what happens at nodes[i] ('depot', 'stop', 'done', 'rescue',
    'pickup') and cargo_delta[i] how much the load changes there (− delivered,
    + picked up), so the weight leaving node i is the start weight plus every
    delta up to and including i.
    """
    D, T = ctx['matrices']['distance'], ctx['matrices']['time']
    elev = ctx['elevations'] or [0.0] * len(D)
    locations = ctx['locations']
    weight = start_weight_lbs
    arrive, depart = [start_time_s], []
    legs, dist_m, travel_s, service_s, energy = [], 0.0, 0.0, 0.0, 0.0
    for i, node in enumerate(nodes):
        svc = stop_service_s(kinds[i], ctx['unload_s']) if i > 0 else 0.0
        service_s += svc
        depart.append(arrive[i] + svc)
        weight += cargo_delta[i]
        if i == len(nodes) - 1:
            break
        a, b = node, nodes[i + 1]
        d, t = D[a][b], T[a][b]
        e = calculate_energy_consumption(
            distance_km=d / 1000, avg_speed_kmh=estimate_average_speed(d, t),
            temperature_c=ctx['temperature_c'], vehicle_weight_tonnes=weight / LBS_PER_TONNE,
            altitude_diff_m=elev[b] - elev[a], altitude_m=elev[a],
            has_climate_control=ctx['has_climate_control'])['energy_kwh']
        legs.append({
            "from": locations[a], "to": locations[b],
            "distance_m": d, "distance_mi": d / 1609.34,
            "time_s": t, "time_min": t / 60,
            "vehicle_weight_lbs": round(weight, 1),
            "vehicle_weight_tonnes": round(weight / LBS_PER_TONNE, 3),
            "energy_kwh": e,
            "return_leg": b == 0 and i + 1 == len(nodes) - 1,
            "to_kind": kinds[i + 1],
        })
        dist_m += d
        travel_s += t
        energy += e
        arrive.append(depart[i] + t)
    return {'legs': legs, 'distance_m': dist_m, 'travel_s': travel_s, 'service_s': service_s,
            'energy_kwh': energy, 'arrive_s': arrive, 'depart_s': depart,
            'end_s': depart[-1] if depart else start_time_s}


def vehicle_numbers(vcfg: dict) -> dict:
    """Gross, empty, cargo and battery for one vehicle config from the request."""
    packs = int(vcfg.get('battery_packs', DEFAULT_PACKS) or DEFAULT_PACKS)
    if 'weight_lbs' in vcfg:
        gross = float(vcfg['weight_lbs'])
    elif 'weight_tonnes' in vcfg:
        gross = float(vcfg['weight_tonnes']) * LBS_PER_TONNE
    else:
        gross = 13000.0
    base = empty_weight_lbs(packs)
    return {'packs': packs, 'gross': gross, 'base': base, 'cargo': max(0.0, gross - base),
            'battery_kwh': float(vcfg.get('battery_kwh') or packs * PACK_KWH)}


def battery_fields(capacity_kwh: float, energy_kwh: float, reserve_pct) -> dict:
    remaining = max(0.0, capacity_kwh - energy_kwh)
    pct = (remaining / capacity_kwh * 100) if capacity_kwh > 0 else 0
    out = {"battery_capacity_kwh": capacity_kwh,
           "battery_remaining_kwh": round(remaining, 2),
           "battery_remaining_pct": round(pct, 1),
           "battery_depleted": energy_kwh > capacity_kwh}
    if reserve_pct is not None:
        out["battery_reserve_pct"] = reserve_pct
        out["battery_reserve_ok"] = energy_kwh <= capacity_kwh * (1 - reserve_pct / 100) + 1e-6
    return out


def maps_url(locs: list) -> str:
    if not locs:
        return ''
    origin = f"{locs[0]['lat']},{locs[0]['lng']}"
    dest = f"{locs[-1]['lat']},{locs[-1]['lng']}"
    url = f"https://www.google.com/maps/dir/?api=1&origin={origin}&destination={dest}"
    mid = "|".join(f"{l['lat']},{l['lng']}" for l in locs[1:-1])
    return url + (f"&waypoints={mid}" if mid else '')


def fleet_route_payload(v_idx: int, vcfg: dict, nodes: list, kinds: list, ev: dict,
                        num: dict, ctx: dict, reserve_pct, status: str = None) -> dict:
    """One truck's route in the shape the results panel reads (ortools_fleet.routes[])."""
    stops = sum(1 for k in kinds if k in ('stop', 'done', 'rescue'))
    out = {
        "vehicle_index": v_idx,
        "vehicle_id": vcfg.get('id', v_idx + 1),
        "vehicle_name": vcfg.get('name', f'Vehicle {v_idx + 1}'),
        "driver": vcfg.get('driver') or '',
        "vehicle_config": {
            "total_weight_lbs": num['gross'], "cargo_weight_lbs": num['cargo'],
            "weight_per_stop_lbs": round(num.get('cargo_per_stop', 0.0), 1),
            "battery_kwh": num['battery_kwh'], "battery_packs": num['packs'],
        },
        "order": list(nodes),
        "stop_kinds": list(kinds),
        "eta_s": [round(x) for x in ev['arrive_s']],
        "ordered_locations": [ctx['locations'][i] for i in nodes],
        "total_distance_m": ev['distance_m'],
        "total_distance_mi": ev['distance_m'] / 1609.34,
        "travel_time_s": ev['travel_s'],
        "unload_time_s": ev['service_s'],
        "total_time_s": ev['end_s'],
        "total_time_min": ev['end_s'] / 60,
        "num_delivery_stops": stops,
        "total_energy_kwh": ev['energy_kwh'],
        "google_maps_url": maps_url([ctx['locations'][i] for i in nodes]),
        "legs": ev['legs'],
        **battery_fields(num['battery_kwh'], ev['energy_kwh'], reserve_pct),
    }
    if status:
        out['status'] = status
    return out


def fleet_totals(routes: list, extra: dict) -> dict:
    total_dist = sum(r['total_distance_m'] for r in routes)
    total_energy = sum(r['total_energy_kwh'] for r in routes)
    makespan = max((r['total_time_s'] for r in routes), default=0)
    return {
        "num_vehicles_used": len(routes),
        "routes": routes,
        "total_distance_m": total_dist,
        "total_distance_mi": total_dist / 1609.34,
        "makespan_s": makespan,
        "makespan_min": makespan / 60,
        "travel_makespan_s": max((r['travel_time_s'] for r in routes), default=0),
        "total_travel_time_s": sum(r['travel_time_s'] for r in routes),
        "total_unload_time_s": sum(r['unload_time_s'] for r in routes),
        "total_driver_time_s": sum(r['total_time_s'] for r in routes),
        "total_energy_kwh": total_energy,
        "avg_energy_per_mile": total_energy / (total_dist / 1609.34) if total_dist > 0 else 0,
        **extra,
    }


def solve_fleet(ctx: dict, vehicles_config: list, goals: dict, priority=(), time_limit_s: int = None) -> dict:
    """Split the stops across the fleet with OR-Tools, under the goals.

    Defaults (distance only, no reserve) build the same model as
    solve_vrp_ortools: distance cost and a cap of ceil(stops / K) per truck.
    A battery reserve adds a per-truck energy limit (planned at full load, so
    it errs on the safe side), softens the stop cap so a big-battery truck can
    take extra stops, and lets stops that fit nowhere be reported as unserved.
    """
    matrices, n = ctx['matrices'], len(ctx['matrices']['distance'])
    K = len(vehicles_config)
    num_stops = n - 1
    if K > num_stops:
        raise Exception(f"Too many vehicles ({K}) for only {num_stops} stops. Use at most {num_stops} vehicles.")
    nums = [vehicle_numbers(v) for v in vehicles_config]
    reserve = goals.get('battery_reserve_pct')
    w = goal_weights(goals)

    energy_obj = None
    if w['energy'] > 0:
        mid = sum((x['gross'] + x['base']) / 2 for x in nums) / K
        energy_obj = energy_matrix_kwh(matrices, ctx['elevations'], mid, ctx['temperature_c'],
                                       ctx['has_climate_control'])
    obj = objective_matrix(matrices, energy_obj, goals, K, priority)

    kw = {'max_stops': [math.ceil(num_stops / K)] * K}
    if obj['span_coef']:
        kw.update(time_matrix=matrices['time'],
                  service_s=[0] + [ctx['unload_s']] * num_stops, span_cost=obj['span_coef'])
    if reserve is not None:
        kw.update(energy=[energy_matrix_wh(energy_matrix_kwh(
                      matrices, ctx['elevations'], x['gross'], ctx['temperature_c'], ctx['has_climate_control']))
                      for x in nums],
                  energy_caps=[x['battery_kwh'] * (1 - reserve / 100) * 1000 for x in nums],
                  soft_stop_cap=True, optional=True, drop_penalty=DROP_PENALTY)

    start = time.time()
    sol = solve_vrp_constrained(obj['cost'], K, time_limit_s=time_limit_s or max(5, min(30, n * 2)), **kw)
    solve_ms = (time.time() - start) * 1000

    routes = []
    for v_idx, route in enumerate(sol['routes']):
        if len(route) <= 2:
            continue                       # trucks left without stops stay home
        num = dict(nums[v_idx])
        stops = len(route) - 2
        num['cargo_per_stop'] = num['cargo'] / stops
        kinds = ['depot'] + ['stop'] * stops + ['depot']
        delta = [0.0] + [-num['cargo_per_stop']] * stops + [0.0]
        ev = evaluate_route(route, kinds, delta, ctx, num['gross'])
        routes.append(fleet_route_payload(v_idx, vehicles_config[v_idx], route, kinds, ev, num, ctx, reserve))

    unserved = [{"index": i, "location": ctx['locations'][i]} for i in sol['dropped']]
    return fleet_totals(routes, {
        "num_vehicles_requested": K,
        "unserved": unserved,
        "solve_time_ms": solve_ms,
        "goals": goals,
        "weights": obj['weights'],
    })


# ---- breakdown re-plan -------------------------------------------------------
#
# A truck breaks down part-way through the day. Every truck left the depot at
# the same time, so at that moment each one is somewhere on its route:
#
#   • the broken truck has delivered its first k stops; the rest are stranded
#     with their cargo on board at the breakdown site (its k-th stop).
#   • every other truck finishes the stop it is driving to or unloading at,
#     then continues from there with the battery and cargo it has left. Its own
#     remaining stops stay on it — that cargo is already loaded.
#   • stranded stops go to whichever truck can take them. That truck first
#     drives to the breakdown site and moves the cargo across (pickup before
#     delivery, same truck, TRANSFER_MIN_PER_ORDER per stop), within its
#     battery and the 19,500 lbs gross limit.
#   • optionally a spare truck leaves the depot at the moment of the breakdown.
#
# Stops no truck can fit are reported as unserved rather than silently dropped.

REPLAN_TEXT = {
    'nothing':   {'en': "{veh} had already finished its {n} deliveries by then — nothing to re-plan.",
                  'zh': "{veh} 那时已经送完全部 {n} 个站——不需要重新规划。"},
    'summary':   {'en': "{veh} broke down {when} ({at} after dispatch). {n} stranded stop{s}: {moves}.",
                  'zh': "{veh} {when}抛锚（出发后 {at}）。{n} 个滞留站点：{moves}。"},
    'after_k':   {'en': "after stop {k}", 'zh': "在第 {k} 站之后"},
    'at_depot':  {'en': "leaving the depot", 'zh': "刚离开仓库时"},
    'move':      {'en': "{stop} → {veh}", 'zh': "{stop} → {veh}"},
    'unserved':  {'en': "{stop} → unserved", 'zh': "{stop} → 无车可送"},
    'finish':    {'en': " Last truck back {end} (was {was}).", 'zh': "最后一辆车 {end} 返回（原计划 {was}）。"},
    'need_spare': {'en': " No truck had the battery or payload left for {n} stranded stop{s} — ask me to send "
                         "a spare truck.",
                   'zh': "有 {n} 个滞留站点没有车有足够的电量或载重——可以让我派一辆备用车。"},
    'own_short': {'en': " {veh} doesn't have the battery left for {stops}.",
                  'zh': "{veh} 剩下的电量不够跑 {stops}。"},
    'spare':     {'en': "Spare truck", 'zh': "备用车"},
    'spare_unused': {'en': " The spare truck wasn't needed — the trucks on the road cover it more cheaply.",
                     'zh': "不需要备用车——路上的车接手更省。"},
}


def _rt(key: str, lang: str, **kw) -> str:
    return REPLAN_TEXT[key].get(lang, REPLAN_TEXT[key]['en']).format(**kw)


def fmt_clock(seconds: float) -> str:
    """+H:MM after dispatch."""
    m = int(round(seconds / 60))
    return f"+{m // 60}:{m % 60:02d}"


def short_place(loc: dict) -> str:
    """First part of an address: "11232 Knott Ave" from "11232 Knott Ave, Stanton, CA 90680"."""
    text = loc.get('formatted_address') or loc.get('address') or '?'
    return text.split(',')[0].strip()


def vehicle_label(vcfg: dict, idx: int) -> str:
    name = vcfg.get('name') or f'Vehicle {idx + 1}'
    return f"{name} ({vcfg['driver']})" if vcfg.get('driver') else name


def replan_breakdown(ctx: dict, vehicles_config: list, plan: list, event: dict,
                     goals: dict, lang: str = 'en', time_limit_s: int = None) -> dict:
    """Re-plan the rest of the day after a breakdown. See the notes above."""
    matrices, locations = ctx['matrices'], ctx['locations']
    reserve = goals.get('battery_reserve_pct')
    nums = [vehicle_numbers(v) for v in vehicles_config]

    # 1. Replay the original plan: when is every truck where?
    timelines = []
    for p in plan:
        v, order = int(p['vehicle_index']), list(p['order'])
        if order[-1] != 0:
            order.append(0)
        stops = len(order) - 2
        num = dict(nums[v])
        num['cargo_per_stop'] = num['cargo'] / stops if stops else 0.0
        kinds = ['depot'] + ['stop'] * stops + ['depot']
        delta = [0.0] + [-num['cargo_per_stop']] * stops + [0.0]
        timelines.append({'v': v, 'order': order, 'num': num, 'kinds': kinds, 'delta': delta,
                          'ev': evaluate_route(order, kinds, delta, ctx, num['gross'])})
    was_makespan = max(t['ev']['end_s'] for t in timelines)

    broken = next((t for t in timelines if t['v'] == int(event['vehicle_index'])), None)
    if broken is None:
        raise ValueError("That vehicle has no route in the current plan")
    b_order, b_ev = broken['order'], broken['ev']
    b_stops = len(b_order) - 2
    b_label = vehicle_label(vehicles_config[broken['v']], broken['v'])

    # 2. When and where it broke down.
    if event.get('at_minute') is not None:
        t_break = float(event['at_minute']) * 60
        k = sum(1 for j in range(1, b_stops + 1) if b_ev['depart_s'][j] <= t_break)
    else:
        k = max(0, int(event.get('after_stop') or 0))
        t_break = b_ev['depart_s'][k] if 1 <= k <= b_stops else 0.0
    if k >= b_stops:
        return {'nothing': True, 'message': _rt('nothing', lang, veh=b_label, n=b_stops)}
    crash = b_order[k] if k >= 1 else 0
    stranded = b_order[k + 1:b_stops + 1]
    b_cps = broken['num']['cargo_per_stop']

    # 3. Where every other truck picks up from.
    fleet = []          # one entry per truck in the re-plan
    for t in timelines:
        if t is broken:
            continue
        order, ev, stops = t['order'], t['ev'], len(t['order']) - 2
        j = next((j for j in range(1, stops + 1) if ev['depart_s'][j] >= t_break), None)
        if j is None:                      # done for the day: free once back at the depot
            done_nodes, start_node = order, 0
            start_time = max(t_break, ev['end_s'])
            used = ev['energy_kwh']
            remaining = []
        else:
            done_nodes, start_node = order[:j + 1], order[j]
            start_time = ev['depart_s'][j]
            used = sum(l['energy_kwh'] for l in ev['legs'][:j])
            remaining = order[j + 1:stops + 1]
        cps = t['num']['cargo_per_stop']
        fleet.append({'v': t['v'], 'num': t['num'], 'done': done_nodes, 'start': start_node,
                      'start_time': start_time, 'used': used, 'remaining': remaining,
                      'load': len(remaining) * int(cps), 'cps': cps,
                      'weight': t['num']['base'] + len(remaining) * cps, 'spare': False})

    spare_requested = bool(event.get('spare_truck')) or not fleet
    spare_cfg = None
    if spare_requested:
        spare_cfg = dict(vehicles_config[broken['v']])
        spare_cfg.update(id=max(int(v.get('id', i + 1)) for i, v in enumerate(vehicles_config)) + 1,
                         name=_rt('spare', lang), driver='')
        num = vehicle_numbers(spare_cfg)
        num.update(gross=num['base'], cargo=0.0, cargo_per_stop=0.0)
        fleet.append({'v': len(vehicles_config), 'num': num, 'done': [0], 'start': 0,
                      'start_time': t_break, 'used': 0.0, 'remaining': [], 'load': 0, 'cps': 0.0,
                      'weight': num['base'], 'spare': True})

    # 4. The re-plan problem: local node 0 is the depot; then each truck's start,
    #    its own remaining stops, the stranded stops and one pickup per stranded
    #    stop at the breakdown site.
    local = [{'orig': 0, 'kind': 'depot'}]
    starts = []
    for f in fleet:
        if f['start'] == 0:
            starts.append(0)
        else:
            starts.append(len(local))
            local.append({'orig': f['start'], 'kind': 'start'})
    allowed, deltas, pairs = {}, {}, []
    for vi, f in enumerate(fleet):
        for s in f['remaining']:
            allowed[len(local)] = [vi]
            deltas[len(local)] = -int(f['cps'])
            local.append({'orig': s, 'kind': 'stop'})
    for s in stranded:
        d_idx = len(local)
        local.append({'orig': s, 'kind': 'rescue'})
        p_idx = len(local)
        local.append({'orig': crash, 'kind': 'pickup'})
        deltas[d_idx], deltas[p_idx] = -int(b_cps), int(b_cps)
        pairs.append((p_idx, d_idx))
    m = len(local)
    orig = [x['orig'] for x in local]

    def sub(mat):
        return [[0 if a == b else mat[orig[a]][orig[b]] for b in range(m)] for a in range(m)]

    stranded_lbs = len(stranded) * b_cps
    energy_obj = None
    w = goal_weights(goals)
    if w['energy'] > 0:
        mid = sum(f['weight'] for f in fleet) / len(fleet) + stranded_lbs / 2
        energy_obj = energy_matrix_kwh(matrices, ctx['elevations'], mid, ctx['temperature_c'],
                                       ctx['has_climate_control'])
    obj = objective_matrix(matrices, energy_obj, goals, len(fleet))
    energies = []
    for f in fleet:
        heavy = min(MAX_GROSS_LBS, f['weight'] + stranded_lbs)
        energies.append(sub(energy_matrix_wh(energy_matrix_kwh(
            matrices, ctx['elevations'], heavy, ctx['temperature_c'], ctx['has_climate_control']))))
    usable = [max(0.0, f['num']['battery_kwh'] * (1 - (reserve or 0) / 100) - f['used']) * 1000 for f in fleet]

    sol = solve_vrp_constrained(
        sub(obj['cost']), len(fleet), starts=starts, ends=[0] * len(fleet),
        time_matrix=sub(matrices['time']),
        service_s=[stop_service_s(x['kind'], ctx['unload_s']) if x['kind'] != 'start' else 0 for x in local],
        start_times=[f['start_time'] for f in fleet], span_cost=obj['span_coef'],
        energy=energies, energy_caps=usable,
        load_deltas=[deltas.get(i, 0) for i in range(m)], start_loads=[f['load'] for f in fleet],
        load_caps=[MAX_GROSS_LBS - f['num']['base'] for f in fleet],
        allowed=allowed, pairs=pairs, optional=True, drop_penalty=DROP_PENALTY,
        time_limit_s=time_limit_s or max(3, min(10, m)))

    # 5. Stitch each truck's day back together and price it leg by leg.
    routes, moves = [], []
    for vi, f in enumerate(fleet):
        new = sol['routes'][vi][1:]                         # after the start node
        done_kinds = ['depot'] + ['done'] * (len(f['done']) - 1)
        if f['start'] == 0 and len(f['done']) > 1:
            done_kinds[-1] = 'depot'                        # finished, back at the depot
        nodes = list(f['done']) + [orig[x] for x in new]
        kinds = done_kinds + [('depot' if local[x]['kind'] == 'depot' else local[x]['kind']) for x in new]
        if f['spare'] and len(nodes) <= 2:
            continue                                        # spare not needed
        delta = []
        for kind in kinds:
            delta.append(-f['cps'] if kind in ('done', 'stop') else -b_cps if kind == 'rescue'
                         else b_cps if kind == 'pickup' else 0.0)
        start_weight = f['num']['gross'] if not f['spare'] else f['num']['base']
        ev = evaluate_route(nodes, kinds, delta, ctx, start_weight,
                            start_time_s=f['start_time'] if f['spare'] else 0.0)
        cfg = spare_cfg if f['spare'] else vehicles_config[f['v']]
        took = [orig[x] for x in new if local[x]['kind'] == 'rescue']
        moves += [(s, vehicle_label(cfg, f['v'])) for s in took]
        status = 'spare' if f['spare'] else ('rescue' if took else 'continues')
        num = dict(f['num'])
        routes.append(fleet_route_payload(f['v'], cfg, nodes, kinds, ev, num, ctx, reserve, status))

    b_nodes = b_order[:k + 1]
    b_kinds = ['depot'] + ['done'] * k
    b_delta = [0.0] + [-b_cps] * k
    b_eval = evaluate_route(b_nodes, b_kinds, b_delta, ctx, broken['num']['gross'])
    b_route = fleet_route_payload(broken['v'], vehicles_config[broken['v']], b_nodes, b_kinds,
                                  b_eval, broken['num'], ctx, reserve, 'broken_down')
    b_route['total_time_s'] = t_break
    b_route['total_time_min'] = t_break / 60
    b_route['breakdown_index'] = k
    routes.insert(0, b_route)
    routes.sort(key=lambda r: r['vehicle_index'])

    dropped = [orig[x] for x in sol['dropped'] if local[x]['kind'] in ('rescue', 'stop')]
    # Stops the plan already left unserved (no truck fit them) are still unserved.
    planned = {i for t in timelines for i in t['order']}
    already = [i for i in range(1, len(locations)) if i not in planned]
    own_short = {}                      # truck label -> its own stops it can no longer reach
    for x in sol['dropped']:
        if local[x]['kind'] == 'stop':
            vi = allowed[x][0]
            label = vehicle_label(vehicles_config[fleet[vi]['v']], fleet[vi]['v'])
            own_short.setdefault(label, []).append(short_place(locations[orig[x]]))
    stranded_dropped = [orig[x] for x in sol['dropped'] if local[x]['kind'] == 'rescue']
    unserved = ([{"index": i, "location": locations[i]} for i in dropped]
                + [{"index": i, "location": locations[i], "before_breakdown": True} for i in already])
    rescued = {s for s, _ in moves}
    move_text = [_rt('move', lang, stop=short_place(locations[s]), veh=label) for s, label in moves]
    move_text += [_rt('unserved', lang, stop=short_place(locations[s])) for s in stranded if s not in rescued]

    fleet_out = fleet_totals(routes, {
        "num_vehicles_requested": len(vehicles_config) + (1 if any(r.get('status') == 'spare' for r in routes) else 0),
        "unserved": unserved, "goals": goals, "weights": obj['weights'],
        "solve_time_ms": None,
    })
    sep = '；' if lang == 'zh' else '; '
    message = _rt('summary', lang, veh=b_label,
                  when=_rt('after_k', lang, k=k) if k else _rt('at_depot', lang),
                  at=fmt_clock(t_break)[1:], n=len(stranded), s='' if len(stranded) == 1 else 's',
                  moves=sep.join(move_text))
    message += _rt('finish', lang, end=fmt_clock(fleet_out['makespan_s']), was=fmt_clock(was_makespan))
    for label, stops in own_short.items():
        message += _rt('own_short', lang, veh=label, stops=', '.join(stops))
    if spare_cfg and not any(r.get('status') == 'spare' for r in routes):
        message += _rt('spare_unused', lang)
    if stranded_dropped and not spare_cfg:
        message += _rt('need_spare', lang, n=len(stranded_dropped), s='' if len(stranded_dropped) == 1 else 's')
    fleet_out['disruption'] = {
        "type": "breakdown",
        "vehicle_index": broken['v'],
        "vehicle_label": b_label,
        "after_stop": k,
        "at_s": t_break,
        "crash_site": locations[crash],
        "stranded": [locations[s] for s in stranded],
        "reassigned": [{"location": locations[s], "to": label} for s, label in moves],
        "spare_used": any(r.get('status') == 'spare' for r in routes),
        "was_makespan_s": was_makespan,
        "transfer_min_per_order": TRANSFER_MIN_PER_ORDER,
        "message": message,
    }
    return {'nothing': False, 'fleet': fleet_out, 'message': message}


# ---- truck limits ------------------------------------------------------------

LIMITS_TEXT = {
    'head': {'en': "A {packs}-pack truck carries {kwh:.0f} kWh{usable}. Empty it weighs {empty:,.0f} lbs, so it "
                   "can take up to {payload:,.0f} lbs of cargo before the {gross:,.0f} lbs limit.",
             'zh': "{packs} 块电池包的卡车共 {kwh:.0f} kWh{usable}。空车 {empty:,.0f} lbs，"
                   "在 {gross:,.0f} lbs 上限前最多能装 {payload:,.0f} lbs 货。"},
    'usable': {'en': " ({usable:.0f} kWh usable with a {r}% reserve)", 'zh': "（保留 {r}% 后可用 {usable:.0f} kWh）"},
    'range': {'en': " At {temp:.0f} °C with climate control {hvac}, mixed driving uses {lo:.2f}–{hi:.2f} kWh/mi "
                    "(empty → {w:,.0f} lbs), so it reaches about {rlo:.0f}–{rhi:.0f} miles per charge.",
              'zh': "在 {temp:.0f} °C、空调{hvac}的条件下，混合路况耗电 {lo:.2f}–{hi:.2f} kWh/英里"
                    "（空车 → {w:,.0f} lbs），一次充电约能跑 {rlo:.0f}–{rhi:.0f} 英里。"},
    'plan_ok': {'en': " Today's longest route ({veh}) needs {need:.1f} kWh — this truck covers it{kept} with "
                      "{left:.0f}% of the battery to spare.",
                'zh': "今天最长的路线（{veh}）需要 {need:.1f} kWh——这辆车跑得下{kept}，还能多出 {left:.0f}% 电量。"},
    'kept': {'en': ", keeps the {r}% reserve,", 'zh': "并保住 {r}% 的保留电量"},
    'plan_short': {'en': " Today's longest route ({veh}) needs {need:.1f} kWh — {short:.1f} kWh more than "
                         "this truck can use. It needs at least {min_packs} packs, or a shorter route.",
                   'zh': "今天最长的路线（{veh}）需要 {need:.1f} kWh——比这辆车可用的多 {short:.1f} kWh。"
                         "至少需要 {min_packs} 块电池包，或者缩短路线。"},
    'plan_none': {'en': " Today's longest route ({veh}) needs {need:.1f} kWh — more than even 9 packs "
                        "can cover; split it across more trucks.",
                  'zh': "今天最长的路线（{veh}）需要 {need:.1f} kWh——9 块电池包也不够，需要分给更多车。"},
    'on': {'en': 'on', 'zh': '开'}, 'off': {'en': 'off', 'zh': '关'},
}


def _lt(key: str, lang: str, **kw) -> str:
    return LIMITS_TEXT[key].get(lang, LIMITS_TEXT[key]['en']).format(**kw)


def truck_limits(packs: int, temperature_c: float = 20.0, has_climate_control: bool = True,
                 weight_lbs: float = None, reserve_pct=None, plan: dict = None, lang: str = 'en') -> dict:
    """Everything the assistant says about what a truck can do, computed here.

    Range comes from Model E on a flat road at three average speeds, empty and
    loaded to `weight_lbs` (default: the 19,500 lbs limit). The plan check uses
    the energy each truck's route needed in the last solve.
    """
    packs = int(packs)
    kwh = packs * PACK_KWH
    usable = kwh * (1 - (reserve_pct or 0) / 100)
    empty = empty_weight_lbs(packs)
    loaded = min(MAX_GROSS_LBS, max(empty, float(weight_lbs))) if weight_lbs else MAX_GROSS_LBS

    def kwh_per_mile(speed, w_lbs):
        e = calculate_energy_consumption(10.0, speed, temperature_c, w_lbs / LBS_PER_TONNE, 0, 0,
                                         has_climate_control)
        return e['energy_per_mile']

    rows = []
    for profile, speed in SPEED_PROFILES:
        lo, hi = kwh_per_mile(speed, empty), kwh_per_mile(speed, loaded)
        rows.append({'profile': profile, 'speed_kmh': speed, 'speed_mph': round(speed * 0.621371),
                     'empty_kwh_mi': round(lo, 3), 'loaded_kwh_mi': round(hi, 3),
                     'range_empty_mi': round(usable / lo, 1), 'range_loaded_mi': round(usable / hi, 1)})
    mixed = next(r for r in rows if r['profile'] == 'mixed')

    msg = _lt('head', lang, packs=packs, kwh=kwh, empty=empty, payload=MAX_GROSS_LBS - empty,
              gross=MAX_GROSS_LBS,
              usable=_lt('usable', lang, usable=usable, r=reserve_pct) if reserve_pct else '')
    msg += _lt('range', lang, temp=temperature_c, hvac=_lt('on' if has_climate_control else 'off', lang),
               lo=mixed['empty_kwh_mi'], hi=mixed['loaded_kwh_mi'], w=loaded,
               rlo=mixed['range_loaded_mi'], rhi=mixed['range_empty_mi'])

    plan_check = None
    routes = [r for r in (plan or {}).get('vehicles') or [] if _finite(r.get('energy_kwh'))]
    if routes:
        worst = max(routes, key=lambda r: r['energy_kwh'])
        need = float(worst['energy_kwh'])
        veh = worst.get('name') or f"Vehicle {worst.get('number', 1)}"
        if worst.get('driver'):
            veh += f" · {worst['driver']}"
        per_pack = PACK_KWH * (1 - (reserve_pct or 0) / 100)
        min_packs = max(3, math.ceil(need / per_pack - 1e-9)) if per_pack > 0 else None
        plan_check = {'vehicle': veh, 'need_kwh': round(need, 1), 'usable_kwh': round(usable, 1),
                      'fits': need <= usable, 'min_packs': min_packs}
        if need <= usable:
            msg += _lt('plan_ok', lang, veh=veh, need=need, left=(usable - need) / kwh * 100,
                       kept=_lt('kept', lang, r=reserve_pct) if reserve_pct else '')
        elif min_packs and min_packs <= 9:
            msg += _lt('plan_short', lang, veh=veh, need=need, short=need - usable, min_packs=min_packs)
        else:
            msg += _lt('plan_none', lang, veh=veh, need=need)

    return {'packs': packs, 'battery_kwh': kwh, 'usable_kwh': round(usable, 1), 'reserve_pct': reserve_pct,
            'empty_weight_lbs': round(empty), 'max_payload_lbs': round(MAX_GROSS_LBS - empty),
            'max_gross_lbs': MAX_GROSS_LBS, 'loaded_weight_lbs': round(loaded),
            'temperature_c': temperature_c, 'has_climate_control': has_climate_control,
            'rows': rows, 'plan_check': plan_check, 'message': msg, 'lang': lang}


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
    algorithms = data.get('algorithms')
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

    # Solver selection. `auto` prices Held-Karp before running it, so a large
    # request degrades to 2-opt instead of hanging the server for minutes.
    num_stops_for_solver = len(locations) - (1 if return_to_start else 0)
    solver_decision = resolve_solver(data.get('solver', 'auto'), num_stops_for_solver, num_vehicles)
    if algorithms is None:
        algorithms = solver_decision['algorithms']

    # Optimization goals. Without any, `mode` keeps its old meaning: minimise
    # distance, or driving time when mode is "time".
    if data.get('goals') is not None:
        goals = normalize_goals(data['goals'])
    else:
        goals = normalize_goals({'distance': 'off', 'time': 'high'} if mode == 'time' else None)
    priority = sorted({int(i) for i in data.get('priority_stops') or []
                       if str(i).lstrip('-').isdigit() and 0 < int(i) < len(locations)})

    try:
        # Distance / time matrices and elevations: from the client when it sends
        # back the ones it got for these same locations (a re-solve after a chat
        # change), otherwise from Google.
        pre = precomputed_inputs(data.get('precomputed'), len(locations))
        matrices = pre['matrices'] if pre else get_distance_matrix(locations)
        elevations = pre['elevations'] if pre else get_elevation_data(locations)

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

        # What the single-vehicle solvers minimise: the goals' weighted mix of
        # distance, time and energy (energy priced at vehicle 1's mid-load weight).
        energy_obj = None
        if goal_weights(goals)['energy'] > 0:
            energy_obj = energy_matrix_kwh(matrices, elevations, (total_weight_lbs + hk_base_weight_lbs) / 2,
                                           temperature_c, has_climate_control)
        objective = objective_matrix(matrices, energy_obj, goals, 1, priority)
        matrix = objective['cost']
        hk_battery_kwh = float(hk_cfg.get('battery_kwh') or
                               int(hk_cfg.get('battery_packs', DEFAULT_PACKS)) * PACK_KWH)
        reserve = goals.get('battery_reserve_pct')

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

        # The single-vehicle baseline panel always reads results['optimal'].
        # When Held-Karp was priced out, the chosen heuristic stands in and says so.
        if 'optimal' not in results:
            fb = solver_decision.get('fallback_baseline', '2opt')
            src = results.get('greedy' if fb == 'nearest_neighbor' else '2opt') or results.get('2opt')
            if src:
                stand_in = dict(src)
                stand_in['order'] = list(src['order'])
                stand_in['substituted_for'] = 'held_karp'
                results['optimal'] = stand_in

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
            # Checked against vehicle 1's battery. One truck has to visit every
            # stop, so the limit is reported here, not enforced.
            result.update(battery_fields(hk_battery_kwh, total_energy_kwh, reserve))
            # Arrival at each location after dispatch: driving, plus unloading at every stop before it.
            eta, t = [0], 0.0
            for i, leg in enumerate(legs):
                t += leg['time_s'] + (unload_time_min * 60 if i > 0 else 0)
                eta.append(round(t))
            result['eta_s'] = eta
        
        # Name the solver whose route the baseline panel is actually showing.
        baseline_key = ('held_karp' if 'held_karp' in algorithms
                        else solver_decision.get('fallback_baseline', '2opt'))
        solver_decision['baseline_solver'] = baseline_key
        solver_decision['baseline_label'] = SOLVERS[baseline_key]['label']
        solver_decision['baseline_exact'] = baseline_key == 'held_karp'

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
            ctx = {'matrices': matrices, 'elevations': elevations, 'locations': locations,
                   'temperature_c': temperature_c, 'has_climate_control': has_climate_control,
                   'unload_s': unload_time_min * 60}
            results['ortools_fleet'] = solve_fleet(ctx, vehicles_config, goals, priority)

        return jsonify({
            "status": "ok",
            "mode": mode,
            "num_locations": len(locations),
            "results": results,
            "comparison": comparison,
            "solver": solver_decision,
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
            },
            "elevations": elevations,
            "goals": {**goals, "weights": objective['weights']},
            "priority_stops": priority,
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


@app.route('/api/replan', methods=['POST'])
def replan_route():
    """
    Simulate a breakdown on the current plan and re-plan the rest of the day.

    Request body:
    {
        "locations": [...],              same as /api/optimize, index 0 = depot
        "vehicles": [...],               same as /api/optimize
        "energy_params": {...},
        "goals": {...},
        "plan": [{"vehicle_index": 1, "order": [0, 3, 1, 0]}, ...],
        "event": {"vehicle_index": 1, "after_stop": 2}     or "at_minute": 90;
                 "spare_truck": true sends a spare from the depot
        "precomputed": {"distance", "time", "elevations"}  from the plan's solve
        "lang": "en" | "zh"
    }
    """
    data = request.json or {}
    locations = data.get('locations') or []
    vehicles_config = data.get('vehicles') or []
    plan = data.get('plan') or []
    event = data.get('event') or {}
    lang = data.get('lang') if data.get('lang') in ('en', 'zh') else 'en'
    if len(locations) < 2 or not vehicles_config or not plan:
        return jsonify({"status": "error", "message": "Need locations, vehicles and the current plan"}), 400
    n = len(locations)
    for p in plan:
        order = p.get('order') or []
        if (not isinstance(p.get('vehicle_index'), int) or not 0 <= p['vehicle_index'] < len(vehicles_config)
                or not order or order[0] != 0 or any(not isinstance(i, int) or not 0 <= i < n for i in order)):
            return jsonify({"status": "error", "message": "Malformed plan"}), 400
    if not isinstance(event.get('vehicle_index'), int):
        return jsonify({"status": "error", "message": "event.vehicle_index is required"}), 400

    energy_params = data.get('energy_params') or {}
    try:
        pre = precomputed_inputs(data.get('precomputed'), n)
        ctx = {
            'matrices': pre['matrices'] if pre else get_distance_matrix(locations),
            'elevations': pre['elevations'] if pre else get_elevation_data(locations),
            'locations': locations,
            'temperature_c': float(energy_params.get('temperature_c', 20.0)),
            'has_climate_control': bool(energy_params.get('has_climate_control', True)),
            'unload_s': float(energy_params.get('unload_time_min', 30.0)) * 60,
        }
        out = replan_breakdown(ctx, vehicles_config, plan, event, normalize_goals(data.get('goals')), lang)
    except ValueError as e:
        return jsonify({"status": "error", "message": str(e)}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    if out['nothing']:
        return jsonify({"status": "ok", "nothing": True, "message": out['message']})
    return jsonify({"status": "ok", "nothing": False, "message": out['message'],
                    "results": {"ortools_fleet": out['fleet']}})


# =============================================================================
# AI Chat – lexicon fast path → constrained extraction → deterministic validation
# =============================================================================
#
#   operator utterance
#     │
#     ├─ 1. lexicon fast path ──── hit ─────────────────────────────────┐
#     │       known phrasings, no model call                            │
#     │  miss                                                           │
#     ├─ 2. constrained extraction                                      │
#     │       GPT-4o-mini, strict closed schema, four intents           │
#     │                                                                 │
#     └─ 3. deterministic validation                                    │
#             schema + null coupling                                    │
#             evidence span must appear in the utterance                │
#             ambiguity flag / term forces a question                   │
#                  │                                                    │
#                  ├─ apply change    one step, one axis  ◄─────────────┘
#                  ├─ ask a question  remembered for the session
#                  └─ out of scope    names the real capabilities
#
# The model only *proposes*; every value that reaches the form has passed
# through plain Python checks in this section.

# Any OpenAI-compatible chat endpoint: OpenAI by default, or set LLM_BASE_URL,
# LLM_API_KEY and LLM_MODEL in .env, e.g. for the U-M GPT Toolkit
# (https://api.toolkit.umgpt.umich.edu/v1), which serves the same gpt-4o-mini.
LLM_BASE_URL = os.environ.get('LLM_BASE_URL', 'https://api.openai.com/v1').rstrip('/')
OPENAI_API_KEY = os.environ.get('LLM_API_KEY') or os.environ.get('OPENAI_API_KEY', '')
OPENAI_CHAT_URL = f"{LLM_BASE_URL}/chat/completions"
OPENAI_MODEL = os.environ.get('LLM_MODEL', 'gpt-4o-mini')

INTENTS = ('apply', 'ask', 'confirm', 'recommend', 'memory', 'goals', 'limits', 'disruption', 'out_of_scope')

# Closed vocabulary: every axis the chatbot may touch, its value type and hard
# limits. Anything not listed here is out of scope by definition.
AXES = {
    'depot':               {'type': 'text',   'label': 'Depot'},
    'addresses':           {'type': 'list',   'label': 'Stops'},
    'vehicle_count':       {'type': 'number', 'label': 'Vehicles',       'min': 1,     'max': 10,    'unit': ''},
    'battery_packs':       {'type': 'number', 'label': 'Battery packs',  'min': 3,     'max': 9,     'unit': ' packs'},
    'vehicle_weight_lbs':  {'type': 'number', 'label': 'Vehicle weight', 'min': 10000, 'max': 19500, 'unit': ' lbs'},
    'temperature_c':       {'type': 'number', 'label': 'Temperature',    'min': -10,   'max': 40,    'unit': ' °C'},
    'unload_time_min':     {'type': 'number', 'label': 'Unload time',    'min': 5,     'max': 120,   'unit': ' min'},
    'has_climate_control': {'type': 'bool',   'label': 'Climate control'},
    'solver':              {'type': 'enum',   'label': 'Solver', 'choices': SOLVER_CHOICES},
    # Stops served before the others on their truck. Values are stops already on the form.
    'priority_stops':      {'type': 'list',   'label': 'Priority stops'},
    # Set only by a memory recall: one vehicle per recalled driver, not a uniform value.
    'fleet':               {'type': 'fleet',  'label': 'Fleet'},
    # Set only by the "goals" intent: levels per goal plus the battery reserve.
    'goals':               {'type': 'goals',  'label': 'Goals'},
}

AXIS_LABEL_ZH = {
    'depot': '仓库', 'addresses': '站点', 'vehicle_count': '车辆数', 'battery_packs': '电池包',
    'vehicle_weight_lbs': '整车重量', 'temperature_c': '温度', 'unload_time_min': '卸货时间',
    'has_climate_control': '空调', 'solver': '求解器', 'fleet': '车队',
    'priority_stops': '优先站点', 'goals': '优化目标',
}

CAPABILITIES_ZH = [
    "设置仓库地址",
    "设置、新增或删除配送站点",
    "设置车辆数量（1–10）",
    "设置每辆车的电池包数（3–9，每包 20 kWh）",
    "设置整车重量（10,000–19,500 lbs）",
    "设置环境温度（−10 到 40 °C）",
    "设置每站卸货时间（5–120 分钟）",
    "开启或关闭空调",
    "根据问题规模推荐求解器，并切换过去",
    "按名字记住司机（可附带所开卡车的电池包数和重量），之后直接调用",
    "根据你的情况设定优化目标（距离、时间、电量、均衡）和电量保留",
    "把某些站点设为优先，先送",
    "回答卡车的极限：电量、载重、续航，以及今天的路线跑不跑得下",
    "模拟某辆车抛锚，重新分配它没送完的站点（可派备用车）",
]


def axis_label(axis: str, lang: str = 'en') -> str:
    return AXIS_LABEL_ZH[axis] if lang == 'zh' else AXES[axis]['label']

# What the assistant can really do. Returned verbatim on the out-of-scope
# branch so the answer never depends on the model's self-description.
CAPABILITIES = [
    "Set the depot address",
    "Set, add or remove delivery stops",
    "Set the number of vehicles (1–10)",
    "Set battery packs per vehicle (3–9, 20 kWh each)",
    "Set vehicle weight (10,000–19,500 lbs)",
    "Set ambient temperature (−10 to 40 °C)",
    "Set unload time per stop (5–120 min)",
    "Turn climate control on or off",
    "Recommend which solver fits your problem size, and switch to it",
    "Remember a driver by name (optionally with their truck's packs and weight) and load them back later",
    "Set what to optimize for (distance, time, energy, balance) and a battery reserve — just describe the day",
    "Mark stops as priority so they are served first",
    "Explain a truck's limits: battery, payload, range, and whether today's routes fit",
    "Simulate a truck breaking down and re-plan its stranded stops (optionally with a spare truck)",
]

# One utterance → one applied change. Further validated changes are queued and
# released one per confirmation, so the operator always sees exactly what moved.
ONE_CHANGE_PER_TURN = True


# Digits, English and Chinese number words, and the relative forms the model
# resolves against the form state.
NUMERIC_EVIDENCE_RE = re.compile(
    r'\d|\b(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|'
    r'a|an|another|more|less|fewer|half|double|twice)\b'
    r'|[一二两三四五六七八九十零半]|多|少|再|加|减', re.I)


SIZE_STOPS_RE = re.compile(r'(\d+)\s*(?:delivery\s+)?(?:stops?|locations?|addresses|个站|个站点|站)', re.I)
SIZE_VEH_RE = re.compile(r'(\d+)\s*(?:trucks?|vehicles?|vans?|辆|台)', re.I)
NEED_OPTIMAL_RE = re.compile(r'guarantee|proven|exact|optimal|optimum|最优|精确|保证', re.I)

DEPOT_LEAD_RE = re.compile(r'^(?:the\s+)?(?:depot|warehouse|base)(?:\s+(?:is|in|at|to|=)|\s*[:=])\s+', re.I)

SOLVER_EVIDENCE_RE = re.compile(
    r'solver|algorithm|algo|held|karp|2.?opt|two.?opt|\bopt\b|nearest|neighbou?r|greedy|or.?tools|vrp|'
    r'exact|optimal|optimum|guarantee|proven|auto|heuristic|求解器|算法|精确|最优|最近邻|贪心|自动', re.I)


BOOL_EVIDENCE_RE = re.compile(
    r'\b(?:on|off|enable[ds]?|disable[ds]?|turn|switch|with|without|no|yes|keep|stop|'
    r'run(?:ning)?|use|using|hvac|a/?c|climate|cooling|heating)\b'
    r'|[开关启停]|不用|关闭|打开|空调', re.I)


def _norm(s: str) -> str:
    """Whitespace-insensitive, case-insensitive form used for substring checks."""
    return re.sub(r'\s+', '', str(s)).lower()


_EDGE_PUNCT = ' \t\r\n.,;:!?。，；：！？"\'“”‘’()（）'


def _strip_edges(s: str) -> str:
    """Models like to add a trailing period to a quote; that must not fail the span check."""
    return str(s or '').strip(_EDGE_PUNCT)


# A bare place name with no street number, ZIP, comma or state code
# ("Springfield", "Ann Arbor") can geocode to several places.
_STATE_CODE = re.compile(r'\b[A-Z]{2}\b')


def looks_ambiguous_place(text: str) -> bool:
    t = str(text or '').strip()
    return bool(t) and not any(ch.isdigit() for ch in t) and ',' not in t and '，' not in t and not _STATE_CODE.search(t)


def describe_change(change: dict, lang: str = 'en') -> str:
    """Deterministic one-line label for a validated change (shown as a chip)."""
    spec = AXES[change['axis']]
    label = axis_label(change['axis'], lang)
    value, op = change['value'], change['op']
    zh = lang == 'zh'
    if spec['type'] == 'fleet':
        names = ", ".join(v.get('driver') or v.get('name', '?') for v in value)
        return (f"{label} → {names}（{len(value)} 辆）" if zh
                else f"{label} → {names} ({len(value)} vehicle{'s' if len(value) != 1 else ''})")
    if spec['type'] == 'goals':
        return f"{label} → {goals_summary(value, lang)}"
    if change['axis'] == 'priority_stops':
        names = ", ".join(short_stop(v) for v in value)
        if op == 'remove':
            return f"取消优先 → {names}" if zh else f"Not priority → {names}"
        if op == 'add':
            return f"优先 → {names}" if zh else f"Priority → {names}"
        return f"{label} → {names or ('无' if zh else 'none')}"
    if spec['type'] == 'enum':
        return f"{label} → {'Auto' if value == 'auto' else SOLVERS[value]['label']}"
    if spec['type'] == 'bool':
        return f"{label} → {('开' if value else '关') if zh else ('on' if value else 'off')}"
    if spec['type'] == 'number':
        unit = UNIT_ZH.get(change['axis'], spec['unit']) if zh else spec['unit']
        return f"{label} → {value}{unit}"
    if spec['type'] == 'list':
        n = len(value)
        if op == 'add':
            if zh:
                return f"新增站点 → {value[0]}" if n == 1 else f"新增 {n} 个站点"
            return f"Add stop → {value[0]}" if n == 1 else f"Add {n} stops"
        if op == 'remove':
            if zh:
                return f"删除站点 → {value[0]}" if n == 1 else f"删除 {n} 个站点"
            return f"Remove stop → {value[0]}" if n == 1 else f"Remove {n} stops"
        return f"{label} → {n} 个地址" if zh else f"{label} → {n} address{'es' if n != 1 else ''}"
    return f"{label} → {value}"


UNIT_ZH = {'vehicle_count': ' 辆', 'battery_packs': ' 块', 'vehicle_weight_lbs': ' lbs',
           'temperature_c': ' °C', 'unload_time_min': ' 分钟'}

# Rejection reasons the operator reads, in both languages.
REJECT = {
    'unknown_field': {'en': "unknown field '{axis}'", 'zh': "未知字段「{axis}」"},
    'unknown_op':    {'en': "unknown operation '{op}'", 'zh': "未知操作「{op}」"},
    'op_stops_only': {'en': "'{op}' only applies to stops", 'zh': "「{op}」只适用于站点"},
    'not_number':    {'en': "{label} must be a number", 'zh': "{label}必须是数字"},
    'out_of_range':  {'en': "{label} must be between {lo} and {hi}{unit}",
                      'zh': "{label}必须在 {lo} 到 {hi}{unit} 之间"},
    'empty':         {'en': "{label} is empty", 'zh': "{label}是空的"},
    'not_list':      {'en': "{label} must be a list", 'zh': "{label}必须是列表"},
    'bad_enum':      {'en': "{label} must be one of: {choices}", 'zh': "{label}必须是以下之一：{choices}"},
    'no_evidence':   {'en': "{label}: not found in your message", 'zh': "{label}：在你的话里找不到依据"},
    'no_number':     {'en': "{label}: you didn't give a number for it", 'zh': "{label}：你没有给出数字"},
    'no_onoff':      {'en': "{label}: you didn't say on or off", 'zh': "{label}：你没有说开还是关"},
    'slot':          {'en': "{label}: value slot mismatch (got {got}, need {need})",
                      'zh': "{label}：值的类型不对（收到 {got}，应为 {need}）"},
    'not_a_stop':    {'en': "{label}: \"{v}\" is not one of your stops", 'zh': "{label}：「{v}」不在你的站点里"},
}


def _r(key: str, lang: str, **kw) -> str:
    return REJECT[key].get(lang, REJECT[key]['en']).format(**kw)


def check_value(axis: str, op: str, value, lang: str = 'en'):
    """Range / shape check shared by both paths. Returns (value, None) or (None, reason)."""
    spec = AXES.get(axis)
    if not spec:
        return None, _r('unknown_field', lang, axis=axis)
    label = axis_label(axis, lang) if spec else axis
    if op not in ('set', 'add', 'remove'):
        return None, _r('unknown_op', lang, op=op)
    if op != 'set' and spec['type'] != 'list':
        return None, _r('op_stops_only', lang, op=op)

    if spec['type'] == 'number':
        try:
            value = float(value)
        except (TypeError, ValueError):
            return None, _r('not_number', lang, label=label)
        value = int(round(value))
        if not spec['min'] <= value <= spec['max']:
            unit = UNIT_ZH.get(axis, spec['unit']) if lang == 'zh' else spec['unit']
            return None, _r('out_of_range', lang, label=label, lo=spec['min'], hi=spec['max'], unit=unit)
    elif spec['type'] == 'text':
        value = str(value or '').strip()
        if axis == 'depot':
            # "depot in Anaheim CA" quoted whole: the address is what follows.
            value = DEPOT_LEAD_RE.sub('', value).strip() or value
        if not value:
            return None, _r('empty', lang, label=label)
    elif spec['type'] == 'list':
        if not isinstance(value, list):
            return None, _r('not_list', lang, label=label)
        value = [str(v).strip() for v in value if str(v or '').strip()]
        if not value:
            return None, _r('empty', lang, label=label)
    elif spec['type'] == 'fleet':
        if not isinstance(value, list) or not value:
            return None, _r('empty', lang, label=label)
    elif spec['type'] == 'goals':
        if not isinstance(value, dict):
            return None, _r('empty', lang, label=label)
        value = normalize_goals(value)
    elif spec['type'] == 'bool':
        value = bool(value)
    elif spec['type'] == 'enum':
        value = str(value or '').strip().lower().replace('-', '_').replace(' ', '_')
        aliases = {'optimal': 'held_karp', 'heldkarp': 'held_karp', 'exact': 'held_karp',
                   'greedy': 'nearest_neighbor', 'nn': 'nearest_neighbor',
                   'two_opt': '2opt', 'opt2': '2opt', '2_opt': '2opt', '2opt_local_search': '2opt',
                   'ortools': 'ortools_vrp', 'or_tools': 'ortools_vrp', 'vrp': 'ortools_vrp'}
        value = aliases.get(value, value)
        if value not in spec['choices']:
            return None, _r('bad_enum', lang, label=label, choices=', '.join(spec['choices']))
    return value, None


def make_change(axis: str, op: str, value, evidence: str, lang: str = 'en') -> dict:
    change = {'axis': axis, 'op': op, 'value': value, 'evidence': evidence}
    change['label'] = describe_change(change, lang)
    return change


# ---- outcome builders (the three boxes at the bottom of the diagram) ---------

def is_destructive(change: dict) -> bool:
    """Changes that throw work away rather than adjust it."""
    return change['axis'] == 'addresses' and change['op'] == 'remove' and len(change['value']) > 1


OUTCOME_TEXT = {
    'wipe':    {'en': "That removes {n} stops. Confirm? (yes / no)", 'zh': "这会删掉 {n} 个站点，确认吗？（是 / 否）"},
    'also':    {'en': "Also apply: {label}? (yes / no)", 'zh': "还要设置 {label} 吗？（是 / 否）"},
    'applied': {'en': "Applied: {list}.", 'zh': "已应用：{list}。"},
    'skipped': {'en': " Skipped: {list}.", 'zh': "已跳过：{list}。"},
    'dropped': {'en': "OK, dropped that.", 'zh': "好的，不改了。"},
    'nothing': {'en': "There's nothing pending to confirm.", 'zh': "当前没有待确认的内容。"},
}


def _o(key: str, lang: str, **kw) -> str:
    return OUTCOME_TEXT[key].get(lang, OUTCOME_TEXT[key]['en']).format(**kw)


def outcome_apply(changes: list, source: str, rejected=None, confirmed: bool = False,
                  lang: str = 'en') -> dict:
    # Never wipe a list of stops on one sentence; make the operator say yes.
    # `confirmed` means they just did, so the gate steps aside.
    if changes and not confirmed and is_destructive(changes[0]):
        return outcome_ask(_o('wipe', lang, n=len(changes[0]['value'])),
                           'addresses', source, queued=changes, rejected=rejected)

    sep = '；' if lang == 'zh' else '; '
    if ONE_CHANGE_PER_TURN and len(changes) > 1:
        applied, queued = changes[:1], changes[1:]
        pending = {
            'question': _o('also', lang, label=queued[0]['label']),
            'axis': None,
            'queued': queued,
        }
    else:
        applied, pending = changes, None

    message = _o('applied', lang, list=sep.join(c['label'] for c in applied))
    if rejected:
        message += _o('skipped', lang, list=sep.join(rejected))
    if pending:
        message += ("" if lang == 'zh' else " ") + pending['question']

    return {
        'status': 'ok', 'intent': 'apply', 'source': source,
        'message': message, 'changes': applied, 'pending': pending,
        'capabilities': None, 'rejected': rejected or [], 'advice': None, 'profiles': [],
    }


def outcome_ask(question: str, axis, source: str, queued=None, rejected=None, lang: str = 'en') -> dict:
    """Ask a question; `pending` is echoed back by the client so the session remembers it."""
    return {
        'status': 'ok', 'intent': 'ask', 'source': source,
        'message': question, 'changes': [],
        'pending': {'question': question, 'axis': axis, 'queued': queued or []},
        'capabilities': None, 'rejected': rejected or [], 'advice': None, 'profiles': [],
    }


def outcome_out_of_scope(message: str, source: str, list_capabilities: bool = True,
                         lang: str = 'en') -> dict:
    caps = (CAPABILITIES_ZH if lang == 'zh' else CAPABILITIES) if list_capabilities else None
    return {
        'status': 'ok', 'intent': 'out_of_scope', 'source': source,
        'message': message, 'changes': [], 'pending': None,
        'capabilities': caps, 'rejected': [], 'advice': None, 'profiles': [],
    }


def why_not(solver: str, num_stops: int, num_vehicles: int, lang: str) -> str:
    """Deterministic reason a named solver is the wrong pick here, or '' if it is fine."""
    n = num_stops + 1
    if num_vehicles > 1 and not SOLVERS[solver]['multi_vehicle']:
        return _t('why_multi', lang)
    if num_vehicles <= 1 and SOLVERS[solver]['multi_vehicle']:
        return _t('why_single', lang)
    if solver == 'held_karp' and estimate_held_karp_ms(n) > HK_TIME_BUDGET_MS:
        return _t('why_slow', lang, stops=num_stops, hk=format_duration(estimate_held_karp_ms(n), lang))
    if solver == 'nearest_neighbor':
        return _t('why_worse', lang, gap=NN_TYPICAL_GAP)
    return ''


def outcome_recommend(topic: str, num_stops: int, num_vehicles: int, current: str, source: str,
                      hypothetical: bool = False, need_optimal: bool = False,
                      lang: str = 'en', about: str = None) -> dict:
    """Solver advice. Every number and claim below is computed, never generated.

    topic: 'which' (what should I use), 'why' (why not always the optimal one),
           'current' (what am I using now).
    about: a solver the operator named, e.g. "should I use Held-Karp?".
    """
    rec = recommend_solver(num_stops, num_vehicles, need_optimal, lang)
    table = solver_table(num_stops, num_vehicles, lang)
    current = current if current in SOLVER_CHOICES else 'auto'
    effective_now = (recommend_solver(num_stops, num_vehicles, lang=lang)['solver']
                     if current == 'auto' else current)
    plural = '' if num_vehicles == 1 else 's'
    scope = _t('scope_hypo' if hypothetical else 'scope_current', lang,
               stops=num_stops, veh=num_vehicles, s=plural)
    label = SOLVERS[rec['solver']]['label']
    pending = None

    if topic == 'current':
        cur_label = 'Auto' if current == 'auto' else SOLVERS[current]['label']
        msg = (_t('current_auto', lang, cur=cur_label, eff=SOLVERS[effective_now]['label'], reason=rec['reason'])
               if current == 'auto' else
               _t('current_pin', lang, cur=cur_label, reason=rec['reason']))
    elif topic == 'why':
        msg = _t('why', lang, gap=TWO_OPT_TYPICAL_GAP, scope=scope,
                 hk=format_duration(rec['hk_estimate_ms'], lang))
    elif about and about in SOLVERS:
        # They named a solver: answer about that one, then give the pick.
        reason = why_not(about, num_stops, num_vehicles, lang)
        if reason:
            msg = _t('about_no', lang, label=SOLVERS[about]['label'], why=reason, reason=rec['reason'])
        elif about == rec['solver']:
            msg = _t('about_ok', lang, label=SOLVERS[about]['label'], reason=rec['reason'])
        else:
            # Usable, just not the best trade at this size.
            msg = _t('about_workable', lang, label=SOLVERS[about]['label'], pick=label, reason=rec['reason'])
    else:
        msg = _t('use', lang, scope=scope, label=label, reason=rec['reason'])

    # Offer the switch only for a question about the form as it stands.
    if topic == 'which' and not hypothetical:
        if rec['solver'] != effective_now:
            msg += _t('switch_q', lang, label=label)
            pending = {'question': _t('switch_pend', lang, label=label), 'axis': 'solver',
                       'queued': [make_change('solver', 'set', rec['solver'],
                                              f"recommended for {num_stops} stops / {num_vehicles} vehicles")]}
        elif not about:
            msg += _t('already', lang)

    return {
        'status': 'ok', 'intent': 'recommend', 'source': source,
        'message': msg, 'changes': [], 'pending': pending,
        'capabilities': None, 'rejected': [], 'profiles': [], 'advice': {
            'topic': topic, 'about': about,
            'recommended': rec['solver'], 'recommended_label': label,
            'current': current, 'effective_now': effective_now,
            'num_stops': num_stops, 'num_vehicles': num_vehicles,
            'hypothetical': hypothetical, 'lang': lang,
            'held_karp_estimate': format_duration(rec['hk_estimate_ms'], lang),
            'table': table,
        },
    }


# ---- optimisation in plain language: goals, limits, disruptions -------------
# The model classifies the situation and quotes the operator; everything said
# back — weights, ranges, which truck takes which stop — is computed here.

GOALS_TEXT = {
    'updated':   {'en': "Optimization goals: {changes}. Weights now {weights}.",
                  'zh': "优化目标：{changes}。现在的权重：{weights}。"},
    'because':   {'en': " You said: “{evidence}”.", 'zh': "（依据你说的：「{evidence}」）"},
    'eff_energy': {'en': " Energy prices every leg with the Model E energy model — speed, climbs and the weight "
                         "still on board — so loaded trucks avoid costly climbs and fast highway stretches.",
                   'zh': "电量目标用 Model E 能耗模型给每一段路定价——速度、爬坡和车上剩余的货重都算进去，"
                         "所以满载时会避开费电的爬坡和高速路段。"},
    'eff_time':  {'en': " Time prices every leg by its typical driving time, so faster roads win even when longer.",
                  'zh': "时间目标按每段路的常规行驶时间定价，所以即使远一点，更快的路也会胜出。"},
    'eff_distance': {'en': " Distance favours the fewest total miles.", 'zh': "距离目标偏向总里程最短。"},
    'eff_balance': {'en': " Balance charges for the latest truck's finish time, so stops spread out and the whole "
                          "fleet is back sooner.",
                    'zh': "均衡目标会为最晚回来的那辆车计费，所以站点会分得更均匀，整个车队更早收工。"},
    'balance_single': {'en': " Balance only matters with 2+ trucks; with one truck it counts as time.",
                       'zh': "均衡只在 2 辆及以上时有意义；只有 1 辆车时它按时间目标算。"},
    'reserve_on': {'en': " Battery reserve: every truck must finish with at least {pct}% left; stops that fit "
                         "no truck are reported as unserved.",
                   'zh': "电量保留：每辆车收工时至少剩 {pct}%；哪辆车都放不下的站点会标为无法配送。"},
    'reserve_zero': {'en': " Battery limit on: no truck may plan more energy than its battery holds.",
                     'zh': "电量上限已启用：任何车都不能规划超过电池容量的能耗。"},
    'reserve_off': {'en': " Battery limit off: battery is reported, not enforced.",
                    'zh': "电量上限已关闭：只报告电量，不强制约束。"},
    'reserve_single': {'en': " With one truck the limit is checked and flagged, not enforced — it has to visit "
                             "every stop.",
                       'zh': "只有 1 辆车时只检查并提示，不强制——它必须跑完所有站点。"},
    'nochange':  {'en': "Those goals are already set: {levels}. Weights {weights}.",
                  'zh': "这些目标已经是这样了：{levels}。权重：{weights}。"},
    'explain':   {'en': "Current goals: {levels} — weights {weights}. Every leg costs w_distance × (miles ÷ the "
                        "average leg) + w_time × (minutes ÷ average) + w_energy × (kWh ÷ average); balance adds "
                        "the latest truck's finish time. Levels map to off 0 · low 1 · medium 2 · high 4, scaled "
                        "to 100%. You don't need to pick numbers — tell me about the day (\"battery is tight\", "
                        "\"we're running late\") and I'll set them.",
                  'zh': "当前目标：{levels}——权重 {weights}。每段路的代价 = w_距离 ×（里程 ÷ 平均每段）"
                        "+ w_时间 ×（分钟 ÷ 平均）+ w_电量 ×（kWh ÷ 平均）；均衡再加上最晚那辆车的收工时间。"
                        "档位对应：关 0 · 低 1 · 中 2 · 高 4，再换算成 100%。你不用自己定数字——"
                        "告诉我今天的情况（「电量紧张」「今天赶时间」），我来设。"},
    'no_evidence': {'en': "I couldn't tie that goal change to your words — what matters most today?",
                    'zh': "我没法从你的话里确认要怎么改目标——今天最看重什么？"},
    'reserve_range': {'en': "Battery reserve must be between 0 and {max}%", 'zh': "电量保留必须在 0 到 {max}% 之间"},
    'reserve_no_number': {'en': "Battery reserve: I couldn't find {pct}% in your message",
                          'zh': "电量保留：你的话里没有 {pct}%"},
}

LIMITS_TEXT.update({
    'no_plan': {'en': " Run Optimize and I'll also check today's routes against it.",
                'zh': "运行优化后，我还能帮你核对今天的路线跑不跑得下。"},
})

DISRUPT_TEXT = {
    'no_plan':   {'en': "Run Optimize first — I simulate a breakdown on the current plan.",
                  'zh': "请先运行优化——抛锚模拟是在当前方案上做的。"},
    'which':     {'en': "Which truck broke down? The plan has {list}.", 'zh': "哪辆车抛锚了？当前方案里有：{list}。"},
    'no_driver': {'en': "No truck in the current plan is driven by {name}. The plan has {list}.",
                  'zh': "当前方案里没有 {name} 开的车。方案里有：{list}。"},
    'no_truck':  {'en': "Vehicle {n} has no route in the current plan. The plan has {list}.",
                  'zh': "当前方案里车辆 {n} 没有路线。方案里有：{list}。"},
    'when':      {'en': "When did {veh} break down — after which stop (0 = leaving the depot), or how many "
                        "minutes after dispatch?",
                  'zh': "{veh} 是什么时候抛锚的——第几站之后（0 = 刚出仓库），还是出发后多少分钟？"},
    'finished':  {'en': "{veh} only has {n} stop{s}; after stop {k} it is done for the day — nothing to re-plan.",
                  'zh': "{veh} 只有 {n} 个站；送完第 {k} 站就收工了——不需要重新规划。"},
    'bad_time':  {'en': "That time is outside the working day (0–{max} minutes).",
                  'zh': "这个时间超出了一天的范围（0–{max} 分钟）。"},
    'go':        {'en': "Simulating a breakdown: {veh} {when}{spare}. Re-planning the rest of the day…",
                  'zh': "模拟抛锚：{veh} {when}{spare}。正在重新规划当天剩下的配送……"},
    'after_k':   {'en': "after stop {k} of {n}", 'zh': "在第 {k}/{n} 站之后"},
    'at_depot':  {'en': "leaving the depot", 'zh': "刚离开仓库时"},
    'at_min':    {'en': "{m} minutes after dispatch", 'zh': "出发后 {m} 分钟"},
    'spare':     {'en': ", with a spare truck from the depot", 'zh': "，并从仓库派一辆备用车"},
    'spare_only': {'en': ", with a spare truck from the depot (the only truck in the plan)",
                   'zh': "，并从仓库派一辆备用车（方案里只有这一辆车）"},
}

ACTION_TEXT = {
    'resolve': {'en': "Re-solving with the current settings…", 'zh': "按当前设置重新求解……"},
}

LIMITS_WORD_RE = re.compile(r'\bpacks?\b|batter(?:y|ies)|\brange\b|how\s+far|payload|\bcargo\b|\blimits?\b|'
                            r'kwh|电池|续航|载重|极限', re.I)
PACKS_IN_TEXT_RE = re.compile(r'(\d+)\s*-?\s*(?:battery\s*)?packs?\b|(\d+)\s*(?:块|个)?\s*电池', re.I)
LIMITS_FIELD_OF_AXIS = {'battery_packs': 'battery_packs', 'vehicle_weight_lbs': 'weight_lbs',
                        'temperature_c': 'temperature_c'}

ORDINAL_RE = re.compile(r'\b(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|\d+(?:st|nd|rd|th))\b'
                        r'|第', re.I)
RESET_RE = re.compile(r'\breset\b|\bdefaults?\b|back\s+to\s+normal|start\s+over|重置|恢复|默认', re.I)
DEPOT_WORD_RE = re.compile(r'depot|warehouse|before\s+(?:its|the|any)\s+first|leav|outset|仓库|刚出|还没', re.I)
TIME_WORD_RE = re.compile(r'\b(?:min(?:ute)?s?|hours?|hrs?)\b|分钟|小时|钟头', re.I)
SPARE_RE = re.compile(r'spare|backup|back-up|extra|another\s+(?:truck|van|vehicle)|replacement|备用|替补|再派', re.I)
# A request to run the optimizer itself.
RESOLVE_RE = re.compile(r'^\s*(?:please\s+|can\s+you\s+|could\s+you\s+)?(?:re-?\s*)?(?:optimi[sz]e|solve|run|plan)'
                        r'(?:\s+(?:it|this|that|again|now|please|for\s+me|the\s+routes?|the\s+plan|'
                        r'the\s+optimi[sz](?:er|ation)))*\s*[?.!]*\s*$'
                        r'|^\s*(?:重新|再)(?:优化|规划|计算|求解)(?:一(?:次|遍|下))?\s*[。!！]*\s*$', re.I)


def _gt(key: str, lang: str, **kw) -> str:
    return GOALS_TEXT[key].get(lang, GOALS_TEXT[key]['en']).format(**kw)


def _dt(key: str, lang: str, **kw) -> str:
    return DISRUPT_TEXT[key].get(lang, DISRUPT_TEXT[key]['en']).format(**kw)


def _has_number(text: str) -> bool:
    return bool(NUMERIC_EVIDENCE_RE.search(text or '') or ORDINAL_RE.search(text or ''))


_NUMBER_WORDS = ['zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten']
_ORDINAL_WORDS = ['', 'first', 'second', 'third', 'fourth', 'fifth', 'sixth', 'seventh', 'eighth', 'ninth', 'tenth']
_ZH_DIGITS = '零一二三四五六七八九十'


def number_stated(n: int, text: str) -> bool:
    """Is this exact number written in the text — as digits, a word or an
    ordinal? "A truck broke down" must not count as truck 1."""
    n, text = int(n), str(text or '')
    if re.search(rf'(?<!\d){n}(?!\d)', text):
        return True
    words = []
    if 0 <= n <= 10:
        words += [_NUMBER_WORDS[n], _ORDINAL_WORDS[n]] + ([_ZH_DIGITS[n]] if n <= 10 else [])
    if n == 2:
        words.append('两')
    return any(w and (re.search(rf'\b{w}\b', text, re.I) if w.isascii() else w in text) for w in words)


def _outcome(intent: str, message: str, source: str, **extra) -> dict:
    out = {'status': 'ok', 'intent': intent, 'source': source, 'message': message,
           'changes': [], 'pending': None, 'capabilities': None, 'rejected': [],
           'advice': None, 'profiles': [], 'action': None, 'limits': None, 'goals_info': None}
    out.update(extra)
    return out


def outcome_goals(raw, utterance: str, form_state: dict, source: str, lang: str = 'en') -> dict:
    """Turn the model's reading of the situation into goal levels. The levels
    are a closed enum, the motivating words must be in the message, and the
    reply — weights and what each goal does — is written here."""
    before = normalize_goals(form_state.get('goals'))
    multi = int(form_state.get('vehicle_count') or 1) > 1
    raw = raw if isinstance(raw, dict) else {}
    info = {'before': before, 'after': before, 'weights_before': goal_weights(before),
            'weights_after': goal_weights(before), 'evidence': None}
    wants_change = (raw.get('reset') or raw.get('battery_reserve_pct') is not None
                    or any(raw.get(k) for k in GOAL_KEYS))
    if not wants_change:
        return _outcome('goals', _gt('explain', lang, levels=goals_summary(before, lang),
                                     weights=weights_summary(before, lang)), source, goals_info=info)

    evidence = _strip_edges(raw.get('evidence'))
    if raw.get('reset') and (not evidence or _norm(evidence) not in _norm(utterance)):
        m = RESET_RE.search(utterance)     # "reset the goals" says so itself
        evidence = m.group(0) if m else evidence
    if not evidence or _norm(evidence) not in _norm(utterance):
        return outcome_ask(_gt('no_evidence', lang), None, source, lang=lang)

    after = dict(DEFAULT_GOALS) if raw.get('reset') else dict(before)
    for k in GOAL_KEYS:
        lvl = raw.get(k)
        if lvl in GOAL_LEVELS:
            after[k] = lvl
        elif lvl in ('raise', 'lower'):
            i = GOAL_LEVELS.index(after[k]) + (1 if lvl == 'raise' else -1)
            after[k] = GOAL_LEVELS[max(0, min(len(GOAL_LEVELS) - 1, i))]

    rejected = []
    r = raw.get('battery_reserve_pct')
    if r is not None and not raw.get('reset'):
        r = int(round(float(r)))
        if r < 0:
            after['battery_reserve_pct'] = None
        elif r > MAX_RESERVE_PCT:
            rejected.append(_gt('reserve_range', lang, max=MAX_RESERVE_PCT))
        elif r > 0 and not re.search(rf'(?<!\d){r}(?!\d)', utterance):
            rejected.append(_gt('reserve_no_number', lang, pct=r))
        else:
            after['battery_reserve_pct'] = r
    after = normalize_goals(after)

    if after == before:
        out = _outcome('goals', _gt('nochange', lang, levels=goals_summary(before, lang),
                                    weights=weights_summary(before, lang)), source, goals_info=info)
        out['rejected'] = rejected
        return out

    key = 'zh' if lang == 'zh' else 'en'
    moved = [f"{GOAL_LABEL[k][key]} {LEVEL_LABEL[before[k]][key]} → {LEVEL_LABEL[after[k]][key]}"
             for k in GOAL_KEYS if after[k] != before[k]]
    if after['battery_reserve_pct'] != before['battery_reserve_pct']:
        fmt = lambda v: ('关' if key == 'zh' else 'off') if v is None else f"{v}%"
        moved.append(f"{'电量保留' if key == 'zh' else 'Battery reserve'} "
                     f"{fmt(before['battery_reserve_pct'])} → {fmt(after['battery_reserve_pct'])}")
    sep = '；' if lang == 'zh' else '; '
    msg = _gt('updated', lang, changes=sep.join(moved), weights=weights_summary(after, lang))
    for k in GOAL_KEYS:
        if GOAL_LEVEL_WEIGHT[after[k]] > GOAL_LEVEL_WEIGHT[before[k]]:
            msg += _gt('balance_single' if k == 'balance' and not multi else f'eff_{k}', lang)
    if after['battery_reserve_pct'] != before['battery_reserve_pct']:
        r = after['battery_reserve_pct']
        msg += (_gt('reserve_off', lang) if r is None else _gt('reserve_zero', lang) if r == 0
                else _gt('reserve_on', lang, pct=r))
        if r is not None and not multi:
            msg += _gt('reserve_single', lang)
    msg += _gt('because', lang, evidence=evidence)

    change = make_change('goals', 'set', after, evidence, lang)
    info.update(after=after, weights_after=goal_weights(after), evidence=evidence)
    out = _outcome('goals', msg, source, changes=[change], goals_info=info)
    out['rejected'] = rejected
    return out


def outcome_limits(raw, utterance: str, form_state: dict, source: str, lang: str = 'en') -> dict:
    """What a truck can do, at the numbers the operator stated or the form's own."""
    raw = raw if isinstance(raw, dict) else {}
    rejected = []

    def evidenced(field):
        ev = _strip_edges((raw.get('evidence_by_field') or {}).get(field) or raw.get('evidence'))
        return bool(ev) and _norm(ev) in _norm(utterance) and _has_number(ev)

    def stated(field, axis):
        v = raw.get(field)
        if v is None:
            return None
        if not evidenced(field):
            rejected.append(_r('no_number', lang, label=axis_label(axis, lang)))
            return None
        val, reason = check_value(axis, 'set', v, lang)
        if reason:
            rejected.append(reason)
            return None
        return val

    packs = stated('battery_packs', 'battery_packs')
    weight = stated('weight_lbs', 'vehicle_weight_lbs')
    temp = stated('temperature_c', 'temperature_c')
    packs = packs if packs is not None else int(form_state.get('battery_packs') or DEFAULT_PACKS)
    temp = temp if temp is not None else float(form_state.get('temperature_c', 20))
    goals = normalize_goals(form_state.get('goals'))
    data = truck_limits(packs, temp, bool(form_state.get('has_climate_control', True)), weight,
                        goals['battery_reserve_pct'], form_state.get('plan'), lang)
    msg = data['message']
    if not data['plan_check']:
        msg += _lt('no_plan', lang)
    out = _outcome('limits', msg, source, limits=data)
    out['rejected'] = rejected
    return out


def outcome_disruption(raw, utterance: str, form_state: dict, source: str, lang: str = 'en') -> dict:
    """Validate a breakdown against the current plan and hand the client an
    action to run (/api/replan). Never guesses the truck or the moment."""
    plan_vehicles = ((form_state.get('plan') or {}).get('vehicles')) or []
    if not plan_vehicles:
        return _outcome('disruption', _dt('no_plan', lang), source)
    raw = raw if isinstance(raw, dict) else {}
    ev = _strip_edges(raw.get('evidence'))
    ev_ok = bool(ev) and _norm(ev) in _norm(utterance)

    def label(v):
        name = v.get('name') or f"Vehicle {v.get('number')}"
        return f"{name} ({v['driver']})" if v.get('driver') else name

    listing = ", ".join(label(v) for v in plan_vehicles)
    veh = None
    driver, number = raw.get('driver'), raw.get('vehicle_number')
    driver = re.sub(r"['’]s$", '', str(driver).strip()) if driver else None       # "Wang's" → "Wang"
    if driver and name_in(driver, utterance):
        veh = next((v for v in plan_vehicles
                    if v.get('driver') and profile_key(v['driver']) == profile_key(driver)), None)
    elif number is not None and number_stated(number, utterance):
        veh = next((v for v in plan_vehicles if int(v.get('number', 0)) == int(number)), None)
        if veh is None:
            return outcome_ask(_dt('no_truck', lang, n=number, list=listing), None, source, lang=lang)
    if veh is None:
        # Nothing usable from the model ("Wang's truck" as the name, or a
        # guessed number), but one driver on today's plan is named in the message.
        named = [v for v in plan_vehicles if v.get('driver') and name_in(v['driver'], utterance)]
        if len(named) == 1:
            veh = named[0]
        elif driver and name_in(driver, utterance):
            return outcome_ask(_dt('no_driver', lang, name=driver, list=listing), None, source, lang=lang)
        elif len(plan_vehicles) == 1:
            veh = plan_vehicles[0]
        else:
            return outcome_ask(_dt('which', lang, list=listing), None, source, lang=lang)

    k, minute = raw.get('after_stop'), raw.get('at_minute')
    if k is not None and minute is not None:
        k, minute = (None, minute) if TIME_WORD_RE.search(utterance) else (k, None)
    # Stop 0 is "it never got going": it needs depot / before-the-first-stop
    # words, or "an hour in" would come back as stop 0.
    if k is not None and int(k) == 0 and not (DEPOT_WORD_RE.search(utterance) or re.search(r'\b0\b', utterance)):
        k = None
    if k is not None and int(k) > 0 and not number_stated(k, utterance):
        k = None
    if minute is not None and not (ev_ok and _has_number(ev)):
        minute = None
    if k is None and minute is None:
        return outcome_ask(_dt('when', lang, veh=label(veh)), None, source, lang=lang)

    stops = int(veh.get('stops') or 0)
    if k is not None:
        k, minute = int(k), None
        if k < 0:
            return outcome_ask(_dt('when', lang, veh=label(veh)), None, source, lang=lang)
        if k >= stops:
            return _outcome('disruption', _dt('finished', lang, veh=label(veh), n=stops,
                                              s='' if stops == 1 else 's', k=k), source)
        when = _dt('after_k', lang, k=k, n=stops) if k else _dt('at_depot', lang)
    else:
        minute = int(minute)
        if not 0 <= minute <= 24 * 60:
            return outcome_ask(_dt('bad_time', lang, max=24 * 60), None, source, lang=lang)
        when = _dt('at_min', lang, m=minute)

    only_truck = len(plan_vehicles) == 1
    spare = only_truck or (bool(raw.get('spare_truck')) and bool(SPARE_RE.search(utterance)))
    action = {'type': 'breakdown', 'vehicle_index': int(veh.get('vehicle_index', int(veh.get('number', 1)) - 1)),
              'vehicle_label': label(veh), 'after_stop': k, 'at_minute': minute,
              'spare_truck': spare, 'lang': lang}
    spare_text = _dt('spare_only', lang) if only_truck else _dt('spare', lang) if spare else ''
    return _outcome('disruption', _dt('go', lang, veh=label(veh), when=when, spare=spare_text), source,
                    action=action)


# ---- 1. lexicon fast path ----------------------------------------------------
# Known phrasings for the numeric / boolean axes. A hit skips the model entirely.
# Free-text axes (depot, stops) are never fast-pathed: they always go through
# extraction + evidence validation.

_NUM = r'(-?\d+(?:\.\d+)?)'
_SEP = r'\s*(?:is|to|of|at|=|:|set\s*to|改成|改为|设为|设置为|调到|是|为)?\s*'
_AC = r'(?:a/?c|climate(?:\s*control)?|hvac|air\s*con(?:ditioning)?|空调)'


def _num(m):
    return float(m.group(1))


def _f_to_c(m):
    return (float(m.group(1)) - 32) * 5 / 9


# (axis, pattern, required-context pattern or None, value extractor)
LEXICON = [
    ('temperature_c', re.compile(_NUM + r'\s*°?\s*(?:deg(?:rees?)?\s*)?(?:c\b|celsius|摄氏度?)', re.I), None, _num),
    ('temperature_c', re.compile(_NUM + r'\s*°?\s*(?:deg(?:rees?)?\s*)?(?:f\b|fahrenheit|华氏度?)', re.I), None, _f_to_c),
    ('temperature_c', re.compile(_NUM + r'\s*°?\s*度', re.I), None, _num),
    ('temperature_c', re.compile(r'(?:temp(?:erature)?|温度)' + _SEP + _NUM, re.I), None, _num),

    ('battery_packs', re.compile(_NUM + r'\s*(?:battery\s*)?packs?\b', re.I), None, _num),
    ('battery_packs', re.compile(_NUM + r'\s*(?:块|个|组)?\s*电池', re.I), None, _num),
    ('battery_packs', re.compile(r'(?:packs?|batter(?:y|ies)|电池(?:包|组)?)' + _SEP + _NUM, re.I), None, _num),

    ('unload_time_min', re.compile(_NUM + r'\s*(?:min(?:ute)?s?|分钟)', re.I),
     re.compile(r'unload|service|dwell|per\s*stop|卸货|每站|停留', re.I), _num),
    ('unload_time_min', re.compile(r'(?:unload(?:ing)?(?:\s*time)?|卸货(?:时间)?)' + _SEP + _NUM, re.I), None, _num),

    ('vehicle_count', re.compile(_NUM + r'\s*(?:trucks?|vehicles?|vans?|辆(?:车)?|台(?:车)?)', re.I), None, _num),
    ('vehicle_count', re.compile(r'(?:trucks?|vehicles?|fleet(?:\s*size)?|车辆|车)' + _SEP + _NUM, re.I), None, _num),

    ('vehicle_weight_lbs', re.compile(_NUM + r'\s*(?:lbs?|pounds?|磅)', re.I), None, _num),
    ('vehicle_weight_lbs', re.compile(r'(?:weigh(?:t|s)?|重量)' + _SEP + _NUM, re.I), None, _num),

    ('has_climate_control', re.compile(r'(?:turn\s*on|switch\s*on|enable|with|开|打开)\s*(?:the\s*)?' + _AC, re.I), None, lambda m: True),
    ('has_climate_control', re.compile(r'(?:turn\s*off|switch\s*off|disable|without|no|关|关闭)\s*(?:the\s*)?' + _AC, re.I), None, lambda m: False),
    ('has_climate_control', re.compile(_AC + r'\s*(?:on|开)\b', re.I), None, lambda m: True),
    ('has_climate_control', re.compile(_AC + r'\s*(?:off|关)', re.I), None, lambda m: False),
]

# Replies to a pending question.
YES_RE = re.compile(r'^\s*(?:y|yes|yeah|yep|yup|ok|okay|sure|go\s*ahead|continue|next|do\s*it|please|好|好的|是|是的|对|继续|可以|行|嗯)\s*[.!。！]*\s*$', re.I)
NO_RE = re.compile(r'^\s*(?:n|no|nope|nah|cancel|skip|stop|不|不用|不要|取消|算了|跳过)\s*[.!。！]*\s*$', re.I)
BARE_NUM_RE = re.compile(r'^\s*' + _NUM + r'\s*$')

# Bare solver questions with no numbers in them: the answer is computed either
# way, so there is nothing for the model to add.
_SOLVER_WORD = r'(?:solver|algorithm|algo|求解器|算法)'
# An actual "why" / "how slow" question, as opposed to "is X good enough".
WHY_RE = re.compile(r'\bwhy\b|how\s+(?:slow|long|fast|big|much\s+time|expensive)'
                    r'|what\s+makes\s+it|为什么|为啥|多(?:慢|久|快)|要多长时间', re.I)
SOLVER_TOPIC_LEXICON = [
    ('current', re.compile(r'(?:which|what)\s+' + _SOLVER_WORD + r'.{0,20}?(?:using|running|active|now|currently|set to)'
                           r'|current\s+' + _SOLVER_WORD + r'|(?:当前|现在|目前).{0,4}' + _SOLVER_WORD, re.I)),
    ('why', re.compile(r'why\s+not.{0,30}(?:optimal|exact|held.?karp)'
                       r'|why.{0,30}(?:not\s+)?always.{0,20}(?:optimal|exact|best|held.?karp)'
                       r'|为(?:什么|啥).{0,12}(?:不|别).{0,12}(?:总是|一直|都)?.{0,8}(?:最优|精确|held.?karp)'
                       r'|为(?:什么|啥).{0,8}(?:不用|不选).{0,8}(?:最优|精确)', re.I)),
    ('which', re.compile(r'(?:which|what)\s+' + _SOLVER_WORD
                         + r'|' + _SOLVER_WORD + r'\s+(?:should|would|to use|recommend)'
                         + r'|recommend.{0,12}' + _SOLVER_WORD
                         + r'|用(?:哪个|什么|啥)' + _SOLVER_WORD
                         + r'|' + _SOLVER_WORD + r'(?:推荐|选哪个|用哪个)'
                         + r'|推荐.{0,4}' + _SOLVER_WORD, re.I)),
]


def solver_topic_fast_path(utterance: str):
    """Return 'which' / 'why' / 'current' for a plain solver question, else None."""
    if re.search(r'\d', utterance):     # a stated size is a hypothetical → needs the model
        return None
    for topic, pattern in SOLVER_TOPIC_LEXICON:
        if pattern.search(utterance):
            return topic
    return None


# A question is never a command. Without this guard "30 stops and 1 truck?"
# would match the vehicle pattern and silently set the fleet to one.
QUESTION_RE = re.compile(r'[?？]\s*$|^\s*(?:what|which|how|why|should|can|could|is|are|do|does|would)\b'
                         r'|吗\s*[?？]?\s*$|呢\s*[?？]?\s*$|(?:怎么|哪个|多少|是否|要不要)', re.I)

# Utterances longer than this, or containing list separators, are too rich for
# a regex table and always go to the model.
LEXICON_MAX_LEN = 60

# Talk about goals, limits or a breakdown always goes to the model. Without
# this, "truck 2 broke down after 2 stops" sets the fleet to 2 trucks and
# "leave the battery at 20%" asks for 20 battery packs.
NOT_A_FORM_VALUE_RE = re.compile(
    r'%|percent|reserve|\brange\b|how\s+far|\blimits?\b|capacity|payload|\bbroke|break\s*down|crash|accident|'
    r'collision|stranded|\bstuck\b|flat\s+tyre|flat\s+tire|\btow(?:ed|ing)?\b|\bgoals?\b|priorit|urgent|\bfirst\b|'
    r'\bweights\b|optimi[sz]e\s+for|minimi[sz]|maximi[sz]|\bsav(?:e|ing)\s+(?:energy|battery|power|time)|faster|'
    r'fastest|\bquick|shortest|balanc|\bfair|\btight\b|\blate\b|\bhurry|'
    r'故障|抛锚|事故|撞|坏了|续航|极限|载重|省电|最快|最短|均衡|优先|权重|目标|保留', re.I)


# "one more truck" / "再加一辆车": a +1 / -1 move resolved against the form.
RELATIVE_LEXICON = [
    ('vehicle_count', +1, re.compile(r'(?:add|one more|another)\s*(?:one\s*)?(?:more\s*)?(?:truck|vehicle|van)'
                                     r'|(?:再|多)(?:加|来)(?:一)?(?:辆|台)', re.I)),
    ('vehicle_count', -1, re.compile(r'(?:remove|drop|one less|take away)\s*(?:one\s*)?(?:truck|vehicle|van)'
                                     r'|(?:少|减)(?:一)?(?:辆|台)', re.I)),
    ('battery_packs', +1, re.compile(r'(?:add|one more|another)\s*(?:one\s*)?(?:more\s*)?(?:battery\s*)?pack'
                                     r'|(?:再|多)(?:加|来)(?:一)?(?:块|个|组)?电池', re.I)),
    ('battery_packs', -1, re.compile(r'(?:remove|drop|one less)\s*(?:one\s*)?(?:battery\s*)?pack'
                                     r'|(?:少|减)(?:一)?(?:块|个|组)?电池', re.I)),
]


def _name_pattern(name: str) -> str:
    """Whole-word, case- and spacing-insensitive pattern for a Latin-script name,
    so a driver called "Li" does not match inside "climate"."""
    # Optional whitespace between every character: "WangWei" matches "Wang Wei".
    return r'(?<!\w)' + r'\s*'.join(map(re.escape, re.sub(r'\s+', '', name))) + r'(?!\w)'


def name_in(name: str, text: str) -> bool:
    """Is this driver name written in the text? Chinese has no word boundaries,
    so a CJK name is a plain substring check."""
    name = str(name or '').strip()
    if not name:
        return False
    if CJK_RE.search(name):
        return _norm(name) in _norm(text)
    return bool(re.search(_name_pattern(name), text, re.I))


# Words that mark a message as being about driver memory. "add" is left out:
# "add one more truck" is a form change the lexicon should keep handling.
MEMORY_WORD_RE = re.compile(r'\b(?:drivers?|remember|save|store|register)\b|司机|记住|保存', re.I)


def mentions_saved_driver(utterance: str) -> bool:
    return any(name_in(p.get('name'), utterance) for p in load_memory()['drivers'].values())


def typed_spelling(name: str, utterance: str) -> str:
    """The name exactly as the operator wrote it. The evidence checks ignore
    case, so without this the model's casing ("donghua" for "Donghua") is what
    gets stored."""
    if CJK_RE.search(name):
        return name          # no letter case to recover
    m = re.search(_name_pattern(name), utterance, re.I)
    return m.group(0) if m else name


def lexicon_fast_path(utterance: str, pending, form_state: dict = None):
    """Return a finished outcome dict on a hit, or None on a miss."""
    form_state = form_state or {}
    lang = detect_lang(utterance)

    # (a) replies to a question we asked earlier in the session
    if pending:
        queued = pending.get('queued') or []
        if queued and YES_RE.match(utterance):
            return outcome_apply(queued, source='session', confirmed=True, lang=lang)
        if NO_RE.match(utterance):
            return outcome_out_of_scope(_o('dropped', lang), source='session', list_capabilities=False, lang=lang)
        axis = pending.get('axis')
        m = BARE_NUM_RE.match(utterance)
        if axis and AXES.get(axis, {}).get('type') == 'number' and m:
            value, reason = check_value(axis, 'set', float(m.group(1)), lang)
            if reason:
                return outcome_ask(reason + ('。' if lang == 'zh' else '.'), axis, source='lexicon', lang=lang)
            return outcome_apply([make_change(axis, 'set', value, m.group(0).strip(), lang)], source='lexicon', lang=lang)

    # "re-optimize", "run it again": the client runs the optimizer.
    if RESOLVE_RE.match(utterance):
        return _outcome('apply', ACTION_TEXT['resolve'].get(lang, ACTION_TEXT['resolve']['en']), 'lexicon',
                        action={'type': 'resolve', 'lang': lang})

    is_question = bool(QUESTION_RE.search(utterance))
    if NOT_A_FORM_VALUE_RE.search(utterance):
        return None

    # "Wang runs 8 packs now" must update the stored profile, not the form.
    # The regex table cannot tell those apart, so any saved driver's name sends
    # the utterance to the model — and so does talk of drivers or saving, or
    # "add driver Linus with 6 packs" would set the form's packs to 6.
    if mentions_saved_driver(utterance) or MEMORY_WORD_RE.search(utterance):
        return None

    # (b) relative nudges that only need the current value
    if not is_question and not re.search(r'\d', utterance):
        for axis, step, pattern in RELATIVE_LEXICON:
            m = pattern.search(utterance)
            if m and isinstance(form_state.get(axis), (int, float)):
                value, reason = check_value(axis, 'set', form_state[axis] + step, lang)
                if reason:
                    return outcome_ask(reason + ('。' if lang == 'zh' else '.'), axis, source='lexicon', lang=lang)
                return outcome_apply([make_change(axis, 'set', value, m.group(0).strip(), lang)], source='lexicon', lang=lang)

    # (c) a plain solver question — the advice is computed, not generated
    topic = solver_topic_fast_path(utterance)
    if topic:
        return outcome_recommend(topic,
                                 len(form_state.get('addresses') or []),
                                 int(form_state.get('vehicle_count') or 1),
                                 form_state.get('solver', 'auto'), source='lexicon', lang=lang)

    # (d) known phrasings
    if is_question or len(utterance) > LEXICON_MAX_LEN or ',' in utterance or '，' in utterance:
        return None

    hits = {}
    for axis, pattern, context, extract in LEXICON:
        if context and not context.search(utterance):
            continue
        m = pattern.search(utterance)
        if m and axis not in hits:
            hits[axis] = (m, extract(m))

    # Exactly one axis must match; anything richer needs the model.
    if len(hits) != 1:
        return None

    axis, (m, raw_value) = next(iter(hits.items()))
    value, reason = check_value(axis, 'set', raw_value, lang)
    if reason:
        return outcome_ask(reason + ('。' if lang == 'zh' else '.'), axis, source='lexicon', lang=lang)
    return outcome_apply([make_change(axis, 'set', value, m.group(0).strip(), lang)], source='lexicon', lang=lang)


# ---- 2. constrained extraction -----------------------------------------------
# Strict JSON schema: the model cannot add fields, cannot leave fields out, and
# every enum is closed. Values are split into typed slots so the validator can
# check null coupling instead of guessing what a string means.

_NULLABLE_STR = {"type": ["string", "null"]}
# "raise" / "lower" nudge a goal one level from where it is now.
_GOAL_LEVEL = {"anyOf": [{"type": "string", "enum": list(GOAL_LEVELS) + ["raise", "lower"]}, {"type": "null"}]}

# The axes the model may name. 'fleet' is left out: only a driver recall sets
# it, and offering it lets the model emit a fleet change that is then rejected.
# 'goals' likewise: the model fills `goal_update`, and code builds the change.
MODEL_AXES = [a for a in AXES if a not in ('fleet', 'goals')]

# One step = one action. A message usually has one; "delete Han, then list all
# drivers" has two, and run_steps executes them in order.
STEP_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "intent": {"type": "string", "enum": list(INTENTS)},
        "changes": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "axis": {"type": "string", "enum": MODEL_AXES},
                    "op": {"type": "string", "enum": ["set", "add", "remove"]},
                    "value_text": _NULLABLE_STR,
                    "value_number": {"type": ["number", "null"]},
                    "value_bool": {"type": ["boolean", "null"]},
                    "value_list": {"type": ["array", "null"], "items": {"type": "string"}},
                    "evidence": {"type": "string"},
                },
                "required": ["axis", "op", "value_text", "value_number", "value_bool", "value_list", "evidence"],
            },
        },
        "question": _NULLABLE_STR,
        "question_axis": {"anyOf": [{"type": "string", "enum": MODEL_AXES}, {"type": "null"}]},
        "ambiguous_term": _NULLABLE_STR,
        "advice_topic": {"anyOf": [{"type": "string", "enum": ["which", "why", "current"]}, {"type": "null"}]},
        "advice_about": {"anyOf": [{"type": "string", "enum": list(SOLVERS)}, {"type": "null"}]},
        "memory_op": {"anyOf": [{"type": "string", "enum": ["remember", "recall", "forget", "list"]},
                                {"type": "null"}]},
        "memory_profiles": {"anyOf": [{
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string"},
                    "battery_packs": {"type": ["integer", "null"]},
                    "weight_lbs": {"type": ["integer", "null"]},
                    "evidence": {"type": "string"},
                },
                "required": ["name", "battery_packs", "weight_lbs", "evidence"],
            }}, {"type": "null"}]},
        "memory_names": {"anyOf": [{"type": "array", "items": {"type": "string"}}, {"type": "null"}]},
        "advice_scenario": {"anyOf": [{
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "stops": {"type": ["integer", "null"]},
                "vehicles": {"type": ["integer", "null"]},
                "need_optimal": {"type": "boolean"},
                "evidence": {"type": "string"},
            },
            "required": ["stops", "vehicles", "need_optimal", "evidence"],
        }, {"type": "null"}]},
        "goal_update": {"anyOf": [{
            "type": "object",
            "additionalProperties": False,
            "properties": {
                **{k: _GOAL_LEVEL for k in GOAL_KEYS},
                "battery_reserve_pct": {"type": ["number", "null"]},
                "reset": {"type": "boolean"},
                "evidence": {"type": "string"},
            },
            "required": list(GOAL_KEYS) + ["battery_reserve_pct", "reset", "evidence"],
        }, {"type": "null"}]},
        "limits_query": {"anyOf": [{
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "battery_packs": {"type": ["integer", "null"]},
                "weight_lbs": {"type": ["integer", "null"]},
                "temperature_c": {"type": ["number", "null"]},
                "evidence": {"type": "string"},
            },
            "required": ["battery_packs", "weight_lbs", "temperature_c", "evidence"],
        }, {"type": "null"}]},
        "disruption": {"anyOf": [{
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "vehicle_number": {"type": ["integer", "null"]},
                "driver": _NULLABLE_STR,
                "after_stop": {"type": ["integer", "null"]},
                "at_minute": {"type": ["integer", "null"]},
                "spare_truck": {"type": "boolean"},
                "evidence": {"type": "string"},
            },
            "required": ["vehicle_number", "driver", "after_stop", "at_minute", "spare_truck", "evidence"],
        }, {"type": "null"}]},
        "message": {"type": "string"},
    },
    "required": ["intent", "changes", "question", "question_axis", "ambiguous_term",
                 "advice_topic", "advice_about", "advice_scenario",
                 "memory_op", "memory_profiles", "memory_names",
                 "goal_update", "limits_query", "disruption", "message"],
}

EXTRACTION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"steps": {"type": "array", "items": STEP_SCHEMA}},
    "required": ["steps"],
}
MAX_STEPS = 5

EXTRACTION_SYSTEM_PROMPT = """You are the form-filling assistant inside the Isuzu BEV Fleet Route Optimizer.
You never change the form yourself. You only EXTRACT what the operator asked for into the fixed schema; deterministic code validates and applies it afterwards.

STEPS: return `steps`, normally exactly ONE. Use several steps only when the operator asks for separate actions in sequence ("delete Han and Linus, then list all drivers", "save driver Wang and load him for today", "use 3 trucks, then tell me which solver to use"). One step per action, in the order asked; code runs them in that order, so a later step sees the result of an earlier one. Never split a single action: several form values in one sentence ("2 trucks with 6 packs", "set temperature to 28 and use 3 trucks") are ONE "apply" step with several `changes`, and saving or deleting several drivers is ONE "memory" step listing all of them. Each step fills every field below on its own, with its own `evidence` and `message`. Resolve pronouns to names ("load him" → the name).

INTENTS (each step has exactly one):
- "apply": the operator asked to change one or more form fields. List every requested change in `changes`, in the order mentioned, one entry per axis. A situation or a priority ("we're late", "battery is tight", "be fair to the drivers") is never "apply" — that is "goals".
- "ask": the request is ambiguous or incomplete (a place name that exists in several states or countries, a number whose unit or target field is unclear, a missing value). Put the clarifying question in `question`, name the field in `question_axis` when there is one, copy the ambiguous word(s) into `ambiguous_term`, and leave `changes` empty.
- "confirm": ONLY when a PENDING QUESTION is listed below AND the message is nothing but a yes/no answer to it. If there is no pending question, never use this intent.
- "recommend": the operator is asking WHICH SOLVER / ALGORITHM to use, why one is better, why not always use the optimal one, or which one is active. This includes questions about how long a solver takes, how many stops it can handle, or whether one is fast/good enough — route them here rather than declining. Set `advice_topic`: "which" (what should I use / can solver X handle this), "why" (why not always the exact one / how slow is it / explain the tradeoff), "current" (what am I running now). Do NOT answer with runtimes, limits or a pick of your own — code computes all of that and writes the reply. No changes.
- "memory": the operator wants to save, load, list or delete a DRIVER PROFILE. The memory tracks drivers, not vehicles: a profile is a driver's name, optionally with the battery packs and weight of the truck that driver runs. Phrasing does not matter: a command, a statement and a polite question ("can you…", "could you…", "is it possible to…", "please…", "能帮我…吗") are the same request — carry it out, do not ask for confirmation. Always set `memory_op`:
  * "remember" — save a new driver or update a saved one: save, add, store, register, create, remember, 记住, 保存, 添加, or a description of the driver's truck. Examples: "remember driver Wang", "save driver Donghua to the system", "can you save driver Donghua?", "could you add a new driver called Li", "could you save driver Li with 8 packs?", "Wang runs 6 packs at 15000 lbs", "记住司机老王，6 块电池", "能帮我记住司机东华吗". Fill `memory_profiles` with one entry per driver — never leave it empty for "remember". `name` is the driver's name in exactly the characters the operator typed: "donghua" stays "donghua" and "东华" stays "东华". Never convert between pinyin and Chinese characters, translate or re-spell a name. `evidence` is ONE contiguous, exact substring of the message covering that driver's words — never join words from different places: in "add driver Linus and Han with 6 packs", Han's evidence is "Han with 6 packs", not "add driver Han". Leave packs or weight null if they did not say it — a name alone is a valid profile. Never invent a name, packs or weight.
  * "recall" — load drivers that are ALREADY SAVED into the form for a run ("Wang and Li are driving today", "use Wang today", "load driver Li", "今天老王出车"). Put the names in `memory_names`. Adding or saving a driver is "remember", not "recall".
  * "forget" — delete saved drivers the operator NAMES ("forget Wang", "delete driver Li"). Put the names in `memory_names` exactly as written. "delete everything", "clear all" or "remove all" with no driver named are about the STOPS (axis addresses), never memory.
  * "list" — show who is saved ("which drivers do you know?", "存了哪些司机"). Leave `memory_names` null.
  A message that names a driver ("driver Wang", "司机老王", or a name listed under SAVED DRIVERS) is memory. Changing the count, packs or weight of the CURRENT vehicles without naming a driver is intent "apply", not memory. Licence plates and vehicle names are not tracked: leave them out.
- "goals": the operator describes their day, a constraint or what matters ("battery is tight today", "we're running late", "drivers say the routes are unfair", "it's freezing", "save electricity", "keep 20% battery in every truck"), or asks how the goals / weights work. They will rarely name weights — YOU choose which goals move, and code turns your levels into numbers. Fill `goal_update`: for each goal give "off" | "low" | "medium" | "high", or "raise" / "lower" to nudge it one level from CURRENT FORM STATE goals, and null to leave it as it is. Guide:
  * battery tight, small battery (3 packs), cold or very hot weather, hills, long routes, save energy / electricity / charging cost → energy "high"; distance "low" if distance is currently "high"
  * running late, urgent, customers waiting, finish sooner, overtime costs → time "high", and balance "medium" when there are 2+ vehicles
  * workloads uneven, unfair routes, everyone back at the same time, one driver doing everything → balance "high"
  * fewest miles, tyre wear, mileage cost → distance "high"
  * "back to normal" / "reset the goals" → reset true, every level null
  Change only the goals the situation calls for, and never set a goal to "off" unless the operator says it does not matter — to shift the emphasis, lower it to "low". Apply every cue in the message: "battery is tight, keep 20% in every truck" raises energy AND sets the reserve; quote the whole span that covers them as `evidence`.
  `battery_reserve_pct`: % of each truck's battery that must be left at the end of its route — "keep 20% in reserve" → 20; "make sure no truck runs out" / "never run flat" → 0; "stop enforcing the battery limit" → -1; otherwise null. `evidence`: the exact words of the message that motivated the change. A question about how goals or weights work, or what they are now, is "goals" with `goal_update` null. Code writes the reply.
- "limits": a question about what a truck can do — battery capacity, range, how far it gets, payload, max weight, how many packs a route needs ("what are the limits of a truck with 3 battery packs?", "how far can a 5-pack truck go when it's -5 C?", "can 4 packs handle today's routes?"). Fill `limits_query` with only the numbers the operator stated (packs, gross weight in lbs, temperature in °C; convert °F) and an exact `evidence` substring; null where not stated. Do NOT answer with numbers yourself — code computes the answer. No changes.
- "disruption": a truck breaks down, crashes, has an accident, a flat tyre or gets stuck — simulate it on the current plan ("truck 2 broke down after its second stop", "simulate a crash for Wang's truck an hour in", "vehicle 1 had an accident leaving the depot, send a spare"). Fill `disruption`: `vehicle_number` (1-based: "truck 2", "the second van"), or `driver` (the driver's name exactly as written), `after_stop` (stops it completed first: "after its 2nd stop" → 2, "leaving the depot" / "before its first stop" → 0) OR `at_minute` (minutes after dispatch: "an hour in" → 60, "after 90 minutes" → 90), `spare_truck` true only if they ask for a spare / backup / extra truck, and an exact `evidence` substring. Use at_minute for any time ("an hour in", "at 90 minutes") and after_stop for a stop count — never both. Leave anything not said null — never guess the truck or the stop; with the truck or the moment missing it is still "disruption" (code asks). No changes.
- "out_of_scope": anything the form cannot do (weather forecasts, live traffic, delivery time windows, reading individual results, small talk, unrelated questions). Resetting or clearing the whole form ("reset the form", "start over") is out_of_scope too — only resetting the GOALS is "goals". No changes. Say briefly what you cannot do.

AXES (the only fields that exist):
- depot (text): the depot / warehouse / start address
- addresses (list of text): the delivery stops. op "set" replaces the whole list, "add" appends, "remove" deletes matching stops
- vehicle_count (integer 1–10)
- battery_packs (integer 3–9; each pack is 20 kWh; applies to every vehicle)
- vehicle_weight_lbs (integer 10000–19500)
- temperature_c (integer −10 to 40; convert °F to °C)
- unload_time_min (integer 5–120; minutes per stop)
- has_climate_control (boolean; AC / HVAC on or off)
- priority_stops (list of text): stops to serve before the others on their truck ("deliver to Brea first", "Stanton is urgent", "Cerritos is no longer a priority"). op "add" / "remove" / "set"; the values are the stop names as the operator wrote them. Only when the message names the stop — being late or in a hurry without naming a stop is "goals", never a priority stop.
- solver — one of: "auto" (let the app decide), "held_karp" (the exact / optimal / proven / guaranteed solver), "2opt" (fast approximation), "nearest_neighbor" (greedy, like Google Maps), "ortools_vrp" (multi-vehicle). "force the exact solver", "I want the guaranteed optimum" → held_karp, never auto. an instruction naming a solver ("switch to 2-opt", "use nearest neighbor", "force the exact solver", "go back to auto") is intent "apply" on this axis. A question ABOUT solvers ("which one should I use", "why not the optimal one") is intent "recommend".

VALUE RULES:
- Fill exactly ONE of value_text / value_number / value_bool / value_list per change, matching the axis type. The other three must be null. Stops always go in value_list, even a single stop; a solver name goes in value_text.
- `evidence` must be an EXACT substring copied from the operator's latest message that justifies the change — copy it verbatim, do not add punctuation. Never invent a value the message does not contain; if it is not there, do not emit the change.
- Keep addresses exactly as written. Do not add a city, state or country the operator did not write. A place name without a state or country that could be in several places → intent "ask".
- `op` is "set" for every axis except addresses, which also accepts "add" and "remove". Resolve relative requests ("one more truck", "5 degrees warmer") against CURRENT FORM STATE and emit the resulting absolute number with op "set".
- A plain statement of a value ("depot is X", "仓库设在 X", "temperature 30") is intent "apply", even when it already equals CURRENT FORM STATE — emit the change anyway rather than asking whether they want it.
- An on-topic message with no usable value ("it's really hot today", "the trucks feel heavy") is intent "ask": ask for the number. Reserve "out_of_scope" for things this form genuinely cannot express.
- Leave `goal_update`, `limits_query` and `disruption` null except for their own intents ("goals", "limits", "disruption").
- Leave `memory_op`, `memory_profiles` and `memory_names` null for every intent except "memory". For intent "memory", `memory_op` is never null and `question` is null: do the operation, do not echo the operator's question back.
- For intent "recommend": whenever the message names a size at all — "22 stops and 3 trucks", "on 25 stops", "for 12 stops" — fill `advice_scenario` with those numbers plus an exact `evidence` substring, even if it reads as a hypothetical. A question about how long a solver takes or how big it scales is `advice_topic` "why"; leave the numbers in `advice_scenario`. If the message names one solver ("should I use Held-Karp?", "is 2-opt good enough?", "Held-Karp 能跑 25 个站吗"), put that solver's key in `advice_about`. Chinese counts too: "25 个站" is 25 stops, "3 辆车" is 3 vehicles. set `need_optimal` true only if they said they require a guaranteed/proven optimum. Otherwise leave `advice_scenario` null and the current form state is used. Leave `advice_topic` and `advice_scenario` null for every other intent.
- `message`: one short, friendly sentence in the operator's language. For "ask" it must equal `question`. For "recommend", "goals", "limits" and "disruption" it is ignored — code writes the answer.

CURRENT FORM STATE:
{form_state}
{pending_block}"""


def build_system_prompt(form_state: dict, pending) -> str:
    saved = load_memory()['drivers']
    saved_block = ("\nSAVED DRIVERS (memory): "
                   + ", ".join(describe_profile(p) for p in saved.values())
                   + "\nA message naming one of these updates or loads that SAVED driver (intent \"memory\"), "
                     "not the current form."
                   if saved else "\nSAVED DRIVERS (memory): none yet.")
    pending_block = ""
    if pending and pending.get('question'):
        pending_block = (
            f"\nPENDING QUESTION you asked earlier: \"{pending['question']}\""
            "\nIf the latest message answers it, emit the corresponding change with intent \"apply\"."
        )
    return EXTRACTION_SYSTEM_PROMPT.format(
        form_state=json.dumps(form_state or {}, ensure_ascii=False, indent=2),
        pending_block=saved_block + pending_block,
    )


def extraction_schema_for(pending) -> dict:
    """Closed schema, narrowed to the situation: with nothing pending there is
    nothing to confirm, so the model is not offered that intent at all."""
    if pending and pending.get('question'):
        return EXTRACTION_SCHEMA
    schema = json.loads(json.dumps(EXTRACTION_SCHEMA))
    step = schema['properties']['steps']['items']
    step['properties']['intent']['enum'] = [i for i in INTENTS if i != 'confirm']
    return schema


def call_extraction(utterance: str, history: list, form_state: dict, pending) -> dict:
    messages = [{"role": "system", "content": build_system_prompt(form_state, pending)}]
    for h in history[-10:]:
        if h.get('role') in ('user', 'assistant') and h.get('content'):
            messages.append({"role": h['role'], "content": str(h['content'])})
    messages.append({"role": "user", "content": utterance})

    body = {
        "model": OPENAI_MODEL,
        "messages": messages,
        "temperature": 0,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "route_chat_extraction", "strict": True,
                            "schema": extraction_schema_for(pending)},
        },
    }
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"}
    response = requests.post(OPENAI_CHAT_URL, headers=headers, json=body, timeout=60)
    # Reasoning models (gpt-5 and later) only accept the default temperature.
    if response.status_code == 400 and 'temperature' in response.text:
        body.pop('temperature')
        response = requests.post(OPENAI_CHAT_URL, headers=headers, json=body, timeout=60)
    if response.status_code != 200:
        raise RuntimeError(f"OpenAI {response.status_code}: {response.text[:300]}")

    choice = response.json()['choices'][0]['message']
    if choice.get('refusal'):
        return {"steps": [{"intent": "out_of_scope", "changes": [], "question": None, "question_axis": None,
                           "ambiguous_term": None, "message": choice['refusal']}]}
    return json.loads(choice['content'])


# ---- 3. deterministic validation --------------------------------------------

# Words that make an empty stops change mean "remove all of them".
CLEAR_ALL_RE = re.compile(r'\b(?:all|every(?:thing|one)?|clear|wipe|empty|remove|delete)\b'
                          r'|全部|所有|清空|清除|删除|删掉', re.I)


def validate_change(raw: dict, utterance: str, form_state: dict = None, lang: str = 'en'):
    """Gate for one extracted change. Returns (change, None), (None, reason), or
    (None, None) for padding that is dropped silently."""
    form_state = form_state or {}
    axis = raw.get('axis')
    op = raw.get('op') or 'set'
    spec = AXES.get(axis)
    if not spec:
        return None, _r('unknown_field', lang, axis=axis)
    label = axis_label(axis, lang)

    if all(raw.get(k) in (None, '', []) for k in ('value_text', 'value_number', 'value_bool', 'value_list')):
        # "clear all stops": a remove with nothing named means all of them —
        # but only when the operator's words say so. Filling in the current
        # list turns it into an ordinary change the gates can see.
        current = [a for a in (form_state.get('addresses') or []) if str(a).strip()]
        if axis == 'priority_stops':
            current = list(form_state.get('priority_stops') or [])
        if (axis in ('addresses', 'priority_stops') and op in ('remove', 'set') and current
                and CLEAR_ALL_RE.search(_strip_edges(raw.get('evidence')))):
            raw = dict(raw, value_list=list(current))
            op = 'remove'
        else:
            # Any other change with no value is the model padding the array
            # (one entry per field, quoting the same words). There is nothing
            # to apply, so drop it without a "skipped" note.
            return None, None

    # "one more truck" / "5 degrees warmer": a relative move on a numeric axis.
    # Resolve it against the form here rather than trusting the model to add up.
    if spec['type'] == 'number' and op in ('add', 'remove') and raw.get('value_number') is not None:
        current = form_state.get(axis)
        if isinstance(current, (int, float)):
            delta = float(raw['value_number'])
            raw = dict(raw, op='set',
                       value_number=current + (delta if op == 'add' else -delta))
            op = 'set'

    # Null coupling: exactly one typed slot is filled, and it is this axis's slot.
    slots = {
        'text': raw.get('value_text'),
        'number': raw.get('value_number'),
        'bool': raw.get('value_bool'),
        'list': raw.get('value_list'),
    }
    filled = [k for k, v in slots.items() if v is not None]
    if spec['type'] == 'list' and filled == ['text']:
        # one stop written into the text slot: same information, wrong container
        slots['list'], slots['text'], filled = [slots['text']], None, ['list']
    if spec['type'] == 'number' and filled == ['text'] and re.fullmatch(r'\s*-?\d+(?:\.\d+)?\s*', str(slots['text'])):
        # "6" for battery packs: a number in the text slot, same information
        slots['number'], slots['text'], filled = float(slots['text']), None, ['number']
    # An enum is carried as a string; check_value below maps it onto the choices.
    slot = 'text' if spec['type'] == 'enum' else spec['type']
    if slot not in filled:
        # The wrong container means the model misread the axis — that is a real
        # type error. Extra slots alongside the right one are just noise.
        return None, _r('slot', lang, label=label, got=filled or '-', need=slot)

    value, reason = check_value(axis, op, slots[slot], lang)
    if reason:
        return None, reason

    # Evidence span: the justification must literally appear in the utterance.
    evidence = _strip_edges(raw.get('evidence'))
    if not evidence or _norm(evidence) not in _norm(utterance):
        return None, _r('no_evidence', lang, label=label)

    # Quoting the whole sentence would let any invented number through the span
    # check, so a numeric change has to point at something numeric.
    if spec['type'] == 'number' and not NUMERIC_EVIDENCE_RE.search(evidence):
        return None, _r('no_number', lang, label=label)
    if spec['type'] == 'bool' and not BOOL_EVIDENCE_RE.search(evidence):
        return None, _r('no_onoff', lang, label=label)
    # "get everyone back sooner" is a goal, not a request for nearest neighbor.
    if axis == 'solver' and not SOLVER_EVIDENCE_RE.search(evidence):
        return None, _r('no_evidence', lang, label=label)

    # A priority stop has to be a stop already on the form; store it as written there.
    if axis == 'priority_stops':
        pool = (form_state.get('priority_stops') if op == 'remove' else form_state.get('addresses')) or []
        matched = []
        for v in value:
            hit = next((a for a in pool if v.lower() in a.lower() or a.lower() in v.lower()), None)
            if not hit:
                # "deliver to Brea first" names the stop by its city.
                named = [a for a in pool if any(
                    len(part) >= 3 and re.search(r'[^\W\d_]', part) and not _STATE_ZIP.match(part)
                    and re.search(rf'(?<!\w){re.escape(part)}(?!\w)', v, re.I)
                    for part in (x.strip() for x in a.split(',')))]
                hit = named[0] if len(named) == 1 else None
            if not hit:
                return None, _r('not_a_stop', lang, label=label, v=v)
            # The operator has to name the stop: "we're running late" must not
            # turn into a priority stop the model picked.
            parts = [v] + [x.strip() for x in hit.split(',') if len(x.strip()) >= 3 and not _STATE_ZIP.match(x.strip())]
            if not any(_loose(x) and _loose(x) in _loose(utterance) for x in parts):
                return None, _r('no_evidence', lang, label=label)
            if hit not in matched:
                matched.append(hit)
        value = matched

    return make_change(axis, op, value, evidence, lang), None


_STATE_ZIP = re.compile(r'^(?:[A-Z]{2}(?:\s+\d{5}(?:-\d{4})?)?|USA|United States)$')


def _loose(s: str) -> str:
    """Letters and digits only: "1400 S Douglass Rd, Anaheim" ~ "1400 s douglass rd anaheim"."""
    return re.sub(r'[\W_]+', '', str(s)).lower()


def restates_unwritten_place(change: dict, form_state: dict, utterance: str) -> bool:
    if change['axis'] == 'depot':
        current, values = form_state.get('depot'), [change['value']]
    elif change['axis'] == 'addresses' and change['op'] == 'set':
        current, values = form_state.get('addresses'), change['value']
        values = values if list(values) == list(current or []) else None
    else:
        return False
    if values is None or (change['axis'] == 'depot' and change['value'] != current):
        return False
    return not any(_loose(v) in _loose(utterance) for v in values)


def ambiguous_place_in(change: dict):
    """Return the ambiguous place name in an address change, or None."""
    if change['axis'] == 'depot' and looks_ambiguous_place(change['value']):
        return change['value']
    if change['axis'] == 'addresses' and change['op'] != 'remove':
        for v in change['value']:
            if looks_ambiguous_place(v):
                return v
    return None


# Save-type verbs. Read only when the model labelled a message "memory" but
# filled in no operation. Bare "remember" is left out: "which drivers do you
# remember?" is a list question.
SAVE_VERB_RE = re.compile(r'\b(?:save|add|store|register|create)\b|记住|保存|添加|新增|登记', re.I)


def validate_extraction(raw: dict, utterance: str, form_state: dict = None,
                        pending_exists: bool = False) -> dict:
    """Apply the coupling rules to a whole extraction. Never trusts the model."""
    form_state = form_state or {}
    lang = detect_lang(utterance)
    intent = raw.get('intent')
    if intent not in INTENTS:
        intent = 'out_of_scope'
    question = (raw.get('question') or '').strip() or None
    question_axis = raw.get('question_axis') if raw.get('question_axis') in AXES else None
    ambiguous = (raw.get('ambiguous_term') or '').strip() or None
    message = (raw.get('message') or '').strip()

    # Solver advice: the topic is a closed enum, and a hypothetical size is only
    # honoured when its evidence really appears in the utterance.
    topic = raw.get('advice_topic') if raw.get('advice_topic') in ('which', 'why', 'current') else None
    about = raw.get('advice_about') if raw.get('advice_about') in SOLVERS else None
    mem_op = raw.get('memory_op') if raw.get('memory_op') in ('remember', 'recall', 'forget', 'list') else None
    mem_profiles = raw.get('memory_profiles') if isinstance(raw.get('memory_profiles'), list) else []
    mem_names = [str(n).strip() for n in (raw.get('memory_names') or []) if str(n).strip()]
    if intent == 'memory' and not mem_op:
        # Nothing extracted: let the operator's verb decide, so someone who asked
        # to save a driver is told the name was missed rather than shown a list.
        mem_op = ('remember' if mem_profiles else 'recall' if mem_names
                  else 'remember' if SAVE_VERB_RE.search(utterance) else 'list')
    # "remember" with the names in memory_names instead of memory_profiles: same
    # information, wrong container. validate_profile still checks the name.
    if intent == 'memory' and mem_op == 'remember' and not mem_profiles and mem_names:
        mem_profiles = [{'name': n, 'evidence': n} for n in mem_names]
    # Same anti-hallucination rule as saving: a driver is only deleted or loaded
    # if the operator wrote that name. Without it "delete everything" came back
    # as forget [Wang, Li, Han, Linus], names lifted from the prompt's examples.
    names_refused = []
    if intent == 'memory' and mem_op in ('forget', 'recall'):
        names_refused = [n for n in mem_names if not name_in(n, utterance)]
        mem_names = [n for n in mem_names if name_in(n, utterance)]
    # "is 2-opt good enough?" is a pick question, not a tradeoff lecture. The model
    # mislabels it as "why" about a third of the time, so the utterance decides.
    if topic == 'why' and not WHY_RE.search(utterance):
        topic = 'which'
    scenario = None
    if intent == 'recommend':
        raw_scen = raw.get('advice_scenario')
        if isinstance(raw_scen, dict) and _norm(_strip_edges(raw_scen.get('evidence'))) in _norm(utterance):
            stops, vehicles = raw_scen.get('stops'), raw_scen.get('vehicles')
            if stops is not None or vehicles is not None:
                scenario = {'stops': stops, 'vehicles': vehicles,
                            'need_optimal': bool(raw_scen.get('need_optimal'))}
        topic = topic or 'which'

    goal_update = raw.get('goal_update') if isinstance(raw.get('goal_update'), dict) else None
    limits_query = raw.get('limits_query') if isinstance(raw.get('limits_query'), dict) else None
    disruption = raw.get('disruption') if isinstance(raw.get('disruption'), dict) else None
    # "can 4 packs handle today's routes?" is about the truck, not the solver.
    # The model files it under "recommend" often enough that the words decide.
    if (intent == 'recommend' and LIMITS_WORD_RE.search(utterance)
            and not SOLVER_EVIDENCE_RE.search(utterance) and not re.search(_SOLVER_WORD, utterance, re.I)):
        intent = 'limits'
        m = PACKS_IN_TEXT_RE.search(utterance)
        limits_query = ({'battery_packs': float(m.group(1) or m.group(2)), 'evidence': m.group(0)}
                        if m else None)
    if intent == 'limits':
        # "how far can a 5-pack truck go at -5 C?" often comes back with the
        # numbers as form changes: same information, wrong container. They are
        # a hypothetical truck, never applied to the form.
        limits_query = dict(limits_query or {})
        for c in raw.get('changes') or []:
            field = LIMITS_FIELD_OF_AXIS.get((c or {}).get('axis')) if isinstance(c, dict) else None
            val = c.get('value_number') if field and c.get('value_number') is not None else (c or {}).get('value_text')
            if not field or val is None or limits_query.get(field) is not None:
                continue
            try:
                limits_query[field] = float(val)
            except (TypeError, ValueError):
                continue
            limits_query.setdefault('evidence_by_field', {})[field] = c.get('evidence')

    changes, rejected = [], []
    if intent in ('apply', 'confirm'):
        for c in raw.get('changes') or []:
            change, reason = validate_change(c if isinstance(c, dict) else {}, utterance, form_state, lang)
            if change:
                changes.append(change)
            elif reason:
                rejected.append(reason)
        # Padding with a value: a field restated as it already is, quoting the
        # words of an earlier change ("use 3 trucks" → also "battery packs 4").
        # It would change nothing, so drop it rather than ask about it.
        kept, seen_evidence = [], set()
        for c in changes:
            ev = _norm(c['evidence'])
            if ev in seen_evidence and form_state.get(c['axis']) == c['value']:
                continue
            seen_evidence.add(ev)
            kept.append(c)
        # An address "change" that is just the form's current value, which the
        # operator never wrote ("reset the form" → depot = the current depot),
        # is padding too.
        changes = [c for c in kept if not restates_unwritten_place(c, form_state, utterance)]

    # "I need a guaranteed optimal route for 12 stops" names a problem size,
    # so it asks what fits that size; it does not pin the solver on this form.
    if intent == 'apply' and changes and all(c['axis'] == 'solver' for c in changes):
        m_s, m_v = SIZE_STOPS_RE.search(utterance), SIZE_VEH_RE.search(utterance)
        n_s = int(m_s.group(1)) if m_s else None
        n_v = int(m_v.group(1)) if m_v else None
        if (n_s is not None and n_s != len(form_state.get('addresses') or [])) or \
                (n_v is not None and n_v != int(form_state.get('vehicle_count') or 1)):
            intent, topic = 'recommend', 'which'
            about = changes[0]['value'] if changes[0]['value'] in SOLVERS else None
            scenario = {'stops': n_s, 'vehicles': n_v, 'need_optimal': bool(NEED_OPTIMAL_RE.search(utterance))}
            changes = []

    # Ambiguity flag / term: an ambiguous extraction may never be applied.
    # The model may raise the flag; the place check below raises it regardless.
    if intent == 'apply' and not ambiguous:
        for c in changes:
            term = ambiguous_place_in(c)
            if term:
                ambiguous, question_axis = term, c['axis']
                break
    if ambiguous and intent == 'apply':
        intent = 'ask'
        question = question or (f"「{ambiguous}」可能指好几个地方——你说的是哪个城市和州？" if lang == 'zh'
                                else f"\"{ambiguous}\" could be in several places — which city and state do you mean?")

    # Null coupling at the intent level.
    if intent == 'apply' and not changes:
        intent = 'ask'
        detail = ("（" + "；".join(rejected) + "）") if rejected else ""
        question = (f"这条我没法从你的话里核实。{detail}能再说一遍具体的值吗？" if lang == 'zh'
                    else "I couldn't verify that in your message. "
                         + (("(" + "; ".join(rejected) + ".) ") if rejected else "")
                         + "Could you say it again with the exact value?")
        question_axis = question_axis or _single_rejected_axis(raw)
    if intent == 'ask' and not question:
        question = "你想改哪一项？能说得具体点吗？" if lang == 'zh' else "Could you clarify what you'd like to change?"
    if intent == 'confirm' and changes and not pending_exists:
        # The model labelled a plain instruction as a confirmation. The changes
        # passed validation, so honour them instead of dropping them.
        intent = 'apply'
    if intent in ('confirm', 'out_of_scope', 'recommend', 'memory', 'goals', 'limits', 'disruption'):
        changes, question = [], None
    if intent == 'out_of_scope' and not message:
        message = "这个我帮不了。" if lang == 'zh' else "I can't help with that."

    return {'intent': intent, 'changes': changes, 'rejected': rejected, 'lang': lang,
            'question': question, 'question_axis': question_axis, 'message': message,
            'advice_topic': topic, 'advice_scenario': scenario, 'advice_about': about,
            'memory_op': mem_op, 'memory_profiles': mem_profiles, 'memory_names': mem_names,
            'memory_names_refused': names_refused, 'utterance': utterance,
            'goal_update': goal_update, 'limits_query': limits_query, 'disruption': disruption}


def _single_rejected_axis(raw: dict):
    axes = {c.get('axis') for c in (raw.get('changes') or []) if isinstance(c, dict)}
    axes = {a for a in axes if a in AXES}
    return axes.pop() if len(axes) == 1 else None


def route_outcome(verdict: dict, pending, source: str, form_state: dict = None) -> dict:
    intent = verdict['intent']
    form_state = form_state or {}
    lang = verdict.get('lang', 'en')
    if intent == 'confirm':
        queued = (pending or {}).get('queued') or []
        if queued:
            return outcome_apply(queued, source='session', confirmed=True, lang=lang)
        return outcome_out_of_scope(_o('nothing', lang), source, list_capabilities=False, lang=lang)
    if intent == 'memory':
        op = verdict.get('memory_op') or 'list'
        names = verdict.get('memory_names') or []
        refused = [_m('no_evidence', lang, name=n) for n in verdict.get('memory_names_refused') or []]
        if op == 'remember':
            return do_remember(verdict.get('memory_profiles'), verdict.get('utterance', ''), source, lang)
        # No usable name: ask which driver rather than delete or load a guess.
        if op == 'forget' and not names:
            return outcome_memory(_m('which_forget', lang), source, lang, rejected=refused)
        if op == 'recall' and not names and refused:
            return outcome_memory(_m('which_recall', lang), source, lang, rejected=refused)
        out = (do_forget(names, source, lang) if op == 'forget'
               else do_recall(names if op == 'recall' else [], source, lang))
        out['rejected'] = refused + out['rejected']
        return out
    if intent == 'goals':
        return outcome_goals(verdict.get('goal_update'), verdict.get('utterance', ''), form_state, source, lang)
    if intent == 'limits':
        return outcome_limits(verdict.get('limits_query'), verdict.get('utterance', ''), form_state, source, lang)
    if intent == 'disruption':
        return outcome_disruption(verdict.get('disruption'), verdict.get('utterance', ''), form_state, source, lang)
    if intent == 'recommend':
        scen = verdict.get('advice_scenario') or {}
        stops = scen.get('stops')
        vehicles = scen.get('vehicles')
        hypothetical = stops is not None or vehicles is not None
        if stops is None:
            stops = len(form_state.get('addresses') or [])
        if vehicles is None:
            vehicles = form_state.get('vehicle_count') or 1
        return outcome_recommend(
            verdict.get('advice_topic') or 'which',
            int(stops), int(vehicles),
            form_state.get('solver', 'auto'), source,
            hypothetical=hypothetical, need_optimal=bool(scen.get('need_optimal')),
            lang=verdict.get('lang', 'en'), about=verdict.get('advice_about'))
    if intent == 'apply':
        return outcome_apply(verdict['changes'], source, rejected=verdict['rejected'], lang=lang)
    if intent == 'ask':
        return outcome_ask(verdict['question'], verdict['question_axis'], source,
                           rejected=verdict['rejected'], lang=lang)
    return outcome_out_of_scope(verdict['message'], source, lang=lang)


def advance_form_state(state: dict, changes: list) -> dict:
    """The form as it will look once the client applies `changes`, so a later
    step in the same message ("…then which solver?") sees the new values.
    Mirrors applyChatChange in RouteOptimizer.jsx."""
    state = dict(state or {})
    for c in changes:
        axis, op, value = c['axis'], c['op'], c['value']
        if axis == 'addresses':
            current = list(state.get('addresses') or [])
            if op == 'add':
                current += value
            elif op == 'remove':
                current = [a for a in current if not any(v.lower() in a.lower() for v in value)]
            else:
                current = list(value)
            state['addresses'] = current
        elif axis == 'priority_stops':
            current = list(state.get('priority_stops') or [])
            if op == 'add':
                current += [v for v in value if v not in current]
            elif op == 'remove':
                current = [a for a in current if a not in value]
            else:
                current = list(value)
            state['priority_stops'] = current
        elif axis == 'fleet':
            state['fleet'] = value
            state['vehicle_count'] = len(value)
        else:
            state[axis] = value
    return state


def outcome_hold(changes: list, source: str, lang: str = 'en') -> dict:
    """A form change in a message that already moved one: queued for a yes,
    exactly as outcome_apply queues the second change of a single step."""
    question = _o('also', lang, label=changes[0]['label'])
    return {
        'status': 'ok', 'intent': 'apply', 'source': source,
        'message': question, 'changes': [],
        'pending': {'question': question, 'axis': None, 'queued': changes},
        'capabilities': None, 'rejected': [], 'advice': None, 'profiles': [],
    }


STEP_TEXT = {
    'on_hold': {'en': "The rest of your request is on hold until you answer — say it again afterwards.",
                'zh': "剩下的请求先暂停，回答之后请再说一遍。"},
}


def run_steps(raw_steps: list, utterance: str, pending, source: str, form_state: dict = None) -> dict:
    """Validate and run each extracted step in order, then merge the replies.

    A later step is validated against the form as the earlier steps left it.
    The first step that needs an answer (a question, a queued change, a
    switch offer) ends the turn: only one question can be pending, and the
    steps after it may depend on the answer.
    """
    steps = [s for s in raw_steps if isinstance(s, dict)][:MAX_STEPS] or [{}]
    state = dict(form_state or {})
    outcomes, moved_form = [], False
    for raw in steps:
        verdict = validate_extraction(raw, utterance, state, pending_exists=bool(pending))
        if verdict['intent'] == 'apply' and moved_form and ONE_CHANGE_PER_TURN:
            out = outcome_hold(verdict['changes'], source, verdict.get('lang', 'en'))
        else:
            out = route_outcome(verdict, pending, source, state)
            if out['intent'] == 'goals' and out['changes'] and moved_form and ONE_CHANGE_PER_TURN:
                out = outcome_hold(out['changes'], source, verdict.get('lang', 'en'))
        moved_form |= out['intent'] in ('apply', 'goals') and bool(out['changes'])
        state = advance_form_state(state, out['changes'])
        outcomes.append(out)
        if out['pending']:
            break

    if len(outcomes) == 1 and len(steps) == 1:
        return outcomes[0]

    lang = detect_lang(utterance)
    messages = [o['message'] for o in outcomes if o['message']]
    if len(outcomes) < len(steps):
        messages.append(STEP_TEXT['on_hold'].get(lang, STEP_TEXT['on_hold']['en']))
    last = outcomes[-1]
    return {
        'status': 'ok', 'intent': last['intent'], 'source': source,
        'message': "\n".join(messages),
        'changes': [c for o in outcomes for c in o['changes']],
        'pending': last['pending'],
        'capabilities': next((o['capabilities'] for o in outcomes if o['capabilities']), None),
        'rejected': [r for o in outcomes for r in o['rejected']],
        'advice': next((o['advice'] for o in reversed(outcomes) if o['advice']), None),
        'profiles': [p for o in outcomes for p in o['profiles']],
        'action': next((o.get('action') for o in outcomes if o.get('action')), None),
        'limits': next((o.get('limits') for o in reversed(outcomes) if o.get('limits')), None),
        'goals_info': next((o.get('goals_info') for o in reversed(outcomes) if o.get('goals_info')), None),
    }


@app.route('/api/chat', methods=['POST'])
def ai_chat():
    """Operator utterance → lexicon → extraction → validation → one of three outcomes."""
    data = request.json or {}
    utterance = str(data.get('message', '')).strip()
    history = data.get('history') or []
    form_state = data.get('form_state') or {}
    pending = data.get('pending') or None

    if not utterance:
        return jsonify({"status": "error", "message": "No message provided"}), 400

    # 1. lexicon fast path
    hit = lexicon_fast_path(utterance, pending, form_state)
    if hit:
        return jsonify(hit)

    # 2. constrained extraction
    if not OPENAI_API_KEY:
        return jsonify(outcome_out_of_scope(
            "AI extraction is unavailable (no LLM_API_KEY or OPENAI_API_KEY is set). "
            "Short phrasings still work without it, e.g. \"temperature 30 C\" or \"6 battery packs\":",
            source='lexicon'))
    try:
        raw = call_extraction(utterance, history, form_state, pending)
    except Exception as e:
        return jsonify({"status": "error", "message": f"Extraction failed: {e}"}), 502

    # 3. deterministic validation of each step, run in order
    return jsonify(run_steps(raw.get('steps') or [], utterance, pending, 'model', form_state))


# =============================================================================
# Driver memory — named driver profiles that survive the session
# =============================================================================
#
# The memory tracks drivers, not vehicles: no plates, no truck names. A profile
# is the driver's name plus, optionally, the battery packs and weight of the
# truck that driver runs. When present those feed the energy model on recall;
# a name-only profile still records who is driving and leaves the form's
# vehicle specs alone.
#
# Stored as one JSON file next to the server: small, inspectable, and easy to
# show to someone who asks where it actually lives.

MEMORY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fleet_memory.json')

# Optional truck specs on a driver profile → the form axis whose range check they reuse.
PROFILE_FIELDS = {
    'battery_packs': 'battery_packs',
    'weight_lbs':    'vehicle_weight_lbs',
}
MAX_PROFILES = 40
MAX_LABEL_LEN = 40


def _memory_default() -> dict:
    return {'drivers': {}}


def load_memory() -> dict:
    # A file written before driver profiles holds 'vehicles' (with plates); it
    # is not carried over, and the next save replaces it.
    try:
        with open(MEMORY_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if not isinstance(data, dict) or not isinstance(data.get('drivers'), dict):
            return _memory_default()
        return data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return _memory_default()


def save_memory(mem: dict) -> None:
    tmp = MEMORY_PATH + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(mem, f, ensure_ascii=False, indent=2)
    os.replace(tmp, MEMORY_PATH)


def profile_key(name: str) -> str:
    """Lookup key: case- and space-insensitive, so "wang" finds "Wang"."""
    return re.sub(r'\s+', '', str(name or '')).lower()


def profile_to_vehicle(profile: dict, vid: int) -> dict:
    """A recalled driver in the shape the form's `fleet` axis expects.

    Packs and weight are None when the profile does not store them; the form
    then keeps whatever that vehicle slot already has.
    """
    packs = profile.get('battery_packs')
    return {
        'id': vid,
        'driver': profile.get('name', ''),
        'battery_packs': packs,
        'battery_kwh': packs * 20 if packs is not None else None,
        'weight_lbs': profile.get('weight_lbs'),
    }


def describe_profile(profile: dict, lang: str = 'en') -> str:
    bits = []
    if profile.get('battery_packs') is not None:
        packs = profile['battery_packs']
        bits.append(f"{packs} packs ({packs * 20} kWh)")
    if profile.get('weight_lbs') is not None:
        bits.append(f"{int(profile['weight_lbs']):,} lbs")
    name = profile.get('name', '?')
    return f"{name} — " + " · ".join(bits) if bits else name


def validate_profile(raw: dict, utterance: str, lang: str = 'en'):
    """Gate for one remembered driver. Returns (profile, None) or (None, reason)."""
    name = _strip_edges(raw.get('name'))[:MAX_LABEL_LEN]
    if not name:
        return None, _m('no_name', lang)

    # The driver's name must be written in the message, so the model cannot
    # invent a driver.
    if not name_in(name, utterance):
        return None, _m('no_evidence', lang, name=name)

    profile = {'name': typed_spelling(name, utterance)}
    for field, axis in PROFILE_FIELDS.items():
        value = raw.get(field)
        if value is None or value == '':
            continue
        # Reuse the form's own range checks — a profile cannot store a truck
        # the form would refuse.
        value, reason = check_value(axis, 'set', value, lang)
        if reason:
            return None, f"{name}: {reason}"
        profile[field] = value

    # Packs and weight need the evidence span: the model must quote the
    # operator's own words for this driver, so it cannot invent the numbers or
    # move one driver's onto another. A name-only profile has nothing more to
    # guard. Checking the span there anyway rejected "add driver Linus and Han",
    # because the model quoted "add driver Han", which is not in the message.
    if len(profile) > 1:
        evidence = _strip_edges(raw.get('evidence'))
        if not evidence or _norm(evidence) not in _norm(utterance):
            return None, _m('no_spec_evidence', lang, name=name)
    return profile, None


MEMORY_TEXT = {
    'no_name':     {'en': "a driver profile needs the driver's name", 'zh': "司机档案得有司机的名字"},
    'no_driver':   {'en': "I couldn't find a driver's name to save. Try: \"save driver Donghua\"",
                    'zh': "没找到要保存的司机名字。试试：「记住司机东华」"},
    'no_evidence': {'en': "{name}: not found in your message", 'zh': "{name}：在你的话里找不到依据"},
    'no_spec_evidence': {'en': "{name}: couldn't tell which packs or weight are {name}'s. "
                               "Try: \"{name} runs 6 packs\"",
                         'zh': "{name}：分不清哪些电池包数或重量属于 {name}。试试：「{name} 6 块电池」"},
    'saved':       {'en': "Saved {n} driver{s}: {list}", 'zh': "已记住 {n} 位司机：{list}"},
    'updated':     {'en': "Updated {list}", 'zh': "已更新 {list}"},
    'full':        {'en': "Memory is full ({max} drivers). Forget one first.",
                    'zh': "记忆已满（{max} 位司机）。先删掉一位吧。"},
    'loaded':      {'en': "Loaded {n} driver{s} into the form: {list}",
                    'zh': "已载入 {n} 位司机到表单：{list}"},
    'unknown':     {'en': "I don't have {missing} saved. I know: {known}",
                    'zh': "我没存过 {missing}。已存的有：{known}"},
    'none_saved':  {'en': "No drivers saved yet. Try: \"remember driver Wang, 6 packs, 15000 lbs\"",
                    'zh': "还没存过司机。试试：「记住司机老王，6 块电池，15000 磅」"},
    'forgot':      {'en': "Driver{s} {list} deleted", 'zh': "已删除司机 {list}"},
    'which_forget': {'en': "Which driver should I delete? Name them, e.g. \"delete driver Li\"",
                     'zh': "要删除哪位司机？请说出名字，例如「删除司机老王」"},
    'which_recall': {'en': "Which drivers are driving? Name them, e.g. \"Li and Wang are driving today\"",
                     'zh': "哪几位司机出车？请说出名字，例如「今天老王和小李出车」"},
    'listing':     {'en': "{n} driver{s} saved:", 'zh': "已存 {n} 位司机："},
}


def _m(key: str, lang: str, **kw) -> str:
    return MEMORY_TEXT[key].get(lang, MEMORY_TEXT[key]['en']).format(**kw)


def outcome_memory(message: str, source: str, lang: str = 'en', changes=None,
                   profiles=None, rejected=None) -> dict:
    """A memory read/write. `changes` is set when a recall also fills the form."""
    return {
        'status': 'ok', 'intent': 'memory', 'source': source,
        'message': message, 'changes': changes or [], 'pending': None,
        'capabilities': None, 'rejected': rejected or [], 'advice': None,
        'profiles': profiles or [],
    }


def do_remember(raw_profiles: list, utterance: str, source: str, lang: str) -> dict:
    mem = load_memory()
    saved, rejected = [], []
    for raw in raw_profiles or []:
        profile, reason = validate_profile(raw if isinstance(raw, dict) else {}, utterance, lang)
        if not profile:
            rejected.append(reason)
            continue
        key = profile_key(profile['name'])
        existing = mem['drivers'].get(key)
        if existing is None and len(mem['drivers']) >= MAX_PROFILES:
            rejected.append(_m('full', lang, max=MAX_PROFILES))
            continue
        # The same driver typed in different case: "Donghua" corrects a stored
        # "donghua", but a casual all-lowercase update keeps the stored capitals.
        if existing and profile['name'] == profile['name'].lower():
            profile['name'] = existing['name']
        # Merge, so "Wang runs 8 packs now" keeps the stored weight.
        merged = {**(existing or {}), **profile}
        mem['drivers'][key] = merged
        saved.append(merged)

    if saved:
        save_memory(mem)
    sep = '；' if lang == 'zh' else '; '
    message = (_m('saved', lang, n=len(saved), s='' if len(saved) == 1 else 's',
                  list=sep.join(describe_profile(p, lang) for p in saved))
               if saved else (sep.join(rejected) if rejected else _m('no_driver', lang)))
    return outcome_memory(message, source, lang, profiles=saved,
                          rejected=rejected if saved else [])


def do_recall(names: list, source: str, lang: str) -> dict:
    mem = load_memory()
    if not mem['drivers']:
        return outcome_memory(_m('none_saved', lang), source, lang)

    # No names given → list who is stored rather than guessing. The chat shows
    # each profile as a card, so the text is just the count.
    if not names:
        return outcome_memory(_m('listing', lang, n=len(mem['drivers']),
                                 s='' if len(mem['drivers']) == 1 else 's'),
                              source, lang, profiles=list(mem['drivers'].values()))

    found, missing = [], []
    for n in names:
        p = mem['drivers'].get(profile_key(n))
        (found if p else missing).append(p or n)
    if not found:
        return outcome_memory(_m('unknown', lang, missing=", ".join(missing),
                                 known=", ".join(p['name'] for p in mem['drivers'].values())),
                              source, lang)

    fleet = [profile_to_vehicle(p, i + 1) for i, p in enumerate(found)]
    change = make_change('fleet', 'set', fleet,
                         ", ".join(p['name'] for p in found), lang)
    sep = '；' if lang == 'zh' else '; '
    message = _m('loaded', lang, n=len(found), s='' if len(found) == 1 else 's',
                 list=sep.join(describe_profile(p, lang) for p in found))
    rejected = [_m('unknown', lang, missing=", ".join(missing),
                   known=", ".join(p['name'] for p in mem['drivers'].values()))] if missing else []
    return outcome_memory(message, source, lang, changes=[change],
                          profiles=found, rejected=rejected)


def do_forget(names: list, source: str, lang: str) -> dict:
    mem = load_memory()
    gone, missing = [], []
    for n in names or []:
        p = mem['drivers'].pop(profile_key(n), None)
        (gone.append(p['name']) if p else missing.append(n))
    if gone:
        save_memory(mem)
        return outcome_memory(_m('forgot', lang, s='' if len(gone) == 1 else 's',
                                 list=", ".join(gone)), source, lang)
    known = ", ".join(p['name'] for p in mem['drivers'].values()) or '-'
    return outcome_memory(_m('unknown', lang, missing=", ".join(missing or ['?']), known=known),
                          source, lang)


@app.route('/api/memory', methods=['GET'])
def get_memory():
    """Lists the saved driver profiles."""
    mem = load_memory()
    return jsonify({"status": "ok", "drivers": list(mem['drivers'].values())})


@app.route('/api/memory', methods=['DELETE'])
def clear_memory():
    name = (request.args.get('name') or '').strip()
    mem = load_memory()
    if name:
        removed = mem['drivers'].pop(profile_key(name), None)
        save_memory(mem)
        return jsonify({"status": "ok", "removed": removed['name'] if removed else None,
                        "drivers": list(mem['drivers'].values())})
    save_memory(_memory_default())
    return jsonify({"status": "ok", "removed": "all", "drivers": []})


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
    print(f"Chat model: {OPENAI_MODEL} at {LLM_BASE_URL}" if OPENAI_API_KEY
          else "Chat model: none (no LLM_API_KEY / OPENAI_API_KEY) - lexicon fast path only")
    print()
    print("Endpoints:")
    print("  POST /api/set_key        - Set Google Maps API key")
    print("  POST /api/geocode        - Geocode single address")
    print("  POST /api/geocode_batch  - Geocode multiple addresses")
    print("  POST /api/optimize       - Optimize route (main endpoint)")
    print("  POST /api/directions     - Get turn-by-turn directions")
    print("  POST /api/chat           - Chat assistant (lexicon → extraction → validation)")
    print("  GET  /api/health         - Health check")
    print("  GET  /api/benchmark/scenarios  - List benchmark scenarios")
    print("  GET  /api/benchmark/algorithms - List benchmark algorithms")
    print("  POST /api/benchmark/run        - Run algorithm comparison")
    print()
    print("Starting server on http://localhost:5001")
    print("=" * 60)

    app.run(host='0.0.0.0', port=5001, debug=True)
