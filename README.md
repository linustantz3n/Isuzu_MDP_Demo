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
| `/api/chat` | POST | Chat assistant – fills the form one field at a time, recommends solvers (see below) |
| `/api/memory` | GET/DELETE | Saved vehicle profiles (the chat writes them; see below) |

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

### 3. AI Chat Assistant (`/api/chat` + `AIChatbot.jsx`)

A form-filling assistant that turns an operator's sentence into **one validated change at a time**. The model only proposes; plain Python decides.

```
operator utterance
  ├─ 1. lexicon fast path      known phrasings ("temperature 30 C", "6 packs", "开空调") → no model call
  │      miss ↓
  ├─ 2. constrained extraction  GPT-4o-mini, strict JSON schema, four intents: apply / ask / confirm / out_of_scope
  └─ 3. deterministic validation
         • schema + null coupling   exactly one typed value slot, matching the axis; ranges enforced
         • evidence span            the quoted `evidence` must literally appear in the utterance (anti-hallucination)
         • ambiguity flag / term    model flag OR bare place name with no state/ZIP → forces a question
              ├─ apply change       one step, one axis; extra changes are queued and released per "yes"
              ├─ ask a question     `pending` is echoed back by the client, so the session remembers it
              ├─ recommend solver   which algorithm fits this problem size, and why (see below)
              ├─ memory             save / load / forget a named vehicle (see below)
              └─ out of scope       replies with the fixed capability list from code, not the model
```

#### Solver recommendation

The assistant can advise which of the five solvers fits the operator's problem, and switch to it.
Everything it claims — runtimes, limits, the pick itself — is computed by `recommend_solver()` /
`solver_table()`, never generated by the model, so it cannot invent a number.

Held-Karp is O(n²·2ⁿ). Measured on one machine (matrix size n = stops + depot): 118 ms at n=15,
621 ms at n=17, 3222 ms at n=19 — a near-constant ~17 ns per unit of n²·2ⁿ, which `HK_SEC_PER_UNIT`
encodes. That lets the server **price the solve before running it**:

| Stops | Held-Karp estimate | `auto` runs it? |
|---|---|---|
| 8 | <1 ms | yes |
| 14 | 125 ms | yes |
| 17 | 1.4 s | yes |
| 18 | 3.2 s | no → 2-opt |
| 22 | 75 s | no → 2-opt |
| 25 | 13 min | no → 2-opt |

Three question shapes, each answered deterministically:

```
"which algorithm should I use?"        → recommendation + comparison table + offer to switch
"why not always use the optimal one?"  → the exponential-vs-O(n²) tradeoff, priced at your size
"what solver am I using now?"          → current setting and what it resolves to
```

Bare solver questions (no numbers) hit the lexicon and cost no model call. A hypothetical
("if I had 22 stops and 3 trucks") goes to the model, which only extracts the numbers — with an
evidence span — while the advice itself is still computed.

#### Fleet memory

Vehicles can be saved by name and loaded back in a later session. A profile holds
`battery_packs` and `weight_lbs` — real inputs to the energy model and the range of the
route — plus `driver` and `plate` as identification that rides along.

```
"Truck A is 6 packs, 15000 lbs, driver Wang, plate 8ABC123"
   -> Saved 1 vehicle: Truck A — 6 packs (120 kWh) · 15,000 lbs · driver Wang · 8ABC123

   ... later session ...

"use Truck A and Truck C today"
   -> Loaded 2 vehicles into the form   (sets the `fleet` axis: per-vehicle packs and weight)
```

Four operations, all through the same chat endpoint: **remember** (save or update),
**recall** (load into the form), **forget** (delete), **list** (show what is stored).

The gates that protect form changes protect profiles too: `battery_packs` and `weight_lbs`
go through the same range checks (3–9, 10,000–19,500), and the model must quote the words
it read the profile out of, so it cannot invent a driver or a plate. An update merges, so
"Truck A has 8 packs now" keeps the stored weight, driver and plate. A new profile needs at
least one real spec — a name and a plate alone store nothing the optimiser can use.

Naming a saved vehicle also disables the lexicon fast path: "Truck A has 8 packs now" must
update the *profile*, while "set battery packs to 8" changes the *form*, and only the model
can tell those apart.

Storage is one JSON file, `fleet_memory.json`, next to the server — inspectable, and easy to
show to someone who asks where it actually lives. `GET /api/memory` lists it;
`DELETE /api/memory?name=Truck%20A` removes one, no `name` clears all.

**`/api/optimize` accepts `solver`**: `auto` (default) | `held_karp` | `2opt` | `nearest_neighbor` |
`ortools_vrp`. The cheap heuristics always run so the comparison panel keeps working; the setting
decides whether the exponential one is worth paying for. The response carries a `solver` object
(`baseline_label`, `baseline_exact`, `note`) and `results.optimal` is always present — when
Held-Karp is priced out, the chosen heuristic stands in and is marked `substituted_for`.
Tuning knobs: `HK_TIME_BUDGET_MS` (2 s) and `HK_HARD_LIMIT_N` (22).

**Request** `POST /api/chat`
```json
{ "message": "Use 2 trucks with 6 packs",
  "history": [{"role": "user", "content": "..."}, ...],
  "form_state": {"depot": "...", "addresses": [], "vehicle_count": 1, "battery_packs": 4,
                 "vehicle_weight_lbs": 13000, "temperature_c": 20, "unload_time_min": 30, "has_climate_control": true},
  "pending": null }
```

**Response**
```json
{ "status": "ok", "intent": "apply", "source": "model",
  "message": "Applied: Vehicles → 2. Also apply: Battery packs → 6 packs? (yes / no)",
  "changes": [{"axis": "vehicle_count", "op": "set", "value": 2, "evidence": "2 trucks", "label": "Vehicles → 2"}],
  "pending": {"question": "Also apply: Battery packs → 6 packs? (yes / no)", "axis": null, "queued": [...]},
  "capabilities": null, "rejected": [] }
```

`intent` is one of `apply` / `ask` / `out_of_scope`; `source` is `lexicon`, `model` or `session`. Axes and limits live in `AXES` in `route_optimizer_backend.py`; the capability list in `CAPABILITIES`. Set `ONE_CHANGE_PER_TURN = False` to apply every validated change in a single turn.

Requires `OPENAI_API_KEY` in `.env`. Without it the lexicon fast path still works; everything else returns *out of scope* with the capability list.

### 4. React Dashboard (`route_optimizer.jsx`)

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
- Python packages: `requests`, `flask`, `flask-cors`, `python-dotenv`, `ortools`
- OpenAI API key (`OPENAI_API_KEY`) for the chat assistant's extraction step

---

## Limitations

- **Stop limit:** Optimal algorithm practical up to ~20 stops
- **No real-time traffic:** Uses average travel times (can add `departure_time` for traffic)
- **Open TSP:** Routes from first stop to last (no return to depot option yet)
- **Single vehicle:** Does not handle fleet assignment (VRP)

These limitations are intentional - this is a **baseline**. Your project extends beyond these constraints.
# Isuzu_MDP_Demo
