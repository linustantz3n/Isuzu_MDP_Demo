import React, { useState, useRef, useEffect } from 'react';
import { MessageSquare, X, Send, Loader2, Bot, Sparkles, Zap, Cpu, History, HelpCircle, Ban, Check, Cog, User, Maximize2, Minimize2 } from 'lucide-react';

import { API_BASE } from './api';

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
  out_of_scope: 'bg-rose-950/50 text-rose-100 border-rose-800/70',
};

// Drivers the memory just saved or loaded. Packs and weight are optional; when
// a profile has them they are real inputs to the energy model.
function ProfileCards({ profiles }) {
  if (!profiles?.length) return null;
  return (
    <div className="mt-2 space-y-1">
      {profiles.map((p, i) => (
        <div key={i} className="flex items-center gap-1.5 px-2 py-1 bg-slate-900/70 border border-slate-700 rounded text-[10px]">
          <User className="w-3 h-3 text-teal-400 flex-shrink-0" />
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
      <div className="px-2 py-1 bg-slate-800/80 text-[9px] text-slate-500 uppercase tracking-wider">
        {advice.lang === 'zh'
          ? `${advice.num_stops} 个站 · ${advice.num_vehicles} 辆车${advice.hypothetical ? '（假设）' : ''}`
          : `${advice.num_stops} stops · ${advice.num_vehicles} vehicle${advice.num_vehicles !== 1 ? 's' : ''}${advice.hypothetical ? ' (hypothetical)' : ''}`}
      </div>
      <table className="w-full text-[10px]">
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

export default function AIChatbot({ formState, onApplyChange }) {
  const [open, setOpen] = useState(false);
  // Full screen: the panel fills the window instead of floating in the corner.
  const [expanded, setExpanded] = useState(false);
  const [input, setInput] = useState('');
  const [messages, setMessages] = useState([
    {
      role: 'assistant',
      content: "Hi! I change the form one field at a time — depot, stops, vehicles, battery packs, temperature, unload time, climate control. I can also tell you which solver fits your problem size; try \"which algorithm should I use?\".",
    },
  ]);
  const [loading, setLoading] = useState(false);
  const [filled, setFilled] = useState(false);
  // "Remember for session": the last question we asked (and any queued changes),
  // echoed back to the backend with the next message.
  const [pending, setPending] = useState(null);
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

  // Esc leaves full screen, as it does for a video or an image viewer.
  useEffect(() => {
    if (!expanded) return;
    const onKey = (e) => { if (e.key === 'Escape') setExpanded(false); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [expanded]);

  // In full screen, keep the conversation in a readable centred column.
  const column = expanded ? 'max-w-3xl mx-auto w-full' : '';
  const textSize = expanded ? 'text-sm' : 'text-xs';

  const send = async () => {
    if (!input.trim() || loading) return;

    const userMsg = { role: 'user', content: input.trim() };
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
          },
        }]);
        // Apply change: each entry touches exactly one axis.
        if (data.intent === 'apply' && data.changes.length) {
          data.changes.forEach(onApplyChange);
          setFilled(true);
          setTimeout(() => setFilled(false), 3000);
        }
        setPending(data.pending || null);
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
          <div key={i} className="flex items-center gap-1 text-[10px] text-emerald-300" title={`evidence: "${c.evidence}"`}>
            <Check className="w-3 h-3 flex-shrink-0" />
            <span className="truncate">{c.label}</span>
          </div>
        ))}
        {meta.rejected?.map((r, i) => (
          <div key={i} className="flex items-center gap-1 text-[10px] text-slate-500">
            <Ban className="w-3 h-3 flex-shrink-0" />
            <span className="truncate">{r}</span>
          </div>
        ))}
        {meta.profiles?.length > 0 && <ProfileCards profiles={meta.profiles} />}
        {meta.advice && <AdviceTable advice={meta.advice} />}
        {meta.capabilities && (
          <ul className="mt-1 pl-1 space-y-0.5 text-[10px] text-slate-400">
            {meta.capabilities.map((cap, i) => <li key={i}>• {cap}</li>)}
          </ul>
        )}
        {badge && (
          <div className="flex items-center gap-1 text-[9px] text-slate-600 pt-0.5" title={badge.title}>
            <badge.icon className="w-2.5 h-2.5" />
            <span>{badge.label}</span>
            {meta.intent === 'ask' && <HelpCircle className="w-2.5 h-2.5 ml-1 text-amber-500" />}
            {meta.intent === 'recommend' && <Cog className="w-2.5 h-2.5 ml-1 text-indigo-400" />}
            {meta.intent === 'memory' && <User className="w-2.5 h-2.5 ml-1 text-teal-400" />}
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
          style={{ width: 52, height: 52 }}
          title="AI Assistant – fills the form one field at a time"
        >
          <MessageSquare className="w-5 h-5" />
          <span className="absolute -top-1 -right-1 w-4 h-4 bg-yellow-400 rounded-full flex items-center justify-center">
            <Sparkles className="w-2.5 h-2.5 text-yellow-900" />
          </span>
        </button>
      )}

      {/* Chat panel */}
      {open && (
        <div className={`fixed bg-slate-900 shadow-2xl flex flex-col z-50 ${
            expanded ? 'inset-0' : 'bottom-6 right-6 w-80 border border-slate-700 rounded-xl'
          }`}
          style={expanded ? undefined : { height: 460 }}>

          {/* Header */}
          <div className={`flex items-center gap-2.5 px-4 py-3 border-b border-slate-700 flex-shrink-0 bg-slate-800 ${expanded ? '' : 'rounded-t-xl'}`}>
            <div className="w-7 h-7 bg-[#C8002F] rounded-full flex items-center justify-center flex-shrink-0">
              <Bot className="w-4 h-4 text-white" />
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-xs font-semibold text-white leading-none">AI Route Assistant</p>
              <p className="text-[10px] text-slate-500 mt-0.5">lexicon → GPT-4o-mini → validation</p>
            </div>
            {filled && (
              <span className="text-[10px] text-green-400 font-medium animate-pulse">Form updated</span>
            )}
            <button
              onClick={() => setExpanded(e => !e)}
              title={expanded ? 'Exit full screen (Esc)' : 'Full screen'}
              aria-label={expanded ? 'Exit full screen' : 'Full screen'}
              className="text-slate-500 hover:text-white transition-colors flex-shrink-0 ml-1"
            >
              {expanded ? <Minimize2 className="w-4 h-4" /> : <Maximize2 className="w-4 h-4" />}
            </button>
            <button
              onClick={() => { setOpen(false); setExpanded(false); }}
              title="Close"
              aria-label="Close"
              className="text-slate-500 hover:text-white transition-colors flex-shrink-0 ml-1"
            >
              <X className="w-4 h-4" />
            </button>
          </div>

          {/* Messages */}
          <div className="flex-1 overflow-y-auto p-3">
            <div className={`space-y-2 ${column}`}>
              {messages.map((m, i) => (
                <div key={i} className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                  <div className={`max-w-[85%] px-3 py-2 rounded-xl ${textSize} leading-relaxed border ${
                    m.role === 'user'
                      ? 'bg-[#C8002F] text-white rounded-br-sm border-transparent'
                      : `${INTENT_STYLE[m.meta?.intent] || INTENT_STYLE.apply} rounded-bl-sm`
                  }`}>
                    {m.content}
                    {m.role === 'assistant' && renderMeta(m.meta)}
                  </div>
                </div>
              ))}
              {loading && (
                <div className="flex justify-start">
                  <div className="bg-slate-800 border border-slate-700 px-3 py-2 rounded-xl rounded-bl-sm flex items-center gap-1.5">
                    <Loader2 className="w-3 h-3 text-slate-400 animate-spin" />
                    <span className="text-[10px] text-slate-500">Thinking...</span>
                  </div>
                </div>
              )}
              <div ref={bottomRef} />
            </div>
          </div>

          {/* Pending question reminder */}
          {pending?.question && (
            <div className="flex-shrink-0 px-3 py-1.5 border-t border-amber-900/60 bg-amber-950/40 text-[10px] text-amber-300">
              <div className={`flex items-center gap-1.5 ${column}`}>
                <HelpCircle className="w-3 h-3 flex-shrink-0" />
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
                placeholder={pending ? 'Answer: yes / no / a value…' : 'e.g. temperature 30 C, or Depot in Anaheim…'}
                className={`flex-1 px-3 py-2 ${expanded ? 'max-h-40' : 'max-h-24'} resize-none overflow-y-auto leading-relaxed bg-slate-800 border border-slate-700 rounded-lg ${textSize} text-white placeholder-slate-600 focus:ring-1 focus:ring-[#C8002F] focus:border-transparent outline-none`}
              />
              <button
                onClick={send}
                disabled={loading || !input.trim()}
                className="w-8 h-8 bg-[#C8002F] hover:bg-[#a80027] disabled:opacity-30 disabled:cursor-not-allowed rounded-lg flex items-center justify-center text-white transition-all flex-shrink-0"
              >
                <Send className="w-3.5 h-3.5" />
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
