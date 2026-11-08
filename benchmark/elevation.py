"""
Add elevations to saved scenarios so the benchmark can score and optimize
energy (the uphill/downhill terms of the Model E regression).

Fetches once from the Google Elevation API and writes `elevation_m` into the
scenario file. Matrices and everything else in the file are left untouched,
and benchmark runs still make no Google API calls.

Usage (from the repo root, with GOOGLE_MAPS_API_KEY in .env):
    python -m benchmark.elevation --all
    python -m benchmark.elevation case3_piedmont_sc --force
"""

import argparse
import json
import os

import requests
from dotenv import load_dotenv

from .runner import SCENARIO_DIR, list_scenarios

ELEVATION_URL = "https://maps.googleapis.com/maps/api/elevation/json"
MAX_LOCATIONS_PER_REQUEST = 256  # well inside the API's 512 limit and the URL length limit


def fetch_elevations(locations: list, api_key: str = None) -> list:
    """Elevation in meters per location. Raises on any API error instead of returning zeros."""
    api_key = api_key or os.environ.get("GOOGLE_MAPS_API_KEY", "")
    out = []
    for i in range(0, len(locations), MAX_LOCATIONS_PER_REQUEST):
        chunk = locations[i:i + MAX_LOCATIONS_PER_REQUEST]
        resp = requests.get(ELEVATION_URL, params={
            "locations": "|".join(f"{loc['lat']},{loc['lng']}" for loc in chunk),
            "key": api_key,
        })
        data = resp.json()
        if data.get("status") != "OK":
            raise RuntimeError(f"Elevation API error: {data.get('status')} {data.get('error_message', '')}".strip())
        out.extend(round(r["elevation"], 2) for r in data["results"])
    if len(out) != len(locations):
        raise RuntimeError(f"Elevation API returned {len(out)} results for {len(locations)} locations")
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("scenario_ids", nargs="*")
    parser.add_argument("--all", action="store_true", help="Every saved scenario")
    parser.add_argument("--force", action="store_true", help="Refetch even if elevations already exist")
    args = parser.parse_args()

    load_dotenv()
    known = [s["id"] for s in list_scenarios()]
    ids = known if args.all else args.scenario_ids
    if not ids:
        parser.error("name at least one scenario id, or pass --all")
    unknown = [i for i in ids if i not in known]
    if unknown:
        parser.error(f"unknown scenarios: {unknown}")

    for sid in ids:
        path = os.path.join(SCENARIO_DIR, f"{sid}.json")
        with open(path) as f:
            scenario = json.load(f)
        if scenario.get("elevation_m") and not args.force:
            print(f"{sid}: already has elevations (use --force to refetch)")
            continue
        scenario["elevation_m"] = fetch_elevations(scenario["locations"])
        with open(path, "w") as f:
            json.dump(scenario, f, indent=2)
        e = scenario["elevation_m"]
        print(f"{sid}: {len(e)} elevations, {min(e):.0f} m to {max(e):.0f} m")


if __name__ == "__main__":
    main()
