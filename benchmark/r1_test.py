"""
R1 verification test: build the 48 test instances, run every algorithm on each,
and write one raw CSV row per instance x pass x algorithm x repeat.

This implements Section 8 (steps 8-10) of the R1 verification method. Analyse
the output with benchmark.r1_analyze.

Usage (from the repo root):
    # Main test, reference machine (48 instances, ~25-35 min)
    python -m benchmark.r1_test --set all --out results/r1/raw_ref.csv --machine laptopA

    # Repeatability: Set A, OR-Tools only, 3 repeats (~12 min)
    python -m benchmark.r1_test --set A --repeats 3 --algorithms ortools --out results/r1/repeat_ref.csv --machine laptopA

    # Replication, second machine: Set A, OR-Tools only (~4 min)
    python -m benchmark.r1_test --set A --algorithms ortools --out results/r1/raw_rep.csv --machine laptopB

    # Quick smoke test (2 instances, 5 s OR-Tools limit) -- NOT a valid test run
    python -m benchmark.r1_test --set B --limit 2 --ortools-time-limit 5 --out results/r1/smoke.csv

Test instances (Section 7 of the method):
    Set A  case1_plymouth_mi and case3_piedmont_sc, each at K = 2, 3, 4, 5      (8)
    Set B  seeds 0-39. For seed s: base = case1 if s is even, case3 if odd;
           rng = random.Random(s); stops = rng.randint(10, min(15, base stops));
           stop indices = sorted(rng.sample(1..base stops, stops)); depot kept;
           K = 2 + (s mod 4)                                                    (40)

Passes per instance:
    free   the selected algorithms with stops free to move between trucks and reorder
    fixed  the fixed stop order (benchmark.fixed_plan), scored once, never solved

Raw files are never overwritten: if --out exists, a date-time suffix is added.
"""

import argparse
import csv
import json
import os
import platform
import random
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime

from . import runner
from .runner import run_benchmark

OBJECTIVE = {"mode": "blend", "weights": {"distance": 25, "time": 25, "energy": 50}}
SET_A_SCENARIOS = ["case1_plymouth_mi", "case3_piedmont_sc"]
SET_A_FLEET_SIZES = [2, 3, 4, 5]
SET_B_SEEDS = range(0, 40)
SET_B_BASES = ("case1_plymouth_mi", "case3_piedmont_sc")  # even seed -> first, odd -> second
SET_B_MIN_STOPS, SET_B_MAX_STOPS = 10, 15
DEFAULT_ALGORITHMS = ["ortools", "rr_nn", "kmeans_2opt", "sweep_2opt", "clarke_wright"]

FIELDS = [
    "machine", "commit", "run_started", "instance_id", "set", "base_scenario", "seed", "num_stops", "k",
    "pass", "repeat", "algorithm", "objective_cost", "total_distance_mi", "total_drive_time_min",
    "total_energy_kwh", "makespan_min", "vehicles_used", "max_stops_per_truck", "stop_cap",
    "solve_time_ms", "feasible", "issues", "routes", "error", "attempt",
]


# =============================================================================
# Instances
# =============================================================================

def build_instances(which: str) -> list:
    """Return the instance list: dicts with id, set, base, seed, k and the scenario dict."""
    instances = []
    if which in ("all", "A"):
        for base in SET_A_SCENARIOS:
            sc = runner.load_scenario(base)
            for k in SET_A_FLEET_SIZES:
                instances.append({"id": f"A_{base}_k{k}", "set": "A", "base": base, "seed": "",
                                  "k": k, "scenario": sc})
    if which in ("all", "B"):
        bases = {b: runner.load_scenario(b) for b in SET_B_BASES}
        for seed in SET_B_SEEDS:
            base = SET_B_BASES[seed % 2]
            sc = bases[base]
            n_base = len(sc["locations"]) - 1
            rng = random.Random(seed)
            n_stops = rng.randint(SET_B_MIN_STOPS, min(SET_B_MAX_STOPS, n_base))
            idx = [0] + sorted(rng.sample(range(1, n_base + 1), n_stops))
            sub = {
                "id": f"B_s{seed:02d}",
                "name": f"R1 subset of {base}, seed {seed}",
                "locations": [sc["locations"][i] for i in idx],
                "distance_m": [[sc["distance_m"][i][j] for j in idx] for i in idx],
                "time_s": [[sc["time_s"][i][j] for j in idx] for i in idx],
                "elevation_m": [sc["elevation_m"][i] for i in idx] if sc.get("elevation_m") else None,
                "source_stop_indices": idx,
            }
            instances.append({"id": sub["id"], "set": "B", "base": base, "seed": seed,
                              "k": 2 + seed % 4, "scenario": sub})
    return instances


