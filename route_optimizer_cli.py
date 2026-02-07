#!/usr/bin/env python3
"""
Route Optimizer CLI
===================

Standalone command-line tool for route optimization.
No server required - directly calls Google Maps API.

Usage:
    python route_optimizer_cli.py --addresses "Address 1" "Address 2" "Address 3"
    python route_optimizer_cli.py --file stops.txt
    python route_optimizer_cli.py --interactive

Environment:
    GOOGLE_MAPS_API_KEY - Your Google Maps API key

Examples:
    export GOOGLE_MAPS_API_KEY="your-key-here"
    
    # Quick test with addresses
    python route_optimizer_cli.py -a "2281 Bonisteel Blvd, Ann Arbor, MI" \
                                    "500 S State St, Ann Arbor, MI" \
                                    "530 S State St, Ann Arbor, MI" \
                                    "1109 Geddes Ave, Ann Arbor, MI"
    
    # From file (one address per line)
    python route_optimizer_cli.py -f my_stops.txt
    
    # Interactive mode
    python route_optimizer_cli.py -i
"""

import os
import sys
import json
import argparse
import requests
from typing import List, Dict, Tuple
from itertools import permutations


# =============================================================================
# Configuration
# =============================================================================

API_KEY = os.environ.get('GOOGLE_MAPS_API_KEY', '')

if not API_KEY:
    print("WARNING: GOOGLE_MAPS_API_KEY environment variable not set")
    print("Set it with: export GOOGLE_MAPS_API_KEY='your-key-here'")
    print()


# =============================================================================
# Google Maps API
# =============================================================================

def geocode(address: str) -> Dict:
    """Convert address to coordinates."""
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    response = requests.get(url, params={"address": address, "key": API_KEY})
    data = response.json()
    
    if data["status"] != "OK":
        raise Exception(f"Geocoding failed: {data['status']} - {address}")
    
    result = data["results"][0]
    loc = result["geometry"]["location"]
    
    return {
        "input": address,
        "formatted": result["formatted_address"],
        "lat": loc["lat"],
        "lng": loc["lng"]
    }


def get_distance_matrix(locations: List[Dict]) -> Tuple[List[List[int]], List[List[int]]]:
    """Get distance and time matrices with batching for large requests."""
    coords = [f"{loc['lat']},{loc['lng']}" for loc in locations]
    n = len(locations)

    distance_matrix = [[0] * n for _ in range(n)]
    time_matrix = [[0] * n for _ in range(n)]

    # Google Distance Matrix API limits:
    # - Max 100 elements per request (origins × destinations)
    # - Max 25 origins OR 25 destinations per request
    # For n×n matrix, we need to batch if n > 10 (since 10×10=100)

    batch_size = 10  # Safe batch size: 10×10=100 elements

    url = "https://maps.googleapis.com/maps/api/distancematrix/json"

    # Batch requests for origins
    for i_start in range(0, n, batch_size):
        i_end = min(i_start + batch_size, n)
        origin_coords = coords[i_start:i_end]

        # Batch requests for destinations
        for j_start in range(0, n, batch_size):
            j_end = min(j_start + batch_size, n)
            dest_coords = coords[j_start:j_end]

            # Make API request for this batch
            response = requests.get(url, params={
                "origins": "|".join(origin_coords),
                "destinations": "|".join(dest_coords),
                "key": API_KEY
            })
            data = response.json()

            if data["status"] != "OK":
                raise Exception(f"Distance Matrix API error: {data['status']}")

            # Fill in the corresponding section of the matrix
            for i, row in enumerate(data["rows"]):
                for j, elem in enumerate(row["elements"]):
                    global_i = i_start + i
                    global_j = j_start + j

                    if elem["status"] == "OK":
                        distance_matrix[global_i][global_j] = elem["distance"]["value"]
                        time_matrix[global_i][global_j] = elem["duration"]["value"]
                    else:
                        distance_matrix[global_i][global_j] = float('inf')
                        time_matrix[global_i][global_j] = float('inf')

    return distance_matrix, time_matrix


# =============================================================================
# TSP Solvers
# =============================================================================

