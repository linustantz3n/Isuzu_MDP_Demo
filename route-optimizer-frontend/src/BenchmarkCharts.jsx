import React from 'react';
import {
  BarChart, Bar, LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend,
  ResponsiveContainer, Cell, LabelList
} from 'recharts';

export const FAMILY_COLORS = {
  ortools: '#C8002F',
  exact: '#facc15',
  naive: '#64748b',
  tsp_heuristic: '#3b82f6',
  cluster_first: '#10b981',
  vrp_heuristic: '#a855f7',
};

// Distinct colors for the distance-vs-K line chart
const LINE_COLORS = {
  ortools: '#C8002F',
  ortools_cheap: '#f87171',
  rr_nn: '#64748b',
  rr_2opt: '#94a3b8',
  kmeans_2opt: '#10b981',
  sweep_2opt: '#06b6d4',
  clarke_wright: '#a855f7',
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
  const rows = result.rows.filter(r => r.num_vehicles === selectedK && r.metrics);
  const byDistance = [...rows].sort((a, b) => a.metrics.total_distance_m - b.metrics.total_distance_m);

  const distanceData = byDistance.map(r => ({
    name: r.label, value: r.metrics.total_distance_mi, fill: algoColor(r),
  }));
  const timeData = [...rows]
    .sort((a, b) => a.metrics.total_drive_time_s - b.metrics.total_drive_time_s)
    .map(r => ({ name: r.label, value: r.metrics.total_drive_time_min, fill: algoColor(r) }));
  const gapData = byDistance
    .filter(r => r.metrics.gap_to_best_distance_pct != null)
    .map(r => ({ name: r.label, value: r.metrics.gap_to_best_distance_pct, fill: algoColor(r) }));

  // Distance vs fleet size, for algorithms that can run a fleet
  const fleetAlgos = result.algorithms.filter(a => a.vehicles !== 'single');
  const kValues = Array.from({ length: result.max_vehicles }, (_, i) => i + 1);
  const lineData = kValues.map(k => {
    const point = { k: `K=${k}` };
    result.rows
      .filter(r => r.num_vehicles === k && r.metrics)
      .forEach(r => { point[r.algorithm] = r.metrics.total_distance_mi; });
    return point;
  });

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <HorizontalBars
          title="Total Distance"
          subtitle={`All trucks combined, K=${selectedK}`}
          data={distanceData}
          dataKey="value"
          unit="mi"
        />
        <HorizontalBars
          title="Total Drive Time"
          subtitle={`All trucks combined, K=${selectedK}`}
          data={timeData}
          dataKey="value"
          unit="min"
        />
      </div>

      <HorizontalBars
        title="Distance Gap to Best"
        subtitle="How much farther each algorithm drives than the best feasible result (0% = best)"
        data={gapData}
        dataKey="value"
        unit="%"
      />

      {kValues.length > 1 && (
        <div className="bg-slate-900 rounded-xl p-5 border border-slate-800">
          <h3 className="text-sm font-semibold text-white">Total Distance vs Fleet Size</h3>
          <p className="text-slate-500 text-xs mt-0.5 mb-3">Fleet-capable algorithms only</p>
          <ResponsiveContainer width="100%" height={300}>
            <LineChart data={lineData} margin={{ left: 10, right: 30, top: 10, bottom: 4 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
              <XAxis dataKey="k" stroke="#475569" tick={AXIS_STYLE} />
              <YAxis stroke="#475569" tick={AXIS_STYLE} unit=" mi" width={70} />
              <Tooltip contentStyle={TOOLTIP_STYLE} formatter={(v, name) => [`${v} mi`, name]} />
              <Legend wrapperStyle={{ fontSize: 12, color: '#94a3b8' }} />
              {fleetAlgos.map(a => (
                <Line
                  key={a.id}
                  type="monotone"
                  dataKey={a.id}
                  name={a.label}
                  stroke={LINE_COLORS[a.id] || FAMILY_COLORS[a.family]}
                  strokeWidth={a.id === 'ortools' ? 3 : 1.5}
                  dot={{ r: 3 }}
                  connectNulls
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}
