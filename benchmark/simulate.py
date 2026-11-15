"""
Drive simulation: what happens when trucks drain their batteries faster than
the energy model predicted, and replan at every stop.

There is no live telemetry, so "actual" energy is simulated: each leg uses the
weight-aware model energy times a drain factor (e.g. 1.3 = 30% more than
planned), and the factor can jump after a truck's Nth delivery (a cold snap,
traffic, a headwind).

Each truck starts on the battery-limited OR-Tools plan (see solvers'
energy_matrices) and is simulated on its own:

  1. Drive one leg; subtract the actual energy; deliver one stop's cargo.
  2. At the stop, estimate the drain the truck has seen so far (worst of the
     running average and the last leg, so a sudden change counts at once) and
     predict the rest of the route with the upper-bound arc energies.
  3. If battery minus reserve covers that, carry on. Otherwise re-solve the
     remaining stops from here (solvers.replan_route): reorder if that is
     enough, else drop the fewest stops that make it fit. Dropped stops are
     unserved; their cargo stays on the truck.
  4. If even driving straight home does not fit, give up the remaining stops
     and drive home; the truck ends below its reserve.

The same drain is also applied to the original plan with no replanning
("blind"), and the original plan at the model's own energy is the "planned"
trace, so the three can be compared.

    python -m benchmark.simulate case1_plymouth_mi --vehicles 4 --battery-kwh 110 --drain 1.3
"""

import argparse
import math

from energy import energy_upper_bound_matrix_wh
from solvers import replan_route

from .algorithms import solve_ortools
from .energy import battery_limit, leg_energy_kwh, resolve_params, route_energy_kwh, static_energy_matrix_kwh
from .objective import build_cost_matrix, describe, parse_objective
from .problem import Problem
from .runner import load_scenario

DEFAULT_DRAIN_FACTOR = 1.3


def parse_drain(spec: dict = None) -> dict:
    """Validate {"factor": 1.3, "event": {"after_stop": 3, "factor": 1.6} | None}."""
    spec = spec or {}
    try:
        factor = float(spec.get("factor", DEFAULT_DRAIN_FACTOR))
    except (TypeError, ValueError):
        raise ValueError("Drain factor must be a number")
    if factor <= 0:
        raise ValueError("Drain factor must be above 0")
    event = spec.get("event")
    if event:
        try:
            event = {"after_stop": int(event["after_stop"]), "factor": float(event["factor"])}
        except (KeyError, TypeError, ValueError):
            raise ValueError("Drain event needs a whole-number after_stop and a numeric factor")
        if event["after_stop"] < 0 or event["factor"] <= 0:
            raise ValueError("Drain event needs after_stop >= 0 and factor above 0")
    return {"factor": factor, "event": event or None}


def _drain_at(drain: dict, deliveries: int) -> float:
    """Drain factor on the leg after `deliveries` stops have been made."""
    event = drain["event"]
    return event["factor"] if event and deliveries >= event["after_stop"] else drain["factor"]


class _Context:
    """Scenario data shared by every truck in one simulation."""

    def __init__(self, scenario, params, cost):
        self.locations = scenario["locations"]
        self.dist = scenario["distance_m"]
        self.time = scenario["time_s"]
        self.elevation_m = scenario.get("elevation_m")
        self.params = params
        self.cost = cost
        self.battery_kwh = params["battery_kwh"]
        self.reserve_kwh = params["battery_kwh"] * params["battery_reserve_pct"] / 100

    def soc_pct(self, battery_kwh):
        return round(battery_kwh / self.battery_kwh * 100, 1)

    def point(self, node, battery_kwh):
        loc = self.locations[node]
        return {"node": node, "name": loc.get("name") or loc["address"], "soc_pct": self.soc_pct(battery_kwh)}


def _drive(ctx, plan, drain, replan, replan_time_limit_s=None):
    """
    Drive one truck's planned route [0, s1, ..., sn, 0].

    replan=False just follows the plan (the "planned" and "blind" traces).
    """
    p = ctx.params
    base = p["base_weight_lbs"]
    n = len(plan) - 2
    per_stop = max(0.0, p["loaded_weight_lbs"] - base) / n if n else 0.0

    node, battery = 0, ctx.battery_kwh
    remaining = list(plan[1:-1])
    served, dropped, events = [], [], []
    trace = [ctx.point(0, battery)]
    planned_sum = actual_sum = 0.0
    last_ratio = None
    ran_out_at = None

    while True:
        nxt = remaining.pop(0) if remaining else 0
        weight = base + per_stop * (n - len(served))  # undelivered cargo (dropped stops' too) stays on
        planned = leg_energy_kwh(ctx.dist, ctx.time, ctx.elevation_m, node, nxt, weight, p)
        factor = _drain_at(drain, len(served))
        actual = planned * factor
        battery -= actual
        planned_sum += planned
        actual_sum += actual
        if planned > 0:
            last_ratio = factor
        if battery < 0 and ran_out_at is None:
            ran_out_at = nxt
        node = nxt
        trace.append(ctx.point(node, battery))
        if node == 0:
            break
        served.append(node)
        if replan:
            event = _check(ctx, node, battery, remaining, dropped, served, n, per_stop,
                           planned_sum, actual_sum, last_ratio, replan_time_limit_s)
            remaining = list(event["remaining_after"])  # the log keeps its own copy
            dropped.extend(event["dropped"])
            events.append(event)

    socs = [t["soc_pct"] for t in trace]
    return {
        "actual_route": [0, *served, 0],
        "served": served,
        "dropped": dropped,
        "energy_kwh": round(ctx.battery_kwh - battery, 3),
        "final_soc_pct": socs[-1],
        "min_soc_pct": min(socs),
        "below_reserve": min(socs) < p["battery_reserve_pct"],
        "ran_out_at": ran_out_at,
        "soc_trace": trace,
        "events": events,
    }


