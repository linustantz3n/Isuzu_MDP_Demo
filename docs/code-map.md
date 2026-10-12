# Code map

A file-by-file and function-by-function guide. Line links are as of October 2026; if one has
drifted, search for the name.

```
.
├── route_optimizer_backend.py   Flask API server: everything server-side except the solvers
├── solvers.py                   Routing solvers (TSP + OR-Tools VRP)
├── benchmark/                   Offline algorithm comparison
├── tests/                       pytest suite (solvers + benchmark)
├── route-optimizer-frontend/    React web app
├── docs/                        These docs
├── prompt_test.py, prompt_demo.py (+ *_results.txt)   Chat-assistant regression scripts
├── route_optimizer.jsx          Legacy prototype (not used, see bottom)
├── google_maps_api_guide.md     Notes on the Google APIs
├── my_addresses.txt, test_stops.txt, test_stops_wide.txt   Sample stop lists
└── requirements.txt             Python dependencies
```

---

## `route_optimizer_backend.py`

One file, in sections, top to bottom:

### Google Maps calls
| Function | Does |
|---|---|
| [`geocode_address(address)`](../route_optimizer_backend.py#L46) | Address → `{address, formatted_address, lat, lng}` |
| [`get_distance_matrix(locations)`](../route_optimizer_backend.py#L71) | n×n driving distance (m) and time (s) matrices, fetched in 10×10 batches. Pairs Google cannot route become `inf` |
| [`get_directions(locations, order)`](../route_optimizer_backend.py#L131) | Raw Directions API response for an ordered route |
| [`get_elevation_data(locations)`](../route_optimizer_backend.py#L165) | Elevation in metres per location; all zeros if the call fails |

### Energy model ([explained here](energy-model.md))
| Function | Does |
|---|---|
| [`calculate_energy_consumption(distance_km, avg_speed_kmh, temperature_c, vehicle_weight_tonnes, altitude_diff_m, altitude_m, has_climate_control=True)`](../route_optimizer_backend.py#L197) | Model E regression for one leg → `{energy_kwh, energy_per_km, energy_per_mile, miles_per_kwh, kwh_per_mile, breakdown}` |
| [`estimate_average_speed(distance_m, time_s)`](../route_optimizer_backend.py#L308) | Leg average speed in km/h |

### Solver registry and recommendation ([explained here](solvers.md#how-apioptimize-decides-what-to-run))
| Name | Does |
|---|---|
| [`HK_SEC_PER_UNIT`, `HK_TIME_BUDGET_MS`, `HK_HARD_LIMIT_N`](../route_optimizer_backend.py#L339) | Held-Karp cost model and limits |
| [`SOLVERS`](../route_optimizer_backend.py#L347) | The four user-selectable solvers and their properties |
| [`estimate_held_karp_ms(n)`](../route_optimizer_backend.py#L381) | Predicted Held-Karp runtime for an n×n matrix |
| [`recommend_solver(stops, vehicles, need_optimal, lang)`](../route_optimizer_backend.py#L499) | Which solver fits, and the reason (used by the chat and by `auto`) |
| [`solver_table(stops, vehicles, lang)`](../route_optimizer_backend.py#L544) | Per-solver feasibility and cost (chat table) |
| [`resolve_solver(requested, stops, vehicles)`](../route_optimizer_backend.py#L573) | **Turns the `solver` setting into the list of algorithms `/api/optimize` runs** |
| `REASONS`, `PHRASES`, `format_duration`, `detect_lang` | English/Chinese text for the advice |

### API endpoints
| Function | Route |
|---|---|
| [`health_check`](../route_optimizer_backend.py#L626) | `GET /api/health` |
| [`set_api_key`](../route_optimizer_backend.py#L635) | `POST /api/set_key` |
| [`geocode`](../route_optimizer_backend.py#L644), [`geocode_batch`](../route_optimizer_backend.py#L657) | `POST /api/geocode`, `/api/geocode_batch` |
| [**`optimize_route`**](../route_optimizer_backend.py#L681) | **`POST /api/optimize`, the main pipeline.** Details below |
| [`get_route_directions`](../route_optimizer_backend.py#L1169) | `POST /api/directions` |

**Inside `optimize_route()`**, in order:

| Step | Line |
|---|---|
| Parse the request; cap at 25 locations; choose solvers (`resolve_solver`) | [L697](../route_optimizer_backend.py#L697) |
| Distance/time matrix; pick `matrix` by `mode` (this is what the solvers minimize) | [L723](../route_optimizer_backend.py#L723) |
| Elevations | [L727](../route_optimizer_backend.py#L727) |
| Energy parameters and defaults | [L730](../route_optimizer_backend.py#L730) |
| Vehicle config, empty-truck weight from packs, cargo weight | [L740](../route_optimizer_backend.py#L740) |
| Run Held-Karp / nearest neighbor / 2-opt | [L775](../route_optimizer_backend.py#L775) |
| Stand-in when Held-Karp was skipped | [L793](../route_optimizer_backend.py#L793) |
| Per route: totals, legs, weight per leg, **energy per leg** | [L804](../route_optimizer_backend.py#L804) |
| Return leg (empty truck) | [L888](../route_optimizer_backend.py#L888) |
| Unloading time; per-route totals | [L934](../route_optimizer_backend.py#L934) |
| Greedy-vs-optimal comparison | [L961](../route_optimizer_backend.py#L961) |
| Google Maps URLs | [L984](../route_optimizer_backend.py#L984) |
| **OR-Tools fleet** (more than one vehicle): solve, per-vehicle legs, energy, battery | [L1002](../route_optimizer_backend.py#L1002) |
| Build the JSON response | [L1143](../route_optimizer_backend.py#L1143) |

### AI chat ([explained here](chat-assistant.md))
| Name | Does |
|---|---|
| [`AXES`](../route_optimizer_backend.py#L1220), [`CAPABILITIES`](../route_optimizer_backend.py#L1259) | The fields the assistant may change, their limits, and what it can do |
| [`check_value`](../route_optimizer_backend.py#L1371), [`make_change`](../route_optimizer_backend.py#L1418), [`describe_change`](../route_optimizer_backend.py#L1314) | Range checks and labels, shared by every path |
| [`outcome_apply`](../route_optimizer_backend.py#L1445), [`outcome_ask`](../route_optimizer_backend.py#L1477), [`outcome_out_of_scope`](../route_optimizer_backend.py#L1487), [`outcome_recommend`](../route_optimizer_backend.py#L1511) | Build the reply for each intent |
| [`LEXICON`](../route_optimizer_backend.py#L1596), [`lexicon_fast_path`](../route_optimizer_backend.py#L1719) | Stage 1: regex fast path, no model call |
| [`mentions_saved_driver`](../route_optimizer_backend.py#L1705) | Sends a message that names a saved driver to the model |
| [`EXTRACTION_SCHEMA`](../route_optimizer_backend.py#L1863), [`EXTRACTION_SYSTEM_PROMPT`](../route_optimizer_backend.py#L1871), [`call_extraction`](../route_optimizer_backend.py#L1946) | Stage 2: GPT-4o-mini with a strict JSON schema |
| [`validate_change`](../route_optimizer_backend.py#L1985), [`validate_extraction`](../route_optimizer_backend.py#L2075), [`route_outcome`](../route_optimizer_backend.py#L2191) | Stage 3: deterministic validation and routing |
| [`run_steps`](../route_optimizer_backend.py#L2280), [`advance_form_state`](../route_optimizer_backend.py#L2238) | Run a multi-step message in order and merge the replies |
| [`ai_chat`](../route_optimizer_backend.py#L2324) | `POST /api/chat` |

### Driver memory
| Name | Does |
|---|---|
| [`MEMORY_PATH`](../route_optimizer_backend.py#L2368), [`load_memory`](../route_optimizer_backend.py#L2383), [`save_memory`](../route_optimizer_backend.py#L2396) | The `fleet_memory.json` store |
| [`validate_profile`](../route_optimizer_backend.py#L2435) | Evidence and range gate for a driver profile |
| [`do_remember`](../route_optimizer_backend.py#L2512), [`do_recall`](../route_optimizer_backend.py#L2544), [`do_forget`](../route_optimizer_backend.py#L2577) | The memory operations |
| [`profile_to_vehicle`](../route_optimizer_backend.py#L2408) | Recalled driver → a vehicle entry for the form |
| [`get_memory`](../route_optimizer_backend.py#L2593), [`clear_memory`](../route_optimizer_backend.py#L2600) | `GET` / `DELETE /api/memory` |

### Benchmark endpoints
[`benchmark_scenarios`](../route_optimizer_backend.py#L2617), [`benchmark_algorithms`](../route_optimizer_backend.py#L2623), [`benchmark_run`](../route_optimizer_backend.py#L2629): thin wrappers around `benchmark.run_benchmark`.

The server starts at the bottom of the file: `app.run(port=5001)`.

---

## `solvers.py`

[Explained here](solvers.md). `solve_tsp_brute_force`, `solve_tsp_held_karp`,
`solve_tsp_nearest_neighbor`, `solve_tsp_2opt`, `solve_vrp_ortools`.

## `benchmark/`

[Explained here](solvers.md#benchmark). `problem.py` (types), `algorithms.py` (registry),
`metrics.py` (scoring), `runner.py` (orchestration), `snapshot.py` (build a scenario),
`cli.py` (command line), `scenarios/` (saved inputs), `stops/` (the stop lists behind them).

## `tests/`

`pytest tests/` needs no API keys.
[`test_benchmark.py`](../tests/test_benchmark.py) checks that every algorithm returns valid,
capped routes; that Held-Karp is optimal at K = 1; that scoring is recomputed from the matrices;
the OR-Tools production defaults; and the runner on the demo scenario.
[`conftest.py`](../tests/conftest.py) puts the repo root on the import path.

---

## `route-optimizer-frontend/` (React)

Created with Create React App and styled with Tailwind. The entry chain is `src/index.js` → `App.js` → `RouteOptimizer.jsx`.

| File | Does |
|---|---|
| [`src/RouteOptimizer.jsx`](../route-optimizer-frontend/src/RouteOptimizer.jsx) | **The main page.** Holds all form state: [`vehicles`](../route-optimizer-frontend/src/RouteOptimizer.jsx#L33) (default 13,000 lbs, 4 packs, 80 kWh), [`energyParams`](../route-optimizer-frontend/src/RouteOptimizer.jsx#L40) (20 °C, 30 min unload, HVAC on), depot, stops and solver. [`validateAddresses`](../route-optimizer-frontend/src/RouteOptimizer.jsx#L126) geocodes; [`optimizeRoute`](../route-optimizer-frontend/src/RouteOptimizer.jsx#L196) builds the `/api/optimize` request; [`applyChatChange`](../route-optimizer-frontend/src/RouteOptimizer.jsx#L272) applies chat changes. Renders the result cards, comparison table, map, charts and stop lists. Has the Solver and Benchmark tabs |
| [`src/AIChatbot.jsx`](../route-optimizer-frontend/src/AIChatbot.jsx) | Floating chat panel. Sends `{message, history, form_state, pending}` to `/api/chat` and applies the returned changes |
| [`src/RouteMap.jsx`](../route-optimizer-frontend/src/RouteMap.jsx) | Google Maps view of the routes (Directions renderer, one colour per vehicle) |
| [`src/FleetCharts.jsx`](../route-optimizer-frontend/src/FleetCharts.jsx) | Bar charts (Recharts) of time and energy: single vehicle vs each fleet vehicle |
| [`src/BenchmarkPanel.jsx`](../route-optimizer-frontend/src/BenchmarkPanel.jsx) | Benchmark tab: pick a scenario, fleet sizes, algorithms and time limit; run; results table |
| `src/BenchmarkCharts.jsx`, `BenchmarkRoutes.jsx`, `BenchmarkRouteMap.jsx` | Benchmark charts, per-algorithm route lists and map |
| [`src/api.js`](../route-optimizer-frontend/src/api.js) | `API_BASE = 'http://localhost:5001/api'`. **Change this when deploying** |
| `public/demo_stops.txt` | What the "Demo" button loads. Line 1 is the depot |
| `.env` (not committed) | `REACT_APP_GOOGLE_MAPS_API_KEY` for the map |

---

## Other files

| File | What it is |
|---|---|
| `prompt_test.py` / `prompt_test_results.txt` | Prompt battery for `/api/chat`, with its last results |
| `prompt_demo.py` / `prompt_demo_results.txt` | Ten example prompts with full replies |
| `route_optimizer.jsx` | **Legacy.** A standalone React prototype from the first commit that solved the TSP in the browser. Nothing imports it; the live app is `route-optimizer-frontend/` |
| `google_maps_api_guide.md` | Reference notes on the Geocoding, Distance Matrix and Directions APIs |
| `my_addresses.txt`, `test_stops.txt`, `test_stops_wide.txt` | Sample stop lists, one address per line |
| `.env` (not committed) | `GOOGLE_MAPS_API_KEY`, `OPENAI_API_KEY` |
| `fleet_memory.json` (not committed) | Driver memory, created at runtime |
