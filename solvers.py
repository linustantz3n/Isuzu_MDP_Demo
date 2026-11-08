"""
Pure routing solvers shared by the API server and the benchmark framework.

Every TSP solver takes a square cost matrix with node 0 as the fixed start and
returns {"order", "cost", "algorithm"}. solve_vrp_ortools returns a list of
closed depot routes ([0, ..., 0]).
"""

import math
from itertools import permutations
from ortools.constraint_solver import routing_enums_pb2
from ortools.constraint_solver import pywrapcp


class BatteryLimitError(Exception):
    """No route plan keeps every truck above its battery reserve."""

    def __init__(self, message: str, unreachable_stops: list = None):
        super().__init__(message)
        self.unreachable_stops = unreachable_stops or []

# =============================================================================
# TSP Solvers
# =============================================================================

def solve_tsp_brute_force(matrix: list, return_to_start: bool = False) -> dict:
    """
    Solve TSP by checking all permutations.
    Only practical for n <= 10.

    Returns optimal order and distance (starting from index 0).

    Args:
        matrix: Distance/time matrix
        return_to_start: If True, adds return cost to start
    """
    n = len(matrix)
    if n <= 1:
        return {"order": [0], "cost": 0, "algorithm": "brute_force"}
    if n == 2:
        cost = matrix[0][1]
        if return_to_start:
            cost += matrix[1][0]
        return {"order": [0, 1], "cost": cost, "algorithm": "brute_force"}

    # Generate all permutations of nodes 1 to n-1 (keeping 0 as start)
    other_nodes = list(range(1, n))

    best_order = None
    best_cost = float('inf')

    for perm in permutations(other_nodes):
        order = [0] + list(perm)
        cost = sum(matrix[order[i]][order[i+1]] for i in range(len(order)-1))

        # Add return cost if closed TSP
        if return_to_start:
            cost += matrix[order[-1]][order[0]]

        if cost < best_cost:
            best_cost = cost
            best_order = order

    return {
        "order": best_order,
        "cost": best_cost,
        "algorithm": "brute_force"
    }


def solve_tsp_held_karp(matrix: list, return_to_start: bool = False) -> dict:
    """
    Solve TSP using Held-Karp dynamic programming algorithm.
    Optimal solution in O(n^2 * 2^n) time.
    Practical for n <= 20.

    Args:
        matrix: Distance/time matrix
        return_to_start: If True, finds closed TSP (return to start). If False, open TSP.
    """
    n = len(matrix)
    if n <= 1:
        return {"order": [0], "cost": 0, "algorithm": "held_karp"}
    if n == 2:
        cost = matrix[0][1]
        if return_to_start:
            cost += matrix[1][0]
        return {"order": [0, 1], "cost": cost, "algorithm": "held_karp"}

    # For small n, brute force is simpler
    if n <= 8:
        result = solve_tsp_brute_force(matrix, return_to_start)
        result["algorithm"] = "held_karp (via brute_force)"
        return result
    
    INF = float('inf')
    
    # dp[mask][i] = min cost to visit all nodes in mask, ending at node i
    # mask is a bitmask where bit j is set if node j has been visited
    dp = [[INF] * n for _ in range(1 << n)]
    parent = [[-1] * n for _ in range(1 << n)]
    
    # Start at node 0
    dp[1][0] = 0  # mask=1 means only node 0 visited, ending at node 0
    
    for mask in range(1, 1 << n):
        for last in range(n):
            if not (mask & (1 << last)):
                continue
            if dp[mask][last] == INF:
                continue
            
            for next_node in range(n):
                if mask & (1 << next_node):
                    continue
                
                new_mask = mask | (1 << next_node)
                new_cost = dp[mask][last] + matrix[last][next_node]
                
                if new_cost < dp[new_mask][next_node]:
                    dp[new_mask][next_node] = new_cost
                    parent[new_mask][next_node] = last
    
    # Find best ending node
    full_mask = (1 << n) - 1
    best_cost = INF
    best_end = -1

    if return_to_start:
        # Closed TSP: must return to start (node 0)
        # Find minimum cost to visit all nodes and return to 0
        for i in range(1, n):
            cost_with_return = dp[full_mask][i] + matrix[i][0]
            if cost_with_return < best_cost:
                best_cost = cost_with_return
                best_end = i
    else:
        # Open TSP: end at best node (no return)
        for i in range(1, n):
            if dp[full_mask][i] < best_cost:
                best_cost = dp[full_mask][i]
                best_end = i
    
    # Reconstruct path
    order = []
    mask = full_mask
    current = best_end
    
    while current != -1:
        order.append(current)
        prev = parent[mask][current]
        mask ^= (1 << current)
        current = prev
    
    order.reverse()
    
    return {
        "order": order,
        "cost": best_cost,
        "algorithm": "held_karp"
    }