def _check(ctx, node, battery, remaining, dropped, served, n, per_stop,
           planned_sum, actual_sum, last_ratio, replan_time_limit_s):
    """At a stop: does the rest of the route still fit above the reserve? Replan if not."""
    p = ctx.params
    ratio = actual_sum / planned_sum if planned_sum > 0 else 1.0
    estimate = max(ratio, last_ratio or ratio)
    # Upper-bound arcs at today's weights: loaded now, and empty-but-for-dropped-cargo on the way home
    weight_now = p["base_weight_lbs"] + per_stop * (n - len(served))
    weight_home = p["base_weight_lbs"] + per_stop * len(dropped)
    bound = energy_upper_bound_matrix_wh(
        ctx.dist, ctx.time, ctx.elevation_m,
        loaded_weight_lbs=weight_now, base_weight_lbs=weight_home,
        temperature_c=p["temperature_c"], has_climate_control=p["has_climate_control"],
    )
    scaled = [[math.ceil(v * estimate) for v in row] for row in bound]
    path = [node, *remaining, 0]
    need_wh = sum(scaled[path[i]][path[i + 1]] for i in range(len(path) - 1))
    budget_wh = math.floor((battery - ctx.reserve_kwh) * 1000)

    newly_dropped = []
    if need_wh <= budget_wh:
        action, after = "continue", list(remaining)
    elif scaled[node][0] > budget_wh:
        action, after, newly_dropped = "return_to_depot", [], list(remaining)
    else:
        result = replan_route(ctx.cost, node, 0, remaining, scaled, budget_wh, time_limit_s=replan_time_limit_s)
        after, newly_dropped = result["route"][1:-1], result["dropped"]
        action = "drop" if newly_dropped else "reorder"

    return {
        **ctx.point(node, battery),
        "drain_estimate": round(estimate, 3),
        "predicted_need_kwh": round(need_wh / 1000, 2),
        "budget_kwh": round(budget_wh / 1000, 2),
        "action": action,
        "remaining_before": list(remaining),
        "remaining_after": after,
        "dropped": newly_dropped,
    }


def simulate(
    scenario_id: str,
    num_vehicles: int,
    energy_params: dict = None,
    drain: dict = None,
    objective: dict = None,
    ortools_time_limit_s: int = None,
    replan_time_limit_s: int = None,
) -> dict:
    """Plan a battery-limited fleet, then drive it with extra drain, replanning at every stop."""
    params = resolve_params(energy_params)
    if not params["battery_kwh"]:
        raise ValueError("Set battery_kwh above 0 to simulate battery drain")
    drain = parse_drain(drain)
    objective = parse_objective(objective)
    scenario = load_scenario(scenario_id)
    num_stops = len(scenario["locations"]) - 1
    if not 1 <= int(num_vehicles) <= num_stops:
        raise ValueError(f"Fleet size must be between 1 and {num_stops}")

    dist, time_s, elevation_m = scenario["distance_m"], scenario["time_s"], scenario.get("elevation_m")
    energy_kwh = static_energy_matrix_kwh(dist, time_s, elevation_m, params)
    cost = build_cost_matrix(objective, dist, time_s, energy_kwh)
    bound, limit = battery_limit(dist, time_s, elevation_m, params)
    problem = Problem(
        scenario_id=scenario_id, locations=scenario["locations"], dist=dist, time=time_s,
        num_vehicles=int(num_vehicles), ortools_time_limit_s=ortools_time_limit_s, cost=cost,
        elevation_m=elevation_m, energy_params=params, objective=objective,
        energy_bound_wh=bound, energy_limit_wh=limit,
    )
    plan = solve_ortools(problem)  # BatteryLimitError if even the model's own numbers don't fit

    ctx = _Context(scenario, params, cost)
    no_drain = {"factor": 1.0, "event": None}
    trucks = []
    for v, route in enumerate(plan):
        driven = _drive(ctx, route, drain, replan=True, replan_time_limit_s=replan_time_limit_s)
        blind = _drive(ctx, route, drain, replan=False)
        planned = _drive(ctx, route, no_drain, replan=False)
        trucks.append({
            "vehicle": v + 1,
            "planned_route": route,
            "planned_energy_kwh": round(route_energy_kwh(route, dist, time_s, elevation_m, params), 3),
            "replans": sum(e["action"] != "continue" for e in driven["events"]),
            **driven,
            "planned": {k: planned[k] for k in ("soc_trace", "final_soc_pct", "min_soc_pct")},
            "blind": {k: blind[k] for k in ("soc_trace", "final_soc_pct", "min_soc_pct",
                                            "below_reserve", "ran_out_at", "energy_kwh")},
        })

    return {
        "scenario": {"id": scenario["id"], "name": scenario["name"], "num_stops": num_stops,
                     "locations": scenario["locations"]},
        "num_vehicles": int(num_vehicles),
        "objective": {**objective, "label": describe(objective)},
        "energy": {**params, "has_elevation": bool(elevation_m)},
        "drain": drain,
        "trucks": trucks,
        "summary": {
            "stops": num_stops,
            "served": sum(len(t["served"]) for t in trucks),
            "dropped": sum(len(t["dropped"]) for t in trucks),
            "replans": sum(t["replans"] for t in trucks),
            "trucks_below_reserve": sum(t["below_reserve"] for t in trucks),
            "blind_trucks_below_reserve": sum(t["blind"]["below_reserve"] for t in trucks),
            "blind_trucks_ran_out": sum(t["blind"]["ran_out_at"] is not None for t in trucks),
        },
    }