@contextmanager
def scenario_in_memory(scenario: dict):
    """Let run_benchmark load this scenario without writing a file into benchmark/scenarios."""
    original = runner.load_scenario

    def load(scenario_id):
        if scenario_id == scenario["id"]:
            return scenario
        return original(scenario_id)

    runner.load_scenario = load
    try:
        yield
    finally:
        runner.load_scenario = original


# =============================================================================
# Machine record
# =============================================================================

def git_commit() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10)
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:
        pass
    return "unknown"


def total_ram_gb():
    try:
        import psutil  # optional
        return round(psutil.virtual_memory().total / 1e9, 1)
    except Exception:
        pass
    try:
        if hasattr(os, "sysconf") and "SC_PHYS_PAGES" in os.sysconf_names:
            return round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1e9, 1)
    except Exception:
        pass
    return None  # fill in by hand


def cpu_model() -> str:
    try:
        if sys.platform.startswith("linux"):
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if line.startswith("model name"):
                        return line.split(":", 1)[1].strip()
        if sys.platform == "darwin":
            out = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True)
            if out.stdout.strip():
                return out.stdout.strip()
    except Exception:
        pass
    return platform.processor() or "unknown (fill in by hand)"


def write_machine_file(out_dir: str, machine: str, commit: str) -> str:
    try:
        import ortools
        ortools_version = ortools.__version__
    except Exception:
        ortools_version = "unknown"
    record = {
        "machine": machine,
        "recorded_at": datetime.now().isoformat(timespec="seconds"),
        "hostname": socket.gethostname(),
        "os": platform.platform(),
        "cpu_model": cpu_model(),
        "cpu_logical_cores": os.cpu_count(),
        "ram_gb": total_ram_gb(),
        "python": platform.python_version(),
        "ortools": ortools_version,
        "commit": commit,
    }
    path = os.path.join(out_dir, f"machine_{machine}.json")
    with open(path, "w") as f:
        json.dump(record, f, indent=2)
    return path


# =============================================================================
# Running
# =============================================================================

def row_values(r: dict) -> dict:
    m = r.get("metrics") or {}
    return {
        "algorithm": r["algorithm"],
        "objective_cost": m.get("objective_cost"),
        "total_distance_mi": m.get("total_distance_mi"),
        "total_drive_time_min": m.get("total_drive_time_min"),
        "total_energy_kwh": m.get("total_energy_kwh"),
        "makespan_min": m.get("makespan_min"),
        "vehicles_used": m.get("vehicles_used"),
        "max_stops_per_truck": m.get("max_stops_per_truck"),
        "stop_cap": m.get("stop_cap"),
        "solve_time_ms": m.get("solve_time_ms"),
        "feasible": m.get("feasible"),
        "issues": "; ".join(m.get("issues") or []),
        "routes": json.dumps([p["route"] for p in m.get("routes", [])]),
        "error": r.get("error", ""),
    }


def run_pass(inst: dict, algorithms: list, preserve: bool, time_limit) -> tuple:
    """Run one pass; on any algorithm error, rerun the pass once (Section 8, Errors)."""
    for attempt in (1, 2):
        with scenario_in_memory(inst["scenario"]):
            res = run_benchmark(inst["scenario"]["id"], algorithm_ids=algorithms, ortools_time_limit_s=time_limit,
                                objective=OBJECTIVE, fleet_sizes=[inst["k"]], preserve_stop_order=preserve)
        rows = res["rows"]
        if preserve:
            rows = [r for r in rows if r["algorithm"] == "fixed_order"]
        if not any("error" in r for r in rows) or attempt == 2:
            return rows, attempt
    return rows, 2


