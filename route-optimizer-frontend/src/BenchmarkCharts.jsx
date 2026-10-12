import React from 'react';
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell, LabelList
} from 'recharts';

export const FAMILY_COLORS = {
  ortools: '#C8002F',
  exact: '#facc15',
  naive: '#64748b',
  tsp_heuristic: '#3b82f6',
  cluster_first: '#10b981',
  vrp_heuristic: '#a855f7',
};

const TOOLTIP_STYLE = {
  backgroundColor: '#1e293b',
  border: '1px solid #475569',
  borderRadius: '8px',
  color: '#e2e8f0',
  fontSize: '13px'
};

const AXIS_STYLE = { fill: '#94a3b8', fontSize: 12 };

export function algoColor(row) {
  if (row.algorithm === 'ortools_cheap') return '#f87171';
  return FAMILY_COLORS[row.family] || '#64748b';
}

function HorizontalBars({ title, subtitle, data, dataKey, unit }) {
  const height = Math.max(160, data.length * 40);
  return (
    <div className="bg-slate-900 rounded-xl p-5 border border-slate-800">
      <h3 className="text-sm font-semibold text-white">{title}</h3>
      {subtitle && <p className="text-slate-500 text-xs mt-0.5 mb-3">{subtitle}</p>}
      <ResponsiveContainer width="100%" height={height}>
        <BarChart data={data} layout="vertical" margin={{ left: 10, right: 70, top: 4, bottom: 4 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#334155" horizontal={false} />
          <XAxis type="number" stroke="#475569" tick={AXIS_STYLE} />
          <YAxis type="category" dataKey="name" stroke="#475569" tick={AXIS_STYLE} width={190} />
          <Tooltip
            formatter={(v) => [`${v} ${unit}`, title]}
            contentStyle={TOOLTIP_STYLE}
            cursor={{ fill: 'rgba(255,255,255,0.04)' }}
          />
          <Bar dataKey={dataKey} radius={[0, 5, 5, 0]} maxBarSize={26}>
            {data.map((d, i) => <Cell key={i} fill={d.fill} />)}
            <LabelList
              dataKey={dataKey}
              position="right"
              formatter={(v) => `${v} ${unit}`}
              style={{ fill: '#94a3b8', fontSize: 12 }}
            />
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

export default function BenchmarkCharts({ result, selectedK }) {
  const gapData = result.rows
    .filter(r => r.num_vehicles === selectedK && r.metrics?.gap_to_best_distance_pct != null)
    .sort((a, b) => a.metrics.total_distance_m - b.metrics.total_distance_m)
    .map(r => ({ name: r.label, value: r.metrics.gap_to_best_distance_pct, fill: algoColor(r) }));

  return (
    <HorizontalBars
      title="Distance Gap to Best"
      subtitle="How much farther each algorithm drives than the best feasible result (0% = best)"
      data={gapData}
      dataKey="value"
      unit="%"
    />
  );
}