def print_report(result: dict) -> None:
    s, d = result["summary"], result["drain"]
    e = result["energy"]
    drain = f"{d['factor']:g}x"
    if d["event"]:
        drain += f", {d['event']['factor']:g}x after stop {d['event']['after_stop']}"
    print(f"\n{result['scenario']['name']}  K={result['num_vehicles']}  battery {e['battery_kwh']:g} kWh, "
          f"reserve {e['battery_reserve_pct']:g}%, drain {drain}")
    for t in result["trucks"]:
        print(f"\nTruck {t['vehicle']}: planned {t['planned_route']}  ->  drove {t['actual_route']}"
              f"{'  dropped ' + str(t['dropped']) if t['dropped'] else ''}")
        print(f"  {'Stop':>5} {'SoC %':>6} {'Drain':>6} {'Need kWh':>9} {'Budget':>7}  Action")
        for ev in t["events"]:
            extra = ""
            if ev["action"] in ("reorder", "drop", "return_to_depot"):
                extra = f"  -> {ev['remaining_after']}"
                if ev["dropped"]:
                    extra += f", dropped {ev['dropped']}"
            print(f"  {ev['node']:>5} {ev['soc_pct']:>6.1f} {ev['drain_estimate']:>6.2f} "
                  f"{ev['predicted_need_kwh']:>9.1f} {ev['budget_kwh']:>7.1f}  {ev['action']}{extra}")
        b = t["blind"]
        blind = f"ran out before node {b['ran_out_at']}" if b["ran_out_at"] is not None else f"ends {b['final_soc_pct']}%"
        print(f"  Ends at {t['final_soc_pct']}% (min {t['min_soc_pct']}%).  "
              f"Planned: {t['planned']['final_soc_pct']}%.  Without replanning: {blind}"
              f"{' (below reserve)' if b['below_reserve'] else ''}")
    print(f"\nServed {s['served']}/{s['stops']} stops, dropped {s['dropped']}, {s['replans']} replan(s). "
          f"Below reserve: {s['trucks_below_reserve']} truck(s) with replanning, "
          f"{s['blind_trucks_below_reserve']} without ({s['blind_trucks_ran_out']} ran out).")


def main():
    parser = argparse.ArgumentParser(description="Simulate battery drain and replanning on a saved scenario")
    parser.add_argument("scenario_id")
    parser.add_argument("--vehicles", type=int, default=2, help="Fleet size")
    parser.add_argument("--battery-kwh", type=float, default=80.0, help="Per-truck battery")
    parser.add_argument("--reserve", type=float, help="Battery share every truck must keep (default 20)")
    parser.add_argument("--drain", type=float, default=DEFAULT_DRAIN_FACTOR,
                        help="Actual energy as a multiple of the model's (1.3 = 30%% more)")
    parser.add_argument("--event-after", type=int, metavar="N", help="Change the drain after each truck's Nth stop")
    parser.add_argument("--event-drain", type=float, help="Drain factor after the event")
    parser.add_argument("--ortools-time-limit", type=int, help="Initial plan time limit (s)")
    args = parser.parse_args()

    drain = {"factor": args.drain}
    if args.event_after is not None:
        if args.event_drain is None:
            parser.error("--event-after needs --event-drain")
        drain["event"] = {"after_stop": args.event_after, "factor": args.event_drain}
    result = simulate(args.scenario_id, args.vehicles,
                      energy_params={"battery_kwh": args.battery_kwh, "battery_reserve_pct": args.reserve},
                      drain=drain, ortools_time_limit_s=args.ortools_time_limit)
    print_report(result)


if __name__ == "__main__":
    main()
