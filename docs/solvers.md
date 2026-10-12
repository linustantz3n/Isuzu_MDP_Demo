# Solvers

All solver code is in [`solvers.py`](../solvers.py). It is plain Python with no Flask and no
Google calls. The API server imports these functions, and the benchmark reuses them.

## Conventions

- The input is a **square cost matrix**: `matrix[i][j]` is the cost of driving from location *i*
  to location *j*. Index 0 is the start or depot, which is the first location the client sends.
- The matrix holds either **distances in metres** or **durations in seconds** from Google,
  depending on the request's `mode`. It does not need to be symmetric (one-way streets are fine).
- `return_to_start=True` means a closed tour: the cost includes the drive back to node 0. The
  returned `order` does not repeat the 0 at the end.
- TSP solvers return `{"order": [0, ...], "cost": <number>, "algorithm": <str>}`.
- `solve_vrp_ortools` returns a list of closed routes: `[[0, 3, 1, 0], [0, 2, 4, 0]]`.

## The solvers

| Function | What it does | Cost / limits |
|---|---|---|
| [`solve_tsp_brute_force(matrix, return_to_start=False)`](../solvers.py#L18) | Tries every ordering | O(n!). Held-Karp hands off to it for n ≤ 8 |
| [`solve_tsp_held_karp(matrix, return_to_start=False)`](../solvers.py#L63) | Exact dynamic programming over subsets of stops. **Provably optimal** | O(n²·2ⁿ) time, O(n·2ⁿ) memory. About 125 ms at 14 stops and 1.4 s at 17 stops; each extra stop roughly doubles it |
| [`solve_tsp_nearest_neighbor(matrix, return_to_start=False)`](../solvers.py#L156) | Greedy: always drive to the closest unvisited stop (roughly what Google Maps' "optimize waypoints" does) | O(n²). Typically 10–25% above optimal |
| [`solve_tsp_2opt(matrix, initial_order=None, return_to_start=False)`](../solvers.py#L206) | Starts from nearest neighbor, then reverses route segments while the total cost drops | O(n²) per pass. Usually within 0–5% of optimal, and often exactly optimal |
| [`solve_vrp_ortools(matrix, num_vehicles, first_solution_strategy="PATH_CHEAPEST_ARC", metaheuristic="GUIDED_LOCAL_SEARCH", time_limit_s=None, solution_limit=None)`](../solvers.py#L256) | Multi-vehicle routing with Google OR-Tools | See below |

### OR-Tools VRP: what it does and does not model

**Models:**
- `num_vehicles` trucks. Every truck starts and ends at depot 0.
- **Objective:** the sum of matrix costs over all trucks, so total distance (or total time).
  It does not minimize the time until the last truck finishes (makespan).
- **One constraint:** a `StopCount` dimension that caps each truck at `ceil(stops / K)` stops.
  This spreads work across the fleet. Without it, one truck doing everything is usually the
  cheapest answer for total distance.
- **Search:** a PATH_CHEAPEST_ARC first solution, then Guided Local Search.
- **Time limit:** `max(5, min(30, 2 × n))` seconds, where n is the number of locations including
  the depot. Guided Local Search keeps searching until the limit, so a fleet solve takes that
  long: 12 s for 6 locations, 30 s for 15 or more.

**Does not model:** payload capacity (weight or volume), battery or energy, time windows, driver
hours, or differences between trucks. The solver treats all trucks as identical; per-vehicle
weight and packs only matter in the energy calculation afterwards.

**Other behaviour:**
- Trucks that end up with no stops are dropped, so `num_vehicles_used` can be lower than requested.
- The function raises an error if K is larger than the number of stops, or if no solution is found.
- Costs must be integers. Google's metres and seconds already are. A stop pair Google cannot
  route between becomes `inf`, which breaks the solver.
- In `/api/optimize`, the vehicles from the request are matched to the non-empty routes in
  order. The first route gets `vehicles[0]`'s weight, packs and driver, and so on.

---

## How `/api/optimize` decides what to run

**Code:** [`resolve_solver(requested, num_stops, num_vehicles)`](../route_optimizer_backend.py#L573), called from [`optimize_route()`](../route_optimizer_backend.py#L718).

The request's `solver` field is one of `auto` (default), `held_karp`, `2opt`, `nearest_neighbor` or `ortools_vrp`.

1. **Nearest neighbor and 2-opt always run.** They are cheap and they feed the comparison in the UI.
2. **Held-Karp runs depending on the setting:**
   - `auto` or `ortools_vrp`: only if the *predicted* runtime is ≤ 2 s. The prediction is
     [`estimate_held_karp_ms(n)`](../route_optimizer_backend.py#L381) = n²·2ⁿ × 17 ns, measured on
     one machine. With a depot this means **up to 17 stops**.
   - `held_karp` (forced): up to **21 stops** (`HK_HARD_LIMIT_N = 22` locations). Beyond that the
     request is refused and 2-opt is shown instead.
   - `2opt` or `nearest_neighbor`: never.
3. **If Held-Karp did not run**, the 2-opt route (or nearest neighbor, if that is the setting) is
   copied into `results.optimal` and marked `substituted_for: "held_karp"`. The UI's baseline card
   always reads `results.optimal`. `solver.note` explains what happened.
4. **OR-Tools runs whenever there is more than one vehicle**, whatever the `solver` setting.
   The single-vehicle results are still returned as the "one truck does everything" reference,
   using vehicle 1's weight and packs.

An explicit `algorithms` list in the request (`["held_karp", "nearest_neighbor", "2opt"]`) skips
this logic. Tuning constants are at the top of the solver section:
[`HK_SEC_PER_UNIT`, `HK_TIME_BUDGET_MS`, `HK_HARD_LIMIT_N`](../route_optimizer_backend.py#L339).

| Stops | Held-Karp estimate | `auto` runs it? | Forced `held_karp` runs it? |
|---|---|---|---|
| 8 | <1 ms | yes | yes |
| 12 | 24 ms | yes | yes |
| 14 | 125 ms | yes | yes |
| 16 | 644 ms | yes | yes |
| 17 | 1.4 s | yes | yes |
| 18 | 3.2 s | no, 2-opt | yes |
| 20 | 15.7 s | no, 2-opt | yes |
| 21 | 34.5 s | no, 2-opt | yes |
| 22 | 75 s | no, 2-opt | no, refused |
| 24 | 6 min | no, 2-opt | no, refused |

`/api/optimize` accepts at most 25 locations (depot + 24 stops).

[`recommend_solver()`](../route_optimizer_backend.py#L499) and [`solver_table()`](../route_optimizer_backend.py#L544)
build the same reasoning as text and tables for the chat assistant. They do not change what
`/api/optimize` runs; `resolve_solver` only uses `recommend_solver` for the `auto` decision.

---

## Benchmark

The [`benchmark/`](../benchmark/) package runs every algorithm on the same saved scenario for
fleet sizes K = 1..N and scores them all the same way. It answers "how much better is OR-Tools
than naive routing, and on which measures?" It only looks at distance and time, not energy.

| File | Contents |
|---|---|
| [`problem.py`](../benchmark/problem.py) | `Problem` (scenario, locations, `dist`, `time`, `num_vehicles`, `ortools_time_limit_s`) and `Solution` |
| [`algorithms.py`](../benchmark/algorithms.py) | The registry `ALGORITHMS`. Each entry is a `solve(problem)` function that returns closed routes |
| [`metrics.py`](../benchmark/metrics.py) | `score()` recomputes every metric from the scenario matrices (it never trusts what an algorithm reports); `add_gaps()`; `headline()` |
| [`runner.py`](../benchmark/runner.py) | `run_benchmark(scenario_id, max_vehicles=3, algorithm_ids=None, ortools_time_limit_s=None)`, `list_scenarios()` |
| [`snapshot.py`](../benchmark/snapshot.py) | Turns a stop list into a scenario JSON. Geocodes and fetches the matrices once, which needs a Google key |
| [`cli.py`](../benchmark/cli.py) | Command-line table and CSV export |
| `scenarios/*.json` | Saved locations and distance/time matrices. Runs are offline and repeatable |

| Algorithm id | Label | K |
|---|---|---|
| `nn` | Nearest Neighbor | 1 |
| `2opt` | 2-opt | 1 |
| `held_karp` | Held-Karp (optimal); only up to 12 stops in the benchmark | 1 |
| `rr_nn`, `rr_2opt` | Round-robin + NN / 2-opt (deal stops out in list order) | ≥ 2 |
| `kmeans_2opt` | Capacity-balanced k-means on lat/lng + 2-opt | ≥ 2 |
| `sweep_2opt` | Sweep (radial sectors around the depot) + 2-opt | any |
| `clarke_wright` | Clarke-Wright savings | any |
| `ortools_cheap` | OR-Tools construction only, no local search | any |
| `ortools` | OR-Tools production configuration | any |

Every algorithm uses the same cap of `ceil(stops / K)` stops per truck as OR-Tools.

Saved scenarios: `demo_anaheim` (5 stops), `case2_charlotte_lansing_mi` (7), `case3_piedmont_sc` (15), `case1_plymouth_mi` (19).

```bash
python -m benchmark.cli --list
python -m benchmark.cli demo_anaheim --max-vehicles 3 --csv results.csv
python -m benchmark.snapshot stops.txt my_case "My case"     # line 1 of stops.txt = depot
```

**To add an algorithm**, for example an energy-aware router: write
`solve(problem) -> [[0, ..., 0], ...]` and append an `Algorithm(...)` entry to `ALGORITHMS` in
`benchmark/algorithms.py`. It then shows up in the Benchmark tab and the CLI automatically.
