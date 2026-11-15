# Isuzu BEV Fleet Route Optimizer

Route planning for battery-electric Isuzu delivery trucks. You give it a depot, delivery stops
and the truck setup. It orders the stops for one truck using Held-Karp (exact), 2-opt or nearest
neighbor. With more than one truck, it also splits the stops across the fleet with Google
OR-Tools. It then estimates each route's energy use with a fitted regression model ("Model E")
and checks the result against each truck's battery.

Operators tune it in plain language. They tell the chat assistant what the day looks like
("battery is tight today", "we're running late", "truck 2 broke down after its first stop").
The assistant picks the optimization goals and their weights, explains what a truck can
handle, or re-plans around a breakdown. The plan is then re-solved, and the UI shows the
plan before and after side by side.

> **By default the solvers minimize driving distance; energy is calculated afterwards, on the
> finished route.** The [optimization goals](docs/planning.md#optimization-goals) can weight
> time, Model E energy and fleet balance instead, and a battery reserve (20% by default) makes each
> truck's battery a hard limit. See [docs/energy-model.md](docs/energy-model.md#how-energy-relates-to-the-solvers).

## Where to find things

| You want… | Go to |
|---|---|
| **The energy model**: formula, coefficients, inputs, weight and battery logic | [docs/energy-model.md](docs/energy-model.md) · code: [`energy.py`](energy.py) (`calculate_energy_consumption()`) |
| **The solvers** and how one gets picked | [docs/solvers.md](docs/solvers.md) · code: [`solvers.py`](solvers.py), [`resolve_solver()`](route_optimizer_backend.py#L605) |
| **What a solve takes as input and returns** | [docs/api.md](docs/api.md) · code: [`optimize_route()`](route_optimizer_backend.py#L1934) |
| **What every file and function does** | [docs/code-map.md](docs/code-map.md) |
| The chat assistant and driver memory | [docs/chat-assistant.md](docs/chat-assistant.md) |
| **Optimization goals (weights), battery reserve, priority stops, breakdown re-planning, truck limits** | [docs/planning.md](docs/planning.md) · code: [`objective_matrix()`](route_optimizer_backend.py#L907), [`solve_fleet()`](route_optimizer_backend.py#L1175), [`replan_breakdown()`](route_optimizer_backend.py#L1298) |
| The before / after comparison | [`PlanComparison.jsx`](route-optimizer-frontend/src/PlanComparison.jsx), [`planDiff.js`](route-optimizer-frontend/src/planDiff.js) |
| The algorithm benchmark and battery-drain simulation | [docs/solvers.md#benchmark](docs/solvers.md#benchmark), [benchmark/README.md](benchmark/README.md) · code: [`benchmark/`](benchmark/) |
| Traffic data package (measured / forecast speeds) | [Traffic data](#traffic-data-optional) below · code: [`traffic_data.py`](traffic_data.py) |
| Accident replanning at crash hotspots, charging stops | [Incident replanning](#incident-replanning-demo) below · code: `replan_route_around_hotspots()`, `plan_charging_stop()` |
| The web UI | [`route-optimizer-frontend/src/RouteOptimizer.jsx`](route-optimizer-frontend/src/RouteOptimizer.jsx) |

## How a request flows

```
Browser (React, :3000)                     Flask backend (:5001) — route_optimizer_backend.py
──────────────────────                     ─────────────────────────────────────────────────
RouteOptimizer.jsx
  Validate Addresses ── /api/geocode(_batch) ──► geocode_address()          Google Geocoding
  Optimize Routes ───── /api/optimize ─────────► optimize_route()
                                                   1. get_distance_matrix()    Google Routes API matrix (m, s) ┐ skipped when the client
                                                   2. get_elevation_data()     Google Elevation (m)            ┘ sends them back (re-solve)
                                                   3. resolve_solver()         which TSP solvers to run
                                                   4. objective_matrix()       goals → the cost the solvers minimise
                                                   5. solvers.py               Held-Karp / 2-opt / NN    (1 truck)
                                                      solve_fleet()            OR-Tools VRP              (2+ trucks)
                                                   6. for every leg:           calculate_energy_consumption()
                                                   7. JSON: routes, legs, time, energy, battery, Maps links
  result cards, RouteMap, FleetCharts ◄────────────┘
  PlanComparison  ◄── every solve is a plan version: before | after side by side
AIChatbot.jsx ───────── /api/chat ─────────────► lexicon → GPT-4o-mini → validation → one change, goals,
     │                                            truck limits, or a breakdown action
     └─ after the turn: re-solve (/api/optimize) or ── /api/replan ──► replan_breakdown()
  Find Crash Hotspots ─ /api/replan_incidents ─► replan_route_around_hotspots()   Routes API alternatives
  Drive simulation ──── /api/reroute_from ─────► reroute_from_position()
  Find Charging Stops ─ /api/plan_charging ────► plan_charging_stop()            Places API (New)
BenchmarkPanel.jsx ──── /api/benchmark/* ──────► benchmark/ (saved scenarios, no Google calls)
```

## Repository layout

```
route_optimizer_backend.py   Flask API: Google calls, solver selection, optimization goals, /api/optimize,
                             /api/replan (breakdowns), accident replanning, charging stops, chat assistant,
                             driver memory, benchmark endpoints
energy.py                    Model E energy model and battery-limit helpers (shared with benchmark/)
traffic_data.py              Travel times from the licensed traffic data package (optional)
data/crash_history.csv.gz    Historical fatal crashes (NHTSA FARS) for accident replanning
scripts/build_crash_history.py   Rebuilds the crash data from FARS downloads
solvers.py                   Solvers: brute force, Held-Karp, nearest neighbor, 2-opt, OR-Tools VRP
                             (plain, and constrained: battery, payload, pickups, optional stops),
                             mid-drive re-route of one truck (replan_route)
benchmark/                   Offline algorithm comparison and battery-drain simulation (scenarios, registry, scoring, CLI)
tests/                       pytest suite: solvers, planning, battery, charging, replanning, simulation, benchmark
route-optimizer-frontend/    React app: src/RouteOptimizer.jsx is the main page
docs/                        Energy model, solvers, planning (goals, breakdowns), API, code map, chat assistant
prompt_test.py, prompt_demo.py   Chat-assistant regression scripts (need a running server)
r4_test.py                   R4 test set: 50 bilingual weight / traffic / style requests
route_optimizer.jsx          Legacy prototype from the first commit; not used
```

## Running it

**Backend** (Python 3; the tests pass on 3.14):

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
# .env in the repo root:
#   GOOGLE_MAPS_API_KEY=...   Geocoding, Routes, Elevation, Time Zone and Places (New) APIs enabled
#   OPENAI_API_KEY=...        optional; only the chat assistant's model step uses it
#   or, for the U-M GPT Toolkit (any OpenAI-compatible endpoint works), instead of OPENAI_API_KEY:
#   LLM_BASE_URL=https://api.toolkit.umgpt.umich.edu/v1
#   LLM_API_KEY=...           your Toolkit key
#   LLM_MODEL=gpt-4o-mini     the default; the prompt tests were run with it
python route_optimizer_backend.py          # http://localhost:5001
```

**Frontend:**

```bash
cd route-optimizer-frontend
npm install
# route-optimizer-frontend/.env:
#   REACT_APP_GOOGLE_MAPS_API_KEY=...       Maps JavaScript API, for the route map
npm start                                  # http://localhost:3000
```

The frontend calls `http://localhost:5001/api`, which is hard-coded in `src/api.js`.

**Tests and benchmark** (no API keys needed):

```bash
pytest tests/
python -m benchmark.cli --list
python -m benchmark.cli demo_anaheim --max-vehicles 3 --csv results.csv
```

## What is and is not modelled

| Modelled | Not modelled (yet) |
|---|---|
| Stop order for one truck: exact (Held-Karp, up to ~17 stops by default) or heuristic | State-of-charge curves; more than one charging stop per route |
| Splitting stops across 2+ trucks (OR-Tools, balanced stop counts) | Time windows, driver-hours limits |
| What to optimize: distance, time, Model E energy and fleet balance, weighted by goals | Payload capacity in normal planning (only in breakdown re-plans) |
| Energy per leg from speed, temperature, weight, elevation, HVAC | Road closures other than simulated accidents |
| Cargo weight dropping stop by stop; the truck returns empty | More than one breakdown at a time |
| Battery per truck (packs × 20 kWh): checked always; fleets keep a 20% reserve by default (adjustable or off) | More than 25 locations per request |
| Priority stops, served first on their truck | |
| Suggestions after each solve: a problem found in the plan, and a fix tested by re-solving | |
| A truck breaking down mid-route: stranded stops re-assigned, cargo collected, optional spare truck | |
| Unloading time per stop | |
| Live traffic for a departure time (Google's traffic prediction) | |
| Driving style eco / normal / aggressive (assumed speed and energy factors) | |
| Measured / forecast traffic replays of four past days (traffic data package, Cerritos–Stanton only) | |
| Accidents at historical crash hotspots: the road between two stops is rerouted, stop order kept | |
| One charging stop for a fleet truck that would end below its battery reserve | |
| Battery-drain simulation with re-planning at every stop (benchmark) | |

## Traffic data (optional)

`/api/optimize` can take travel times from a traffic data package instead of Google: four days of 5-minute speeds (measured and 2-hour forecasts) on the major roads of a 15 x 14 km area around Cerritos and Stanton. `traffic_data.py` follows the package's travel-time rules (corridor speed in the interval the edge is entered, plus intersection length and traffic-light delays) and reproduces its `example.py` result.

- **Setup:** unzip the package to `poc_route_planning_v1/` in the repo root (or set `POC_TRAFFIC_DIR`). The data is licensed for this project only and is git-ignored, so never commit it.
- **Request:** add `"traffic_source": "forecast" | "historical" | "ground_truth"` and `"departure_time": "2025-11-12T07:30"` (days: 2025-11-12, 11-18, 11-22, 11-23). `historical` uses the ground truth of a comparable other day (Wed↔Tue, Sat↔Sun) at the same times. A traffic data source overrides the Google `traffic` departure setting.
- **Plan vs reality:** the Held-Karp result gets `traffic.actual_travel_time_s`, which is the planned route driven on the measured speeds with the clock running (unload time at each stop).
- **Dashboard:** choose the source under *Travel Times* and load the *Demo 0* stops (all stops must be inside the area). Raise the *Time* goal so traffic can change the stop order.
- **Simplifications:** every pair in the matrix is timed as if it started at the departure time; freeway speeds are capped at 88 km/h (the data has car speeds); stops snap to the nearest non-freeway road, so the short stretch from the address to that road is not counted.

## Incident replanning (demo)

Routes are planned ahead and trucks are loaded for that plan. On the day, the plan is rerun against simulated accidents at historical crash hotspots, but because the trucks are already loaded, the stop order is kept. (A truck *breaking down* is different: see [breakdown re-planning](docs/planning.md), where stranded stops move to other trucks.)

Accidents are placed where fatal crashes actually happened along the route, using NHTSA FARS data (2019–2023, all US fatal crashes, public domain) in `data/crash_history.csv.gz`. Crashes within 150 m of each leg's road are grouped into 1 km stretches; the stretches with the most crashes become the accidents (default 3, spread over different legs, none next to a stop). To refresh the data, download newer `FARS<YEAR>NationalCSV.zip` files and run `python scripts/build_crash_history.py FARS*.zip`.

`/api/replan_incidents` takes the planned legs (Held-Karp order, or with several vehicles every OR-Tools route's legs tagged with `vehicle_id`) and only replaces the road between the two stops an accident sits on (stop i → stop i+1), via a Google alternative route or a forced detour. With a fleet, hotspots are spread over the vehicles first. Stops are never added, removed or reordered. `/api/reroute_from` reroutes a truck that is already driving, for the **drive simulation**: with traffic data, **Simulate Drive** animates the Held-Karp route, or with a fleet every truck at once on one shared clock, each leg taking its measured time. When a truck nears an active accident the whole simulation pauses and asks whether that truck should reroute; the panel shows each truck's status and when it gets back versus plan. `tests/test_replanning.py` checks this offline.

**Charging stops.** A fleet truck that would end below its battery reserve (the reserve goal, or 20% when none is set) is flagged, and **Find Charging Stops** calls `/api/plan_charging`: it locates where the battery would hit the reserve (energy spread evenly over each leg), searches DC fast chargers (CCS1, 50 kW+) along the legs up to that point with Google Places Text Search, and inserts the charger that adds the least time (detour + charging) into its leg. The truck charges just enough to finish with the reserve; stops are not reordered. Charging time assumes the truck accepts at most 50 kW (`VEHICLE_MAX_CHARGE_KW`, to be confirmed against the Isuzu spec) and a flat charging curve. Routes that need more than one stop are reported, not planned. `tests/test_charging.py` checks the rules offline.

**Energy in accident replanning: deferred.** Energy is calculated for the original and rerouted legs and shown for comparison, but the reroute is the fastest route that avoids the accident.
