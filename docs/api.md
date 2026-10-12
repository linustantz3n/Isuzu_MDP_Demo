# Backend API

Everything is served by [`route_optimizer_backend.py`](../route_optimizer_backend.py) (Flask) on
**http://localhost:5001**. The React app reaches it through `API_BASE` in
[`route-optimizer-frontend/src/api.js`](../route-optimizer-frontend/src/api.js), which is hard-coded
to `http://localhost:5001/api`.

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/health` | GET | `{status, api_key_set}` |
| `/api/set_key` | POST | `{api_key}`. Sets the Google key at runtime instead of using `.env` |
| `/api/geocode` | POST | `{address}` → `{result: {address, formatted_address, lat, lng}}` |
| `/api/geocode_batch` | POST | `{addresses: [...]}` → `{results: [...], errors: [...]}`; `status` is `partial` if any failed |
| **`/api/optimize`** | POST | **The main endpoint.** Solve the routes and estimate energy (below) |
| `/api/directions` | POST | `{locations, order}` → raw Google Directions response |
| `/api/chat` | POST | Chat assistant. See [chat-assistant.md](chat-assistant.md) |
| `/api/memory` | GET / DELETE | Saved driver profiles. See [chat-assistant.md](chat-assistant.md#driver-memory) |
| `/api/benchmark/scenarios` | GET | Saved benchmark scenarios |
| `/api/benchmark/algorithms` | GET | Registered benchmark algorithms |
| `/api/benchmark/run` | POST | `{scenario_id, max_vehicles=3, algorithms?, ortools_time_limit_s?}` → per-K, per-algorithm metrics |

Every endpoint returns `"status": "ok"` on success, or `"status": "error"` with a `message`.

---

## `POST /api/optimize`

**Code:** [`optimize_route()`](../route_optimizer_backend.py#L681).

The steps:

1. Fetch the distance and time matrices from Google, in 10×10 batches.
2. Fetch the elevation of every location.
3. Pick which solvers run.
4. Solve.
5. For every route, compute distance, time and per-leg energy.
6. If there is more than one vehicle, also solve with OR-Tools and compute per-vehicle energy and battery.

### Request

```json
{
  "locations": [
    {"address": "1400 S Douglass Rd, Anaheim, CA", "lat": 33.8113, "lng": -117.8770, "formatted_address": "..."},
    {"address": "11232 Knott Ave, Stanton, CA",    "lat": 33.8040, "lng": -118.0026, "formatted_address": "..."}
  ],
  "mode": "distance",
  "return_to_start": true,
  "solver": "auto",
  "vehicles": [
    {"id": 1, "name": "Vehicle 1", "weight_lbs": 13000, "battery_packs": 4, "battery_kwh": 80, "driver": "Wang"},
    {"id": 2, "name": "Vehicle 2", "weight_lbs": 15000, "battery_packs": 6, "battery_kwh": 120}
  ],
  "energy_params": {
    "temperature_c": 20,
    "unload_time_min": 30,
    "has_climate_control": true
  }
}
```

| Field | Default | Meaning |
|---|---|---|
| `locations` | required | 2–25 geocoded points. **Index 0 is the depot** (start). Each needs `lat` and `lng`; `address` and `formatted_address` are passed through to the output |
| `mode` | `"distance"` | Which matrix the solvers minimize: `"distance"` (metres) or `"time"` (seconds). The UI always sends `"distance"` |
| `return_to_start` | `false` | Closed tour back to the depot. Forced to `true` when there is more than one vehicle. The UI always sends `true` |
| `solver` | `"auto"` | `auto`, `held_karp`, `2opt`, `nearest_neighbor` or `ortools_vrp`. Decides whether Held-Karp runs (see [solvers.md](solvers.md#how-apioptimize-decides-what-to-run)) |
| `vehicles` | `num_vehicles` default vehicles | One entry per truck. Its length is the fleet size K. `weight_lbs` is the gross weight leaving the depot (truck, batteries and cargo). `battery_packs` sets the empty-truck weight. `battery_kwh` is the capacity used in the fleet battery check. `name` and `driver` are passed through to the fleet results. Older clients may send `weight_tonnes` instead of `weight_lbs` |
| `num_vehicles` | 1 | Older form of the fleet size. Ignored when `vehicles` is sent |
| `energy_params.temperature_c` | 20 | Ambient °C, used for every leg |
| `energy_params.unload_time_min` | 30 | Minutes per delivery stop. Adds to time, not energy |
| `energy_params.has_climate_control` | `true` | Adds 0.092 kWh/km |
| `energy_params.total_weight_lbs`, `base_weight_lbs` | 19,500 / 7,000 | Effectively unused (see [energy-model.md](energy-model.md#other-inputs-and-what-they-affect)) |
| `algorithms` | (from `solver`) | Explicit list to run (`held_karp` / `optimal`, `nearest_neighbor` / `greedy`, `2opt`). Overrides `solver` |

What the vehicle defaults mean: 13,000 lbs, 4 packs and 80 kWh. One pack is 20 kWh and 200 kg.
The empty truck weighs 7,000 lbs at 4 packs. The details are in [energy-model.md](energy-model.md#weight-model).

### Response

```
{
  "status": "ok",
  "mode": "distance",
  "num_locations": 6,
  "results": {
    "optimal":  <route>,      Held-Karp, or the 2-opt/NN stand-in (then "substituted_for": "held_karp")
    "greedy":   <route>,      nearest neighbor
    "2opt":     <route>,
    "ortools_fleet": <fleet>  only when there is more than one vehicle
  },
  "comparison": {...},        greedy vs optimal savings
  "solver":     {...},        what ran and why
  "energy_params": {...},     the conditions used
  "matrices": {"distance": [[m]], "time": [[s]]}
}
```

**`<route>`**: a single-vehicle route over all stops, using vehicle 1's weight and packs.

| Field | Meaning |
|---|---|
| `order` | Indices into `locations`, starting at 0 |
| `cost` | The solver's objective, in matrix units (m or s) |
| `algorithm`, `solve_time_ms` | Which solver ran and how long it took |
| `ordered_locations` | `locations` in visiting order |
| `total_distance_m`, `total_distance_mi` | Including the return to the depot |
| `travel_time_s` | Driving time |
| `unload_time_s` | `unload_time_min × number of stops` |
| `total_time_s`, `total_time_min` | Driving time plus unloading time |
| `total_energy_kwh`, `avg_energy_per_km`, `avg_energy_per_mile` | Sum over the legs |
| `weight_params` | `{total_weight_lbs, base_weight_lbs, cargo_weight_lbs, weight_per_stop_lbs, num_delivery_stops}` |
| `legs[]` | One per leg (below); the last one has `return_leg: true` |
| `google_maps_url` | Opens the route in Google Maps |
| `return_to_start` | Echo of the request |

**`legs[]`** (single-vehicle routes):

| Field | Meaning |
|---|---|
| `from`, `to` | Location objects |
| `distance_m`, `distance_mi`, `distance_km` | Leg distance |
| `time_s`, `time_min` | Leg driving time |
| `avg_speed_kmh`, `avg_speed_mph` | The `v` used in the energy model |
| `elevation_from_m`, `elevation_to_m`, `elevation_gain_m` | `h` and `Δh` in the energy model |
| `vehicle_weight_lbs`, `vehicle_weight_tonnes` | `W` for this leg |
| `energy_kwh`, `energy_per_km`, `energy_per_mile` | The leg's energy |
| `energy_breakdown` | kWh per regression term (see [energy-model.md](energy-model.md#per-term-output)) |
| `return_leg` | `true` on the leg back to the depot |

**`<fleet>`** (`results.ortools_fleet`):

| Field | Meaning |
|---|---|
| `num_vehicles_requested`, `num_vehicles_used` | Empty trucks are dropped |
| `routes[]` | One per truck that has stops (below) |
| `total_distance_m`, `total_distance_mi` | All trucks combined |
| `makespan_s`, `makespan_min` | When the last truck finishes (driving + unloading); trucks leave together |
| `travel_makespan_s` | The longest driving time of any one truck |
| `total_travel_time_s`, `total_unload_time_s`, `total_driver_time_s` | Sums over trucks. Driver time is a proxy for labour cost |
| `total_energy_kwh`, `avg_energy_per_mile` | All trucks combined |
| `solve_time_ms` | OR-Tools solve time |

**`routes[]`**: `vehicle_id`, `vehicle_name`, `driver`, `vehicle_config` `{total_weight_lbs, cargo_weight_lbs, weight_per_stop_lbs, battery_kwh}`,
`ordered_locations` (depot at both ends), `total_distance_m/_mi`, `travel_time_s`, `unload_time_s`, `total_time_s/_min`,
`num_delivery_stops`, `total_energy_kwh`, `battery_capacity_kwh`, `battery_remaining_kwh`, `battery_remaining_pct`,
`battery_depleted`, `google_maps_url`, and `legs[]`. Fleet legs are a lighter version of the single-vehicle legs:
`from`, `to`, `distance_m/_mi`, `time_s/_min`, `vehicle_weight_lbs/_tonnes`, `energy_kwh`, `return_leg`.

**`comparison`**: present when both `optimal` and `greedy` ran. Contains `distance_saved_m/_mi/_percent`,
`time_saved_s/_min/_percent` and `energy_saved_kwh/_percent`, each computed as greedy minus optimal.

**`solver`**:

| Field | Meaning |
|---|---|
| `requested` | The setting that was sent |
| `effective` | What it resolved to |
| `algorithms` | The TSP solvers that ran |
| `fallback_baseline` | The solver used as a stand-in if Held-Karp did not run |
| `held_karp_estimate_ms` | Predicted Held-Karp runtime |
| `note` | Why Held-Karp was skipped, if it was |
| `baseline_solver`, `baseline_label`, `baseline_exact` | Which solver's route `results.optimal` holds, and whether it is the proven optimum |

**`energy_params`**: `temperature_c`, `temperature_f`, `vehicle_weight_tonnes`, `vehicle_weight_lbs` (vehicle 1), `has_climate_control`, `model`.

### Example

```bash
curl -s -X POST http://localhost:5001/api/optimize -H "Content-Type: application/json" -d '{
  "locations": [
    {"address": "Ann Arbor, MI",  "lat": 42.2808, "lng": -83.7430},
    {"address": "Detroit, MI",    "lat": 42.3314, "lng": -83.0458},
    {"address": "Ypsilanti, MI",  "lat": 42.2411, "lng": -83.6130}
  ],
  "return_to_start": true,
  "vehicles": [{"id": 1, "weight_lbs": 15000, "battery_packs": 6, "battery_kwh": 120}],
  "energy_params": {"temperature_c": 5, "has_climate_control": true}
}'
```

This needs `GOOGLE_MAPS_API_KEY` in `.env`. Each call makes `ceil(n/10)²` Distance Matrix
requests and one Elevation request, where n is the number of locations. Geocoding happens
earlier, in the UI's "Validate Addresses" step.
