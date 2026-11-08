"""
R1 verification analysis: apply pass criteria C1-C5 and the replication checks
to the raw CSVs written by benchmark.r1_test, and write the verdict.

This implements Section 10 of the R1 verification method.

Usage (from the repo root):
    python -m benchmark.r1_analyze results/r1/
    python -m benchmark.r1_analyze results/r1/ --ref raw_ref.csv --repeat repeat_ref.csv --rep raw_rep.csv

Inputs (inside the results folder; names can be overridden):
    raw_ref.csv     main 48-instance run, reference machine          (required)
    repeat_ref.csv  Set A, OR-Tools, 3 repeats, reference machine    (replication check 1)
    raw_rep.csv     Set A, OR-Tools, second machine                  (replication check 2)

Outputs (same folder):
    summary_by_instance.csv, summary_by_k.csv, verdict.txt

Definitions:
    Improvement over baseline b:  I_b = (J_b - J_OR) / J_b * 100
    Best heuristic H:              lowest feasible objective among K-means + 2-opt,
                                   Sweep + 2-opt and Clarke-Wright on that instance
"""

import argparse
import csv
import math
import os
import sys
from collections import defaultdict

try:
    import numpy as np
    from scipy.stats import wilcoxon
except ImportError:
    sys.exit("r1_analyze needs numpy and scipy:  pip install numpy scipy")

# ---- Pass thresholds (Section 3) ------------------------------------------
EXPECTED_INSTANCES = 48
C2_MAX_SOLVE_MS = 300_000          # 300 s per instance
C3_MEAN_MIN, C3_EACH_MIN = 20.0, 10.0
C4_MEAN_MIN, C4_EACH_GT = 10.0, 0.0
C5_EACH_MIN, C5_MEAN_MIN, C5_ALPHA = 0.0, 1.0, 0.05
REPEAT_SPREAD_MAX_PCT = 0.5
CROSS_MACHINE_MAX_PCT = 1.0
BOOTSTRAP_RESAMPLES, BOOTSTRAP_SEED = 10_000, 0

NAIVE = "rr_nn"
HEURISTICS = ("kmeans_2opt", "sweep_2opt", "clarke_wright")
OR = "ortools"
FIXED = "fixed_order"
# A heuristic within this relative tolerance of OR-Tools counts as a tie, so
# floating-point noise in summed arc costs cannot decide a pass/fail.
TIE_REL_TOL = 1e-9


def read_rows(path: str) -> list:
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def fnum(v):
    return float(v) if v not in (None, "", "None") else None


def is_true(v) -> bool:
    return str(v).strip().lower() == "true"


def improvement(baseline, ortools):
    if baseline is None or ortools is None or baseline == 0:
        return None
    return (baseline - ortools) / baseline * 100


def by_instance(rows: list, pass_name: str = "free", repeat: str = "1") -> dict:
    """instance_id -> {algorithm: row} for one pass and repeat."""
    out = defaultdict(dict)
    for r in rows:
        if r["pass"] == pass_name and r["repeat"] == repeat:
            out[r["instance_id"]][r["algorithm"]] = r
    return out


# =============================================================================
# Per-instance table
# =============================================================================

