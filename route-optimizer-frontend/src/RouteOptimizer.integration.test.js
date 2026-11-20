import React from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import RouteOptimizer from './RouteOptimizer';
import fixture from './__fixtures__/nnPlan.json';

jest.mock('./RouteMap', () => () => null);
jest.mock('./FleetCharts', () => () => null);
jest.mock('./PlanComparison', () => () => null);
jest.mock('./AIChatbot', () => ({ onApplyChange, onTurn }) => <button onClick={() => {
  const change = { axis: 'vehicle_style_overrides', op: 'set', value: [{ vehicle_id: 1, driving_style_override: 'eco' }], label: 'Truck 1: Eco' };
  onApplyChange(change);
  onTurn({ changes: [change], text: 'Use Eco for Truck 1', lang: 'en' });
}}>Apply chat override</button>);

const wang = { id: 'wang', name: 'Wang', driving_style: 'aggressive' };
const locations = ['Depot', 'A', 'B'].map((address, i) => ({ address, formatted_address: address, lat: i, lng: 0 }));
const jsonResponse = data => ({ json: async () => data });
const solveResponse = body => {
  const response = JSON.parse(JSON.stringify(fixture));
  response.vehicles = body.vehicles.map(v => ({ ...v, driver_default_style: v.driver_snapshot?.driving_style || 'normal',
    effective_driving_style: v.driving_style_override || v.driver_snapshot?.driving_style || 'normal',
    style_source: v.driving_style_override ? 'run_override' : v.driver_id ? 'driver' : 'default' }));
  Object.assign(response.results.optimal, response.vehicles[0]);
  return jsonResponse(response);
};

test('UI and chat re-solve using driver snapshots and reject an older response', async () => {
  const requests = [];
  let delayed;
  global.fetch = jest.fn(async (url, options) => {
    const body = options?.body ? JSON.parse(options.body) : null;
    if (url.endsWith('/memory')) return jsonResponse({ status: 'ok', drivers: [wang] });
    if (url.endsWith('/geocode')) return jsonResponse({ status: 'ok', result: locations[0] });
    if (url.endsWith('/geocode_batch')) return jsonResponse({ status: 'ok', results: locations.slice(1) });
    if (url.endsWith('/suggest')) return jsonResponse({ suggestion: null });
    if (url.endsWith('/optimize')) {
      requests.push(body);
      if (body.vehicles[0].driving_style_override === 'eco') return new Promise(resolve => { delayed = () => resolve(solveResponse(body)); });
      return solveResponse(body);
    }
    throw new Error(`Unexpected request: ${url}`);
  });
  Element.prototype.scrollTo = jest.fn();
  render(<RouteOptimizer />);
  await screen.findByRole('option', { name: 'Wang · aggressive' });
  fireEvent.change(screen.getByPlaceholderText('e.g. 1400 S Douglass Rd, Anaheim, CA'), { target: { value: 'Depot' } });
  fireEvent.change(screen.getByPlaceholderText('Stop 1'), { target: { value: 'A' } });
  fireEvent.change(screen.getByPlaceholderText('Stop 2'), { target: { value: 'B' } });
  fireEvent.click(screen.getByRole('button', { name: 'Validate Addresses' }));
  await waitFor(() => expect(screen.getByRole('button', { name: 'Optimize Routes' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: 'Optimize Routes' }));
  await screen.findByText('Optimization Results');

  fireEvent.change(screen.getByLabelText('Vehicle 1 driver'), { target: { value: 'wang' } });
  await waitFor(() => expect(requests).toHaveLength(2));
  expect(requests[1].vehicles[0]).toMatchObject({ driver_snapshot: wang, battery_packs: 4, weight_lbs: 13000 });
  expect(requests[1].precomputed.time).toEqual(fixture.matrices.time);
  await waitFor(() => expect(screen.queryByText(/Re-solving with your chat changes/)).not.toBeInTheDocument());

  fireEvent.click(screen.getByRole('button', { name: 'Apply chat override' }));
  await waitFor(() => expect(requests).toHaveLength(3));
  expect(requests[2].vehicles[0].driving_style_override).toBe('eco');
  fireEvent.change(screen.getByLabelText('Vehicle 1 run driving style'), { target: { value: 'normal' } });
  await waitFor(() => expect(requests).toHaveLength(4));
  await waitFor(() => expect(screen.queryByText(/Re-solving with your chat changes/)).not.toBeInTheDocument());
  await act(async () => { delayed(); });
  expect(screen.getByLabelText('Vehicle 1 run driving style')).toHaveValue('normal');
  expect(screen.getByLabelText('Vehicle 1 driver')).toHaveValue('wang');
  expect(wang.driving_style).toBe('aggressive');
  delete global.fetch;
});

test('the Simulation tab solves the shared stops on measured traffic; the Solver cannot pick it', async () => {
  const requests = [];
  global.fetch = jest.fn(async (url, options) => {
    const body = options?.body ? JSON.parse(options.body) : null;
    if (url.endsWith('/memory')) return jsonResponse({ status: 'ok', drivers: [] });
    if (url.endsWith('/geocode')) return jsonResponse({ status: 'ok', result: locations[0] });
    if (url.endsWith('/geocode_batch')) return jsonResponse({ status: 'ok', results: locations.slice(1) });
    if (url.endsWith('/optimize')) { requests.push(body); return solveResponse(body); }
    throw new Error(`Unexpected request: ${url}`);
  });
  Element.prototype.scrollTo = jest.fn();
  render(<RouteOptimizer />);
  expect(screen.queryByRole('option', { name: 'Measured traffic (hindsight)' })).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole('button', { name: 'Simulation' }));
  // The same inputs show on both tabs; edit the Simulation tab's copy
  const last = (list) => list[list.length - 1];
  fireEvent.change(last(screen.getAllByPlaceholderText('e.g. 1400 S Douglass Rd, Anaheim, CA')), { target: { value: 'Depot' } });
  fireEvent.change(last(screen.getAllByPlaceholderText('Stop 1')), { target: { value: 'A' } });
  fireEvent.change(last(screen.getAllByPlaceholderText('Stop 2')), { target: { value: 'B' } });
  expect(screen.getAllByPlaceholderText('Stop 1')[0]).toHaveValue('A');      // shared with the Solver tab
  fireEvent.click(last(screen.getAllByRole('button', { name: 'Validate Addresses' })));
  const optimize = screen.getByRole('button', { name: 'Optimize on Measured Traffic' });
  await waitFor(() => expect(optimize).toBeEnabled());
  fireEvent.change(screen.getByLabelText('Simulation departure'), { target: { value: '08:15' } });
  fireEvent.click(optimize);
  await waitFor(() => expect(requests).toHaveLength(1));
  expect(requests[0]).toMatchObject({ traffic_source: 'ground_truth', departure_time: '2025-11-12T08:15' });
  expect(requests[0].precomputed).toBeUndefined();
  // The Solver tab has no plan of its own yet
  expect(screen.queryByText('Optimization Results')).not.toBeInTheDocument();
  delete global.fetch;
});
