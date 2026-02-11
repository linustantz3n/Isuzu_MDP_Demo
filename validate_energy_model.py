#!/usr/bin/env python3
"""
Validation Script for Mercedes Energy Model
============================================

Validates the Mercedes eActros energy consumption model against ISUZU Anaheim example data.

ISUZU Anaheim Example (from PDF):
- Total distance: 37 miles (59.5 km)
- Total time: 86 minutes
- Total energy: 33.8 kWh
- Average: 0.91 kWh/mile or 1.1 miles/kWh
- Note: "Estimated efficiency for the vehicle is assumed to be 1miles/kWh"

Expected Mercedes Model Parameters:
- Temperature: ~20°C (68°F) - typical California weather
- Vehicle weight: ~7-10 tonnes (ISUZU medium delivery truck)
- Climate control: Active
- Terrain: Relatively flat (Anaheim, CA)
"""

import requests
import json
from dotenv import load_dotenv
import os

load_dotenv()

API_BASE = "http://localhost:5001"
GOOGLE_API_KEY = os.environ.get('GOOGLE_MAPS_API_KEY', '')

# ISUZU Anaheim example locations from PDF
# Depot: 10903 Auto Square Dr, Cerritos, CA 90703 (33.862, -118.101)
# Warehouse: 11232 Knott Ave, Stanton, CA 90680 (33.800, -118.007)

ANAHEIM_ADDRESSES = [
    "10903 Auto Square Dr, Cerritos, CA 90703",  # Depot (warehouse 2)
    "Macy's, Anaheim, CA",
    "Costco, Anaheim, CA",
    "Target, Anaheim, CA",
    "Walmart, Anaheim, CA",
    "Mall outlets, Anaheim, CA"
]

# ISUZU example shows these metrics for the actual route
ISUZU_REFERENCE = {
    "total_distance_mi": 37.0,
    "total_time_min": 86.0,
    "total_energy_kwh": 33.8,
    "efficiency_miles_per_kwh": 1.1,
    "efficiency_kwh_per_mile": 0.91
}