def solve_tsp_nearest_neighbor(matrix: list, return_to_start: bool = False) -> dict:
    """
    Greedy nearest neighbor heuristic.
    Fast but not optimal - this is similar to what Google Maps does.

    Args:
        matrix: Distance/time matrix
        return_to_start: If True, adds return cost to start

    Returns:
        Dict with order, cost, and algorithm name
    """
    n = len(matrix)
    if n <= 1:
        return {"order": [0], "cost": 0, "algorithm": "nearest_neighbor"}

    visited = {0}
    order = [0]
    total_cost = 0
    current = 0

    while len(visited) < n:
        best_next = None
        best_dist = float('inf')

        for j in range(n):
            if j not in visited and matrix[current][j] < best_dist:
                best_next = j
                best_dist = matrix[current][j]

        if best_next is None:
            # No reachable nodes left - shouldn't happen with valid data
            raise Exception(f"No reachable nodes from position {current}")

        visited.add(best_next)
        order.append(best_next)
        total_cost += best_dist
        current = best_next

    # Add return cost for closed TSP
    if return_to_start and n > 1:
        total_cost += matrix[current][0]

    return {
        "order": order,
        "cost": total_cost,
        "algorithm": "nearest_neighbor"
    }


def solve_tsp_2opt(matrix: list, initial_order: list = None, return_to_start: bool = False) -> dict:
    """
    2-opt local search improvement.
    Starts from nearest neighbor solution and improves it.

    Args:
        matrix: Distance/time matrix
        initial_order: Starting tour order (if None, uses nearest neighbor)
        return_to_start: If True, optimizes for closed TSP

    Returns:
        Dict with order, cost, and algorithm name
    """
    n = len(matrix)
    if n <= 2:
        return solve_tsp_nearest_neighbor(matrix, return_to_start)

    # Start with nearest neighbor solution
    if initial_order is None:
        nn_result = solve_tsp_nearest_neighbor(matrix, return_to_start)
        order = nn_result["order"]
    else:
        order = initial_order.copy()

    def route_cost(route):
        cost = sum(matrix[route[i]][route[i+1]] for i in range(len(route)-1))
        # Add return cost for closed TSP
        if return_to_start and len(route) > 1:
            cost += matrix[route[-1]][route[0]]
        return cost

    improved = True
    while improved:
        improved = False
        for i in range(1, n - 2):
            for j in range(i + 1, n):
                # Try reversing segment between i and j
                new_order = order[:i] + order[i:j+1][::-1] + order[j+1:]

                if route_cost(new_order) < route_cost(order):
                    order = new_order
                    improved = True

    return {
        "order": order,
        "cost": route_cost(order),
        "algorithm": "2-opt"
    }


