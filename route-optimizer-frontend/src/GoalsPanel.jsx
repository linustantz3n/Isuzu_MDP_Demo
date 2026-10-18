import React from 'react';
import { Sparkles, Star, X, Target } from 'lucide-react';
import { GOAL_KEYS, GOAL_LEVELS, GOAL_META, RESERVE_CHOICES, goalWeights, reserveLabel } from './goals';

const LEVEL_SHORT = { off: 'Off', low: 'Low', medium: 'Med', high: 'High' };

// What the solver optimizes for. The assistant sets these from plain language
// ("battery is tight today"); the operator can also set them by hand.
export default function GoalsPanel({ goals, onChange, priorityStops, onRemovePriority, setBy, hot, vehicleCount }) {
  const w = goalWeights(goals);
  const set = (k, v) => onChange({ ...goals, [k]: v });

  return (
    <section className={`text-left rounded-lg transition-shadow duration-500 ${hot ? 'ring-2 ring-emerald-400/70 shadow-lg shadow-emerald-500/10 p-2 -m-2' : ''}`}>
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-[11px] font-semibold text-slate-500 uppercase tracking-widest flex items-center gap-1.5">
          <Target className="w-3 h-3" /> Optimization Goals
        </h3>
        {setBy && (
          <span className="text-[10px] text-emerald-400 flex items-center gap-1" title="Set by the AI assistant">
            <Sparkles className="w-3 h-3" /> set by assistant
          </span>
        )}
      </div>

      {setBy?.evidence && (
        <p className="text-[10px] text-slate-400 italic mb-2 leading-snug">“{setBy.evidence}”</p>
      )}

      {/* The weight mix the solver will use */}
      <div className="flex h-2 rounded-full overflow-hidden bg-slate-800 mb-1" title="Objective weights">
        {GOAL_KEYS.filter(k => w[k] > 0).map(k => (
          <div key={k} style={{ width: `${w[k] * 100}%`, backgroundColor: GOAL_META[k].color }} />
        ))}
      </div>
      <p className="text-[10px] text-slate-500 mb-3">
        {GOAL_KEYS.filter(k => w[k] > 0).map(k => `${GOAL_META[k].label} ${Math.round(w[k] * 100)}%`).join(' · ')}
      </p>

      <div className="space-y-1.5">
        {GOAL_KEYS.map(k => {
          const disabled = k === 'balance' && vehicleCount < 2;
          return (
            <div key={k} className="flex items-center gap-2" title={GOAL_META[k].hint}>
              <span className="w-1.5 h-1.5 rounded-full flex-shrink-0" style={{ backgroundColor: GOAL_META[k].color }} />
              <span className={`text-xs w-14 ${disabled ? 'text-slate-600' : 'text-slate-400'}`}>{GOAL_META[k].label}</span>
              <div className="flex flex-1 bg-slate-800 border border-slate-700 rounded-md p-0.5">
                {GOAL_LEVELS.map(lvl => (
                  <button
                    key={lvl}
                    onClick={() => set(k, lvl)}
                    className={`flex-1 text-[10px] py-0.5 rounded transition-colors ${
                      goals[k] === lvl
                        ? (lvl === 'off' ? 'bg-slate-600 text-white' : 'text-slate-900 font-semibold')
                        : 'text-slate-500 hover:text-slate-300'
                    }`}
                    style={goals[k] === lvl && lvl !== 'off' ? { backgroundColor: GOAL_META[k].color } : undefined}
                  >
                    {LEVEL_SHORT[lvl]}
                  </button>
                ))}
              </div>
              <span className="text-[10px] font-mono text-slate-500 w-8 text-right">{Math.round(w[k] * 100)}%</span>
            </div>
          );
        })}
      </div>
      {vehicleCount < 2 && goals.balance !== 'off' && (
        <p className="text-[10px] text-slate-600 mt-1">Balance counts as time with one truck.</p>
      )}

      <div className="flex items-center justify-between mt-3">
        <label className="text-xs text-slate-400">Battery reserve</label>
        <select
          value={goals.battery_reserve_pct ?? ''}
          onChange={e => set('battery_reserve_pct', e.target.value === '' ? null : parseInt(e.target.value, 10))}
          className="px-2 py-1 bg-slate-800 border border-slate-700 rounded text-xs text-white focus:ring-1 focus:ring-[#C8002F]"
        >
          {RESERVE_CHOICES.map(r => (
            <option key={String(r)} value={r ?? ''}>{reserveLabel(r)}</option>
          ))}
        </select>
      </div>

      <div className="mt-3">
        <div className="flex items-center gap-1.5 mb-1">
          <Star className="w-3 h-3 text-amber-400" />
          <span className="text-xs text-slate-400">Priority stops</span>
        </div>
        {priorityStops.length ? (
          <div className="flex flex-wrap gap-1">
            {priorityStops.map(s => (
              <span key={s} className="flex items-center gap-1 pl-2 pr-1 py-0.5 bg-amber-500/10 border border-amber-500/30 rounded text-[10px] text-amber-300">
                {s.split(',')[1]?.trim() || s}
                <button onClick={() => onRemovePriority(s)} className="text-amber-500/70 hover:text-amber-200">
                  <X className="w-2.5 h-2.5" />
                </button>
              </span>
            ))}
          </div>
        ) : (
          <p className="text-[10px] text-slate-600">None — star a stop below, or tell the assistant “deliver to Brea first”.</p>
        )}
      </div>
    </section>
  );
}
