# Route Optimization Baseline Tool

**Isuzu BEV Fleet Routing Project - Baseline Comparison System**

This toolkit provides a baseline route optimization system to compare against more advanced routing approaches (energy-aware, constraint-based, ML-enhanced) that you'll develop for the Isuzu project.

## What This Does

1. **Takes delivery stops as input** (addresses)
2. **Geocodes addresses** via Google Maps API
3. **Builds a complete distance/time matrix** between all stops
4. **Solves the Traveling Salesman Problem (TSP)** to find the optimal route ordering
5. **Compares algorithms** - optimal (Held-Karp) vs greedy (nearest neighbor, like Google Maps)
6. **Outputs metrics** for baseline comparison

## Why This Matters

Google Maps' built-in "optimize waypoints" uses a **greedy nearest-neighbor heuristic** - it's fast but not guaranteed optimal. For your BEV fleet project, you need:

- A true optimal baseline to measure improvements against
- Understanding of how much simple optimization can gain over greedy approaches
- A foundation to extend with energy consumption, charging constraints, time windows, etc.

---

## Quick Start

### 1. Set Your API Key

```bash
export GOOGLE_MAPS_API_KEY="your-api-key-here"
```

### 2. Install Dependencies

```bash
pip install requests flask flask-cors
```

### 3. Run the CLI Tool

```bash
# Basic usage
python route_optimizer_cli.py -a \
    "2281 Bonisteel Blvd, Ann Arbor, MI" \
    "500 S State St, Ann Arbor, MI" \
    "530 S State St, Ann Arbor, MI" \
    "1109 Geddes Ave, Ann Arbor, MI"

# From a file (one address per line)
python route_optimizer_cli.py -f my_stops.txt

# Optimize for time instead of distance
python route_optimizer_cli.py -f stops.txt -m time

# Interactive mode
python route_optimizer_cli.py -i

# JSON output for programmatic use
python route_optimizer_cli.py -a "Addr1" "Addr2" "Addr3" -j
```

---

## Components

### 1. CLI Tool (`route_optimizer_cli.py`)

Standalone command-line tool for quick route optimization. No server needed.

**Features:**
- Geocode addresses
- Build distance/time matrix
- Solve TSP optimally (Held-Karp algorithm)
- Compare against greedy approach
- Generate Google Maps link for the route

**Output:**
```
============================================================
OPTIMAL ROUTE:
------------------------------------------------------------
  1. 2281 Bonisteel Blvd, Ann Arbor, MI 48109
     ↓ 1.2 mi · 4 min
  2. 1109 Geddes Ave, Ann Arbor, MI 48109
     ↓ 0.8 mi · 3 min
  3. 530 S State St, Ann Arbor, MI 48109
     ↓ 0.1 mi · 1 min
  4. 500 S State St, Ann Arbor, MI 48109

  Total Distance: 2.1 mi
  Total Time:     8 min

============================================================
COMPARISON:
------------------------------------------------------------
Algorithm                  Distance        Time
---------------------------------------------------------
Optimal (Held-Karp)        2.1 mi          8 min
Greedy (Nearest Neighbor)  2.8 mi          11 min

Optimal saves 0.7 mi (25.0%) vs greedy approach
```

### 2. Backend Server (`route_optimizer_backend.py`)

Flask-based API server for integration with frontends or other services.

**Start the server:**
```bash
python route_optimizer_backend.py
```