def instance_table(ref_rows: list) -> tuple:
    free = by_instance(ref_rows, "free", "1")
    fixed = by_instance(ref_rows, "fixed", "1")
    table, notes = [], []
    for iid in sorted(free, key=lambda s: (s[0], s)):
        algos = free[iid]
        o = algos.get(OR)
        if o is None:
            notes.append(f"{iid}: no OR-Tools row (counted infeasible under C1)")
        meta = o or next(iter(algos.values()))
        j_or = fnum(o["objective_cost"]) if o else None
        or_feasible = bool(o) and is_true(o["feasible"]) and not o.get("error")

        naive = algos.get(NAIVE)
        j_naive = fnum(naive["objective_cost"]) if naive and not naive.get("error") else None
        if j_naive is None:
            notes.append(f"{iid}: naive baseline ({NAIVE}) missing or failed")

        fx = fixed.get(iid, {}).get(FIXED)
        j_fixed = fnum(fx["objective_cost"]) if fx and not fx.get("error") else None
        if j_fixed is None:
            notes.append(f"{iid}: fixed-order row missing or failed")

        cands = {}
        for h in HEURISTICS:
            r = algos.get(h)
            if r is None:
                notes.append(f"{iid}: {h} not run; excluded from best heuristic")
            elif r.get("error"):
                notes.append(f"{iid}: {h} failed ({r['error']}); excluded from best heuristic")
            elif not is_true(r["feasible"]):
                notes.append(f"{iid}: {h} infeasible ({r['issues']}); excluded from best heuristic")
            else:
                cands[h] = fnum(r["objective_cost"])
        best_h = min(cands, key=cands.get) if cands else None
        j_h = cands.get(best_h)
        if best_h is None:
            notes.append(f"{iid}: no feasible classical heuristic; C5 cannot be evaluated here")

        i_h = improvement(j_h, j_or)
        if i_h is not None and j_h is not None and abs(j_h - j_or) <= TIE_REL_TOL * j_h:
            i_h = 0.0
        table.append({
            "instance_id": iid,
            "set": meta["set"],
            "base_scenario": meta["base_scenario"],
            "seed": meta["seed"],
            "num_stops": int(meta["num_stops"]),
            "k": int(meta["k"]),
            "or_feasible": or_feasible,
            "or_solve_s": round(fnum(o["solve_time_ms"]) / 1000, 2) if o and o["solve_time_ms"] else None,
            "J_or": j_or,
            "J_naive": j_naive,
            "J_fixed": j_fixed,
            "best_heuristic": best_h,
            "J_best_heuristic": j_h,
            "I_naive_pct": improvement(j_naive, j_or),
            "I_fixed_pct": improvement(j_fixed, j_or),
            "I_heuristic_pct": i_h,
            "or_distance_mi": fnum(o["total_distance_mi"]) if o else None,
            "or_drive_time_min": fnum(o["total_drive_time_min"]) if o else None,
            "or_energy_kwh": fnum(o["total_energy_kwh"]) if o else None,
        })
    return table, notes


# =============================================================================
# Criteria
# =============================================================================

def vals(table, key):
    return [t[key] for t in table if t[key] is not None]


def bootstrap_ci(x: list):
    if len(x) < 2:
        return None
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    arr = np.asarray(x, dtype=float)
    means = rng.choice(arr, size=(BOOTSTRAP_RESAMPLES, len(arr)), replace=True).mean(axis=1)
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def evaluate(table: list) -> dict:
    n = len(table)
    res = {"n": n}

    feasible = sum(t["or_feasible"] for t in table)
    res["C1"] = {"pass": n == EXPECTED_INSTANCES and feasible == n,
                 "detail": f"{feasible} of {n} OR-Tools plans feasible (need {EXPECTED_INSTANCES} of {EXPECTED_INSTANCES})"}

    solve = vals(table, "or_solve_s")
    max_s = max(solve) if solve else None
    res["C2"] = {"pass": bool(solve) and len(solve) == n and max_s * 1000 <= C2_MAX_SOLVE_MS,
                 "detail": f"max solve time {max_s:.1f} s (limit {C2_MAX_SOLVE_MS / 1000:.0f} s)" if solve
                 else "no solve times"}

    def mean_min(key):
        x = vals(table, key)
        return (sum(x) / len(x), min(x), len(x)) if x else (None, None, 0)

    m3, lo3, k3 = mean_min("I_naive_pct")
    res["C3"] = {"pass": k3 == n and m3 >= C3_MEAN_MIN and lo3 >= C3_EACH_MIN,
                 "detail": (f"mean {m3:.2f}%, min {lo3:.2f}% over {k3} instances "
                            f"(need mean >= {C3_MEAN_MIN:g}%, each >= {C3_EACH_MIN:g}%)") if k3 else "no data"}

    m4, lo4, k4 = mean_min("I_fixed_pct")
    res["C4"] = {"pass": k4 == n and m4 >= C4_MEAN_MIN and lo4 > C4_EACH_GT,
                 "detail": (f"mean {m4:.2f}%, min {lo4:.2f}% over {k4} instances "
                            f"(need mean >= {C4_MEAN_MIN:g}%, each > {C4_EACH_GT:g}%)") if k4 else "no data"}

    pairs = [(t["J_best_heuristic"], t["J_or"]) for t in table
             if t["J_best_heuristic"] is not None and t["J_or"] is not None]
    m5, lo5, k5 = mean_min("I_heuristic_pct")
    ih = vals(table, "I_heuristic_pct")
    nonzero = sum(1 for x in ih if x != 0)
    if nonzero:
        jh = np.array([a for a, _ in pairs])
        jo = np.array([b for _, b in pairs])
        # zero differences (ties) are dropped, scipy's default "wilcox" zero_method
        diffs = jh - jo
        diffs[np.abs(diffs) <= TIE_REL_TOL * jh] = 0.0
        stat = wilcoxon(diffs, alternative="greater", zero_method="wilcox")
        p = float(stat.pvalue)
        w_plus = float(stat.statistic)
    else:
        p, w_plus = None, None
    ci = bootstrap_ci(ih)
    c5_ok = (k5 == n and lo5 >= C5_EACH_MIN and m5 >= C5_MEAN_MIN and p is not None and p < C5_ALPHA)
    res["C5"] = {
        "pass": c5_ok,
        "detail": (f"min {lo5:.2f}%, mean {m5:.2f}% over {k5} instances; Wilcoxon one-sided "
                   f"W+ = {w_plus:g}, n non-tied = {nonzero}, p = {p:.4g} "
                   f"(need each >= {C5_EACH_MIN:g}%, mean >= {C5_MEAN_MIN:g}%, p < {C5_ALPHA})"
                   if p is not None else
                   f"mean {m5}%; all differences are ties, Wilcoxon cannot be computed") if k5 else "no data",
        "ci": ci,
    }
    return res