def solve_vrp_ortools(
    matrix: list,
    num_vehicles: int,
    first_solution_strategy: str = "PATH_CHEAPEST_ARC",
    metaheuristic: str = "GUIDED_LOCAL_SEARCH",
    time_limit_s: int = None,
    solution_limit: int = None,
    energy_matrices: list = None,
    energy_limits: list = None,
    keep_empty: bool = False,
) -> list:
    """
    Solve multi-vehicle VRP using Google OR-Tools.
    All vehicles start and end at depot (index 0, closed routes).

    Enforces max-stops-per-vehicle so stops are spread across all vehicles.

    Returns list of routes (each route is a list of location indices
    starting and ending at depot 0, e.g. [0, 2, 4, 0]).
    Vehicles with no stops (depot-only) are excluded.

    The defaults are the production configuration. The optional arguments let
    the benchmark compare cheaper configurations: metaheuristic=None skips
    local-search metaheuristics, and solution_limit=1 returns the first
    solution found by the construction heuristic.

    Battery limit: pass energy_matrices (one integer Wh arc matrix per
    vehicle) and energy_limits (Wh each vehicle may use before its reserve).
    Energy used so far is tracked at every node, so no truck drops below its
    reserve at any point of its route. Raises BatteryLimitError if no plan
    fits.

    keep_empty=True returns one route per vehicle, in vehicle order, with
    [0, 0] for unused vehicles, so callers can match routes to vehicles.
    """
    n = len(matrix)
    num_stops = n - 1

    if num_vehicles > num_stops:
        raise Exception(
            f"Too many vehicles ({num_vehicles}) for only {num_stops} stops. "
            f"Use at most {num_stops} vehicles."
        )

    max_stops_per_vehicle = math.ceil(num_stops / num_vehicles)

    manager = pywrapcp.RoutingIndexManager(n, num_vehicles, 0)
    routing = pywrapcp.RoutingModel(manager)

    def distance_callback(from_index, to_index):
        return matrix[manager.IndexToNode(from_index)][manager.IndexToNode(to_index)]

    transit_idx = routing.RegisterTransitCallback(distance_callback)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_idx)

    def stop_count_callback(from_index):
        node = manager.IndexToNode(from_index)
        return 0 if node == 0 else 1

    stop_count_idx = routing.RegisterUnaryTransitCallback(stop_count_callback)
    routing.AddDimensionWithVehicleCapacity(
        stop_count_idx,
        0,
        [max_stops_per_vehicle] * num_vehicles,
        True,
        'StopCount'
    )

    battery_limited = energy_matrices is not None
    if battery_limited:
        _check_stops_reachable(energy_matrices, energy_limits)
        energy_callbacks = [_arc_callback(manager, m) for m in energy_matrices]
        routing.AddDimensionWithVehicleTransitAndCapacity(
            [routing.RegisterTransitCallback(cb) for cb in energy_callbacks],
            0,
            [int(limit) for limit in energy_limits],
            True,
            'Energy'
        )

    search_params = pywrapcp.DefaultRoutingSearchParameters()
    search_params.first_solution_strategy = getattr(
        routing_enums_pb2.FirstSolutionStrategy, first_solution_strategy
    )
    if metaheuristic:
        search_params.local_search_metaheuristic = getattr(
            routing_enums_pb2.LocalSearchMetaheuristic, metaheuristic
        )
    if solution_limit:
        search_params.solution_limit = solution_limit
    # Scale time limit with problem size: small problems don't need 30s
    time_limit = time_limit_s if time_limit_s else max(5, min(30, n * 2))
    search_params.time_limit.seconds = time_limit

    solution = routing.SolveWithParameters(search_params)
    if not solution and battery_limited:
        # A tight energy limit can defeat the default construction heuristic
        # even when a plan exists; insertion heuristics cope better.
        for strategy in ("PARALLEL_CHEAPEST_INSERTION", "LOCAL_CHEAPEST_INSERTION"):
            if strategy == first_solution_strategy:
                continue
            search_params.first_solution_strategy = getattr(
                routing_enums_pb2.FirstSolutionStrategy, strategy
            )
            solution = routing.SolveWithParameters(search_params)
            if solution:
                break
    if not solution:
        if battery_limited:
            raise BatteryLimitError(
                f"No plan with {num_vehicles} vehicle(s) keeps every truck above its battery "
                f"reserve. Add vehicles or battery packs, or lower the reserve."
            )
        raise Exception("OR-Tools could not find a valid solution")

    routes = []
    for vehicle_id in range(num_vehicles):
        route = []
        index = routing.Start(vehicle_id)
        while not routing.IsEnd(index):
            route.append(manager.IndexToNode(index))
            index = solution.Value(routing.NextVar(index))
        route.append(manager.IndexToNode(index))  # append end depot
        if len(route) > 2 or keep_empty:
            routes.append(route)

    return routes


