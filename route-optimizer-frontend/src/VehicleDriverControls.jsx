import React from 'react';
import { DRIVING_STYLES, effectiveStyle } from './driverState';

const SELECT = 'w-full px-2 py-1 bg-slate-700 border border-slate-600 rounded text-xs text-white';

export default function VehicleDriverControls({ vehicle, drivers, onAssign, onOverride }) {
  const v = vehicle;
  return <div className="space-y-2 mb-3">
    <label className="block text-[10px] text-slate-400">
      Driver
      <select aria-label={`${v.name} driver`} className={SELECT} value={v.driver_id || ''}
        onChange={e => onAssign(drivers.find(p => p.id === e.target.value) || null)}>
        <option value="">Unassigned · Normal default</option>
        {drivers.map(p => <option key={p.id} value={p.id}>{p.name} · {p.driving_style || 'normal'}</option>)}
        {v.driver_id && !drivers.some(p => p.id === v.driver_id) && <option value={v.driver_id}>{v.driver} · saved in this run</option>}
      </select>
    </label>
    <label className="block text-[10px] text-slate-400">
      Run driving style
      <select aria-label={`${v.name} run driving style`} className={SELECT} value={v.driving_style_override || ''}
        onChange={e => onOverride(e.target.value || null)}>
        <option value="">Use driver default ({v.driver_snapshot?.driving_style || v.driver_default_style || 'normal'})</option>
        {DRIVING_STYLES.map(s => <option key={s} value={s}>{s[0].toUpperCase() + s.slice(1)} · this run only</option>)}
      </select>
    </label>
    <p className="text-[10px] text-slate-400">Planning with <strong className="text-teal-300">{effectiveStyle(v)}</strong>
      {v.driving_style_override ? ' · run override; driver profile unchanged' : v.driver_id ? ' · driver default' : ' · unassigned default'}</p>
  </div>;
}
