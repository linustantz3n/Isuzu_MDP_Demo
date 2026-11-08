"""
Run the benchmark from the command line.

Usage (from the repo root):
    python -m benchmark.cli demo_anaheim --max-vehicles 3
    python -m benchmark.cli demo_anaheim --max-vehicles 3 --csv results.csv
    python -m benchmark.cli case1_plymouth_mi --vehicles 5          # K = 5 only
    python -m benchmark.cli case1_plymouth_mi --vehicles 5 --preserve-stop-order
    python -m benchmark.cli demo_anaheim --objective energy
    python -m benchmark.cli demo_anaheim --objective blend --weights 50 25 25   # distance time energy
    python -m benchmark.cli --list
"""

import argparse
import csv

from .objective import MODES
from .runner import list_scenarios, run_benchmark

CSV_FIELDS = [
    "total_distance_mi", "total_drive_time_min", "total_energy_kwh", "kwh_per_mile", "makespan_min", "vehicles_used",
    "min_battery_remaining_pct",
    "max_stops_per_truck", "stop_cap", "solve_time_ms", "feasible",
    "gap_to_best_distance_pct", "gap_to_best_time_pct", "gap_to_best_energy_pct", "gap_to_best_objective_pct",
    "gap_vs_ortools_distance_pct", "gap_vs_ortools_time_pct", "gap_vs_ortools_energy_pct",
    "gap_vs_ortools_objective_pct", "gap_to_optimal_distance_pct", "gap_to_optimal_objective_pct",
]


def _fmt(v):
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.1f}"
    return str(v)


def print_table(result: dict) -> None:
    print(f"\nScenario: {result['scenario']['name']} ({result['scenario']['num_stops']} stops)")
    print(f"Objective: {result['objective']['label']}"
          + ("" if result["energy"]["has_elevation"] else "   (no elevation data: energy assumes flat terrain)"))
    for k in result["fleet_sizes"]:
        print(f"\n=== K = {k} truck{'s' if k > 1 else ''} ===")
        print(f"{'Algorithm':32} {'Miles':>8} {'Drive min':>10} {'kWh':>8} {'Makespan':>9} "
              f"{'Trucks':>6} {'Solve ms':>9} {'vs best':>8} {'vs OR-T':>8}  Feasible")
        for r in (r for r in result["rows"] if r["num_vehicles"] == k):
            if "error" in r:
                print(f"{r['label']:32} ERROR: {r['error']}")
                continue
            m = r["metrics"]
            print(f"{r['label']:32} {m['total_distance_mi']:>8.2f} {m['total_drive_time_min']:>10.1f} "
                  f"{m['total_energy_kwh']:>8.1f} {m['makespan_min']:>9.1f} {m['vehicles_used']:>6} {m['solve_time_ms']:>9.1f} "
                  f"{_fmt(m['gap_to_best_objective_pct']):>7}% {_fmt(m['gap_vs_ortools_objective_pct']):>7}%  "
                  f"{'yes' if m['feasible'] else 'NO: ' + '; '.join(m['issues'])}")

    print("\nOR-Tools (production) savings:")
    for h in result["headline"]:
        for key, title in (("vs_naive", "naive baseline"), ("vs_best_heuristic", "best other heuristic")):
            s = h[key]
            if s:
                print(f"  K={h['num_vehicles']} vs {title} ({s['label']}): "
                      f"{s['distance_saved_pct']:.1f}% distance, {s['time_saved_pct']:.1f}% drive time, "
                      f"{s['energy_saved_pct']:.1f}% energy")


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
    parser.add_argument("--max-vehicles", type=int, default=3, help="Run every fleet size from K = 1 to this")
    parser.add_argument("--vehicles", type=int, help="Run this fleet size only (overrides --max-vehicles)")
    parser.add_argument("--preserve-stop-order", action="store_true",
                        help="Lock every truck to the fixed stop order: all algorithms return that plan unchanged")
    parser.add_argument("--algorithms", nargs="+", help="Subset of algorithm ids")
    parser.add_argument("--ortools-time-limit", type=int, help="Override OR-Tools time limit (s)")
    parser.add_argument("--objective", choices=MODES, default="distance",
                        help="What the algorithms minimize (vs best / vs OR-T gaps follow it)")
    parser.add_argument("--weights", nargs=3, type=float, metavar=("DIST", "TIME", "ENERGY"),
                        help="Blend weights (only with --objective blend)")
    parser.add_argument("--battery-kwh", type=float,
                        help="Per-truck battery; turns on the battery limit (OR-Tools keeps every truck above the reserve)")
    parser.add_argument("--battery-reserve", type=float, metavar="PCT",
                        help="Battery share every truck must keep (default 20)")
    parser.add_argument("--csv", help="Also write results to this CSV path")
    parser.add_argument("--list", action="store_true", help="List saved scenarios")
    args = parser.parse_args()

    if args.list or not args.scenario_id:
        for s in list_scenarios():
            print(f"{s['id']:24} {s['num_stops']:>3} stops  {s['name']}")
        return

    objective = {"mode": args.objective}
    if args.weights:
        objective["weights"] = dict(zip(("distance", "time", "energy"), args.weights))
    result = run_benchmark(args.scenario_id, args.max_vehicles, args.algorithms, args.ortools_time_limit,
                           objective=objective,
                           energy_params={"battery_kwh": args.battery_kwh,
                                          "battery_reserve_pct": args.battery_reserve},
                           fleet_sizes=[args.vehicles] if args.vehicles else None,
                           preserve_stop_order=args.preserve_stop_order)
    print_table(result)
    if args.csv:
        write_csv(result, args.csv)


if __name__ == "__main__":
    main()
