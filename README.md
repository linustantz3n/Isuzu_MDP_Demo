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
> time, Model E energy and fleet balance instead, and a battery reserve makes each truck's
> battery a hard limit. See [docs/energy-model.md](docs/energy-model.md#how-energy-relates-to-the-solvers).

## Where to find things

| You want… | Go to |
|---|---|
| **The energy model**: formula, coefficients, inputs, weight and battery logic | [docs/energy-model.md](docs/energy-model.md) · code: [`calculate_energy_consumption()`](route_optimizer_backend.py#L198) |
| **The solvers** and how one gets picked | [docs/solvers.md](docs/solvers.md) · code: [`solvers.py`](solvers.py), [`resolve_solver()`](route_optimizer_backend.py#L574) |
| **What a solve takes as input and returns** | [docs/api.md](docs/api.md) · code: [`optimize_route()`](route_optimizer_backend.py#L1489) |
| **What every file and function does** | [docs/code-map.md](docs/code-map.md) |
| The chat assistant and driver memory | [docs/chat-assistant.md](docs/chat-assistant.md) |
| **Optimization goals (weights), battery reserve, priority stops, breakdown re-planning, truck limits** | [docs/planning.md](docs/planning.md) · code: [`objective_matrix()`](route_optimizer_backend.py#L759), [`solve_fleet()`](route_optimizer_backend.py#L994), [`replan_breakdown()`](route_optimizer_backend.py#L1116) |
| The before / after comparison | [`PlanComparison.jsx`](route-optimizer-frontend/src/PlanComparison.jsx), [`planDiff.js`](route-optimizer-frontend/src/planDiff.js) |
| The algorithm benchmark | [docs/solvers.md#benchmark](docs/solvers.md#benchmark) · code: [`benchmark/`](benchmark/) |
| The web UI | [`route-optimizer-frontend/src/RouteOptimizer.jsx`](route-optimizer-frontend/src/RouteOptimizer.jsx) |

## How a request flows

```
Browser (React, :3000)                     Flask backend (:5001) — route_optimizer_backend.py
──────────────────────                     ─────────────────────────────────────────────────
RouteOptimizer.jsx
  Validate Addresses ── /api/geocode(_batch) ──► geocode_address()          Google Geocoding
  Optimize Routes ───── /api/optimize ─────────► optimize_route()
                                                   1. get_distance_matrix()    Google Distance Matrix (m, s)   ┐ skipped when the client
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
BenchmarkPanel.jsx ──── /api/benchmark/* ──────► benchmark/ (saved scenarios, no Google calls)
```

## Repository layout

```
route_optimizer_backend.py   Flask API: Google calls, energy model, solver selection, optimization goals,
                             /api/optimize, /api/replan, chat assistant, driver memory, benchmark endpoints
solvers.py                   Solvers: brute force, Held-Karp, nearest neighbor, 2-opt, OR-Tools VRP
                             (plain, and constrained: battery, payload, pickups, optional stops)
benchmark/                   Offline algorithm comparison (scenarios, registry, scoring, CLI)
tests/                       pytest suite for the solvers and benchmark
route-optimizer-frontend/    React app: src/RouteOptimizer.jsx is the main page
docs/                        Energy model, solvers, planning (goals, breakdowns), API, code map, chat assistant
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
| Stop order for one truck: exact (Held-Karp, up to ~17 stops by default) or heuristic | Charging stops, state-of-charge curves |
| Splitting stops across 2+ trucks (OR-Tools, balanced stop counts) | Time windows, driver-hours limits |
| What to optimize: distance, time, Model E energy and fleet balance, weighted by goals | Payload capacity in normal planning (only in breakdown re-plans) |
| Energy per leg from speed, temperature, weight, elevation, HVAC | Live traffic (Google's typical travel times are used) |
| Cargo weight dropping stop by stop; the truck returns empty | More than one breakdown at a time |
| Battery per truck (packs × 20 kWh): checked always, enforced for fleets with a reserve | More than 25 locations per request |
| Priority stops, served first on their truck | |
| A truck breaking down mid-route: stranded stops re-assigned, cargo collected, optional spare truck | |
| Unloading time per stop | |
