"""
Snapshot a stop list into a reproducible benchmark scenario.

Geocodes each address and fetches the distance/time matrices once, then
writes benchmark/scenarios/<id>.json. Benchmark runs read only that file,
so they make no Google API calls and give the same answer every time.

Usage (from the repo root, with GOOGLE_MAPS_API_KEY in .env):
    python -m benchmark.snapshot route-optimizer-frontend/public/demo_stops.txt \
        demo_anaheim "Anaheim demo" --description "Depot + 5 SoCal stops"

The first line of the stops file is the depot.
"""

import argparse
import json
import os
import re
from datetime import datetime, timezone

from .runner import SCENARIO_DIR


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("stops_file", help="Text file, one address per line; first line is the depot")
    parser.add_argument("scenario_id", help="File-safe id, e.g. demo_anaheim")
    parser.add_argument("name", help="Human-readable scenario name")
    parser.add_argument("--description", default="")
    parser.add_argument("--force", action="store_true", help="Overwrite an existing scenario")
    args = parser.parse_args()

    if not re.fullmatch(r"[A-Za-z0-9_\-]+", args.scenario_id):
        parser.error("scenario_id may only contain letters, digits, _ and -")
    out_path = os.path.join(SCENARIO_DIR, f"{args.scenario_id}.json")
    if os.path.exists(out_path) and not args.force:
        parser.error(f"{out_path} already exists (use --force to overwrite)")

    # Imported here so the benchmark package itself never depends on Flask / the API key
    from route_optimizer_backend import geocode_address, get_distance_matrix

    with open(args.stops_file) as f:
        addresses = [line.strip() for line in f if line.strip()]
    if len(addresses) < 2:
        parser.error("need a depot and at least one stop")

    print(f"Geocoding {len(addresses)} addresses...")
    locations = []
    for addr in addresses:
        g = geocode_address(addr)
        locations.append({"address": addr, "formatted_address": g["formatted_address"],
                          "lat": g["lat"], "lng": g["lng"]})
        print(f"  {addr} -> {g['lat']:.5f}, {g['lng']:.5f}")

    print("Fetching distance/time matrices...")
    matrices = get_distance_matrix(locations)
    for name in ("distance", "time"):
        if any(v == float("inf") for row in matrices[name] for v in row):
            raise SystemExit(f"Some {name} matrix elements failed; not writing a scenario.")

    scenario = {
        "id": args.scenario_id,
        "name": args.name,
        "description": args.description,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_file": args.stops_file,
        "locations": locations,
        "distance_m": matrices["distance"],
        "time_s": matrices["time"],
    }
    os.makedirs(SCENARIO_DIR, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(scenario, f, indent=2)
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
