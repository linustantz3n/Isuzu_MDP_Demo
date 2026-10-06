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
└── README.md                   # This file
```

---

## Requirements

- Python 3.8+
- Google Maps API key with:
  - Geocoding API
  - Distance Matrix API
  - Directions API
- Python packages: `requests`, `flask`, `flask-cors`

---

## Limitations

- **Stop limit:** Optimal algorithm practical up to ~20 stops
- **No real-time traffic:** Uses average travel times (can add `departure_time` for traffic)
- **Open TSP:** Routes from first stop to last (no return to depot option yet)
- **Single vehicle:** Does not handle fleet assignment (VRP)

These limitations are intentional - this is a **baseline**. Your project extends beyond these constraints.
# Isuzu_MDP_Demo