def validate_energy_model():
    """
    Validate Mercedes energy model against ISUZU Anaheim example.
    """
    print("\n" + "="*70)
    print("ISUZU ENERGY MODEL VALIDATION")
    print("="*70)

    print("\n📍 ISUZU Anaheim Example Reference Data:")
    print(f"   Distance: {ISUZU_REFERENCE['total_distance_mi']:.1f} miles")
    print(f"   Time: {ISUZU_REFERENCE['total_time_min']:.0f} minutes")
    print(f"   Energy: {ISUZU_REFERENCE['total_energy_kwh']:.1f} kWh")
    print(f"   Efficiency: {ISUZU_REFERENCE['efficiency_miles_per_kwh']:.2f} miles/kWh")

    # Step 1: Geocode addresses
    print("\n🔍 Step 1: Geocoding addresses...")
    response = requests.post(
        f"{API_BASE}/api/geocode_batch",
        json={"addresses": ANAHEIM_ADDRESSES}
    )

    if response.status_code != 200:
        print(f"❌ Geocoding failed: {response.text}")
        return

    geocode_data = response.json()
    locations = geocode_data['results']

    if None in locations:
        print("❌ Some addresses failed to geocode")
        return

    print(f"✅ Geocoded {len(locations)} locations")

    # Step 2: Test different vehicle weight scenarios
    print("\n🔋 Step 2: Testing energy model with different parameters...")

    test_scenarios = [
        {
            "name": "Light load (5 tonnes)",
            "temperature_c": 20,
            "vehicle_weight_tonnes": 5.0,
            "has_climate_control": True
        },
        {
            "name": "Medium load (7 tonnes) - ISUZU typical",
            "temperature_c": 20,
            "vehicle_weight_tonnes": 7.0,
            "has_climate_control": True
        },
        {
            "name": "Heavy load (10 tonnes)",
            "temperature_c": 20,
            "vehicle_weight_tonnes": 10.0,
            "has_climate_control": True
        },
        {
            "name": "Hot day (30°C / 86°F)",
            "temperature_c": 30,
            "vehicle_weight_tonnes": 7.0,
            "has_climate_control": True
        },
        {
            "name": "No climate control",
            "temperature_c": 20,
            "vehicle_weight_tonnes": 7.0,
            "has_climate_control": False
        }
    ]

    print("\n" + "="*70)

    for scenario in test_scenarios:
        print(f"\n📊 Scenario: {scenario['name']}")
        print(f"   Temperature: {scenario['temperature_c']}°C ({scenario['temperature_c']*9/5+32:.0f}°F)")
        print(f"   Vehicle weight: {scenario['vehicle_weight_tonnes']} tonnes ({scenario['vehicle_weight_tonnes']*2204.62:.0f} lbs)")
        print(f"   Climate control: {'Active' if scenario['has_climate_control'] else 'Inactive'}")

        # Optimize route with energy calculations
        response = requests.post(
            f"{API_BASE}/api/optimize",
            json={
                "locations": locations,
                "mode": "distance",
                "algorithms": ["optimal", "greedy"],
                "return_to_start": True,  # Return to depot
                "energy_params": {
                    "temperature_c": scenario['temperature_c'],
                    "vehicle_weight_tonnes": scenario['vehicle_weight_tonnes'],
                    "has_climate_control": scenario['has_climate_control']
                }
            }
        )

        if response.status_code != 200:
            print(f"❌ Optimization failed: {response.text}")
            continue

        result = response.json()

        # Analyze optimal route
        optimal = result['results']['optimal']

        print(f"\n   🚛 Optimal Route Results:")
        print(f"      Distance: {optimal['total_distance_mi']:.2f} miles ({optimal['total_distance_m']/1000:.2f} km)")
        print(f"      Time: {optimal['total_time_min']:.1f} minutes")
        print(f"      Energy: {optimal['total_energy_kwh']:.2f} kWh")
        print(f"      Efficiency: {optimal['total_distance_mi']/optimal['total_energy_kwh']:.2f} miles/kWh")
        print(f"      Avg consumption: {optimal['avg_energy_per_mile']:.3f} kWh/mile")

        # Compare to ISUZU reference
        distance_error = abs(optimal['total_distance_mi'] - ISUZU_REFERENCE['total_distance_mi']) / ISUZU_REFERENCE['total_distance_mi'] * 100
        energy_error = abs(optimal['total_energy_kwh'] - ISUZU_REFERENCE['total_energy_kwh']) / ISUZU_REFERENCE['total_energy_kwh'] * 100

        print(f"\n   📈 Comparison to ISUZU Reference:")
        print(f"      Distance error: {distance_error:.1f}%")
        print(f"      Energy error: {energy_error:.1f}%")

        if energy_error < 15:
            print(f"      ✅ Energy prediction within 15% tolerance")
        else:
            print(f"      ⚠️  Energy prediction exceeds 15% tolerance")

        # Show energy breakdown for first leg
        if len(optimal['legs']) > 0:
            first_leg = optimal['legs'][0]
            print(f"\n   🔍 First Leg Breakdown (Depot → {first_leg['to']['formatted_address'][:30]}...):")
            print(f"      Distance: {first_leg['distance_mi']:.2f} mi, Speed: {first_leg['avg_speed_mph']:.1f} mph")
            print(f"      Energy: {first_leg['energy_kwh']:.3f} kWh ({first_leg['energy_per_mile']:.3f} kWh/mi)")
            print(f"      Elevation: {first_leg['elevation_from_m']:.1f}m → {first_leg['elevation_to_m']:.1f}m (Δ{first_leg['elevation_gain_m']:.1f}m)")

    print("\n" + "="*70)
    print("✅ Validation complete!")
    print("="*70)


def main():
    """Main validation routine."""

    if not GOOGLE_API_KEY:
        print("❌ ERROR: GOOGLE_MAPS_API_KEY not set in .env file")
        return

    # Check if backend is running
    try:
        response = requests.get(f"{API_BASE}/api/health", timeout=2)
        if response.status_code != 200:
            print(f"❌ Backend not responding properly: {response.status_code}")
            return
    except requests.exceptions.RequestException:
        print("❌ ERROR: Backend not running!")
        print("   Please start the backend with: python route_optimizer_backend.py")
        return

    validate_energy_model()


if __name__ == '__main__':
    main()