def solve_tsp_optimal(matrix: List[List[int]]) -> Tuple[List[int], int]:
    """
    Solve TSP optimally using Held-Karp for larger instances,
    brute force for small ones.
    """
    n = len(matrix)
    
    if n <= 1:
        return [0], 0
    if n == 2:
        return [0, 1], matrix[0][1]
    
    # Brute force for small problems
    if n <= 10:
        best_order = None
        best_cost = float('inf')
        
        for perm in permutations(range(1, n)):
            order = [0] + list(perm)
            cost = sum(matrix[order[i]][order[i+1]] for i in range(n-1))
            if cost < best_cost:
                best_cost = cost
                best_order = order
        
        return best_order, best_cost
    
    # Held-Karp for larger problems
    INF = float('inf')
    dp = [[INF] * n for _ in range(1 << n)]
    parent = [[-1] * n for _ in range(1 << n)]
    
    dp[1][0] = 0
    
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
    
    full_mask = (1 << n) - 1
    best_cost = INF
    best_end = -1
    
    for i in range(1, n):
        if dp[full_mask][i] < best_cost:
            best_cost = dp[full_mask][i]
            best_end = i
    
    # Reconstruct
    order = []
    mask = full_mask
    current = best_end
    
    while current != -1:
        order.append(current)
        prev = parent[mask][current]
        mask ^= (1 << current)
        current = prev
    
    order.reverse()
    return order, best_cost


def solve_tsp_greedy(matrix: List[List[int]]) -> Tuple[List[int], int]:
    """Nearest neighbor heuristic."""
    n = len(matrix)
    if n <= 1:
        return [0], 0
    
    visited = {0}
    order = [0]
    total = 0
    current = 0
    
    while len(visited) < n:
        best = None
        best_dist = float('inf')

        for j in range(n):
            if j not in visited and matrix[current][j] < best_dist:
                best = j
                best_dist = matrix[current][j]

        if best is None:
            # No reachable nodes left - shouldn't happen with valid data
            raise Exception(f"No reachable nodes from position {current}")

        visited.add(best)
        order.append(best)
        total += best_dist
        current = best
    
    return order, total


# =============================================================================
# Formatting
# =============================================================================

def format_distance(meters: int) -> str:
    """Format distance in miles."""
    miles = meters / 1609.34
    return f"{miles:.1f} mi"


def format_time(seconds: int) -> str:
    """Format time in hours/minutes."""
    hours = seconds // 3600
    mins = (seconds % 3600) // 60
    if hours > 0:
        return f"{hours}h {mins}m"
    return f"{mins} min"


def generate_maps_url(locations: List[Dict], order: List[int]) -> str:
    """Generate Google Maps URL for the route."""
    ordered = [locations[i] for i in order]
    origin = f"{ordered[0]['lat']},{ordered[0]['lng']}"
    dest = f"{ordered[-1]['lat']},{ordered[-1]['lng']}"
    
    waypoints = []
    for loc in ordered[1:-1]:
        waypoints.append(f"{loc['lat']},{loc['lng']}")
    
    url = f"https://www.google.com/maps/dir/?api=1&origin={origin}&destination={dest}"
    if waypoints:
        url += f"&waypoints={'|'.join(waypoints)}"
    
    return url


# =============================================================================
# Main
# =============================================================================

