# R1 Verification Test: Multi-Objective Fleet Routing

This folder contains the scripted test for requirement **R1, Multi-Objective Fleet Routing with Interchangeable Stops**. It checks that the production route optimizer (OR-Tools with Guided Local Search) builds feasible multi-truck routes that beat simpler routing methods on a weighted objective of distance, drive time and energy, within the sponsor's 5-minute runtime limit.

Two scripts run the test:

| File | What it does |
| --- | --- |
| `benchmark/r1_test.py` | Builds the 48 test instances, runs every algorithm on each, and writes one raw CSV row per instance, pass, algorithm and repeat |
| `benchmark/r1_analyze.py` | Reads the raw CSVs, applies pass criteria C1–C5 and the two replication checks, and writes the verdict |

Both scripts use the existing benchmark code (`runner.py`, `algorithms.py`, `metrics.py`, `objective.py`, `fixed_plan.py`) and the saved scenarios in `benchmark/scenarios/`. They make no API calls.

## Quick start

From the repository root:

```bash
pip install -r requirements.txt numpy scipy
pytest tests/test_benchmark.py              # must report 56 passed

python -m benchmark.r1_test --set all --out results/r1/raw_ref.csv --machine laptopA
python -m benchmark.r1_test --set A --repeats 3 --algorithms ortools --out results/r1/repeat_ref.csv --machine laptopA
python -m benchmark.r1_analyze results/r1/
```

The last command prints PASS or FAIL for each criterion and writes `verdict.txt`.

## Requirements

- Python 3.12 or later
- OR-Tools 9.8 or later
- numpy and scipy, which only `r1_analyze.py` uses (for the Wilcoxon test and the bootstrap interval)
- pytest 8.0 or later, for the pre-test check

## Full procedure

### 1. Set up (every machine)

1. Check out the commit you are testing, and note its hash: `git rev-parse HEAD`. `r1_test` also records it automatically.
2. Create a fresh environment and install dependencies: `python -m venv venv`, activate it, then `pip install -r requirements.txt numpy scipy`.
3. Plug the laptop in, turn off sleep and battery saver, and close other programs. OR-Tools stops at a wall-clock time limit, so a busy machine can give it slightly worse routes.
4. Run `pytest tests/test_benchmark.py`. Continue only if it reports **56 passed**.
5. Sanity check: run `python -m benchmark.cli case1_plymouth_mi --vehicles 3 --objective blend --weights 25 25 50`. The **OR-Tools (production)** row must show 164.28 mi and 160.6 kWh, within 1%. If it doesn't, the code or data differ from the version this test was written for.

### 2. Optional smoke test (about 1 minute)

```bash
python -m benchmark.r1_test --set B --limit 2 --ortools-time-limit 3 --out results/smoke/raw_ref.csv --machine laptopA
```

This checks that everything is installed. It prints a warning that it is **not** a valid R1 run, which is expected.

### 3. Main test (reference machine, about 25 minutes)

```bash
python -m benchmark.r1_test --set all --out results/r1/raw_ref.csv --machine laptopA
```

Each instance prints a progress line with the elapsed time and an estimate of the time remaining:

```
[12/48] B_s11                        K=5 stops=13  elapsed   5.8 min  ETA  17.4 min
```

### 4. Repeat check (reference machine, about 13 minutes)

```bash
python -m benchmark.r1_test --set A --repeats 3 --algorithms ortools --out results/r1/repeat_ref.csv --machine laptopA
```

This solves the 8 full-scenario instances 3 times each.

To run steps 3 and 4 back to back, chain them with `&&`.

### 5. Second-machine check (second laptop, about 5 minutes)

Use a teammate's laptop with a different operating system. Repeat the setup steps, then run:

```bash
python -m benchmark.r1_test --set A --algorithms ortools --out results/r1/raw_rep.csv --machine laptopB
```

Copy `raw_rep.csv` and `machine_laptopB.json` into the reference machine's `results/r1/` folder.

### 6. Analysis

```bash
python -m benchmark.r1_analyze results/r1/
```

This writes `summary_by_instance.csv`, `summary_by_k.csv` and `verdict.txt` into the same folder. If the repeat or second-machine file is missing, that check is reported as **NOT RUN**, and the overall verdict is **NOT PASSED**.