**API Endpoints:**

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/health` | GET | Health check |
| `/api/set_key` | POST | Set Google Maps API key |
| `/api/geocode` | POST | Geocode single address |
| `/api/geocode_batch` | POST | Geocode multiple addresses |
| `/api/optimize` | POST | **Main endpoint** - optimize route |
| `/api/directions` | POST | Get turn-by-turn directions |
| `/api/replan_incidents` | POST | Find historical crash hotspots on the route and reroute around every on/off combination of them (demo) |
| `/api/plan_charging` | POST | Find a charging stop for fleet vehicles that would end below the 20% SoC reserve |

**Incident replanning demo.** Accidents are placed where fatal crashes actually
happened along the route, using NHTSA FARS data (2019–2023, all US fatal crashes,
public domain) in `data/crash_history.csv.gz`. Crashes within 150 m of each leg's
road are grouped into 1 km stretches; the stretches with the most crashes become
the accidents (default 3, spread over different legs, none next to a stop). Every
leg that passes an accident is rerouted via a Google alternative route or a forced
detour. To refresh the data, download newer `FARS<YEAR>NationalCSV.zip` files and
run `python scripts/build_crash_history.py FARS*.zip`.

**Example - Optimize Route:**
```bash
curl -X POST http://localhost:5000/api/optimize \
  -H "Content-Type: application/json" \
  -d '{
    "locations": [
      {"address": "Ann Arbor, MI", "lat": 42.2808, "lng": -83.7430},
      {"address": "Detroit, MI", "lat": 42.3314, "lng": -83.0458},
      {"address": "Ypsilanti, MI", "lat": 42.2411, "lng": -83.6130}
    ],
    "mode": "distance"
  }'
