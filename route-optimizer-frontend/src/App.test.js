import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import App from './App';

test('loads driver defaults, assigns a truck and applies only a run override', async () => {
  global.fetch = jest.fn().mockResolvedValue({ json: async () => ({ status: 'ok', drivers: [
    { id: 'wang', name: 'Wang', driving_style: 'aggressive' },
  ] }) });
  render(<App />);
  await screen.findByRole('option', { name: 'Wang · aggressive' });
  fireEvent.change(screen.getByLabelText('Vehicle 1 driver'), { target: { value: 'wang' } });
  expect(screen.getByRole('option', { name: 'Use driver default (aggressive)' })).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Vehicle 1 run driving style'), { target: { value: 'eco' } });
  expect(screen.getByText(/run override; driver profile unchanged/)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Vehicle 1 run driving style'), { target: { value: '' } });
  await waitFor(() => expect(screen.getByLabelText('Vehicle 1 driver')).toHaveValue('wang'));
  expect(screen.queryByText(/R²=0.9997/)).not.toBeInTheDocument();
  delete global.fetch;
});
