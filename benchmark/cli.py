"""
Run the benchmark from the command line.

Usage (from the repo root):
    python -m benchmark.cli demo_anaheim --max-vehicles 3
    python -m benchmark.cli demo_anaheim --max-vehicles 3 --csv results.csv
    python -m benchmark.cli --list
"""

import argparse
import csv

from .runner import list_scenarios, run_benchmark

CSV_FIELDS = [
    "total_distance_mi", "total_drive_time_min", "makespan_min", "vehicles_used",
    "max_stops_per_truck", "stop_cap", "solve_time_ms", "feasible",
    "gap_to_best_distance_pct", "gap_to_best_time_pct",
    "gap_vs_ortools_distance_pct", "gap_vs_ortools_time_pct", "gap_to_optimal_distance_pct",
]


def _fmt(v):
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.1f}"
    return str(v)


def print_table(result: dict) -> None:
    print(f"\nScenario: {result['scenario']['name']} ({result['scenario']['num_stops']} stops)")
    for k in range(1, result["max_vehicles"] + 1):
        print(f"\n=== K = {k} truck{'s' if k > 1 else ''} ===")
        print(f"{'Algorithm':32} {'Miles':>8} {'Drive min':>10} {'Makespan':>9} "
              f"{'Trucks':>6} {'Solve ms':>9} {'vs best':>8} {'vs OR-T':>8}  Feasible")
        for r in (r for r in result["rows"] if r["num_vehicles"] == k):
            if "error" in r:
                print(f"{r['label']:32} ERROR: {r['error']}")
                continue
            m = r["metrics"]
            print(f"{r['label']:32} {m['total_distance_mi']:>8.2f} {m['total_drive_time_min']:>10.1f} "
                  f"{m['makespan_min']:>9.1f} {m['vehicles_used']:>6} {m['solve_time_ms']:>9.1f} "
                  f"{_fmt(m['gap_to_best_distance_pct']):>7}% {_fmt(m['gap_vs_ortools_distance_pct']):>7}%  "
                  f"{'yes' if m['feasible'] else 'NO: ' + '; '.join(m['issues'])}")

    print("\nOR-Tools (production) savings:")
    for h in result["headline"]:
        for key, title in (("vs_naive", "naive baseline"), ("vs_best_heuristic", "best other heuristic")):
            s = h[key]
            if s:
                print(f"  K={h['num_vehicles']} vs {title} ({s['label']}): "
                      f"{s['distance_saved_pct']:.1f}% distance, {s['time_saved_pct']:.1f}% drive time")


def write_csv(result: dict, path: str) -> None:
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["scenario", "num_vehicles", "algorithm", "label", "family", *CSV_FIELDS, "error"])
        for r in result["rows"]:
            m = r.get("metrics", {})
            w.writerow([result["scenario"]["id"], r["num_vehicles"], r["algorithm"], r["label"],
                        r["family"], *[m.get(k) for k in CSV_FIELDS], r.get("error", "")])
    print(f"\nWrote {path}")


def main():
    parser = argparse.ArgumentParser(description="Run the routing algorithm benchmark")
    parser.add_argument("scenario_id", nargs="?")
    parser.add_argument("--max-vehicles", type=int, default=3)
    parser.add_argument("--algorithms", nargs="+", help="Subset of algorithm ids")
    parser.add_argument("--ortools-time-limit", type=int, help="Override OR-Tools time limit (s)")
    parser.add_argument("--csv", help="Also write results to this CSV path")
    parser.add_argument("--list", action="store_true", help="List saved scenarios")
    args = parser.parse_args()

    if args.list or not args.scenario_id:
        for s in list_scenarios():
            print(f"{s['id']:24} {s['num_stops']:>3} stops  {s['name']}")
        return

    result = run_benchmark(args.scenario_id, args.max_vehicles, args.algorithms, args.ortools_time_limit)
    print_table(result)
    if args.csv:
        write_csv(result, args.csv)


if __name__ == "__main__":
    main()