def _arc_callback(manager, matrix):
    def callback(from_index, to_index):
        return matrix[manager.IndexToNode(from_index)][manager.IndexToNode(to_index)]
    return callback


def _check_stops_reachable(energy_matrices: list, energy_limits: list) -> None:
    """Fail fast, naming the stops no truck can serve even as an out-and-back trip."""
    n = len(energy_matrices[0])
    unreachable = [
        j for j in range(1, n)
        if all(m[0][j] + m[j][0] > limit for m, limit in zip(energy_matrices, energy_limits))
    ]
    if unreachable:
        raise BatteryLimitError(
            f"Stop(s) {unreachable} are too far for any truck to reach and return "
            f"without dropping below its battery reserve.",
            unreachable_stops=unreachable,
        )



def replan_route(
    cost: list,
    start: int,
    end: int,
    stops: list,
    energy_matrix: list,
    energy_limit: int,
    time_limit_s: int = None,
) -> dict:
    """
    Re-route one truck mid-drive: from `start` through as many of `stops` as
    its remaining energy allows, ending at `end`.

    cost and energy_matrix are full matrices indexed by node id; energy_limit
    is the Wh the truck may still use. Every stop may be skipped at a penalty
    larger than any route's total cost, so the solver drops the fewest stops
    that make the energy fit, then minimizes cost among those plans.

    Returns {"route": [start, ..., end], "dropped": [...]}. Raises
    BatteryLimitError if even driving straight from start to end does not fit.
    """
    if energy_matrix[start][end] > energy_limit:
        raise BatteryLimitError(
            f"Not enough energy to drive from node {start} straight to node {end}."
        )
    if not stops:
        return {"route": [start, end], "dropped": []}

    nodes = [start, *stops, end]
    m = len(nodes)
    sub_cost = [[int(round(cost[a][b])) for b in nodes] for a in nodes]
    sub_energy = [[int(energy_matrix[a][b]) for b in nodes] for a in nodes]

    manager = pywrapcp.RoutingIndexManager(m, 1, [0], [m - 1])
    routing = pywrapcp.RoutingModel(manager)

    cost_cb = _arc_callback(manager, sub_cost)
    routing.SetArcCostEvaluatorOfAllVehicles(routing.RegisterTransitCallback(cost_cb))
    energy_cb = _arc_callback(manager, sub_energy)
    routing.AddDimension(routing.RegisterTransitCallback(energy_cb), 0, int(energy_limit), True, 'Energy')

    penalty = sum(max(0, c) for row in sub_cost for c in row) + 1
    for k in range(1, m - 1):
        routing.AddDisjunction([manager.NodeToIndex(k)], penalty)

    search_params = pywrapcp.DefaultRoutingSearchParameters()
    search_params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    search_params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    search_params.time_limit.seconds = time_limit_s if time_limit_s else 1

    solution = routing.SolveWithParameters(search_params)
    if not solution:
        raise BatteryLimitError("OR-Tools could not re-route the remaining stops.")

    route = []
    index = routing.Start(0)
    while not routing.IsEnd(index):
        route.append(nodes[manager.IndexToNode(index)])
        index = solution.Value(routing.NextVar(index))
    route.append(nodes[manager.IndexToNode(index)])
    visited = set(route)
    return {"route": route, "dropped": [s for s in stops if s not in visited]}
