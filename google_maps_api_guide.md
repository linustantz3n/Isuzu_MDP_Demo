# Google Maps API Guide for BEV Fleet Routing

## Overview

This guide covers the Google Maps Platform APIs relevant to building an AI-powered routing system for electric vehicle fleet optimization. Each API serves a specific purpose in the routing pipeline.

| API | Primary Use | Best For |
|-----|-------------|----------|
| Directions API | Single route with turn-by-turn | Individual trip planning |
| Distance Matrix API | Many-to-many travel times | Fleet-wide optimization |
| Routes API | Advanced routing features | Eco-routes, traffic-aware |
| Elevation API | Altitude data along paths | Energy consumption modeling |

---

## Setup

### Authentication

All requests require an API key passed as a query parameter:

```python
import requests
import os

API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY")  # Store securely
BASE_URL = "https://maps.googleapis.com/maps/api"
```

### Rate Limits & Pricing

- Most APIs: 50 requests/second per project
- Free tier: $200/month credit (~40,000 Directions requests)
- Monitor usage: Google Cloud Console → APIs & Services → Quotas

---

## Directions API

### What It Does

Returns the most efficient route between an origin and destination, including:
- Step-by-step directions
- Polyline geometry (for map rendering)
- Duration and distance
- Traffic-aware ETAs

### When to Use

- Planning a single vehicle's route
- Getting turn-by-turn navigation data
- Computing route geometry for visualization

### Basic Request

```python
def get_directions(origin, destination, waypoints=None, departure_time=None):
    """
    Get directions between two points.
    
    Args:
        origin: Starting location (address or "lat,lng")
        destination: End location (address or "lat,lng")
        waypoints: Optional list of intermediate stops
        departure_time: Unix timestamp for traffic-aware routing
    
    Returns:
        dict: Full API response with routes
    """
    url = f"{BASE_URL}/directions/json"
    
    params = {
        "origin": origin,
        "destination": destination,
        "key": API_KEY,
        "units": "imperial",  # or "metric"
    }
    
    if waypoints:
        # Use "optimize:true|" prefix to let Google reorder waypoints
        params["waypoints"] = "optimize:true|" + "|".join(waypoints)
    
    if departure_time:
        params["departure_time"] = departure_time
        params["traffic_model"] = "best_guess"  # or "pessimistic", "optimistic"
    
    response = requests.get(url, params=params)
    return response.json()
```

### Response Structure

```python
{
    "status": "OK",
    "routes": [{
        "summary": "I-94 E",
        "legs": [{
            "distance": {"text": "43.2 mi", "value": 69523},  # value in meters
            "duration": {"text": "45 mins", "value": 2700},   # value in seconds
            "duration_in_traffic": {"text": "52 mins", "value": 3120},
            "start_address": "Ann Arbor, MI",
            "end_address": "Detroit, MI",
            "steps": [{
                "html_instructions": "Head north on State St",
                "distance": {"text": "0.2 mi", "value": 322},
                "duration": {"text": "1 min", "value": 60},
                "polyline": {"points": "encoded_polyline_string"}
            }, ...]
        }],
        "overview_polyline": {"points": "encoded_polyline_string"},
        "waypoint_order": [1, 0, 2]  # If optimization was requested
    }]
}
```

### Extracting Key Data

```python
def parse_route(response):
    """Extract useful data from Directions API response."""
    if response["status"] != "OK":
        raise Exception(f"API Error: {response['status']}")
    
    route = response["routes"][0]
    leg = route["legs"][0]
    
    return {
        "distance_meters": leg["distance"]["value"],
        "duration_seconds": leg["duration"]["value"],
        "duration_traffic_seconds": leg.get("duration_in_traffic", {}).get("value"),
        "polyline": route["overview_polyline"]["points"],
        "steps": len(leg["steps"]),
        "waypoint_order": route.get("waypoint_order", [])
    }
```

### Key Parameters

| Parameter | Description | Example |
|-----------|-------------|---------|
| `mode` | Travel mode | `driving`, `walking`, `bicycling`, `transit` |
| `avoid` | Route restrictions | `tolls`, `highways`, `ferries` |
| `departure_time` | For traffic data | Unix timestamp or `now` |
| `traffic_model` | Traffic prediction | `best_guess`, `pessimistic`, `optimistic` |
| `waypoints` | Intermediate stops | `optimize:true\|place1\|place2` |
| `alternatives` | Return multiple routes | `true` or `false` |

### BEV-Specific Tip

