import React, { useState, useRef, useEffect } from 'react';
import { MessageSquare, X, Send, Loader2, Bot, Sparkles, Zap, Cpu, History, HelpCircle, Ban, Check, Cog, User, Maximize2, Minimize2, Target, Gauge, AlertTriangle, ArrowLeftRight } from 'lucide-react';

import { API_BASE } from './api';
import { GOAL_KEYS, GOAL_META } from './goals';

// Which pipeline stage produced the reply (shown as a tiny badge on each bubble).
const SOURCE_BADGE = {
  lexicon: { icon: Zap, label: 'lexicon', title: 'Matched a known phrasing — no model call' },
  model: { icon: Cpu, label: 'model', title: 'Extracted by GPT-4o-mini, then validated' },
  session: { icon: History, label: 'session', title: 'Resolved from an earlier question in this session' },
};

const INTENT_STYLE = {
  apply: 'bg-slate-800 text-slate-300 border-slate-700',
  ask: 'bg-amber-950/60 text-amber-100 border-amber-700/70',
  recommend: 'bg-indigo-950/50 text-indigo-100 border-indigo-800/70',
  memory: 'bg-teal-950/50 text-teal-100 border-teal-800/70',
  goals: 'bg-emerald-950/50 text-emerald-50 border-emerald-800/70',
  limits: 'bg-sky-950/50 text-sky-50 border-sky-800/70',
  disruption: 'bg-red-950/50 text-red-50 border-red-800/70',
  impact: 'bg-slate-900 text-slate-100 border-emerald-500/50',
  out_of_scope: 'bg-rose-950/50 text-rose-100 border-rose-800/70',
};

// Shown under the greeting; one click sends it.
const SUGGESTIONS = [
  'Battery is tight today — keep 20% in every truck',
  'We\'re running late, get everyone back sooner',
  'What are the limits of a truck with 3 battery packs?',
  'Truck 2 broke down after its first stop',
  'Cerritos is urgent — deliver there first',
  'How do the weights work?',
];

// Goal weights before → after, as two stacked bars.
function GoalShift({ info }) {
  if (!info || info.before === info.after) return null;
  const Bar = ({ w }) => (
    <div className="flex h-1.5 rounded-full overflow-hidden bg-slate-800 flex-1">
      {GOAL_KEYS.filter(k => w[k] > 0).map(k => (
        <div key={k} style={{ width: `${w[k] * 100}%`, backgroundColor: GOAL_META[k].color }} />
      ))}
    </div>
  );
  return (
    <div className="mt-2 space-y-1">
      <div className="flex items-center gap-2 text-[11px] text-slate-500"><span className="w-9">before</span><Bar w={info.weights_before} /></div>
      <div className="flex items-center gap-2 text-[11px] text-emerald-300"><span className="w-9">after</span><Bar w={info.weights_after} /></div>
      <div className="flex flex-wrap gap-x-2 text-[11px] text-slate-500 pl-11">
        {GOAL_KEYS.filter(k => info.weights_after[k] > 0).map(k => (
          <span key={k} className="flex items-center gap-1">
            <span className="w-1.5 h-1.5 rounded-full" style={{ backgroundColor: GOAL_META[k].color }} />
            {GOAL_META[k].label} {Math.round(info.weights_after[k] * 100)}%
          </span>
        ))}
      </div>
    </div>
  );
}

// A truck's range and payload, every number computed by the backend's energy model.
function LimitsCard({ limits }) {
  if (!limits?.rows) return null;
  const pc = limits.plan_check;
  return (
    <div className="mt-2 border border-slate-700/80 rounded-lg overflow-hidden">
      <div className="px-2 py-1 bg-slate-800/80 text-[11px] text-slate-400 uppercase tracking-wider flex justify-between">
        <span>{limits.packs} packs · {limits.battery_kwh} kWh{limits.reserve_pct ? ` · ${limits.usable_kwh} usable` : ''}</span>
        <span>payload ≤ {limits.max_payload_lbs.toLocaleString()} lbs</span>
      </div>
      <table className="w-full text-xs">
        <thead>
          <tr className="text-slate-500">
            <th className="text-left font-normal px-2 py-1">range</th>
            <th className="text-right font-normal px-2 py-1">empty</th>
            <th className="text-right font-normal px-2 py-1">{limits.loaded_weight_lbs.toLocaleString()} lbs</th>
          </tr>
        </thead>
        <tbody>
          {limits.rows.map(r => (
            <tr key={r.profile} className="border-t border-slate-800">
              <td className="px-2 py-1 text-slate-300">{r.profile} <span className="text-slate-600">{r.speed_mph} mph</span></td>
              <td className="px-2 py-1 text-right text-slate-300">{Math.round(r.range_empty_mi)} mi</td>
              <td className="px-2 py-1 text-right text-slate-400">{Math.round(r.range_loaded_mi)} mi</td>
            </tr>
          ))}
        </tbody>
      </table>
      {pc && (
        <div className={`px-2 py-1 text-xs border-t border-slate-800 ${pc.fits ? 'text-emerald-300' : 'text-red-300'}`}>
          {pc.fits ? '✓' : '✗'} today's longest route needs {pc.need_kwh} kWh of {pc.usable_kwh}
          {!pc.fits && pc.min_packs && pc.min_packs <= 9 ? ` → needs ${pc.min_packs} packs` : ''}
        </div>
      )}
    </div>
  );
}

