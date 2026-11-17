import React from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import RouteMap from './RouteMap';
import BenchmarkRouteMap from './BenchmarkRouteMap';
import { fetchRoadRoute } from './roadRoutes';

jest.mock('./roadRoutes', () => ({ ...jest.requireActual('./roadRoutes'), fetchRoadRoute: jest.fn() }));
jest.mock('./DriveSimulation', () => ({ trucks }) => <div data-testid="drive">{JSON.stringify(trucks)}</div>);
jest.mock('@react-google-maps/api', () => ({
  useJsApiLoader: () => ({ isLoaded: true }),
  GoogleMap: ({ children }) => <div>{children}</div>,
  PolylineF: ({ path, options }) => <div data-testid="road" data-color={options.strokeColor}>{JSON.stringify(path)}</div>,
  Marker: ({ position, title }) => <div title={title} data-position={JSON.stringify(position)} />,
  MarkerF: () => null,
  InfoWindow: ({ children }) => <div>{children}</div>,
}));

const locations = [{ lat: 0, lng: 0 }, { lat: 1, lng: 1 }];
const bend = { lat: 0, lng: 1 };
const outward = [locations[0], bend, locations[1]];
const geometry = { path: outward, legs: [{ path: outward, time_s: 60, distance_m: 100 }] };
const fleet = (order = [0, 1]) => ({ ortools_fleet: { routes: [{ vehicle_id: 1,
  vehicle_name: 'Truck 1', order, ordered_locations: order.map(i => locations[i]),
  legs: [{ time_s: 50 }], traffic: { actual_leg_times_s: [60] },
  battery_depletion: { leg_index: 0, fraction: .5 },
}] } });

beforeEach(() => {
  window.google = { maps: { SymbolPath: { CIRCLE: 'circle' } } };
  fetchRoadRoute.mockReset();
});
afterEach(() => { delete window.google; });

test('open truck routes keep road bends for drawing, playback and depletion markers', async () => {
  fetchRoadRoute.mockResolvedValue(geometry);
  render(<RouteMap locations={locations} results={fleet()} drive={{ runId: 1, speed: 1 }} />);
  expect(JSON.parse((await screen.findByTestId('road')).textContent)).toEqual(outward);
  expect(fetchRoadRoute).toHaveBeenCalledWith(locations);
  expect(JSON.parse(screen.getByTitle('Vehicle 1: battery runs out here').dataset.position)).toEqual(bend);
  const truck = JSON.parse(screen.getByTestId('drive').textContent)[0];
  expect(truck.legs[0]).toMatchObject({ path: outward, durationS: 60, plannedS: 50, to: locations[1] });
  fireEvent.click(screen.getByRole('button', { name: 'Vehicle 1' }));
  expect(screen.queryByTestId('road')).not.toBeInTheDocument();
  expect(screen.queryByTitle('Vehicle 1: battery runs out here')).not.toBeInTheDocument();
});

test('single-truck tours request the return to the depot', async () => {
  const closed = { path: [...outward, locations[0]], legs: [...geometry.legs, { path: [locations[1], locations[0]] }] };
  fetchRoadRoute.mockResolvedValue(closed);
  render(<RouteMap locations={locations} results={{ optimal: { order: [0, 1], ordered_locations: locations } }} />);
  await screen.findByTestId('road');
  expect(fetchRoadRoute).toHaveBeenCalledWith([...locations, locations[0]]);
});

test.each(['planner', 'benchmark'])('%s shows route failures and can retry', async kind => {
  fetchRoadRoute.mockRejectedValueOnce(new Error('Routes API is not enabled')).mockResolvedValue(geometry);
  render(kind === 'planner'
    ? <RouteMap locations={locations} results={fleet()} />
    : <BenchmarkRouteMap locations={locations} routes={[[0, 1]]} slots={[2]} />);
  expect(await screen.findByRole('alert')).toHaveTextContent('Routes API is not enabled');
  expect(screen.queryByTestId('road')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Retry road routes' }));
  const road = await screen.findByTestId('road');
  expect(JSON.parse(road.textContent)).toEqual(outward);
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  if (kind === 'benchmark') {
    expect(road).toHaveAttribute('data-color', '#10b981');
    fireEvent.click(screen.getByRole('button', { name: 'Truck 3' }));
    expect(screen.queryByTestId('road')).not.toBeInTheDocument();
  }
});

test('a late road response cannot replace a newer plan', async () => {
  let finishOld;
  fetchRoadRoute.mockImplementationOnce(() => new Promise(resolve => { finishOld = resolve; }));
  const { rerender } = render(<RouteMap locations={locations} results={fleet()} />);
  const reversed = { path: [...outward].reverse(), legs: [{ path: [...outward].reverse() }] };
  fetchRoadRoute.mockResolvedValue(reversed);
  rerender(<RouteMap locations={locations} results={fleet([1, 0])} />);
  await waitFor(() => expect(JSON.parse(screen.getByTestId('road').textContent)).toEqual(reversed.path));
  await act(async () => { finishOld(geometry); });
  expect(JSON.parse(screen.getByTestId('road').textContent)).toEqual(reversed.path);
});