```

### 3. React Dashboard (`route_optimizer.jsx`)

Web-based UI for interactive route planning.

**Features:**
- Add/remove stops with address input
- Validate addresses (geocoding)
- Choose distance vs time optimization
- View optimized route with leg-by-leg breakdown
- Compare optimal vs greedy algorithms
- Open result in Google Maps

---

## Algorithms Included

### 1. Held-Karp (Optimal)

Dynamic programming algorithm that guarantees the optimal solution.

- **Time complexity:** O(n² × 2ⁿ)
- **Space complexity:** O(n × 2ⁿ)
- **Practical limit:** ~20 stops

For your fleet project, this gives you the **theoretical best** route to compare against.

### 2. Nearest Neighbor (Greedy)

Simple heuristic that always picks the closest unvisited stop.

- **Time complexity:** O(n²)
- **Practical limit:** Any size

This approximates what Google Maps does with `optimize:true`. It's fast but typically 10-25% worse than optimal.

### 3. 2-opt Improvement

Local search that improves an initial solution by reversing segments.

- Starts from nearest neighbor solution
- Iteratively improves by swapping edges
- Usually gets within 2-5% of optimal

---

## Traffic Data (optional)

`/api/optimize` can take travel times from a traffic data package instead of Google: four days of 5-minute speeds (measured and 2-hour forecasts) on the major roads of a 15 x 14 km area around Cerritos and Stanton. `traffic_data.py` follows the package's travel-time rules (corridor speed in the interval the edge is entered, plus intersection length and traffic-light delays) and reproduces its `example.py` result.

- **Setup:** unzip the package to `poc_route_planning_v1/` in the repo root (or set `POC_TRAFFIC_DIR`). The data is licensed for this project only and is git-ignored, so never commit it.
- **Request:** add `"traffic_source": "forecast" | "historical" | "ground_truth"` and `"departure_time": "2025-11-12T07:30"` (days: 2025-11-12, 11-18, 11-22, 11-23). `historical` uses the ground truth of a comparable other day (Wed↔Tue, Sat↔Sun) at the same times.
- **Plan vs reality:** the Held-Karp result gets `traffic.actual_travel_time_s`, which is the planned route driven on the measured speeds with the clock running (unload time at each stop).
- **Dashboard:** choose the source under *Travel Times* and load the *Demo 0* stops (all stops must be inside the area).
- **Simplifications:** every pair in the matrix is timed as if it started at the departure time; freeway speeds are capped at 88 km/h (the data has car speeds); stops snap to the nearest non-freeway road, so the short stretch from the address to that road is not counted.

---

## Incident Replanning (demo)

Routes are planned ahead and trucks are loaded for that plan. On the day, the plan is rerun against live conditions (here: simulated accidents at historical crash hotspots), but because the trucks are already loaded, the stop order is kept.

**How the plan is preserved.** `/api/replan_incidents` takes the planned legs (Held-Karp order, or with several vehicles every OR-Tools route's legs tagged with `vehicle_id`) and only replaces the road between the two stops an accident sits on (stop i → stop i+1). With a fleet, hotspots are spread over the vehicles first, and the time shown is the makespan (last vehicle back). Stops are never added, removed or reordered. The dashboard shows the unchanged stop sequence and the whole-route distance, time and energy before and after. `tests/test_replanning.py` checks this offline (no API key needed):

```bash
python -m unittest discover tests
```

**Two replanning cases.**

| Case | Example | Can the stop order change? | Status |
|------|---------|----------------------------|--------|
| Interchangeable loads | Every stop gets the same product (e.g. only Coke syrup) | Yes: the solver may resequence stops after an incident | Not implemented |
| Fixed loads | Each stop has its own goods (e.g. Ulta, then Costco), loaded in delivery order | No: only the roads between stops may change | Implemented (single vehicle and fleet) |

**Charging stops (20% SoC reserve).** Each vehicle must stay above 20% of its battery and be back at the depot with at least 20%. Fleet vehicles that would end below that are flagged, and **Find Charging Stops** calls `/api/plan_charging`: it locates where the battery would hit 20% (energy spread evenly over each leg), searches DC fast chargers (CCS1, 50 kW+) along the legs up to that point with Google Places Text Search (search along route), and inserts the charger that adds the least time (detour + charging) into its leg. The vehicle charges just enough to finish with the reserve; stops are not reordered. Charging time assumes the truck accepts at most 50 kW (`VEHICLE_MAX_CHARGE_KW`, to be confirmed against the Isuzu spec) and a flat charging curve. Routes that need more than one stop are reported, not planned. The solver itself does not enforce the reserve yet. Needs the Places API (New) enabled for the Google key; `tests/test_charging.py` checks the SoC rules offline.

**Energy in replanning: deferred.** Energy is calculated for the original and rerouted legs and shown for comparison, but it does not decide which detour is taken: the reroute is the fastest route that avoids the accident. Making replanning energy-aware is future work. The open design question is whether energy should be part of the objective (e.g. minimize time + λ·kWh, which needs an agreed trade-off λ) or a constraint (reject detours that would drain the battery below a reserve, which needs the battery state at that point of the route).

---

## Extending for BEV Fleet

This baseline optimizes only for distance/time. Your Isuzu project will extend this with:

### Energy-Aware Routing

```python
# Instead of just distance, your cost function becomes:
def energy_cost(from_loc, to_loc, vehicle_state):
    base_energy = distance * efficiency  # kWh/mi
    elevation_factor = compute_elevation_impact(from_loc, to_loc)
    load_factor = vehicle_state.cargo_weight / max_capacity
    weather_factor = get_weather_impact(conditions)
    
    return base_energy * elevation_factor * load_factor * weather_factor
```

### Charging Constraints

```python
# Add charging stations as optional waypoints
# Vehicle can only travel if: current_charge - trip_energy > min_reserve
def is_feasible(route, vehicle):
    charge = vehicle.current_charge
    for leg in route.legs:
        charge -= leg.energy_consumption
        if charge < vehicle.min_reserve:
            return False
        if leg.end.is_charger:
            charge = min(charge + leg.charge_amount, vehicle.max_charge)
    return True
```

### Time Windows

```python
# Each delivery has [earliest, latest] arrival times
def satisfies_time_windows(route, schedule):
    current_time = schedule.start_time
    for stop in route.stops:
        arrival = current_time + stop.travel_time
        if arrival > stop.latest_arrival:
            return False
        departure = max(arrival, stop.earliest_arrival) + stop.service_time
        current_time = departure
    return True
