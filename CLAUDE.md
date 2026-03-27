# Isuzu BEV Fleet Route Optimizer — Project Spec

## Project Overview

A fleet route optimization system for Isuzu Battery Electric Vehicles (BEVs).
The system optimizes multi-vehicle delivery routes subject to real-world constraints:
energy consumption, vehicle capacity, and return-to-depot requirements.

This is a research/comparison tool for an MDP (Major Design Project). It compares:
- **Baseline**: Single-vehicle TSP using Held-Karp (exact optimal)
- **Fleet optimizer**: Multi-vehicle CVRP using Google OR-Tools

---

## Goals

1. Accept a depot address + list of delivery stops as input
2. Geocode all addresses via Google Maps API
3. Build a distance/time matrix via Google Distance Matrix API
4. Solve the route optimization problem with two approaches:
   - Held-Karp TSP (single vehicle, closed, exact optimal — baseline)
   - OR-Tools CVRP (multi-vehicle, closed, heuristic — fleet solution)
5. Apply a validated BEV energy model (Mercedes eActros) to estimate kWh per route
6. Output results as:
   - CLI: formatted text + two Google Maps URLs (one per algorithm)
   - Backend API: structured JSON for a React frontend

---

## Tech Stack

- **Language**: Python 3.11+
- **Optimization**: `ortools` (Google OR-Tools)
- **Web server**: `flask` + `flask-cors`
- **HTTP**: `requests`
- **Env**: `python-dotenv`
- **Frontend**: React (separate directory, calls the Flask backend)

Install:
```bash
pip install ortools flask flask-cors requests python-dotenv
```

---

## Environment

Always use `python3` in commands, never `python`.

API key is loaded from `.env` (never hardcode it):
```
GOOGLE_MAPS_API_KEY=your-key-here
```

`.env` must be in `.gitignore`.

---

## Architecture

```
fleet_optimizer/
├── api/
│   ├── __init__.py
│   ├── geocoding.py        # Google Geocoding + Distance Matrix API calls
│   └── routes.py           # Flask API endpoints
├── solvers/
│   ├── __init__.py
│   ├── held_karp.py        # Exact TSP solver (single vehicle baseline)
│   └── ortools_vrp.py      # OR-Tools CVRP solver (multi-vehicle fleet)
├── models/
│   ├── __init__.py
│   └── energy.py           # Mercedes eActros energy consumption model
├── cli.py                  # Standalone CLI tool
├── server.py               # Flask app entry point
├── requirements.txt
├── .env                    # API key (gitignored)
├── .env.example
├── .gitignore
└── CLAUDE.md               # This file
```

---

## Key Design Decisions

### 1. Depot is Always Index 0

The first address in any input (file or API) is always the depot. All routes
start and end at index 0. This is a hard requirement — never treat it as optional.

### 2. Closed TSP / Closed VRP Only

All routes are Hamiltonian cycles. Every vehicle departs from the depot and
returns to the depot. There is no open TSP mode.

### 3. Two Solvers, Same Interface

Both solvers must accept the same inputs and return the same output schema,
so the frontend/CLI can compare them side-by-side without special-casing.

Input to any solver:
```python
{
    "distance_matrix": List[List[int]],  # meters
    "time_matrix": List[List[int]],      # seconds
    "num_vehicles": int,                 # 1 for Held-Karp, N for OR-Tools
    "depot_index": int,                  # always 0
}
```

Output from any solver:
```python
{
    "routes": [
        {
            "vehicle_id": int,
            "stops": List[int],          # indices into locations list, starts and ends at depot
            "total_distance_m": int,
            "total_time_s": int,
            "total_energy_kwh": float,
            "legs": [
                {
                    "from_index": int,
                    "to_index": int,
                    "distance_m": int,
                    "time_s": int,
                    "energy_kwh": float,
                }
            ]
        }
    ],
    "total_distance_m": int,             # sum across all vehicles
    "total_time_s": int,
    "total_energy_kwh": float,
    "google_maps_urls": List[str],       # one URL per vehicle route
    "solver": str,                       # "held_karp" or "ortools_cvrp"
    "solve_time_ms": float,
}
```

### 4. Held-Karp Implementation

- Brute force permutations for n ≤ 10 (fixes at depot as index 0)
- Held-Karp DP bitmask for n > 10
- **Closed TSP**: when finding the best final node, always add `matrix[i][0]`
  to close the loop BEFORE comparing costs. Do NOT solve open TSP and tack
  on the return leg afterward — that gives wrong results.
- Always single vehicle (num_vehicles must be 1)

```python
# CORRECT closed TSP termination in Held-Karp:
for i in range(1, n):
    cost = dp[full_mask][i] + matrix[i][0]  # close the loop here
    if cost < best_cost:
        best_cost = cost
        best_end = i

# WRONG — do not do this:
# best_end = argmin(dp[full_mask][1:])  # open TSP end
# best_cost += matrix[best_end][0]      # then add return — gives wrong answer
```

### 5. OR-Tools VRP Implementation

Use `ortools.constraint_solver.routing_enums_pb2` and
`ortools.constraint_solver.pywrapcp`.

Standard setup:
```python
manager = pywrapcp.RoutingIndexManager(len(locations), num_vehicles, depot=0)
routing = pywrapcp.RoutingModel(manager)
```