def unique_path(path: str) -> str:
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(path)
    new = f"{stem}_{datetime.now():%Y%m%d_%H%M%S}{ext}"
    print(f"{path} already exists; writing {new} instead (raw files are never overwritten)")
    return new


def main():
    p = argparse.ArgumentParser(description="R1 verification test runner",
                                formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    p.add_argument("--set", choices=["all", "A", "B"], default="all", help="Which instances to run")
    p.add_argument("--repeats", type=int, default=1, help="Solve each instance this many times (free pass)")
    p.add_argument("--algorithms", nargs="+", default=DEFAULT_ALGORITHMS, help="Algorithm ids for the free pass")
    p.add_argument("--out", required=True, help="Raw CSV path, e.g. results/r1/raw_ref.csv")
    p.add_argument("--machine", default=socket.gethostname(), help="Short machine name used in file names")
    p.add_argument("--commit", help="Override the git commit hash (default: git rev-parse HEAD)")
    p.add_argument("--no-fixed", action="store_true", help="Skip the fixed-stop-order pass")
    p.add_argument("--limit", type=int, help="Run only the first N instances (smoke test only)")
    p.add_argument("--ortools-time-limit", type=int,
                   help="Override the OR-Tools time limit in s (smoke test only; official runs use the default)")
    args = p.parse_args()

    if args.repeats < 1:
        p.error("--repeats must be at least 1")
    unknown = [a for a in args.algorithms if a not in runner.ALGORITHMS_BY_ID]
    if unknown:
        p.error(f"unknown algorithms: {unknown}")

    instances = build_instances(args.set)
    if args.limit:
        instances = instances[:args.limit]
    official = not args.limit and not args.ortools_time_limit
    machine = "".join(c if c.isalnum() or c in "-_" else "_" for c in args.machine)
    commit = args.commit or git_commit()

    out = unique_path(args.out)
    out_dir = os.path.dirname(os.path.abspath(out))
    os.makedirs(out_dir, exist_ok=True)
    machine_file = write_machine_file(out_dir, machine, commit)
    started = datetime.now().isoformat(timespec="seconds")

    print(f"R1 test: {len(instances)} instances, {args.repeats} repeat(s), algorithms {args.algorithms}")
    print(f"Machine record: {machine_file}   commit: {commit}")
    if not official:
        print("WARNING: --limit or --ortools-time-limit is set; this run is NOT a valid R1 test.")
    if commit == "unknown":
        print("WARNING: no git commit found; record the commit hash by hand or pass --commit.")

    t0 = time.perf_counter()
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for n, inst in enumerate(instances, 1):
            base = {
                "machine": machine, "commit": commit, "run_started": started,
                "instance_id": inst["id"], "set": inst["set"], "base_scenario": inst["base"],
                "seed": inst["seed"], "num_stops": len(inst["scenario"]["locations"]) - 1, "k": inst["k"],
            }
            for rep in range(1, args.repeats + 1):
                rows, attempt = run_pass(inst, args.algorithms, False, args.ortools_time_limit)
                for r in rows:
                    w.writerow({**base, "pass": "free", "repeat": rep, "attempt": attempt, **row_values(r)})
            if not args.no_fixed:
                rows, attempt = run_pass(inst, ["ortools"], True, args.ortools_time_limit)
                for r in rows:
                    w.writerow({**base, "pass": "fixed", "repeat": 1, "attempt": attempt, **row_values(r)})
            f.flush()
            elapsed = time.perf_counter() - t0
            eta = elapsed / n * (len(instances) - n)
            print(f"[{n:>2}/{len(instances)}] {inst['id']:<28} K={inst['k']} stops={base['num_stops']:<3} "
                  f"elapsed {elapsed / 60:5.1f} min  ETA {eta / 60:5.1f} min", flush=True)

    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