def replication(ref_rows: list, repeat_rows, rep_rows) -> dict:
    ref = {iid: fnum(a[OR]["objective_cost"]) for iid, a in by_instance(ref_rows).items() if OR in a}
    out = {}

    if repeat_rows is None:
        out["repeat"] = {"pass": False, "detail": "NOT RUN (repeat file missing)", "rows": []}
    else:
        runs = defaultdict(list)
        for r in repeat_rows:
            if r["pass"] == "free" and r["algorithm"] == OR and r["objective_cost"]:
                runs[r["instance_id"]].append(fnum(r["objective_cost"]))
        rows, ok = [], bool(runs)
        for iid, js in sorted(runs.items()):
            spread = (max(js) - min(js)) / min(js) * 100
            good = len(js) >= 3 and spread <= REPEAT_SPREAD_MAX_PCT
            ok &= good
            rows.append((iid, len(js), spread, good))
        worst = max((r[2] for r in rows), default=None)
        out["repeat"] = {"pass": ok and len(runs) == 8,
                         "detail": (f"{len(runs)} Set A instances, worst spread {worst:.3f}% "
                                    f"(limit {REPEAT_SPREAD_MAX_PCT}%, 3 repeats each, 8 instances)")
                         if rows else "no OR-Tools rows in repeat file",
                         "rows": rows}

    if rep_rows is None:
        out["cross"] = {"pass": False, "detail": "NOT RUN (second-machine file missing)", "rows": []}
    else:
        rep = {iid: fnum(a[OR]["objective_cost"]) for iid, a in by_instance(rep_rows).items() if OR in a}
        rows, ok = [], bool(rep)
        for iid, j in sorted(rep.items()):
            if iid not in ref:
                rows.append((iid, None, j, None, False))
                ok = False
                continue
            diff = abs(j - ref[iid]) / ref[iid] * 100
            good = diff <= CROSS_MACHINE_MAX_PCT
            ok &= good
            rows.append((iid, ref[iid], j, diff, good))
        worst = max((r[3] for r in rows if r[3] is not None), default=None)
        machine = rep_rows[0]["machine"] if rep_rows else "?"
        missing = [r[0] for r in rows if r[1] is None]
        detail = (f"{len(rep)} Set A instances on '{machine}', worst difference {worst:.3f}% "
                  f"(limit {CROSS_MACHINE_MAX_PCT}%, 8 instances)") if worst is not None else "no comparable OR-Tools rows"
        if missing:
            detail += f"; not in the reference run: {', '.join(missing)}"
        out["cross"] = {"pass": ok and len(rep) == 8, "detail": detail, "rows": rows}
    return out


# =============================================================================
# Output
# =============================================================================

def write_csv(path, rows, fields):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items() if k in fields})


def summary_by_k(table):
    out = []
    for k in sorted({t["k"] for t in table}):
        g = [t for t in table if t["k"] == k]

        def mean(key):
            x = vals(g, key)
            return sum(x) / len(x) if x else None

        def lo(key):
            x = vals(g, key)
            return min(x) if x else None

        out.append({
            "k": k, "instances": len(g), "or_feasible": sum(t["or_feasible"] for t in g),
            "max_solve_s": max(vals(g, "or_solve_s"), default=None),
            "mean_I_naive_pct": mean("I_naive_pct"), "min_I_naive_pct": lo("I_naive_pct"),
            "mean_I_fixed_pct": mean("I_fixed_pct"), "min_I_fixed_pct": lo("I_fixed_pct"),
            "mean_I_heuristic_pct": mean("I_heuristic_pct"), "min_I_heuristic_pct": lo("I_heuristic_pct"),
            "or_wins_vs_heuristic": sum(1 for t in g if (t["I_heuristic_pct"] or 0) > 0),
            "ties_vs_heuristic": sum(1 for t in g if t["I_heuristic_pct"] == 0),
        })
    return out