Request `alternatives=true` to get multiple route options. For BEVs, you may want to compare:
- Fastest route (may use highways, higher energy consumption)
- Shortest route (less distance but possibly more stops/starts)
- Route avoiding highways (potentially more energy-efficient at lower speeds)

```python
params["alternatives"] = "true"
# Response will contain multiple routes in routes[]
```

---

## Distance Matrix API

### What It Does

Computes travel distance and time for a matrix of origins and destinations. Returns an N×M matrix where N is origins and M is destinations.

### When to Use

- Assigning vehicles to deliveries (which vehicle is closest?)
- Optimizing delivery order across a fleet
- Computing accessibility from depot to all delivery points
- Pre-computing travel times for route optimization algorithms

### Basic Request

```python
def get_distance_matrix(origins, destinations, departure_time=None):
    """
    Get travel times/distances between multiple origins and destinations.
    
    Args:
        origins: List of locations (addresses or "lat,lng")
        destinations: List of locations
        departure_time: Unix timestamp for traffic-aware estimates
    
    Returns:
        dict: Matrix of distances and durations
    """
    url = f"{BASE_URL}/distancematrix/json"
    
    params = {
        "origins": "|".join(origins),
        "destinations": "|".join(destinations),
        "key": API_KEY,
        "units": "imperial"
    }
    
    if departure_time:
        params["departure_time"] = departure_time
    
    response = requests.get(url, params=params)
    return response.json()
```

### Response Structure

```python
{
    "status": "OK",
    "origin_addresses": ["Ann Arbor, MI", "Ypsilanti, MI"],
    "destination_addresses": ["Detroit, MI", "Dearborn, MI", "Livonia, MI"],
    "rows": [
        {  # From Ann Arbor
            "elements": [
                {"status": "OK", "distance": {"value": 69523}, "duration": {"value": 2700}},
                {"status": "OK", "distance": {"value": 58000}, "duration": {"value": 2400}},
                {"status": "OK", "distance": {"value": 45000}, "duration": {"value": 1800}}
            ]
        },
        {  # From Ypsilanti
            "elements": [
                {"status": "OK", "distance": {"value": 55000}, "duration": {"value": 2100}},
                {"status": "OK", "distance": {"value": 48000}, "duration": {"value": 1900}},
                {"status": "OK", "distance": {"value": 40000}, "duration": {"value": 1600}}
            ]
        }
    ]
}
```

### Building a Matrix

```python
def build_time_matrix(origins, destinations, response):
    """
    Convert API response to a 2D numpy array of travel times.
    
    Returns:
        np.array: Matrix where [i][j] is seconds from origin i to destination j
    """
    import numpy as np
    
    matrix = []
    for row in response["rows"]:
        times = []
        for element in row["elements"]:
            if element["status"] == "OK":
                times.append(element["duration"]["value"])
            else:
                times.append(float("inf"))  # Unreachable
        matrix.append(times)
    
    return np.array(matrix)
```

### Limits

- Maximum 25 origins OR 25 destinations per request
- Maximum 100 elements (origins × destinations) per request
- For larger matrices, batch your requests:

```python
def batch_distance_matrix(origins, destinations, batch_size=10):
    """Handle large matrices by batching requests."""
    all_results = []
    
    for i in range(0, len(origins), batch_size):
        origin_batch = origins[i:i + batch_size]
        batch_response = get_distance_matrix(origin_batch, destinations)
        all_results.extend(batch_response["rows"])
    
    return all_results
```

---

## Routes API (Recommended for BEV)

### What It Does

Google's newer routing API with advanced features particularly relevant for electric vehicles:
- Eco-friendly routing (fuel/energy efficient)
- Better waypoint handling (up to 25 intermediate stops)
- Polyline quality options
- Route modifiers for vehicle types

### When to Use

- When you need eco-friendly/energy-efficient routes
- Complex multi-stop routing
- When you need more control over route characteristics

### Basic Request

The Routes API uses a different endpoint and request format (JSON body instead of query params):

```python
def get_route_v2(origin, destination, waypoints=None, compute_eco_route=True):
    """
    Get route using the newer Routes API.
    
    Args:
        origin: Dict with location {"latLng": {"latitude": x, "longitude": y}}
        destination: Dict with location
        waypoints: List of intermediate waypoint dicts
        compute_eco_route: Whether to request fuel-efficient routing
    
    Returns:
        dict: Route response
    """
    url = "https://routes.googleapis.com/directions/v2:computeRoutes"
    
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": API_KEY,
        "X-Goog-FieldMask": "routes.duration,routes.distanceMeters,routes.polyline,routes.legs,routes.routeLabels"
    }
    
    body = {
        "origin": {"location": origin},
        "destination": {"location": destination},
        "travelMode": "DRIVE",
        "routingPreference": "TRAFFIC_AWARE_OPTIMAL",
        "computeAlternativeRoutes": True,
        "extraComputations": ["FUEL_CONSUMPTION"] if compute_eco_route else [],
        "requestedReferenceRoutes": ["FUEL_EFFICIENT"] if compute_eco_route else []
    }
    
    if waypoints:
        body["intermediates"] = [{"location": wp} for wp in waypoints]
    
    response = requests.post(url, headers=headers, json=body)
    return response.json()
```

