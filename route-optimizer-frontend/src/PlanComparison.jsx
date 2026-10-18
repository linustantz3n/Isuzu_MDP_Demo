import React from 'react';
import { ArrowLeftRight, MessageSquare, X, AlertTriangle, Check, Repeat, Plus, Zap } from 'lucide-react';
import { VEHICLE_COLORS } from './RouteMap';
import { planOf, metricRows, movedStops, addedStops, settingChanges, weightBar, shortPlace, formatClock } from './planDiff';
import { reserveLabel } from './goals';

const STATUS_BADGE = {
  broken_down: 'bg-red-500/15 text-red-300 border-red-500/40',
  rescue: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/40',
  spare: 'bg-sky-500/15 text-sky-300 border-sky-500/40',
  continues: 'bg-slate-700/50 text-slate-400 border-slate-600',
};
const STATUS_LABEL = { broken_down: 'broke down', rescue: 'took stranded stops', spare: 'spare truck', continues: 'continues' };

function StopChip({ stop, moved, added, isBreakdown }) {
  if (stop.kind === 'depot') {
    return <span className="px-1.5 py-0.5 rounded bg-slate-800 text-slate-500 text-[10px] font-bold">D</span>;
  }
  const name = shortPlace(stop.loc);
  const eta = stop.eta != null ? ` · arrives ${formatClock(stop.eta)}` : '';
  let cls = 'bg-slate-800 border-slate-700 text-slate-300';
  let icon = null;
  let title = `${stop.loc.formatted_address || stop.loc.address}${eta}`;
  if (stop.kind === 'done') {
    cls = 'bg-slate-800/60 border-slate-700/60 text-slate-500';
    icon = <Check className="w-2.5 h-2.5" />;
    title = `Delivered or under way at the breakdown · ${title}`;
  } else if (stop.kind === 'pickup') {
    cls = 'bg-cyan-500/10 border-cyan-500/40 text-cyan-300';
    icon = <Repeat className="w-2.5 h-2.5" />;
    title = `Pick up stranded cargo · ${title}`;
  } else if (stop.kind === 'rescue') {
    cls = 'bg-emerald-500/15 border-emerald-500/50 text-emerald-300';
    icon = <Plus className="w-2.5 h-2.5" />;
    title = `Stranded stop, re-assigned · ${title}`;
  } else if (added) {
    cls = 'bg-emerald-500/10 border-emerald-500/40 text-emerald-300';
    icon = <Plus className="w-2.5 h-2.5" />;
  } else if (moved) {
    cls = 'bg-amber-500/10 border-amber-500/50 text-amber-300';
    title = `Moved · ${title}`;
  }
  if (isBreakdown) {
    cls = 'bg-red-500/15 border-red-500/50 text-red-300';
    icon = <AlertTriangle className="w-2.5 h-2.5" />;
    title = `Broke down here · ${title}`;
  }
  return (
    <span title={title} className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded border text-[10px] whitespace-nowrap ${cls}`}>
      {icon}{stop.kind === 'pickup' ? `pickup @ ${name}` : name}
    </span>
  );
}

function PlanColumn({ title, version, plan, moved, added, accent }) {
  const bar = weightBar(version.settings.goals);
  return (
    <div className="flex-1 min-w-0 p-4">
      <div className="flex items-center gap-2 mb-2">
        <span className={`text-[10px] font-bold uppercase tracking-widest ${accent}`}>{title}</span>
        <span className="text-[11px] text-slate-400 truncate">v{version.id} · {version.label}</span>
      </div>
      <div className="flex h-1.5 rounded-full overflow-hidden bg-slate-800 mb-1">
        {bar.map(b => <div key={b.key} style={{ width: `${b.share * 100}%`, backgroundColor: b.color }} />)}
      </div>
      <p className="text-[10px] text-slate-500 mb-3">
        {bar.map(b => `${b.label} ${Math.round(b.share * 100)}%`).join(' · ')}
        {' · '}battery {reserveLabel(version.settings.goals.battery_reserve_pct)}
      </p>
      <div className="space-y-2.5">
        {plan.routes.map((r, idx) => (
          <div key={r.key}>
            <div className="flex items-center gap-1.5 mb-1">
              <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ backgroundColor: VEHICLE_COLORS[idx % VEHICLE_COLORS.length] }} />
              <span className="text-[11px] font-semibold text-slate-300">{r.label}</span>
              {r.driver && <span className="text-[10px] text-slate-500">{r.driver}</span>}
              {r.status && (
                <span className={`text-[9px] px-1.5 rounded border ${STATUS_BADGE[r.status]}`}>{STATUS_LABEL[r.status]}</span>
              )}
              {r.battery_pct != null && (
                <span className={`ml-auto text-[10px] flex items-center gap-0.5 ${r.depleted ? 'text-red-400' : r.battery_pct < 20 ? 'text-orange-400' : 'text-slate-500'}`}>
                  <Zap className="w-2.5 h-2.5" />{r.depleted ? 'DEPLETED' : `${Math.round(r.battery_pct)}% left`}
                </span>
              )}
            </div>
            <div className="flex flex-wrap items-center gap-1 pl-3.5">
              {r.stops.map((s, i) => (
                <React.Fragment key={i}>
                  {i > 0 && <span className="text-slate-700 text-[10px]">›</span>}
                  <StopChip stop={s} moved={moved.has(s.key)} added={added.has(s.key)}
                            isBreakdown={r.status === 'broken_down' && i === r.stops.length - 1 && s.kind !== 'depot'} />
                </React.Fragment>
              ))}
            </div>
          </div>
        ))}
        {plan.unserved.length > 0 && (
          <div className="flex flex-wrap items-center gap-1 pl-3.5 pt-1">
            <span className="text-[10px] text-red-400 flex items-center gap-1"><AlertTriangle className="w-3 h-3" /> Unserved:</span>
            {plan.unserved.map((loc, i) => (
              <span key={i} className="px-1.5 py-0.5 rounded border border-red-500/40 bg-red-500/10 text-red-300 text-[10px]">{shortPlace(loc)}</span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// Side-by-side: the plan before what the operator said, and after.
export default function PlanComparison({ versions, baseId, currentId, onBaseChange, onClose }) {
  const before = versions.find(v => v.id === baseId);
  const after = versions.find(v => v.id === currentId);
  if (!before || !after || before === after) return null;
  const pb = planOf(before.results), pa = planOf(after.results);
  if (!pb || !pa) return null;
  const rows = metricRows(pb, pa);
  const moved = movedStops(pb, pa);
  const added = addedStops(pb, pa);
  const chips = settingChanges(before.settings, after.settings);

  return (
    <div className="bg-slate-900 border border-emerald-500/30 rounded-xl overflow-hidden shadow-lg shadow-emerald-500/5 text-left">
      {/* Header */}
      <div className="px-4 py-3 border-b border-slate-800 flex items-center gap-3">
        <ArrowLeftRight className="w-4 h-4 text-emerald-400" />
        <h3 className="text-sm font-semibold text-white">What changed</h3>
        <div className="ml-auto flex items-center gap-2 text-[11px] text-slate-500">
          <span>compare</span>
          <select
            value={baseId}
            onChange={e => onBaseChange(parseInt(e.target.value, 10))}
            className="px-2 py-1 bg-slate-800 border border-slate-700 rounded text-[11px] text-slate-200"
          >
            {versions.filter(v => v.id !== currentId).map(v => (
              <option key={v.id} value={v.id}>v{v.id} · {v.label}</option>
            ))}
          </select>
          <span>→ v{after.id} (now)</span>
          <button onClick={onClose} className="text-slate-600 hover:text-slate-300 ml-1" title="Hide"><X className="w-4 h-4" /></button>
        </div>
      </div>

      {/* What triggered it */}
      <div className="px-4 py-2.5 border-b border-slate-800 bg-slate-950/40 space-y-1.5">
        {after.trigger?.text && (
          <p className="text-xs text-slate-300 flex items-start gap-2">
            <MessageSquare className="w-3.5 h-3.5 text-[#e8405f] flex-shrink-0 mt-0.5" />
            <span>
              <span className="italic">“{after.trigger.text}”</span>
              <span className="text-slate-500"> — {after.source === 'manual' ? 'set by hand' : 'via the assistant'}</span>
            </span>
          </p>
        )}
        {after.trigger?.note && <p className="text-[11px] text-slate-400 pl-5">{after.trigger.note}</p>}
        {chips.length > 0 && (
          <div className="flex flex-wrap gap-1 pl-5">
            {chips.map((c, i) => (
              <span key={i} className="px-2 py-0.5 rounded-full bg-emerald-500/10 border border-emerald-500/30 text-emerald-300 text-[10px]">{c}</span>
            ))}
          </div>
        )}
      </div>

      {/* Metrics: before | after | change */}
      <table className="w-full text-xs">
        <thead>
          <tr className="text-[10px] uppercase tracking-wider text-slate-500 border-b border-slate-800">
            <th className="text-left font-medium px-4 py-2">Metric</th>
            <th className="text-right font-medium px-4 py-2">Before · {pb.label}</th>
            <th className="text-right font-medium px-4 py-2">After · {pa.label}</th>
            <th className="text-right font-medium px-4 py-2">Change</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-800/60">
          {rows.map(r => (
            <tr key={r.id}>
              <td className="px-4 py-1.5 text-slate-400">{r.label}</td>
              <td className="px-4 py-1.5 text-right text-slate-400 font-mono">{r.fmt(r.b)}</td>
              <td className={`px-4 py-1.5 text-right font-mono ${r.same ? 'text-slate-400' : 'text-white font-semibold'}`}>{r.fmt(r.a)}</td>
              <td className={`px-4 py-1.5 text-right font-mono font-semibold ${r.same ? 'text-slate-600' : r.better ? 'text-green-400' : 'text-red-400'}`}>{r.delta}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {pb.unserved.length !== pa.unserved.length && (
        <p className="px-4 py-2 text-[11px] text-amber-300/90 border-t border-slate-800 flex items-center gap-1.5">
          <AlertTriangle className="w-3 h-3 flex-shrink-0" />
          The two plans serve a different number of stops, so distance, time and energy are not like-for-like.
        </p>
      )}

      {/* Routes, side by side */}
      <div className="flex flex-col md:flex-row border-t border-slate-800 md:divide-x divide-slate-800">
        <PlanColumn title="Before" version={before} plan={pb} moved={moved} added={new Set()} accent="text-slate-500" />
        <PlanColumn title="After" version={after} plan={pa} moved={moved} added={added} accent="text-emerald-400" />
      </div>
      <div className="px-4 py-2 border-t border-slate-800 flex flex-wrap gap-3 text-[10px] text-slate-500">
        <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded border border-amber-500/60 bg-amber-500/10" /> moved</span>
        <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded border border-emerald-500/60 bg-emerald-500/15" /> new / re-assigned</span>
        <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded border border-cyan-500/60 bg-cyan-500/10" /> cargo pickup</span>
        <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded border border-red-500/60 bg-red-500/15" /> breakdown / unserved</span>
        <span className="flex items-center gap-1"><Check className="w-2.5 h-2.5" /> delivered or under way when the truck broke down</span>
      </div>
    </div>
  );
}