def verdict_text(res, rep, notes, ref_rows, folder) -> str:
    def mark(ok):
        return "PASS" if ok else "FAIL"

    machines = sorted({r["machine"] for r in ref_rows})
    commits = sorted({r["commit"] for r in ref_rows})
    lines = [
        "R1 VERIFICATION VERDICT -- Multi-Objective Fleet Routing with Interchangeable Stops",
        "=" * 82,
        f"Results folder: {os.path.abspath(folder)}",
        f"Reference machine(s): {', '.join(machines)}   commit(s): {', '.join(commits)}",
        f"Instances analysed: {res['n']} (expected {EXPECTED_INSTANCES})",
        "",
        f"C1 Feasibility ............... {mark(res['C1']['pass'])}  {res['C1']['detail']}",
        f"C2 Runtime ................... {mark(res['C2']['pass'])}  {res['C2']['detail']}",
        f"C3 Gain vs naive dispatch .... {mark(res['C3']['pass'])}  {res['C3']['detail']}",
        f"C4 Gain vs fixed stop order .. {mark(res['C4']['pass'])}  {res['C4']['detail']}",
        f"C5 Gain vs best heuristic .... {mark(res['C5']['pass'])}  {res['C5']['detail']}",
    ]
    if res["C5"]["ci"]:
        lo, hi = res["C5"]["ci"]
        lines.append(f"   context: 95% bootstrap CI of mean I_H = [{lo:.2f}%, {hi:.2f}%] "
                     f"({BOOTSTRAP_RESAMPLES} resamples, seed {BOOTSTRAP_SEED})")
    lines += [
        f"Replication: repeats ......... {mark(rep['repeat']['pass'])}  {rep['repeat']['detail']}",
        f"Replication: second machine .. {mark(rep['cross']['pass'])}  {rep['cross']['detail']}",
        "",
    ]
    all_ok = all(res[c]["pass"] for c in ("C1", "C2", "C3", "C4", "C5")) and rep["repeat"]["pass"] and rep["cross"]["pass"]
    lines.append(f"OVERALL: R1 {'PASSED' if all_ok else 'NOT PASSED'}")
    if not all_ok:
        failed = [c for c in ("C1", "C2", "C3", "C4", "C5") if not res[c]["pass"]]
        failed += [n for n, key in (("repeat check", "repeat"), ("second-machine check", "cross")) if not rep[key]["pass"]]
        lines.append(f"  not met: {', '.join(failed)}")
    if notes:
        lines += ["", "Notes and exclusions:"] + [f"  - {n}" for n in notes]
    if any(r["attempt"] == "2" for r in ref_rows):
        reruns = sorted({r["instance_id"] for r in ref_rows if r["attempt"] == "2"})
        lines += ["", f"Instances rerun after an algorithm error: {', '.join(reruns)}"]
    return "\n".join(lines) + "\n"


def main():
    p = argparse.ArgumentParser(description="R1 verification analysis",
                                formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    p.add_argument("folder", help="Results folder, e.g. results/r1/")
    p.add_argument("--ref", default="raw_ref.csv")
    p.add_argument("--repeat", default="repeat_ref.csv")
    p.add_argument("--rep", default="raw_rep.csv")
    args = p.parse_args()

    def path(name):
        return name if os.path.isabs(name) else os.path.join(args.folder, name)

    if not os.path.exists(path(args.ref)):
        sys.exit(f"Missing {path(args.ref)}: run benchmark.r1_test first")
    ref_rows = read_rows(path(args.ref))
    repeat_rows = read_rows(path(args.repeat)) if os.path.exists(path(args.repeat)) else None
    rep_rows = read_rows(path(args.rep)) if os.path.exists(path(args.rep)) else None

    table, notes = instance_table(ref_rows)
    res = evaluate(table)
    rep = replication(ref_rows, repeat_rows, rep_rows)

    write_csv(os.path.join(args.folder, "summary_by_instance.csv"), table, list(table[0].keys()))
    by_k = summary_by_k(table)
    write_csv(os.path.join(args.folder, "summary_by_k.csv"), by_k, list(by_k[0].keys()))
    text = verdict_text(res, rep, notes, ref_rows, args.folder)
    with open(os.path.join(args.folder, "verdict.txt"), "w") as f:
        f.write(text)
    print(text)
    print(f"Wrote summary_by_instance.csv, summary_by_k.csv and verdict.txt in {args.folder}")


if __name__ == "__main__":
    main()