### Location Format

```python
# By coordinates (preferred for precision)
location = {
    "latLng": {
        "latitude": 42.2808,
        "longitude": -83.7430
    }
}

# By address
location = {
    "address": "Ann Arbor, MI"
}

# By Place ID (most stable)
location = {
    "placeId": "ChIJMx9D1A2wPIgR4rXIhkb5Cds"
}
```

### Response Structure

```python
{
    "routes": [{
        "distanceMeters": 69523,
        "duration": "2700s",
        "polyline": {
            "encodedPolyline": "encoded_string"
        },
        "routeLabels": ["DEFAULT_ROUTE"],  # or "FUEL_EFFICIENT"
        "legs": [{
            "distanceMeters": 69523,
            "duration": "2700s",
            "startLocation": {...},
            "endLocation": {...},
            "steps": [...]
        }]
    },
    {
        "routeLabels": ["FUEL_EFFICIENT"],
        "distanceMeters": 72000,  # May be longer distance
        "duration": "2850s",      # But more fuel efficient
        ...
    }]
}
```

### Field Mask

The `X-Goog-FieldMask` header controls which fields are returned. Only request what you need to reduce response size and cost:

```python
# Minimal (just distance and time)
"routes.duration,routes.distanceMeters"

# With geometry
"routes.duration,routes.distanceMeters,routes.polyline.encodedPolyline"

# Full details
"routes.duration,routes.distanceMeters,routes.polyline,routes.legs,routes.routeLabels,routes.travelAdvisory"
```

### BEV-Specific Features

```python
# Request fuel consumption estimation
body["extraComputations"] = ["FUEL_CONSUMPTION"]

# Get eco-friendly route alternative
body["requestedReferenceRoutes"] = ["FUEL_EFFICIENT"]

# Vehicle-specific routing (helps with bridges, tunnels, weight limits)
body["routeModifiers"] = {
    "vehicleInfo": {
        "emissionType": "ELECTRIC"  # Tells Google it's an EV
    }
}
```

---

## Elevation API

### What It Does

Returns elevation data for locations on Earth. Critical for BEV energy modeling since:
- Climbing hills drains battery faster
- Descending allows regenerative braking
- Elevation changes significantly impact range predictions

### When to Use

- Building energy consumption models
- Predicting range based on route terrain
- Identifying energy-intensive route segments

### Request by Locations

```python
def get_elevations_at_points(locations):
    """
    Get elevation at specific points.
    
    Args:
        locations: List of "lat,lng" strings or tuples
    
    Returns:
        dict: Elevations for each point
    """
    url = f"{BASE_URL}/elevation/json"
    
    if isinstance(locations[0], tuple):
        locations = [f"{lat},{lng}" for lat, lng in locations]
    
    params = {
        "locations": "|".join(locations),
        "key": API_KEY
    }
    
    response = requests.get(url, params=params)
    return response.json()
```

### Request Along a Path

More useful for route analysis - samples elevation at regular intervals along a polyline:

```python
def get_elevation_along_path(encoded_polyline, samples=100):
    """
    Get elevation profile along an encoded polyline path.
    
    Args:
        encoded_polyline: Encoded polyline string from Directions API
        samples: Number of elevation points to sample along path
    
    Returns:
        dict: Elevation samples along the route
    """
    url = f"{BASE_URL}/elevation/json"
    
    params = {
        "path": f"enc:{encoded_polyline}",
        "samples": samples,
        "key": API_KEY
    }
    
    response = requests.get(url, params=params)
    return response.json()
```

### Response Structure

```python
{
    "status": "OK",
    "results": [
        {
            "elevation": 265.3,  # Meters above sea level
            "location": {"lat": 42.2808, "lng": -83.7430},
            "resolution": 9.543  # Data resolution in meters (lower is better)
        },
        {
            "elevation": 271.8,
            "location": {"lat": 42.2850, "lng": -83.7350},
            "resolution": 9.543
        },
        ...
    ]
}
```