```

### Multi-Vehicle Fleet

```python
# Assign deliveries to vehicles (Vehicle Routing Problem)
# Consider: vehicle capacity, range, current location, driver hours
def assign_vehicles(deliveries, fleet):
    # This becomes a VRP, not just TSP
    # Use OR-Tools, genetic algorithms, or constraint programming
    pass
```

---

## Baseline Metrics to Track

When comparing your advanced routing against this baseline:

| Metric | Baseline (This Tool) | Your System |
|--------|---------------------|-------------|
| Total Distance | ✓ | ✓ |
| Total Time | ✓ | ✓ |
| Energy Consumption | ✗ | ✓ |
| Charging Stops | ✗ | ✓ |
| Time Window Violations | ✗ | ✓ |
| On-Time Delivery % | ✗ | ✓ |
| Battery End State | ✗ | ✓ |

---

## Algorithm Benchmark

The **Benchmark** tab (and `benchmark/` package) answers "how much better is OR-Tools than naive routing, and on which dimensions?" It runs every registered algorithm on the same saved scenario for each fleet size K = 1..N and scores them identically from the scenario's distance/time matrices.

| Algorithm | K | Notes |
|---|---|---|
| Nearest Neighbor, 2-opt, Held-Karp (optimal) | 1 | Single-truck TSP; Held-Karp is the exact reference |
| Round-robin + NN / 2-opt | ≥2 | Naive dispatcher: deal stops out in list order |
| K-means + 2-opt | ≥2 | Capacity-balanced clusters on lat/lng |
| Sweep + 2-opt | any | Gillett-Miller radial sectors around the depot |
| Clarke-Wright Savings | any | Classic savings merge; flagged infeasible if it needs > K trucks |
| OR-Tools (construction only) | any | PATH_CHEAPEST_ARC, no local search |
| OR-Tools (production) | any | PATH_CHEAPEST_ARC + Guided Local Search |

Every truck is capped at `ceil(stops / K)` stops for every algorithm, matching OR-Tools. Metrics: total distance, total drive time, makespan, trucks used, solve time, and gaps vs the best result, vs production OR-Tools, and (K=1) vs the Held-Karp optimum.

```bash
# Scenarios are snapshotted once (Google API calls), then reused offline
python -m benchmark.snapshot route-optimizer-frontend/public/demo_stops.txt demo_anaheim "Anaheim demo"

# Headless run / CSV export
python -m benchmark.cli --list
python -m benchmark.cli demo_anaheim --max-vehicles 3 --csv results.csv

# Tests
pytest tests/
```

To benchmark a new algorithm (e.g. a BEV-aware router), write `solve(problem) -> list of [0, ..., 0] routes` and register it in `benchmark/algorithms.py` (`ALGORITHMS`).

---

## Files

```
├── route_optimizer_cli.py      # Standalone CLI tool
├── route_optimizer_backend.py  # Flask API server
├── route_optimizer.jsx         # React dashboard component
├── google_maps_api_guide.md    # API reference guide
├── data/crash_history.csv.gz   # Historical fatal crashes (NHTSA FARS) for incident replanning
├── scripts/build_crash_history.py  # Builds the crash data from FARS downloads
└── README.md                   # This file
```

---

## Requirements

- Python 3.8+
- Google Maps API key with:
  - Geocoding API
  - Routes API (replaces the legacy Distance Matrix and Directions APIs)
  - Elevation API (used by the energy model)
- Python packages: `requests`, `flask`, `flask-cors`

---

## Limitations

- **Stop limit:** Optimal algorithm practical up to ~20 stops
- **No real-time traffic:** Uses average travel times (can add `departure_time` for traffic)
- **Open TSP:** Routes from first stop to last (no return to depot option yet)
- **Single vehicle:** Does not handle fleet assignment (VRP)

These limitations are intentional - this is a **baseline**. Your project extends beyond these constraints.
# Isuzu_MDP_Demo
