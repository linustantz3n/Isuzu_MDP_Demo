import { assignDriver, applyStyleOverrides, effectiveStyle } from './driverState';

test('moving a driver preserves trucks and clears overrides on changed assignments', () => {
  const wang = { id: 'wang', name: 'Wang', driving_style: 'aggressive', battery_packs: 9 };
  const fleet = [1, 2].map(id => ({ id, name: `Truck ${id}`, battery_packs: 4, weight_lbs: 13000 }));
  const assigned = assignDriver(fleet, 1, wang);
  const overridden = applyStyleOverrides(assigned, [{ vehicle_id: 1, driving_style_override: 'eco' }]);
  expect(effectiveStyle(overridden[0])).toBe('eco');
  expect(wang.driving_style).toBe('aggressive');
  expect(effectiveStyle(applyStyleOverrides(overridden, [{ vehicle_id: 1, driving_style_override: null }])[0])).toBe('aggressive');
  const moved = assignDriver(overridden, 2, wang);
  expect(moved.map(effectiveStyle)).toEqual(['normal', 'aggressive']);
  expect(moved.map(v => v.battery_packs)).toEqual([4, 4]);
  expect(moved[0].driver_id).toBeNull();
});