// The measured effect of a re-solve, under the chat's plain-language summary.
function ImpactChips({ impact }) {
  if (!impact?.rows?.length) return null;
  return (
    <div className="mt-2 flex flex-wrap gap-1">
      {impact.rows.filter(r => !r.same).map(r => (
        <span key={r.id} className={`px-1.5 py-0.5 rounded text-[11px] border ${r.better ? 'border-green-600/50 text-green-300 bg-green-500/10' : 'border-red-600/50 text-red-300 bg-red-500/10'}`}>
          {r.label}: {r.delta}
        </span>
      ))}
    </div>
  );
}

// Drivers the memory just saved or loaded. Packs and weight are optional; when
// a profile has them they are real inputs to the energy model.
function ProfileCards({ profiles }) {
  if (!profiles?.length) return null;
  return (
    <div className="mt-2 space-y-1">
      {profiles.map((p, i) => (
        <div key={i} className="flex items-center gap-1.5 px-2 py-1 bg-slate-900/70 border border-slate-700 rounded text-xs">
          <User className="w-3.5 h-3.5 text-teal-400 flex-shrink-0" />
          <span className="font-semibold text-slate-200">{p.name}</span>
          {p.battery_packs != null && (
            <span className="text-slate-400">{p.battery_packs}p · {p.battery_packs * 20}kWh</span>
          )}
          {p.weight_lbs != null && (
            <span className="text-slate-500">{Number(p.weight_lbs).toLocaleString()} lbs</span>
          )}
        </div>
      ))}
    </div>
  );
}

