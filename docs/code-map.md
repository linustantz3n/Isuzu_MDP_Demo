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
├── r4_test.py (+ r4_test_results*.txt)                R4 test set: weights, traffic, driving style
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
| [`geocode_address(address)`](../route_optimizer_backend.py#L47) | Address → `{address, formatted_address, lat, lng}` |
| [`get_distance_matrix(locations, departure_ts)`](../route_optimizer_backend.py#L72) | n×n driving distance (m) and time (s) matrices, fetched in 10×10 batches. With a departure time, the times are Google's traffic prediction. Pairs Google cannot route become `inf` |
| [`get_utc_offset(lat, lng, ts)`](../route_optimizer_backend.py#L138) | The depot's UTC offset and time zone (Google Time Zone API) |
| [`get_directions(locations, order)`](../route_optimizer_backend.py#L152) | Raw Directions API response for an ordered route |
| [`get_elevation_data(locations)`](../route_optimizer_backend.py#L186) | Elevation in metres per location; all zeros if the call fails |

### Energy model ([explained here](energy-model.md))
| Function | Does |
|---|---|
| [`calculate_energy_consumption(distance_km, avg_speed_kmh, temperature_c, vehicle_weight_tonnes, altitude_diff_m, altitude_m, has_climate_control=True)`](../route_optimizer_backend.py#L218) | Model E regression for one leg → `{energy_kwh, energy_per_km, energy_per_mile, miles_per_kwh, kwh_per_mile, breakdown}` |
| [`estimate_average_speed(distance_m, time_s)`](../route_optimizer_backend.py#L340) | Leg average speed in km/h |

### Solver registry and recommendation ([explained here](solvers.md#how-apioptimize-decides-what-to-run))
| Name | Does |
|---|---|
| [`HK_SEC_PER_UNIT`, `HK_TIME_BUDGET_MS`, `HK_HARD_LIMIT_N`](../route_optimizer_backend.py#L371) | Held-Karp cost model and limits |
| [`SOLVERS`](../route_optimizer_backend.py#L379) | The four user-selectable solvers and their properties |
| [`estimate_held_karp_ms(n)`](../route_optimizer_backend.py#L413) | Predicted Held-Karp runtime for an n×n matrix |
| [`recommend_solver(stops, vehicles, need_optimal, lang)`](../route_optimizer_backend.py#L531) | Which solver fits, and the reason (used by the chat and by `auto`) |
| [`solver_table(stops, vehicles, lang)`](../route_optimizer_backend.py#L576) | Per-solver feasibility and cost (chat table) |
| [`resolve_solver(requested, stops, vehicles)`](../route_optimizer_backend.py#L605) | **Turns the `solver` setting into the list of algorithms `/api/optimize` runs** |
| `REASONS`, `PHRASES`, `format_duration`, `detect_lang` | English/Chinese text for the advice |

### Optimization goals, truck limits and breakdown re-planning ([explained here](planning.md))
| Name | Does |
|---|---|
| [`GOAL_LEVEL_WEIGHT`, `DEFAULT_GOALS`](../route_optimizer_backend.py#L677), [`normalize_goals`](../route_optimizer_backend.py#L844), [`goal_weights`](../route_optimizer_backend.py#L862) | Goal levels → weights that add up to 1 |
| [`energy_matrix_kwh`](../route_optimizer_backend.py#L878), [`energy_matrix_wh`](../route_optimizer_backend.py#L901) | Model E energy for every pair of stops at one weight; integer Wh for OR-Tools |
| [`objective_matrix`](../route_optimizer_backend.py#L907) | **The cost matrix the solvers minimise**: weighted distance / time / energy, priority-stop penalty, balance (span) price |
| [`precomputed_inputs`](../route_optimizer_backend.py#L965) | Validates matrices and elevations the client sends back, so a re-solve needs no Google calls |
| [`evaluate_route`](../route_optimizer_backend.py#L1004) | Leg-by-leg distance, time, energy (at the weight on board) and arrival times for any route, including pickups |
| [`vehicle_numbers`](../route_optimizer_backend.py#L1054), [`battery_fields`](../route_optimizer_backend.py#L1068), [`battery_depletion`](../route_optimizer_backend.py#L1081), [`fleet_route_payload`](../route_optimizer_backend.py#L1118), [`fleet_totals`](../route_optimizer_backend.py#L1154) | One truck's weights and battery; the battery check; where the battery runs out; a route and the fleet in the response shape |
| [`solve_fleet`](../route_optimizer_backend.py#L1175) | **The OR-Tools fleet solve under the goals**: stop cap, battery reserve, balance, unserved stops |
| [`replan_breakdown`](../route_optimizer_backend.py#L1298) | Breakdown re-plan: replay the plan, find each truck, move stranded stops with a pickup first |
| [`DRIVING_STYLES`](../route_optimizer_backend.py#L702), [`styled_matrices`](../route_optimizer_backend.py#L712) | Driving style factors; the time matrix as a style drives it |
| [`departure_in`](../route_optimizer_backend.py#L735), [`traffic_request`](../route_optimizer_backend.py#L777), [`departure_info`](../route_optimizer_backend.py#L785) | A departure read from the operator's words; the request's traffic field; the Unix time Google prices traffic for |
| [`truck_limits`](../route_optimizer_backend.py#L1551) | Battery, payload and range of an N-pack truck; check against today's longest route |
| [`plan_facts`](../route_optimizer_backend.py#L1673), [`plan_problem`](../route_optimizer_backend.py#L1694) | The facts a suggestion reads from a plan; its most pressing problem |
| [`fix_candidates`](../route_optimizer_backend.py#L1730), [`with_fix`](../route_optimizer_backend.py#L1766), [`fixes_problem`](../route_optimizer_backend.py#L1787), [`suggest_fix`](../route_optimizer_backend.py#L1832) | Fixes to try, in order; the request with one applied; whether it solved the problem; the whole check |

### API endpoints
| Function | Route |
|---|---|
| [`health_check`](../route_optimizer_backend.py#L1879) | `GET /api/health` |
| [`set_api_key`](../route_optimizer_backend.py#L1888) | `POST /api/set_key` |
| [`geocode`](../route_optimizer_backend.py#L1897), [`geocode_batch`](../route_optimizer_backend.py#L1910) | `POST /api/geocode`, `/api/geocode_batch` |
| [**`optimize_route`**](../route_optimizer_backend.py#L1934) | **`POST /api/optimize`, the main pipeline.** Details below |
| [`get_route_directions`](../route_optimizer_backend.py#L2345) | `POST /api/directions` |
| [`suggest_route`](../route_optimizer_backend.py#L2426) | `POST /api/suggest`: check a solved plan and test fixes |
| [`replan_route`](../route_optimizer_backend.py#L2365) | `POST /api/replan`: simulate a breakdown on a plan |

**Inside `optimize_payload()`**, which `optimize_route()` runs, in order:

| Step | Line |
|---|---|
| Parse the request; cap at 25 locations; choose solvers (`resolve_solver`) | [L1962](../route_optimizer_backend.py#L1962) |
| Distance/time matrices: from Google, or `precomputed` by the client for the same locations | [L2004](../route_optimizer_backend.py#L2004) |
| Elevations | [L2009](../route_optimizer_backend.py#L2009) |
| Energy parameters and defaults | [L2011](../route_optimizer_backend.py#L2011) |
| Vehicle config, empty-truck weight from packs, cargo weight | [L2021](../route_optimizer_backend.py#L2021) |
| Goals → the cost matrix the single-truck solvers minimise (`objective_matrix`) | [L2063](../route_optimizer_backend.py#L2063) |
| Run Held-Karp / nearest neighbor / 2-opt | [L2068](../route_optimizer_backend.py#L2068) |
| Stand-in when Held-Karp was skipped | [L2086](../route_optimizer_backend.py#L2086) |
| Per route: totals, legs, weight per leg, **energy per leg** | [L2097](../route_optimizer_backend.py#L2097) |
| Return leg (empty truck) | [L2182](../route_optimizer_backend.py#L2182) |
| Unloading time; per-route totals | [L2229](../route_optimizer_backend.py#L2229) |
| Greedy-vs-optimal comparison | [L2267](../route_optimizer_backend.py#L2267) |
| Google Maps URLs | [L2290](../route_optimizer_backend.py#L2290) |
| **OR-Tools fleet** (more than one vehicle): `solve_fleet()` | [L2308](../route_optimizer_backend.py#L2308) |
| Build the JSON response | [L2316](../route_optimizer_backend.py#L2316) |

### AI chat ([explained here](chat-assistant.md))
| Name | Does |
|---|---|
| [`AXES`](../route_optimizer_backend.py#L2479), [`CAPABILITIES`](../route_optimizer_backend.py#L2534) | The fields the assistant may change, their limits, and what it can do |
| [`check_value`](../route_optimizer_backend.py#L2713), [`make_change`](../route_optimizer_backend.py#L2781), [`describe_change`](../route_optimizer_backend.py#L2631) | Range checks and labels, shared by every path |
| [`outcome_apply`](../route_optimizer_backend.py#L2808), [`outcome_ask`](../route_optimizer_backend.py#L2840), [`outcome_out_of_scope`](../route_optimizer_backend.py#L2850), [`outcome_recommend`](../route_optimizer_backend.py#L2874) | Build the reply for each intent |
| [`LEXICON`](../route_optimizer_backend.py#L3316), [`lexicon_fast_path`](../route_optimizer_backend.py#L3460) | Stage 1: regex fast path, no model call |
| [`mentions_saved_driver`](../route_optimizer_backend.py#L3446) | Sends a message that names a saved driver to the model |
| [`EXTRACTION_SCHEMA`](../route_optimizer_backend.py#L3666), [`EXTRACTION_SYSTEM_PROMPT`](../route_optimizer_backend.py#L3674), [`call_extraction`](../route_optimizer_backend.py#L3763) | Stage 2: GPT-4o-mini with a strict JSON schema |
| [`validate_change`](../route_optimizer_backend.py#L3803), [`validate_extraction`](../route_optimizer_backend.py#L3965), [`route_outcome`](../route_optimizer_backend.py#L4152) | Stage 3: deterministic validation and routing |
| [`outcome_goals`](../route_optimizer_backend.py#L3105), [`outcome_limits`](../route_optimizer_backend.py#L3181), [`outcome_disruption`](../route_optimizer_backend.py#L3220) | Goals from the situation, truck limits, breakdowns: the gates and the computed replies |
| [`NOT_A_FORM_VALUE_RE`](../route_optimizer_backend.py#L3402), [`RESOLVE_RE`](../route_optimizer_backend.py#L3046) | Keeps goal / limit / breakdown talk away from the lexicon; "re-optimize" |
| [`run_steps`](../route_optimizer_backend.py#L4256), [`advance_form_state`](../route_optimizer_backend.py#L4205) | Run a multi-step message in order and merge the replies |
| [`ai_chat`](../route_optimizer_backend.py#L4305) | `POST /api/chat` |

### Driver memory
| Name | Does |
|---|---|
| [`MEMORY_PATH`](../route_optimizer_backend.py#L4349), [`load_memory`](../route_optimizer_backend.py#L4364), [`save_memory`](../route_optimizer_backend.py#L4377) | The `fleet_memory.json` store |
| [`validate_profile`](../route_optimizer_backend.py#L4416) | Evidence and range gate for a driver profile |
| [`do_remember`](../route_optimizer_backend.py#L4493), [`do_recall`](../route_optimizer_backend.py#L4525), [`do_forget`](../route_optimizer_backend.py#L4558) | The memory operations |
| [`profile_to_vehicle`](../route_optimizer_backend.py#L4389) | Recalled driver → a vehicle entry for the form |
| [`get_memory`](../route_optimizer_backend.py#L4574), [`clear_memory`](../route_optimizer_backend.py#L4581) | `GET` / `DELETE /api/memory` |

### Benchmark endpoints
[`benchmark_scenarios`](../route_optimizer_backend.py#L4598), [`benchmark_algorithms`](../route_optimizer_backend.py#L4604), [`benchmark_run`](../route_optimizer_backend.py#L4610): thin wrappers around `benchmark.run_benchmark`.

The server starts at the bottom of the file: `app.run(port=5001)`.

---

## `solvers.py`

[Explained here](solvers.md). `solve_tsp_brute_force`, `solve_tsp_held_karp`,
`solve_tsp_nearest_neighbor`, `solve_tsp_2opt`, `solve_vrp_ortools`, and
`solve_vrp_constrained` (the OR-Tools model with starts, time, energy, payload,
pickup-and-delivery and optional stops).

## `benchmark/`

[Explained here](solvers.md#benchmark). `problem.py` (types), `algorithms.py` (registry),
`metrics.py` (scoring), `runner.py` (orchestration), `snapshot.py` (build a scenario),
`cli.py` (command line), `scenarios/` (saved inputs), `stops/` (the stop lists behind them).

## `tests/`

`pytest tests/` needs no API keys.
[`test_benchmark.py`](../tests/test_benchmark.py) checks that every algorithm returns valid,
capped routes; that Held-Karp is optimal at K = 1; that scoring is recomputed from the matrices;
the OR-Tools production defaults; and the runner on the demo scenario.
[`test_planning.py`](../tests/test_planning.py) covers goals → weights → cost matrix, priority
stops, the constrained OR-Tools model, battery reserves, breakdown re-plans, truck limits, and
the chat gates in front of them, on the saved Anaheim scenario.
[`conftest.py`](../tests/conftest.py) puts the repo root on the import path.

---

## `route-optimizer-frontend/` (React)

Created with Create React App and styled with Tailwind. The entry chain is `src/index.js` → `App.js` → `RouteOptimizer.jsx`.

| File | Does |
|---|---|
| [`src/RouteOptimizer.jsx`](../route-optimizer-frontend/src/RouteOptimizer.jsx) | **The main page.** Holds all form state: [`vehicles`](../route-optimizer-frontend/src/RouteOptimizer.jsx#L47) (default 13,000 lbs, 4 packs, 80 kWh), [`energyParams`](../route-optimizer-frontend/src/RouteOptimizer.jsx#L54) (20 °C, 30 min unload, HVAC on), depot, stops and solver. [`validateAddresses`](../route-optimizer-frontend/src/RouteOptimizer.jsx#L189) geocodes; [`optimizeRoute`](../route-optimizer-frontend/src/RouteOptimizer.jsx#L272) builds the `/api/optimize` request; [`applyChatChange`](../route-optimizer-frontend/src/RouteOptimizer.jsx#L440) applies chat changes; `handleChatTurn` re-solves after a chat turn (`runChatResolve`) or simulates a breakdown (`runBreakdown`), and every solve is kept as a plan version for the comparison. Renders the result cards, comparison table, map, charts and stop lists. Has the Solver and Benchmark tabs |
| [`src/AIChatbot.jsx`](../route-optimizer-frontend/src/AIChatbot.jsx) | Floating chat panel. Sends `{message, history, form_state, pending}` to `/api/chat`, applies the returned changes, hands the turn to the page (`onTurn`) and shows the measured effect. Limits and goal-weight cards, example prompts |
| [`src/GoalsPanel.jsx`](../route-optimizer-frontend/src/GoalsPanel.jsx) | Sidebar Optimization Goals: a level per goal, the weight mix, battery reserve, priority stops |
| [`src/PlanComparison.jsx`](../route-optimizer-frontend/src/PlanComparison.jsx) | **What changed**: two plan versions side by side, with settings, metrics and stop orders |
| [`src/planDiff.js`](../route-optimizer-frontend/src/planDiff.js) | Pure functions behind the comparison: a plan's metrics, moved / added stops, setting changes, the chat's impact text, where a battery runs out |
| [`src/goals.js`](../route-optimizer-frontend/src/goals.js) | Goal levels and weights (mirrors the backend table) |
| [`src/RouteMap.jsx`](../route-optimizer-frontend/src/RouteMap.jsx) | Google Maps view of the routes (Directions renderer, one colour per vehicle). Stops are numbered by delivery order on the truck that serves them, in its colour ([`stopSequence`](../route-optimizer-frontend/src/RouteMap.jsx)); a marker shows where a truck's battery runs out |
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
| `r4_test.py` / `r4_test_results*.txt` | R4 test set: 50 bilingual weight / traffic / style requests, parameters, traceability and metric direction |
| `prompt_demo.py` / `prompt_demo_results.txt` | Ten example prompts with full replies |
| `route_optimizer.jsx` | **Legacy.** A standalone React prototype from the first commit that solved the TSP in the browser. Nothing imports it; the live app is `route-optimizer-frontend/` |
| `google_maps_api_guide.md` | Reference notes on the Geocoding, Distance Matrix and Directions APIs |
| `my_addresses.txt`, `test_stops.txt`, `test_stops_wide.txt` | Sample stop lists, one address per line |
| `.env` (not committed) | `GOOGLE_MAPS_API_KEY`, `OPENAI_API_KEY` or `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` (chat model endpoint, e.g. the U-M GPT Toolkit) |
| `fleet_memory.json` (not committed) | Driver memory, created at runtime |
