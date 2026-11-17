"""Routes API proxy contract, including ordered per-leg map geometry."""
from copy import deepcopy

import pytest
import requests

import route_optimizer_backend as be

POINTS = [{'lat': 33.8, 'lng': -118}, {'lat': 33.81, 'lng': -118.02}, {'lat': 33.83, 'lng': -118.04}]


def google_route(order):
    points = [POINTS[i] for i in order]
    return {'routes': [{'distanceMeters': 3000, 'duration': '180.5s',
        'polyline': {'encodedPolyline': be._encode_polyline(points)},
        'legs': [{'distanceMeters': 1000, 'duration': '60.25s',
                  'polyline': {'encodedPolyline': be._encode_polyline([a, b])}}
                 for a, b in zip(points, points[1:])]}]}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(be, 'GOOGLE_API_KEY', 'test-key')
    return be.app.test_client()


@pytest.mark.parametrize('order', [[0, 2, 1, 0], [0, 2, 1]])
def test_routes_api_preserves_closed_and_breakdown_stop_order(client, monkeypatch, order):
    seen = {}
    data = google_route(order)
    def post(url, **kwargs):
        seen.update(url=url, **kwargs)
        class Response:
            status_code = 200
            def json(self): return data
        return Response()
    monkeypatch.setattr(be.requests, 'post', post)
    response = client.post('/api/directions', json={'locations': POINTS, 'order': order})
    assert response.status_code == 200
    assert seen['url'] == 'https://routes.googleapis.com/directions/v2:computeRoutes'
    assert seen['headers']['X-Goog-Api-Key'] == 'test-key'
    assert 'routes.legs.polyline.encodedPolyline' in seen['headers']['X-Goog-FieldMask']
    assert seen['timeout'] == 30
    body = seen['json']
    assert body['optimizeWaypointOrder'] is False and body['polylineQuality'] == 'HIGH_QUALITY'
    assert body['destination']['location']['latLng']['latitude'] == POINTS[order[-1]]['lat']
    assert body['intermediates'] == [be._routes_waypoint(POINTS[i]) for i in order[1:-1]]
    assert response.json['directions'] == data
    route = response.json['route']
    assert route['path'] == [POINTS[i] for i in order]
    assert len(route['legs']) == len(order) - 1
    assert route['legs'][0]['path'] == [POINTS[0], POINTS[2]]
    assert route['legs'][0]['time_s'] == 60.25
    assert 'test-key' not in response.get_data(as_text=True)


@pytest.mark.parametrize('payload', [
    {}, {'locations': POINTS, 'order': [0, 9]}, {'locations': POINTS, 'order': [True, 1]},
    {'locations': POINTS, 'order': [0]}, {'locations': POINTS, 'order': [0] * 28},
    {'locations': [{'lat': 95, 'lng': 0}, POINTS[0]]}, {'locations': None}, [],
])
def test_invalid_directions_do_not_call_google(client, monkeypatch, payload):
    def forbidden(*a, **kw): pytest.fail('Invalid input must not call Google')
    monkeypatch.setattr(be.requests, 'post', forbidden)
    assert client.post('/api/directions', json=payload).status_code == 400


def test_missing_key_is_explicit(client, monkeypatch):
    monkeypatch.setattr(be, 'GOOGLE_API_KEY', '')
    response = client.post('/api/directions', json={'locations': POINTS})
    assert response.status_code == 503 and response.json['code'] == 'google_key_missing'


@pytest.mark.parametrize('failure, status', [('timeout', 504), ('denied', 502), ('empty', 502), ('missing_leg', 502), ('malformed', 502)])
def test_google_failure_is_reported_not_drawn_as_a_straight_line(client, monkeypatch, failure, status):
    def post(*a, **kw):
        if failure == 'timeout': raise requests.Timeout()
        data = google_route([0, 1, 2])
        if failure == 'denied': data = {'error': {'message': 'Routes API is not enabled'}}
        if failure == 'empty': data = {'routes': []}
        if failure == 'missing_leg': data['routes'][0]['legs'].pop()
        if failure == 'malformed': data = []
        class Response:
            status_code = 403 if failure == 'denied' else 200
            def json(self): return data
        return Response()
    monkeypatch.setattr(be.requests, 'post', post)
    response = client.post('/api/directions', json={'locations': POINTS})
    assert response.status_code == status and response.json['status'] == 'error'
    assert 'route' not in response.json


def test_coincident_pickup_has_a_zero_length_leg():
    data = deepcopy(google_route([0, 0, 1]))
    data['routes'][0]['legs'][0] = {'duration': '0s', 'distanceMeters': 0}
    route = be.direction_geometry(data, [POINTS[0], POINTS[0], POINTS[1]])
    assert route['legs'][0] == {'path': [POINTS[0], POINTS[0]], 'distance_m': 0, 'time_s': 0}
