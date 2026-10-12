# Isuzu BEV Fleet Route Optimizer

Route planning for battery-electric Isuzu delivery trucks. You give it a depot, delivery stops
and the truck setup. It orders the stops for one truck using Held-Karp (exact), 2-opt or nearest
neighbor. With more than one truck, it also splits the stops across the fleet with Google
OR-Tools. It then estimates each route's energy use with a fitted regression model ("Model E")
and checks the result against each truck's battery.

> **The solvers minimize driving distance. Energy is calculated afterwards, on the finished route.**
> See [docs/energy-model.md](docs/energy-model.md#how-energy-relates-to-the-solvers).

## Where to find things

| You want… | Go to |
|---|---|
| **The energy model**: formula, coefficients, inputs, weight and battery logic | [docs/energy-model.md](docs/energy-model.md) · code: [`calculate_energy_consumption()`](route_optimizer_backend.py#L197) |
| **The solvers** and how one gets picked | [docs/solvers.md](docs/solvers.md) · code: [`solvers.py`](solvers.py), [`resolve_solver()`](route_optimizer_backend.py#L573) |
| **What a solve takes as input and returns** | [docs/api.md](docs/api.md) · code: [`optimize_route()`](route_optimizer_backend.py#L681) |
| **What every file and function does** | [docs/code-map.md](docs/code-map.md) |
| The chat assistant and driver memory | [docs/chat-assistant.md](docs/chat-assistant.md) |
| The algorithm benchmark | [docs/solvers.md#benchmark](docs/solvers.md#benchmark) · code: [`benchmark/`](benchmark/) |
| The web UI | [`route-optimizer-frontend/src/RouteOptimizer.jsx`](route-optimizer-frontend/src/RouteOptimizer.jsx) |

## How a request flows

```
Browser (React, :3000)                     Flask backend (:5001) — route_optimizer_backend.py
──────────────────────                     ─────────────────────────────────────────────────
RouteOptimizer.jsx
  Validate Addresses ── /api/geocode(_batch) ──► geocode_address()          Google Geocoding
  Optimize Routes ───── /api/optimize ─────────► optimize_route()
                                                   1. get_distance_matrix()    Google Distance Matrix (m, s)
                                                   2. get_elevation_data()     Google Elevation (m)
                                                   3. resolve_solver()         which TSP solvers to run
                                                   4. solvers.py               Held-Karp / 2-opt / NN    (1 truck)
                                                                               OR-Tools VRP              (2+ trucks)
                                                   5. for every leg:           calculate_energy_consumption()
                                                   6. JSON: routes, legs, time, energy, battery, Maps links
  result cards, RouteMap, FleetCharts ◄────────────┘
AIChatbot.jsx ───────── /api/chat ─────────────► lexicon → GPT-4o-mini → validation → one form change
BenchmarkPanel.jsx ──── /api/benchmark/* ──────► benchmark/ (saved scenarios, no Google calls)
```

## Repository layout

```
route_optimizer_backend.py   Flask API: Google calls, energy model, solver selection, /api/optimize,
                             chat assistant, driver memory, benchmark endpoints
solvers.py                   Solvers: brute force, Held-Karp, nearest neighbor, 2-opt, OR-Tools VRP
benchmark/                   Offline algorithm comparison (scenarios, registry, scoring, CLI)
tests/                       pytest suite for the solvers and benchmark
route-optimizer-frontend/    React app: src/RouteOptimizer.jsx is the main page
docs/                        Energy model, solvers, API, code map, chat assistant
prompt_test.py, prompt_demo.py   Chat-assistant regression scripts (need a running server)
route_optimizer.jsx          Legacy prototype from the first commit; not used
```

## Running it

**Backend** (Python 3; the tests pass on 3.14):

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
# .env in the repo root:
#   GOOGLE_MAPS_API_KEY=...   Geocoding, Distance Matrix, Elevation and Directions APIs enabled
#   OPENAI_API_KEY=...        optional; only the chat assistant's model step uses it
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
| Stop order for one truck: exact (Held-Karp, up to ~17 stops by default) or heuristic | Energy as the routing objective (it is only evaluated afterwards) |
| Splitting stops across 2+ trucks (OR-Tools, total distance, balanced stop counts) | Charging stops, state-of-charge limits, reserve margin |
| Energy per leg from speed, temperature, weight, elevation, HVAC | Payload capacity, time windows, driver-hours limits |
| Cargo weight dropping stop by stop; the truck returns empty | Live traffic (Google's typical travel times are used) |
| Battery check per fleet truck (packs × 20 kWh) | A battery check for the single-truck routes |
| Unloading time per stop | More than 25 locations per request |