To use different file names: `--ref`, `--repeat` and `--rep`, for example `python -m benchmark.r1_analyze results/r1/ --rep raw_rep_mac.csv`.

## Test instances (48)

| Set | Source | Instances | How each is built |
| --- | --- | --- | --- |
| A: full scenarios | `case1_plymouth_mi` (19 stops), `case3_piedmont_sc` (15 stops) | 8 | Each scenario at K = 2, 3, 4 and 5 trucks |
| B: stop subsets | Seeds 0–39 | 40 | Base = Case 1 if the seed is even, Case 3 if odd. `random.Random(seed)` picks the stop count with `randint(10, 15)` and the stops with `sample(...)`. The depot is always kept. K = 2 + (seed mod 4) |

That gives 12 instances per fleet size. Set B instances are built in memory, so no files are added to `benchmark/scenarios/`. Instance IDs look like `A_case1_plymouth_mi_k3` and `B_s07`.

Case 2 (7 stops) and the Anaheim demo (5 stops) are excluded: with 5 trucks they leave only 1–2 stops per truck, so there is nothing for an optimizer to improve.

## What gets run on each instance

| Pass | Algorithms | Purpose |
| --- | --- | --- |
| `free` | `ortools` (production), `rr_nn` (round-robin + nearest neighbor), `kmeans_2opt`, `sweep_2opt`, `clarke_wright` | Stops may move between trucks and be reordered |
| `fixed` | `fixed_order` | The listed stops split in order across K trucks, scored once and never solved (`benchmark/fixed_plan.py`) |

Settings are identical for every run:

| Setting | Value |
| --- | --- |
| Objective | Blend: distance 25%, drive time 25%, energy 50% |
| OR-Tools strategy | PATH_CHEAPEST_ARC first solution + GUIDED_LOCAL_SEARCH |
| OR-Tools time limit | Code default: max(5, min(30, 2 × nodes)) s, which is 22–30 s here |
| Stop cap per truck | ceil(stops ÷ K), for every algorithm |
| Energy model | Model E regression; 13,000 lb loaded, 7,000 lb empty, 20 °C |
| Battery limit | Off (battery feasibility is tested under R2) |

## Pass criteria

R1 passes only if all five criteria hold on all 48 instances **and** both replication checks pass.

**Improvement** over a baseline b = (J_b − J_OR) ÷ J_b × 100, where J is the weighted objective and OR is OR-Tools. The **best heuristic** is the lowest feasible objective among `kmeans_2opt`, `sweep_2opt` and `clarke_wright` on that instance.

| # | Criterion | Pass threshold |
| --- | --- | --- |
| C1 | Feasibility | 48 of 48 OR-Tools plans feasible |
| C2 | Runtime | Every OR-Tools solve ≤ 300 s |
| C3 | Gain over naive dispatch (`rr_nn`) | Mean ≥ 20% and every instance ≥ 10% |
| C4 | Gain from interchangeable stops (vs `fixed_order`) | Mean ≥ 10% and every instance > 0% |
| C5 | Gain over best heuristic | Every instance ≥ 0%, mean ≥ 1%, and one-sided Wilcoxon signed-rank p < 0.05 |
| Repeat | Same machine, 3 runs | Spread (max − min) ÷ min ≤ 0.5% on each of the 8 Set A instances |
| Second machine | Different laptop and OS | \|J_rep − J_ref\| ÷ J_ref ≤ 1% on each of the 8 Set A instances |