### Computing Elevation Gain/Loss

```python
def compute_elevation_profile(elevation_response):
    """
    Analyze elevation data for energy modeling.
    
    Returns:
        dict: Elevation statistics for the route
    """
    elevations = [r["elevation"] for r in elevation_response["results"]]
    
    total_gain = 0
    total_loss = 0
    
    for i in range(1, len(elevations)):
        diff = elevations[i] - elevations[i-1]
        if diff > 0:
            total_gain += diff
        else:
            total_loss += abs(diff)
    
    return {
        "start_elevation_m": elevations[0],
        "end_elevation_m": elevations[-1],
        "min_elevation_m": min(elevations),
        "max_elevation_m": max(elevations),
        "total_gain_m": total_gain,
        "total_loss_m": total_loss,
        "net_elevation_change_m": elevations[-1] - elevations[0]
    }
```

### Limits

- Maximum 512 locations per request
- Maximum 512 samples along a path
- For long routes, split into segments:

```python
def get_elevation_for_long_route(polyline, total_samples=1000):
    """Handle routes needing more than 512 samples."""
    samples_per_request = 500
    all_results = []
    
    # You'll need to decode polyline, split points, re-encode segments
    # Or make multiple path requests with different portions
    
    for i in range(0, total_samples, samples_per_request):
        # Implementation depends on how you want to split the path
        pass
    
    return all_results
```

---

## Putting It Together: BEV Route Analysis

Here's how these APIs work together for your fleet routing system:

```python
import time

def analyze_route_for_bev(origin, destination, waypoints=None):
    """
    Complete route analysis for BEV energy modeling.
    
    Returns comprehensive data for ML energy prediction model.
    """
    # 1. Get base route with traffic
    directions = get_directions(
        origin, 
        destination, 
        waypoints=waypoints,
        departure_time=int(time.time())  # Current time for traffic
    )
    
    if directions["status"] != "OK":
        raise Exception(f"Directions failed: {directions['status']}")
    
    route = directions["routes"][0]
    leg = route["legs"][0]
    
    # 2. Get elevation profile along the route
    elevation_data = get_elevation_along_path(
        route["overview_polyline"]["points"],
        samples=min(512, max(50, leg["distance"]["value"] // 100))  # ~1 sample per 100m
    )
    
    elevation_profile = compute_elevation_profile(elevation_data)
    
    # 3. Compile analysis
    analysis = {
        "route": {
            "distance_m": leg["distance"]["value"],
            "duration_s": leg["duration"]["value"],
            "duration_traffic_s": leg.get("duration_in_traffic", {}).get("value"),
            "polyline": route["overview_polyline"]["points"],
            "num_steps": len(leg["steps"])
        },
        "elevation": elevation_profile,
        "energy_factors": {
            # These feed into your ML energy prediction model
            "distance_km": leg["distance"]["value"] / 1000,
            "elevation_gain_m": elevation_profile["total_gain_m"],
            "elevation_loss_m": elevation_profile["total_loss_m"],
            "avg_speed_kmh": (leg["distance"]["value"] / leg["duration"]["value"]) * 3.6,
        }
    }
    
    # 4. Optionally get eco-route alternative via Routes API
    try:
        eco_route = get_route_v2(
            {"latLng": {"latitude": float(origin.split(",")[0]), 
                       "longitude": float(origin.split(",")[1])}},
            {"latLng": {"latitude": float(destination.split(",")[0]), 
                       "longitude": float(destination.split(",")[1])}},
            compute_eco_route=True
        )
        
        for route in eco_route.get("routes", []):
            if "FUEL_EFFICIENT" in route.get("routeLabels", []):
                analysis["eco_alternative"] = {
                    "distance_m": route["distanceMeters"],
                    "duration_s": int(route["duration"].rstrip("s"))
                }
                break
    except Exception as e:
        analysis["eco_alternative"] = None
        analysis["eco_error"] = str(e)
    
    return analysis


# Example usage
if __name__ == "__main__":
    result = analyze_route_for_bev(
        origin="42.2808,-83.7430",  # Ann Arbor
        destination="42.3314,-83.0458"  # Detroit
    )
    
    print(f"Distance: {result['route']['distance_m']/1000:.1f} km")
    print(f"Duration: {result['route']['duration_s']/60:.0f} min")
    print(f"Elevation gain: {result['elevation']['total_gain_m']:.0f} m")
    print(f"Elevation loss: {result['elevation']['total_loss_m']:.0f} m")
```

---

## Decoding Polylines

Google returns encoded polylines to compress route geometry. Here's how to decode them:

```python
def decode_polyline(encoded):
    """
    Decode a Google Maps encoded polyline string into lat/lng coordinates.
    
    Args:
        encoded: Encoded polyline string
    
    Returns:
        List of (latitude, longitude) tuples
    """
    decoded = []
    index = 0
    lat = 0
    lng = 0
    
    while index < len(encoded):
        # Decode latitude
        shift = 0
        result = 0
        while True:
            b = ord(encoded[index]) - 63
            index += 1
            result |= (b & 0x1f) << shift
            shift += 5
            if b < 0x20:
                break
        lat += (~(result >> 1) if result & 1 else (result >> 1))
        
        # Decode longitude
        shift = 0
        result = 0
        while True:
            b = ord(encoded[index]) - 63
            index += 1
            result |= (b & 0x1f) << shift
            shift += 5
            if b < 0x20:
                break
        lng += (~(result >> 1) if result & 1 else (result >> 1))
        
        decoded.append((lat / 1e5, lng / 1e5))
    
    return decoded
```

Or use the `polyline` library:

```bash
pip install polyline
```

```python
import polyline

coords = polyline.decode("encoded_string_here")
# Returns list of (lat, lng) tuples
```

---

## Error Handling

```python
# Common status codes to handle
STATUS_CODES = {
    "OK": "Success",
    "ZERO_RESULTS": "No route found between points",
    "NOT_FOUND": "One or more locations not found",
    "MAX_WAYPOINTS_EXCEEDED": "Too many waypoints (max 25)",
    "MAX_ROUTE_LENGTH_EXCEEDED": "Route too long",
    "INVALID_REQUEST": "Malformed request",
    "OVER_DAILY_LIMIT": "API key quota exceeded",
    "OVER_QUERY_LIMIT": "Rate limit hit - slow down",
    "REQUEST_DENIED": "API key invalid or API not enabled",
    "UNKNOWN_ERROR": "Server error - retry"
}

def handle_api_response(response, api_name):
    """Standard error handling for Google Maps APIs."""
    status = response.get("status", "UNKNOWN_ERROR")
    
    if status == "OK":
        return response
    
    error_msg = STATUS_CODES.get(status, f"Unknown error: {status}")
    
    if status in ["OVER_QUERY_LIMIT", "UNKNOWN_ERROR"]:
        # Retry with exponential backoff
        raise RetryableError(f"{api_name}: {error_msg}")
    else:
        raise Exception(f"{api_name}: {error_msg}")
```

---

## Cost Optimization Tips

1. **Cache responses** - Same origin/destination pairs rarely change
2. **Batch Distance Matrix requests** - Maximize elements per request
3. **Use appropriate precision** - Don't over-sample elevation
4. **Field masks in Routes API** - Only request fields you need
5. **Avoid redundant calls** - Directions API returns polyline, use that for Elevation API

```python
import functools
import hashlib
import json

@functools.lru_cache(maxsize=1000)
def cached_directions(origin, destination, departure_hour=None):
    """
    Cache directions by origin, destination, and hour.
    departure_hour bins requests to same hour for caching.
    """
    cache_key = hashlib.md5(
        f"{origin}|{destination}|{departure_hour}".encode()
    ).hexdigest()
    
    # Check persistent cache (Redis, file, etc.)
    # ...
    
    return get_directions(origin, destination)
```

---

## Quick Reference

### API Endpoints

| API | Endpoint |
|-----|----------|
| Directions | `https://maps.googleapis.com/maps/api/directions/json` |
| Distance Matrix | `https://maps.googleapis.com/maps/api/distancematrix/json` |
| Routes (v2) | `https://routes.googleapis.com/directions/v2:computeRoutes` |
| Elevation | `https://maps.googleapis.com/maps/api/elevation/json` |

### Required Parameters

| API | Required |
|-----|----------|
| Directions | `origin`, `destination`, `key` |
| Distance Matrix | `origins`, `destinations`, `key` |
| Routes | `origin`, `destination`, `X-Goog-Api-Key` header |
| Elevation | `locations` OR `path` + `samples`, `key` |

### Useful Links

- [Directions API Docs](https://developers.google.com/maps/documentation/directions)
- [Distance Matrix Docs](https://developers.google.com/maps/documentation/distance-matrix)
- [Routes API Docs](https://developers.google.com/maps/documentation/routes)
- [Elevation API Docs](https://developers.google.com/maps/documentation/elevation)
- [API Pricing Calculator](https://mapsplatform.google.com/pricing/)
- [Cloud Console](https://console.cloud.google.com)