// Solver comparison at the operator's problem size. Every cell comes from the
// backend's rule table, so the chat never states a runtime the code didn't compute.
function AdviceTable({ advice }) {
  if (!advice?.table) return null;
  return (
    <div className="mt-2 border border-slate-700/80 rounded-lg overflow-hidden">
      <div className="px-2 py-1 bg-slate-800/80 text-[11px] text-slate-500 uppercase tracking-wider">
        {advice.lang === 'zh'
          ? `${advice.num_stops} 个站 · ${advice.num_vehicles} 辆车${advice.hypothetical ? '（假设）' : ''}`
          : `${advice.num_stops} stops · ${advice.num_vehicles} vehicle${advice.num_vehicles !== 1 ? 's' : ''}${advice.hypothetical ? ' (hypothetical)' : ''}`}
      </div>
      <table className="w-full text-xs">
        <tbody>
          {advice.table.map((row) => {
            const isPick = row.solver === advice.recommended;
            return (
              <tr key={row.solver} className={`border-t border-slate-800 ${isPick ? 'bg-emerald-950/40' : ''}`}>
                <td className="px-2 py-1">
                  <span className={isPick ? 'text-emerald-300 font-semibold' : row.feasible ? 'text-slate-300' : 'text-slate-600 line-through'}>
                    {row.label}
                  </span>
                </td>
                <td className={`px-2 py-1 text-right ${row.feasible ? 'text-slate-400' : 'text-slate-600'}`}>{row.note}</td>
                <td className={`px-2 py-1 text-right ${row.feasible ? 'text-slate-500' : 'text-slate-700'}`}>{row.quality}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export default function AIChatbot({ formState, onApplyChange, onTurn, onDockChange }) {
  const [open, setOpen] = useState(false);
  // Full screen: the panel fills the window instead of floating in the corner.
  const [expanded, setExpanded] = useState(false);
  const [input, setInput] = useState('');
  const [messages, setMessages] = useState([
    {
      role: 'assistant',
      content: "Hi! Tell me about the day and I'll tune the optimizer — I pick the goal weights for you, explain what a truck can handle, and re-plan when a truck breaks down. After each change I re-solve and show you what moved. I can still fill the form too (stops, trucks, packs, temperature…).",
      meta: { suggestions: true },
    },
  ]);
  const [loading, setLoading] = useState(false);
  const [filled, setFilled] = useState(false);
  // "Remember for session": the last question we asked (and any queued changes),
  // echoed back to the backend with the next message.
  const [pending, setPending] = useState(null);
  // Re-solve after the turn. While a "yes / no" for a queued change is open,
  // changes collect here so the plan is re-solved once, not once per answer.
  const [resolving, setResolving] = useState(false);
  const deferred = useRef({ changes: [], texts: [] });
  const bottomRef = useRef(null);
  const inputRef = useRef(null);

  useEffect(() => {
    if (open) {
      bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
      inputRef.current?.focus();
    }
  }, [messages, open, expanded]);

  // Grow the input with its text so long messages wrap instead of scrolling
  // sideways; max-h caps it, after which it scrolls.
  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${el.scrollHeight}px`;
  }, [input, open, expanded]);

  // While the panel is open beside the page, the page makes room for it.
  useEffect(() => { onDockChange?.(open && !expanded); }, [open, expanded, onDockChange]);

  // Esc leaves full screen, as it does for a video or an image viewer.
  useEffect(() => {
    if (!expanded) return;
    const onKey = (e) => { if (e.key === 'Escape') setExpanded(false); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [expanded]);

  // In full screen, keep the conversation in a readable centred column.
  const column = expanded ? 'max-w-3xl mx-auto w-full' : '';
  const textSize = expanded ? 'text-base' : 'text-sm';

  const send = async (preset) => {
    const text = typeof preset === 'string' ? preset : input;
    if (!text.trim() || loading || resolving) return;

    const userMsg = { role: 'user', content: text.trim() };
    // Only role + content go to the model; the rest is UI metadata.
    const history = messages.map(({ role, content }) => ({ role, content }));
    setMessages(prev => [...prev, userMsg]);
    setInput('');
    setLoading(true);

    try {
      const res = await fetch(`${API_BASE}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: userMsg.content, history, form_state: formState, pending }),
      });
      const data = await res.json();

      if (data.status === 'ok') {
        setMessages(prev => [...prev, {
          role: 'assistant',
          content: data.message,
          meta: {
            intent: data.intent,
            source: data.source,
            changes: data.changes || [],
            rejected: data.rejected || [],
            capabilities: data.capabilities,
            advice: data.advice,
            profiles: data.profiles,
            goals_info: data.goals_info,
            limits: data.limits,
          },
        }]);
        // Apply every change the reply carries, in order; each touches exactly
        // one axis. Not only intent 'apply': a driver recall ('memory') sets the
        // fleet, and a multi-step reply can mix both.
        if (data.changes?.length) {
          data.changes.forEach(onApplyChange);
          setFilled(true);
          setTimeout(() => setFilled(false), 3000);
        }
        setPending(data.pending || null);

        // Hand the turn to the page: it re-solves (or simulates the breakdown)
        // and the measured effect comes back as a message.
        const d = deferred.current;
        d.changes.push(...(data.changes || []));
        if (data.changes?.length || data.action) d.texts.push(userMsg.content);
        const waiting = data.pending?.queued?.length > 0;
        if (onTurn && !waiting && (d.changes.length || data.action)) {
          const turn = {
            changes: d.changes, action: data.action || null, text: d.texts.join(' · '),
            lang: data.action?.lang || (/[\u4e00-\u9fff]/.test(userMsg.content) ? 'zh' : 'en'),
          };
          deferred.current = { changes: [], texts: [] };
          setResolving(true);
          try {
            const impact = await onTurn(turn);
            if (impact?.text) {
              setMessages(prev => [...prev, {
                role: 'assistant', content: impact.text,
                meta: { intent: impact.error ? 'out_of_scope' : 'impact', impact },
              }]);
            }
          } finally {
            setResolving(false);
          }
        }
      } else {
        setMessages(prev => [...prev, {
          role: 'assistant',
          content: `Sorry, something went wrong: ${data.message}`,
          meta: { intent: 'out_of_scope', source: 'model' },
        }]);
      }
    } catch {
      setMessages(prev => [...prev, {
        role: 'assistant',
        content: 'Connection error. Make sure the backend is running.',
        meta: { intent: 'out_of_scope' },
      }]);
    } finally {
      setLoading(false);
    }
  };

  const handleKey = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  };

  const renderMeta = (meta) => {
    if (!meta) return null;
    const badge = SOURCE_BADGE[meta.source];
    return (
      <div className="mt-1.5 space-y-1">
        {meta.changes?.map((c, i) => (
          <div key={i} className="flex items-center gap-1 text-xs text-emerald-300" title={`evidence: "${c.evidence}"`}>
            <Check className="w-3.5 h-3.5 flex-shrink-0" />
            <span className="truncate">{c.label}</span>
          </div>
        ))}
        {meta.rejected?.map((r, i) => (
          <div key={i} className="flex items-center gap-1 text-xs text-slate-500">
            <Ban className="w-3.5 h-3.5 flex-shrink-0" />
            <span className="truncate">{r}</span>
          </div>
        ))}
        {meta.profiles?.length > 0 && <ProfileCards profiles={meta.profiles} />}
        {meta.advice && <AdviceTable advice={meta.advice} />}
        {meta.goals_info && <GoalShift info={meta.goals_info} />}
        {meta.limits && <LimitsCard limits={meta.limits} />}
        {meta.impact && <ImpactChips impact={meta.impact} />}
        {meta.suggestions && (
          <div className="mt-2 flex flex-col gap-1">
            {SUGGESTIONS.map(s => (
              <button key={s} onClick={() => send(s)} disabled={loading || resolving}
                      className="text-left px-2 py-1 rounded-md border border-slate-700 bg-slate-900/60 hover:border-[#C8002F] hover:text-white text-xs text-slate-400 transition-colors disabled:opacity-40">
                {s}
              </button>
            ))}
          </div>
        )}
        {meta.capabilities && (
          <ul className="mt-1 pl-1 space-y-0.5 text-xs text-slate-400">
            {meta.capabilities.map((cap, i) => <li key={i}>• {cap}</li>)}
          </ul>
        )}
        {badge && (
          <div className="flex items-center gap-1 text-[11px] text-slate-600 pt-0.5" title={badge.title}>
            <badge.icon className="w-3 h-3" />
            <span>{badge.label}</span>
            {meta.intent === 'ask' && <HelpCircle className="w-3 h-3 ml-1 text-amber-500" />}
            {meta.intent === 'recommend' && <Cog className="w-3 h-3 ml-1 text-indigo-400" />}
            {meta.intent === 'memory' && <User className="w-3 h-3 ml-1 text-teal-400" />}
            {meta.intent === 'goals' && <Target className="w-3 h-3 ml-1 text-emerald-400" />}
            {meta.intent === 'limits' && <Gauge className="w-3 h-3 ml-1 text-sky-400" />}
            {meta.intent === 'disruption' && <AlertTriangle className="w-3 h-3 ml-1 text-red-400" />}
          </div>
        )}
      </div>
    );
  };

  return (
    <>
      {/* Floating trigger button */}
      {!open && (
        <button
          onClick={() => setOpen(true)}
          className="fixed bottom-6 right-6 w-13 h-13 bg-[#C8002F] hover:bg-[#a80027] rounded-full shadow-xl flex items-center justify-center text-white transition-all z-50 group"
          style={{ width: 60, height: 60 }}
          title="AI Assistant – tune the optimizer in plain language"
        >
          <MessageSquare className="w-6 h-6" />
          <span className="absolute -top-1 -right-1 w-5 h-5 bg-yellow-400 rounded-full flex items-center justify-center">
            <Sparkles className="w-3 h-3 text-yellow-900" />
          </span>
        </button>
      )}

      {/* Chat panel */}
      {open && (
        <div className={`fixed bg-slate-900 shadow-2xl flex flex-col z-50 ${
            expanded ? 'inset-0' : 'top-[4.5rem] bottom-6 right-6 w-[30rem] max-w-[calc(100vw-3rem)] border border-slate-700 rounded-xl'
          }`}>

          {/* Header */}
          <div className={`flex items-center gap-2.5 px-4 py-3 border-b border-slate-700 flex-shrink-0 bg-slate-800 ${expanded ? '' : 'rounded-t-xl'}`}>
            <div className="w-9 h-9 bg-[#C8002F] rounded-full flex items-center justify-center flex-shrink-0">
              <Bot className="w-5 h-5 text-white" />
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-sm font-semibold text-white leading-none">AI Route Assistant</p>
              <p className="text-xs text-slate-500 mt-0.5">describe the day · I tune the solver</p>
            </div>
            {filled && (
              <span className="text-xs text-green-400 font-medium animate-pulse">Form updated</span>
            )}
            <button
              onClick={() => setExpanded(e => !e)}
              title={expanded ? 'Exit full screen (Esc)' : 'Full screen'}
              aria-label={expanded ? 'Exit full screen' : 'Full screen'}
              className="text-slate-500 hover:text-white transition-colors flex-shrink-0 ml-1"
            >
              {expanded ? <Minimize2 className="w-5 h-5" /> : <Maximize2 className="w-5 h-5" />}
            </button>
            <button
              onClick={() => { setOpen(false); setExpanded(false); }}
              title="Close"
              aria-label="Close"
              className="text-slate-500 hover:text-white transition-colors flex-shrink-0 ml-1"
            >
              <X className="w-5 h-5" />
            </button>
          </div>

          {/* Messages */}
          <div className="flex-1 overflow-y-auto p-3">
            <div className={`space-y-2 ${column}`}>
              {messages.map((m, i) => (
                <div key={i} className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                  <div className={`max-w-[88%] px-3.5 py-2.5 rounded-xl text-left ${textSize} leading-relaxed border ${
                    m.role === 'user'
                      ? 'bg-[#C8002F] text-white rounded-br-sm border-transparent'
                      : `${INTENT_STYLE[m.meta?.intent] || INTENT_STYLE.apply} rounded-bl-sm`
                  }`}>
                    <div className="whitespace-pre-line">{m.content}</div>
                    {m.role === 'assistant' && renderMeta(m.meta)}
                  </div>
                </div>
              ))}
              {(loading || resolving) && (
                <div className="flex justify-start">
                  <div className={`bg-slate-800 border px-3 py-2 rounded-xl rounded-bl-sm flex items-center gap-1.5 ${resolving ? 'border-emerald-700/60' : 'border-slate-700'}`}>
                    {resolving ? <ArrowLeftRight className="w-3.5 h-3.5 text-emerald-400 animate-pulse" /> : <Loader2 className="w-3.5 h-3.5 text-slate-400 animate-spin" />}
                    <span className="text-xs text-slate-400">{resolving ? 'Re-solving to show what changes…' : 'Thinking...'}</span>
                  </div>
                </div>
              )}
              <div ref={bottomRef} />
            </div>
          </div>

          {/* Pending question reminder */}
          {pending?.question && (
            <div className="flex-shrink-0 px-3 py-1.5 border-t border-amber-900/60 bg-amber-950/40 text-xs text-amber-300">
              <div className={`flex items-center gap-1.5 ${column}`}>
                <HelpCircle className="w-3.5 h-3.5 flex-shrink-0" />
                <span className="truncate" title={pending.question}>Waiting for: {pending.question}</span>
              </div>
            </div>
          )}

          {/* Input */}
          <div className="flex-shrink-0 p-3 border-t border-slate-700">
            <div className={`flex items-end gap-2 ${column}`}>
              <textarea
                ref={inputRef}
                rows={1}
                value={input}
                onChange={e => setInput(e.target.value)}
                onKeyDown={handleKey}
                placeholder={pending ? 'Answer: yes / no / a value…' : 'e.g. battery is tight today, or truck 2 broke down…'}
                className={`flex-1 px-3 py-2 ${expanded ? 'max-h-48' : 'max-h-32'} resize-none overflow-y-auto leading-relaxed bg-slate-800 border border-slate-700 rounded-lg ${textSize} text-white placeholder-slate-600 focus:ring-1 focus:ring-[#C8002F] focus:border-transparent outline-none`}
              />
              <button
                onClick={() => send()}
                disabled={loading || resolving || !input.trim()}
                className="w-10 h-10 bg-[#C8002F] hover:bg-[#a80027] disabled:opacity-30 disabled:cursor-not-allowed rounded-lg flex items-center justify-center text-white transition-all flex-shrink-0"
              >
                <Send className="w-4 h-4" />
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
