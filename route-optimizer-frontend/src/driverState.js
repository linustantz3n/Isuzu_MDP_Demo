export const DRIVING_STYLES = ['eco', 'normal', 'aggressive'];

export const effectiveStyle = v => v.driving_style_override || v.driver_snapshot?.driving_style || v.driver_default_style || 'normal';

export function assignDriver(vehicles, vehicleId, driver) {
  return vehicles.map(v => {
    if (v.id !== vehicleId && (!driver || v.driver_id !== driver.id)) return v;
    const p = v.id === vehicleId ? driver : null;
    return { ...v, driver_id: p?.id || null, driver: p?.name || '',
      driver_snapshot: p ? { id: p.id, name: p.name, driving_style: p.driving_style || 'normal' } : null,
      driver_default_style: p?.driving_style || 'normal', driving_style_override: null };
  });
}

export function applyStyleOverrides(vehicles, patches) {
  return vehicles.map(v => {
    const patch = patches.find(p => p.vehicle_id === v.id);
    return patch ? { ...v, driving_style_override: patch.driving_style_override } : v;
  });
}