Distance callback registered for the arc cost evaluator.
Use `PATH_CHEAPEST_ARC` as the first solution strategy.
Use `GUIDED_LOCAL_SEARCH` as the local search metaheuristic.
Set a time limit (e.g., 30 seconds) to keep it responsive.

For energy-aware optimization, register the energy model as a dimension
with a capacity constraint equal to the vehicle battery (kWh).

### 6. Google Maps URL Generation

For a closed route, origin and destination must be the same point (depot).
All intermediate stops are waypoints:

```python
origin = f"{depot['lat']},{depot['lng']}"
dest = origin  # same as origin for closed route
waypoints = [f"{loc['lat']},{loc['lng']}" for loc in stops[1:]]  # exclude depot
url = f"https://www.google.com/maps/dir/?api=1&origin={origin}&destination={dest}"
if waypoints:
    url += f"&waypoints={'|'.join(waypoints)}"
```

Google Maps has a 25-waypoint limit per URL. For longer routes, split into
multiple URLs or warn the user.

---

## Energy Model

Validated Mercedes eActros regression model (R² = 0.474).
Source: real-world study with 5,431 data points.

```python
def energy_per_km(speed_kmh, temp_c, weight_tonnes, altitude_diff_m, hvac: bool) -> float:
    """Returns kWh/km"""
    m1, k1 = 5.1304, 0.104   # speed exponential
    m2 = -0.0132              # temperature (kWh/km per °C)
    m3 =  0.0183              # weight (kWh/km per tonne)
    m4 =  0.0015              # altitude (kWh/km per meter gain)
    m5 =  0.7091              # constant
    hvac_overhead = 0.092     # kWh/km if HVAC active

    result = m1 * exp(-k1 * speed_kmh) + m2 * temp_c + m3 * weight_tonnes + m4 * altitude_diff_m + m5
    if hvac:
        result += hvac_overhead
    return result
```

Energy parameters are passed per-request (not hardcoded):
- `temperature_c`: ambient temperature
- `vehicle_weight_tonnes`: curb weight + cargo
- `has_hvac`: bool

Speed for each leg is estimated from `distance / time` from the Distance Matrix API.
Elevation difference per leg comes from Google Elevation API.

---

## Google Maps API Integration

Three APIs are required:
1. **Geocoding API** — address → lat/lng
2. **Distance Matrix API** — n×n matrix of distances and travel times
3. **Elevation API** — elevation at each location for energy calculation

Distance Matrix API limits: max 25 origins, 25 destinations, 100 elements per request.
Batch requests in 10×10 chunks to stay within limits.

All API calls should raise a clear exception on non-OK status — do not silently
return partial results.

---

## Flask API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | Health check, confirms API key is set |
| POST | `/api/geocode` | Geocode a single address |
| POST | `/api/geocode_batch` | Geocode multiple addresses |
| POST | `/api/optimize` | Run both solvers, return comparison |

### POST /api/optimize request body:
```json
{
  "addresses": ["depot address", "stop 1", "stop 2", ...],
  "num_vehicles": 3,
  "mode": "distance",
  "energy_params": {
    "temperature_c": 20,
    "vehicle_weight_tonnes": 7.0,
    "has_hvac": true
  }
}
```

### POST /api/optimize response:
```json
{
  "status": "ok",
  "locations": [...],
  "held_karp": { ...solver output schema... },
  "ortools": { ...solver output schema... },
  "comparison": {
    "distance_saved_m": 1200,
    "distance_saved_pct": 5.2,
    "time_saved_s": 480,
    "energy_saved_kwh": 2.1
  }
}
```

---

## CLI Interface

```bash
# From file (first line is depot)
python3 cli.py -f stops.txt

# Inline addresses (first is depot)
python3 cli.py -a "1400 S Douglass Rd, Anaheim, CA" "stop 1" "stop 2"

# Specify number of vehicles (default 1)
python3 cli.py -f stops.txt --vehicles 3

# Optimize for time instead of distance
python3 cli.py -f stops.txt -m time

# JSON output
python3 cli.py -f stops.txt --json
```

Output always shows:
1. Geocoded addresses
2. Held-Karp result (single vehicle, optimal baseline)
3. OR-Tools result (multi-vehicle fleet solution)
4. Comparison table
5. Google Maps URLs for each vehicle's route

---

## Conventions

- Use type hints throughout
- Functions do one thing — separate geocoding, matrix building, solving, formatting
- No global mutable state (API key loaded once at startup, passed as parameter or
  read from env — not mutated at runtime)
- Raise exceptions with clear messages on API errors — never return None silently
- All distances in meters (int), all times in seconds (int), all energy in kWh (float)
- Convert to miles/minutes only in formatting functions, never in core logic

---

## What NOT to Do

- Do not solve open TSP and add the return leg afterward — this is wrong for
  closed TSP and was a bug in the previous codebase
- Do not use `python` in commands — always `python3`
- Do not hardcode the API key anywhere
- Do not run OR-Tools without a time limit — it will hang on large inputs
- Do not mix distance units in core logic (keep everything in meters/seconds)
- Do not add features beyond what is listed here without confirming first
