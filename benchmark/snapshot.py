"""
Snapshot a stop list into a reproducible benchmark scenario.

Geocodes each address and fetches the distance/time matrices once, then
writes benchmark/scenarios/<id>.json. Benchmark runs read only that file,
so they make no Google API calls and give the same answer every time.

Usage (from the repo root, with GOOGLE_MAPS_API_KEY in .env):
    python -m benchmark.snapshot route-optimizer-frontend/public/demo_stops.txt \
        demo_anaheim "Anaheim demo" --description "Depot + 5 SoCal stops"

The first line of the stops file is the depot. A line may optionally carry
known coordinates (skips geocoding), a demand and a display name, separated by "|":
    14900 Beck Rd, Plymouth, MI 48170 | 42.370474, -83.488067 | 8 | USA Hockey Arena
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
        lines = [line.strip() for line in f if line.strip() and not line.startswith("#")]
    if len(lines) < 2:
        parser.error("need a depot and at least one stop")

    print(f"Resolving {len(lines)} locations...")
    locations = []
    for line in lines:
        parts = [p.strip() for p in line.split("|")]
        addr = parts[0]
        if len(parts) > 1 and parts[1]:
            lat, lng = (float(x) for x in parts[1].split(","))
            loc = {"address": addr, "formatted_address": addr, "lat": lat, "lng": lng}
        else:
            g = geocode_address(addr)
            loc = {"address": addr, "formatted_address": g["formatted_address"],
                   "lat": g["lat"], "lng": g["lng"]}
        if len(parts) > 2 and parts[2]:
            loc["demand"] = int(parts[2])
        if len(parts) > 3 and parts[3]:
            loc["name"] = parts[3]
        locations.append(loc)
        print(f"  {addr} -> {loc['lat']:.5f}, {loc['lng']:.5f}")

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
