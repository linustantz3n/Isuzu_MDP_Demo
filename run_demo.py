"""Run a local demo using saved Anaheim road data and the real City NN solver.

Start the frontend with REACT_APP_SAVED_DEMO=true npm start.
No Google or LLM credentials are required for the standard driver commands.
"""
from pathlib import Path
import tempfile

from flask import jsonify, request

import route_optimizer_backend as backend
from benchmark import load_scenario
from drivers import resolve_vehicles


def configure_demo():
    scenario = load_scenario('demo_anaheim')
    locations = scenario['locations']
    elevations = scenario.get('elevation_m') or [0.] * len(locations)
    backend.MEMORY_PATH = str(Path(tempfile.gettempdir()) / 'isuzu-nn-demo-drivers.json')
    memory = backend.load_memory()
    for name, style in [('Wang', 'aggressive'), ('Alex', 'eco'), ('Jordan', 'normal')]:
        memory['drivers'].setdefault(name.lower(), {'id': name.lower(), 'name': name, 'driving_style': style})
    backend.save_memory(memory)

    def indices(points):
        out = []
        for point in points:
            found = next((i for i, p in enumerate(locations)
                          if abs(p['lat'] - point['lat']) < 1e-6 and abs(p['lng'] - point['lng']) < 1e-6), None)
            if found is None:
                raise ValueError('This saved demo supports the Anaheim sample stops. Live addresses require Google Maps credentials.')
            out.append(found)
        return out

    def geocode(address):
        found = next((p for p in locations if address.strip().lower() in
                      (p['address'].lower(), p['formatted_address'].lower())), None)
        if found is None:
            raise ValueError('Load the Anaheim sample to use saved road data. Other addresses require Google Maps credentials.')
        return dict(found)

    def matrices(points, departure_ts=None):
        if departure_ts is not None:
            raise ValueError('The saved demo uses cached road times. Live traffic requires Google Maps credentials.')
        order = indices(points)
        return {key: [[scenario[source][i][j] for j in order] for i in order]
                for key, source in [('distance', 'distance_m'), ('time', 'time_s')]}

    backend.geocode_address = geocode
    backend.get_distance_matrix = matrices
    backend.get_elevation_data = lambda points: [elevations[i] for i in indices(points)]

    @backend.app.before_request
    def saved_data_only():
        if request.path in ['/api/directions', '/api/replan_incidents', '/api/reroute_from', '/api/plan_charging']:
            return jsonify(status='error', message='This live Google Maps feature is unavailable in the saved-data demo.'), 400

    @backend.app.get('/api/demo/bootstrap')
    def bootstrap():
        profiles = backend.load_memory()['drivers']
        vehicles = resolve_vehicles([{'id': i, 'name': f'Truck {i}', 'driver_id': key,
            'battery_packs': 8, 'battery_kwh': 160, 'weight_lbs': 14764}
            for i, key in enumerate(['wang', 'alex', 'jordan'], 1)], profiles)
        return jsonify(status='ok', name='Anaheim saved-data demo', locations=locations,
                       vehicles=vehicles, precomputed={**matrices(locations), 'elevations': elevations},
                       goals={**backend.DEFAULT_GOALS, 'distance': 'low', 'energy': 'high', 'balance': 'medium'})


if __name__ == '__main__':
    configure_demo()
    print('Anaheim saved-data demo: real City NN inference, cached road matrices, schematic map.')
    backend.app.run(host='127.0.0.1', port=5001, debug=False)