def optimize_route(addresses: List[str], mode: str = 'distance', verbose: bool = True):
    """Main optimization function."""
    
    if verbose:
        print(f"\n{'='*60}")
        print("ROUTE OPTIMIZER")
        print(f"{'='*60}\n")
    
    # Geocode addresses
    if verbose:
        print("Geocoding addresses...")
    
    locations = []
    for addr in addresses:
        try:
            loc = geocode(addr)
            locations.append(loc)
            if verbose:
                print(f"  ✓ {loc['formatted']}")
        except Exception as e:
            print(f"  ✗ Failed: {addr} - {e}")
            return None
    
    if len(locations) < 2:
        print("Error: Need at least 2 valid locations")
        return None
    
    # Get distance matrix
    if verbose:
        print(f"\nFetching distance matrix for {len(locations)} locations...")
    
    distance_matrix, time_matrix = get_distance_matrix(locations)
    matrix = distance_matrix if mode == 'distance' else time_matrix
    
    # Solve
    if verbose:
        print(f"Solving TSP (optimizing for {mode})...")
    
    optimal_order, optimal_cost = solve_tsp_optimal(matrix)
    greedy_order, greedy_cost = solve_tsp_greedy(matrix)
    
    # Calculate full metrics
    def calc_metrics(order):
        dist = sum(distance_matrix[order[i]][order[i+1]] for i in range(len(order)-1))
        time = sum(time_matrix[order[i]][order[i+1]] for i in range(len(order)-1))
        return dist, time
    
    opt_dist, opt_time = calc_metrics(optimal_order)
    greedy_dist, greedy_time = calc_metrics(greedy_order)
    
    # Results
    if verbose:
        print(f"\n{'='*60}")
        print("RESULTS")
        print(f"{'='*60}\n")
        
        print("OPTIMAL ROUTE:")
        print("-" * 40)
        for i, idx in enumerate(optimal_order):
            loc = locations[idx]
            print(f"  {i+1}. {loc['formatted']}")
            if i < len(optimal_order) - 1:
                next_idx = optimal_order[i+1]
                leg_dist = distance_matrix[idx][next_idx]
                leg_time = time_matrix[idx][next_idx]
                print(f"     ↓ {format_distance(leg_dist)} · {format_time(leg_time)}")
        
        print()
        print(f"  Total Distance: {format_distance(opt_dist)}")
        print(f"  Total Time:     {format_time(opt_time)}")
        
        print(f"\n{'='*60}")
        print("COMPARISON")
        print(f"{'='*60}\n")
        
        print(f"{'Algorithm':<25} {'Distance':<15} {'Time':<15}")
        print("-" * 55)
        print(f"{'Optimal (Held-Karp)':<25} {format_distance(opt_dist):<15} {format_time(opt_time):<15}")
        print(f"{'Greedy (Nearest Neighbor)':<25} {format_distance(greedy_dist):<15} {format_time(greedy_time):<15}")
        
        if greedy_dist > opt_dist:
            saved_dist = greedy_dist - opt_dist
            saved_pct = (saved_dist / greedy_dist) * 100
            print()
            print(f"Optimal saves {format_distance(saved_dist)} ({saved_pct:.1f}%) vs greedy approach")
        
        print(f"\n{'='*60}")
        print("GOOGLE MAPS LINKS")
        print(f"{'='*60}\n")
        print("OPTIMAL ROUTE:")
        print(generate_maps_url(locations, optimal_order))
        print()
        print("GREEDY ROUTE (for comparison):")
        print(generate_maps_url(locations, greedy_order))
    
    return {
        "locations": locations,
        "optimal": {
            "order": optimal_order,
            "distance_m": opt_dist,
            "time_s": opt_time,
            "maps_url": generate_maps_url(locations, optimal_order)
        },
        "greedy": {
            "order": greedy_order,
            "distance_m": greedy_dist,
            "time_s": greedy_time,
            "maps_url": generate_maps_url(locations, greedy_order)
        }
    }


def interactive_mode():
    """Interactive CLI mode."""
    print("\n" + "="*60)
    print("ROUTE OPTIMIZER - Interactive Mode")
    print("="*60)
    print("\nEnter addresses one per line. Type 'done' when finished.\n")
    
    addresses = []
    while True:
        addr = input(f"Stop {len(addresses)+1}: ").strip()
        if addr.lower() == 'done':
            break
        if addr:
            addresses.append(addr)
    
    if len(addresses) < 2:
        print("Need at least 2 addresses!")
        return
    
    mode = input("\nOptimize for [d]istance or [t]ime? (d): ").strip().lower()
    mode = 'time' if mode == 't' else 'distance'
    
    optimize_route(addresses, mode=mode)


def main():
    parser = argparse.ArgumentParser(
        description='Optimize delivery routes using Google Maps API',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python route_optimizer_cli.py -a "123 Main St" "456 Oak Ave" "789 Pine Rd"
  python route_optimizer_cli.py -f stops.txt -m time
  python route_optimizer_cli.py -i
        """
    )
    
    parser.add_argument('-a', '--addresses', nargs='+', help='List of addresses')
    parser.add_argument('-f', '--file', help='File with addresses (one per line)')
    parser.add_argument('-i', '--interactive', action='store_true', help='Interactive mode')
    parser.add_argument('-m', '--mode', choices=['distance', 'time'], default='distance',
                        help='Optimization mode (default: distance)')
    parser.add_argument('-j', '--json', action='store_true', help='Output as JSON')
    parser.add_argument('-k', '--key', help='Google Maps API key (or use GOOGLE_MAPS_API_KEY env var)')
    
    args = parser.parse_args()
    
    # Set API key if provided
    global API_KEY
    if args.key:
        API_KEY = args.key
    
    if not API_KEY:
        print("Error: No API key. Set GOOGLE_MAPS_API_KEY or use -k flag")
        sys.exit(1)
    
    # Determine input source
    addresses = []
    
    if args.interactive:
        interactive_mode()
        return
    
    if args.file:
        with open(args.file, 'r') as f:
            addresses = [line.strip() for line in f if line.strip()]
    
    if args.addresses:
        addresses = args.addresses
    
    if not addresses:
        parser.print_help()
        return
    
    # Run optimization
    result = optimize_route(addresses, mode=args.mode, verbose=not args.json)
    
    if args.json and result:
        print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
