"""
The fixed stop order: one example plan used when "preserve stop order" is on.

With the option on, the plan is locked. Every truck keeps the stops it is
assigned and visits them in the plan's order, every stop is assigned, and
nothing is added, moved or reordered. Each algorithm therefore returns the plan
unchanged (see runner.run_benchmark), which also lets the tool confirm that no
stop order gets scrambled between runs.

The example plan: the scenario's stops, taken in their listed order, split into
K consecutive groups, one per truck (truck 1 gets the first stops on the list,
truck 2 the next, and so on). Group sizes differ by at most one, so the plan
respects the same stop cap (ceil(stops / K)) the algorithms are held to.

It is deterministic: the same scenario and K always give the same plan. To use a
different example, change fixed_stop_plan; nothing else depends on how the plan
is built.
"""


def fixed_stop_plan(num_stops: int, k: int) -> list:
    """Closed routes [0, ..., 0], one per truck, covering stops 1..num_stops in order."""
    k = max(1, min(k, num_stops))
    base, extra = divmod(num_stops, k)
    routes, start = [], 1
    for truck in range(k):
        size = base + (1 if truck < extra else 0)
        routes.append([0] + list(range(start, start + size)) + [0])
        start += size
    return routes