For C5, the Wilcoxon test runs on the paired (best heuristic, OR-Tools) objectives, and instances where the two are equal are dropped (scipy's default). The script also reports a 95% bootstrap confidence interval of the mean C5 gain (10,000 resamples, seed 0), as context only. The thresholds are constants at the top of `r1_analyze.py`.

## Output files

All files go in `results/r1/`:

| File | Contents |
| --- | --- |
| `raw_ref.csv` | Main 48-instance run: 48 × (5 + 1) = 288 rows |
| `repeat_ref.csv` | Repeat check: 8 instances × 3 repeats, plus fixed-order rows |
| `raw_rep.csv` | Second-machine run |
| `machine_<name>.json` | CPU, cores, RAM, OS, Python and OR-Tools versions, and commit, written automatically. On Windows the RAM field may be blank; fill it in by hand |
| `summary_by_instance.csv` | One row per instance: objectives, improvements, best heuristic, solve time |
| `summary_by_k.csv` | Each criterion's value for K = 2, 3, 4 and 5 separately |
| `verdict.txt` | PASS or FAIL for each criterion and check, the values that decided it, and any exclusions or reruns |

`r1_test` never overwrites a raw file: if the output name already exists, it adds a date-time suffix. If a run is interrupted, delete the partial file before starting again, or the analysis will read the old one.

### Raw CSV columns

| Column | Meaning |
| --- | --- |
| `machine`, `commit`, `run_started` | `--machine` value, `git rev-parse HEAD`, start time (ISO format) |
| `instance_id`, `set`, `base_scenario`, `seed` | Which instance (seed is blank for Set A) |
| `num_stops`, `k` | Stops (excluding the depot) and fleet size |
| `pass` | `free` or `fixed` |
| `repeat`, `attempt` | Repeat number (1–3); attempt 1, or 2 if the pass was rerun after an error |
| `algorithm` | e.g. `ortools`, `clarke_wright`, `fixed_order` |
| `objective_cost` | Weighted objective (dimensionless) |
| `total_distance_mi`, `total_drive_time_min`, `total_energy_kwh`, `makespan_min` | Recomputed from the saved matrices, so no algorithm grades itself |
| `vehicles_used`, `max_stops_per_truck`, `stop_cap` | Fleet use and the stop cap |
| `solve_time_ms` | Wall-clock solve time |
| `feasible`, `issues`, `error` | Feasibility from `metrics.check_routes` and `metrics.score`, any issues, and any error text |
| `routes` | JSON list of stop-index lists, one per truck |

## Command reference

`r1_test.py`:

| Option | Default | Meaning |
| --- | --- | --- |
| `--set {all,A,B}` | `all` | Which instances to run |
| `--repeats N` | 1 | Solve each instance N times (free pass) |
| `--algorithms ...` | the five above | Algorithm IDs for the free pass |
| `--out PATH` | required | Raw CSV path |
| `--machine NAME` | hostname | Short machine name, used in the machine file name |
| `--commit HASH` | `git rev-parse HEAD` | Override the recorded commit |
| `--no-fixed` | off | Skip the fixed-order pass |
| `--limit N` | off | First N instances only. **Smoke test only** |
| `--ortools-time-limit S` | code default | Override the time limit. **Smoke test only** |

A run with `--limit` or `--ortools-time-limit` is not a valid R1 test, and the script says so.

`r1_analyze.py`: `python -m benchmark.r1_analyze FOLDER [--ref raw_ref.csv] [--repeat repeat_ref.csv] [--rep raw_rep.csv]`

## Error handling

If any algorithm raises an error, `r1_test` reruns that pass once automatically and records `attempt = 2`. If OR-Tools fails again, the instance counts as infeasible under C1. If a baseline fails again or returns an infeasible plan, it is left out of that instance's best-heuristic comparison. Every exclusion and rerun is listed in `verdict.txt`.

## Latest result

Official run on 2026-11-08, commit `017cfe3`, reference laptop (AMD Ryzen 7 PRO 8840U, 16 GB RAM, WSL2, Python 3.12.7, OR-Tools 9.15):

| Check | Result | |
| --- | --- | --- |
| C1 Feasibility | 48 of 48 | PASS |
| C2 Runtime | Max 30.0 s | PASS |
| C3 vs naive dispatch | Mean 36.4%, min 21.1% | PASS |
| C4 vs fixed stop order | Mean 27.1%, min 7.6% | PASS |
| C5 vs best heuristic | Min 0.0%, mean 1.6%, p = 3.9 × 10⁻⁸ | PASS |
| Repeat check | Spread 0.000% | PASS |
| Second-machine check | Not run yet | — |

A separate run on a cloud machine (Intel Xeon, Linux) gave identical objectives in all 288 rows.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| `r1_analyze needs numpy and scipy` | `pip install numpy scipy` |
| `unknown algorithms: [...]` | Check the IDs; they are listed in `benchmark/algorithms.py` |
| `Missing results/r1/raw_ref.csv` | Run the main test first, or pass `--ref` with your file name |
| `WARNING: no git commit found` | Run from inside the git repository, or pass `--commit <hash>` |
| Sanity check numbers differ | The code or scenario data changed; check out the tested commit |
| Second-machine check says "not in the reference run" | The second machine ran different instances; use `--set A` on both |