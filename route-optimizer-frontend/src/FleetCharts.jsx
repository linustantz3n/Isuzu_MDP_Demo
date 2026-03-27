import React from 'react';
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, Cell, LabelList
} from 'recharts';

const VEHICLE_COLORS = [
  '#a855f7', '#3b82f6', '#10b981', '#f59e0b',
  '#ef4444', '#06b6d4', '#8b5cf6', '#ec4899', '#84cc16', '#f97316'
];
const SINGLE_COLOR = '#facc15';
const MAKESPAN_COLOR = '#f97316';

const TOOLTIP_STYLE = {
  backgroundColor: '#1e293b',
  border: '1px solid #475569',
  borderRadius: '8px',
  color: '#e2e8f0',
  fontSize: '13px'
};

const AXIS_STYLE = { fill: '#94a3b8', fontSize: 12 };

export default function FleetCharts({ results }) {
  if (!results?.optimal) return null;

  const hasFleet = !!results.ortools_fleet;

  // ── Duration chart data ─────────────────────────────────────────
  const durationData = [
    {
      name: 'Single (Held-Karp)',
      minutes: Math.round(results.optimal.total_time_s / 60),
      fill: SINGLE_COLOR
    }
  ];

  if (hasFleet) {
    results.ortools_fleet.routes.forEach((route, idx) => {
      durationData.push({
        name: `Vehicle ${route.vehicle_id}`,
        minutes: Math.round(route.total_time_s / 60),
        fill: VEHICLE_COLORS[idx % VEHICLE_COLORS.length]
      });
    });
    durationData.push({
      name: 'Fleet Makespan',
      minutes: Math.round(results.ortools_fleet.makespan_s / 60),
      fill: MAKESPAN_COLOR
    });
  }

  // ── Energy chart data ───────────────────────────────────────────
  const energyData = [];
  if (results.optimal?.total_energy_kwh != null) {
    energyData.push({
      name: 'Single Vehicle',
      kwh: parseFloat(results.optimal.total_energy_kwh.toFixed(2)),
      fill: SINGLE_COLOR
    });
  }
  if (hasFleet && results.ortools_fleet.routes) {
    results.ortools_fleet.routes.forEach((route, idx) => {
      if (route.total_energy_kwh != null) {
        energyData.push({
          name: `Vehicle ${route.vehicle_id}`,
          kwh: parseFloat(route.total_energy_kwh.toFixed(2)),
          fill: VEHICLE_COLORS[idx % VEHICLE_COLORS.length]
        });
      }
    });
    if (results.ortools_fleet.total_energy_kwh != null) {
      energyData.push({
        name: 'Fleet Total',
        kwh: parseFloat(results.ortools_fleet.total_energy_kwh.toFixed(2)),
        fill: MAKESPAN_COLOR
      });
    }
  }

  // Dynamic height based on number of bars
  const durationHeight = Math.max(180, durationData.length * 46);
  const energyHeight = Math.max(120, energyData.length * 46);

  return (
    <div className="space-y-4">

      {/* Duration comparison */}
      <div className="bg-slate-800 rounded-xl p-6 border border-slate-700">
        <h2 className="text-lg font-semibold text-white mb-1">Route Duration</h2>
        <p className="text-slate-500 text-xs mb-4">
          Fleet makespan = wall-clock completion time (vehicles depart simultaneously)
        </p>
        <ResponsiveContainer width="100%" height={durationHeight}>
          <BarChart data={durationData} layout="vertical" margin={{ left: 20, right: 60, top: 4, bottom: 4 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#334155" horizontal={false} />
            <XAxis
              type="number"
              stroke="#475569"
              tick={AXIS_STYLE}
              label={{ value: 'Minutes', position: 'insideBottom', fill: '#64748b', offset: -2, fontSize: 11 }}
            />
            <YAxis
              type="category"
              dataKey="name"
              stroke="#475569"
              tick={AXIS_STYLE}
              width={130}
            />
            <Tooltip
              formatter={(val) => [`${val} min`, 'Duration']}
              contentStyle={TOOLTIP_STYLE}
              cursor={{ fill: 'rgba(255,255,255,0.04)' }}
            />
            <Bar dataKey="minutes" radius={[0, 5, 5, 0]} maxBarSize={32}>
              {durationData.map((entry, i) => <Cell key={i} fill={entry.fill} />)}
              <LabelList
                dataKey="minutes"
                position="right"
                formatter={(v) => `${v} min`}
                style={{ fill: '#94a3b8', fontSize: 12 }}
              />
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>

      {/* Energy comparison */}
      {energyData.length >= 2 && (
        <div className="bg-slate-800 rounded-xl p-6 border border-slate-700">
          <h2 className="text-lg font-semibold text-white mb-1">Energy Consumption</h2>
          <p className="text-slate-500 text-xs mb-4">
            Mercedes eActros model (R²=0.474) — accounts for speed, weight, temperature, elevation
          </p>
          <ResponsiveContainer width="100%" height={energyHeight}>
            <BarChart data={energyData} layout="vertical" margin={{ left: 20, right: 70, top: 4, bottom: 4 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#334155" horizontal={false} />
              <XAxis
                type="number"
                stroke="#475569"
                tick={AXIS_STYLE}
                label={{ value: 'kWh', position: 'insideBottom', fill: '#64748b', offset: -2, fontSize: 11 }}
              />
              <YAxis
                type="category"
                dataKey="name"
                stroke="#475569"
                tick={AXIS_STYLE}
                width={130}
              />
              <Tooltip
                formatter={(val) => [`${val} kWh`, 'Energy']}
                contentStyle={TOOLTIP_STYLE}
                cursor={{ fill: 'rgba(255,255,255,0.04)' }}
              />
              <Bar dataKey="kwh" radius={[0, 5, 5, 0]} maxBarSize={32}>
                {energyData.map((entry, i) => <Cell key={i} fill={entry.fill} />)}
                <LabelList
                  dataKey="kwh"
                  position="right"
                  formatter={(v) => `${v} kWh`}
                  style={{ fill: '#94a3b8', fontSize: 12 }}
                />
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}
