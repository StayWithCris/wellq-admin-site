import React, { useCallback, useEffect, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import {
  Bot, X, Send, Square, Sparkles, Loader2, Database, Lock, RotateCcw, AlertTriangle,
} from 'lucide-react';
import { fetchAssistantStatus, streamAssistantChat } from '../../api/client';
import { useLanguage } from '../../contexts/LanguageContext';

// ─────────────────────────────────────────────────────────────────────────────
// AssistantWidget — Mejora #20
//
// Chat flotante (abajo a la derecha) con un asistente de SOLO LECTURA.
// La respuesta llega por SSE desde POST /api/v1/assistant/chat y se va
// escribiendo a medida que llega. Las consultas que hace el modelo (las 6
// herramientas fijas del backend) se muestran como etiquetas bajo la respuesta.
// El backend filtra las herramientas según los permisos del usuario.
// ─────────────────────────────────────────────────────────────────────────────

const MAX_HISTORY = 10;
const SUGGESTIONS = [
  'assistant.suggestions.overview',
  'assistant.suggestions.invoices',
  'assistant.suggestions.alerts',
  'assistant.suggestions.clinics',
];

// Formato mínimo para respuestas del modelo: viñetas y **negrita**.
// Sin HTML: todo se renderiza como texto de React (no hay inyección posible).
const renderInline = (text) =>
  text.split('**').map((chunk, i) => (i % 2 === 1 ? <strong key={i}>{chunk}</strong> : chunk));

const FormattedText = ({ text }) => {
  const lines = text.split('\n');
  return (
    <div className="space-y-1">
      {lines.map((line, i) => {
        const bullet = line.match(/^\s*[-*•]\s+(.*)$/);
        if (bullet) {
          return (
            <div key={i} className="flex gap-2">
              <span className="text-wellq-cyan">•</span>
              <span>{renderInline(bullet[1])}</span>
            </div>
          );
        }
        return line.trim() ? <p key={i}>{renderInline(line)}</p> : <div key={i} className="h-1" />;
      })}
    </div>
  );
};

export const AssistantWidget = () => {
  const { t } = useLanguage();
  const [open, setOpen] = useState(false);
  const [status, setStatus] = useState(null);       // {enabled, model, tools}
  const [statusError, setStatusError] = useState(false);
  const [messages, setMessages] = useState([]);      // {role, text, tools, error}
  const [input, setInput] = useState('');
  const [streaming, setStreaming] = useState(false);
  const abortRef = useRef(null);
  const scrollRef = useRef(null);
  const inputRef = useRef(null);

  useEffect(() => {
    if (!open || status) return;
    fetchAssistantStatus()
      .then((res) => setStatus(res?.data ?? { enabled: false, tools: [] }))
      .catch(() => setStatusError(true));
  }, [open, status]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' });
  }, [messages]);

  useEffect(() => {
    if (open) setTimeout(() => inputRef.current?.focus(), 150);
  }, [open]);

  useEffect(() => () => abortRef.current?.abort(), []);

  const updateLast = (fn) =>
    setMessages((prev) => {
      const next = [...prev];
      next[next.length - 1] = fn(next[next.length - 1]);
      return next;
    });

  const ask = useCallback(async (raw) => {
    const question = (raw ?? '').trim();
    if (!question || streaming) return;

    const history = messages
      .filter((m) => !m.error && m.text)
      .slice(-MAX_HISTORY)
      .map((m) => ({ role: m.role, text: m.text }));

    setMessages((prev) => [
      ...prev,
      { role: 'user', text: question },
      { role: 'assistant', text: '', tools: [], pending: true },
    ]);
    setInput('');
    setStreaming(true);

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      await streamAssistantChat(question, history, (event) => {
        if (event.type === 'delta') {
          updateLast((m) => ({ ...m, text: m.text + event.text, pending: false }));
        } else if (event.type === 'tool') {
          updateLast((m) => ({ ...m, tools: [...m.tools, { name: event.name, status: event.status }] }));
        } else if (event.type === 'error') {
          updateLast((m) => ({ ...m, pending: false, error: event.message || t('assistant.error') }));
        } else if (event.type === 'done') {
          updateLast((m) => ({ ...m, pending: false }));
        }
      }, controller.signal);
    } catch (err) {
      if (err.name === 'AbortError') {
        updateLast((m) => ({ ...m, pending: false, text: m.text || t('assistant.stopped') }));
      } else {
        updateLast((m) => ({ ...m, pending: false, error: err.message || t('assistant.error') }));
      }
    } finally {
      setStreaming(false);
      abortRef.current = null;
    }
  }, [messages, streaming, t]);

  const stop = () => abortRef.current?.abort();
  const reset = () => {
    stop();
    setMessages([]);
  };

  const enabled = status?.enabled;
  const noTools = status && (status.tools ?? []).length === 0;

  return (
    <>
      <AnimatePresence>
        {open && (
          <motion.section
            key="assistant-panel"
            role="dialog"
            aria-label={t('assistant.title')}
            initial={{ opacity: 0, y: 16, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 16, scale: 0.98 }}
            transition={{ duration: 0.2, ease: 'easeOut' }}
            className="fixed bottom-24 right-6 z-[60] w-[min(400px,calc(100vw-2rem))] h-[min(560px,calc(100vh-8rem))] flex flex-col rounded-2xl shadow-2xl border border-wellq-gray/20 dark:border-white/10 bg-white dark:bg-[#0b1220] overflow-hidden"
          >
            {/* Cabecera */}
            <header className="flex items-center justify-between gap-3 px-4 py-3 border-b border-wellq-gray/10 dark:border-white/10">
              <div className="flex items-center gap-2.5 min-w-0">
                <div className="w-8 h-8 rounded-lg flex items-center justify-center bg-wellq-cyan/15">
                  <Bot size={17} className="text-wellq-cyan" />
                </div>
                <div className="min-w-0">
                  <p className="text-sm font-bold text-wellq-dark dark:text-white">{t('assistant.title')}</p>
                  <p className="text-[11px] text-wellq-gray flex items-center gap-1">
                    <Lock size={10} /> {t('assistant.subtitle')}
                  </p>
                </div>
              </div>
              <div className="flex items-center gap-1">
                {messages.length > 0 && (
                  <button
                    type="button"
                    onClick={reset}
                    title={t('assistant.clear')}
                    aria-label={t('assistant.clear')}
                    className="p-1.5 rounded-lg text-wellq-gray hover:text-wellq-dark dark:hover:text-white hover:bg-wellq-gray/10"
                  >
                    <RotateCcw size={15} />
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => setOpen(false)}
                  aria-label={t('assistant.close')}
                  className="p-1.5 rounded-lg text-wellq-gray hover:text-wellq-dark dark:hover:text-white hover:bg-wellq-gray/10"
                >
                  <X size={16} />
                </button>
              </div>
            </header>

            {/* Mensajes */}
            <div ref={scrollRef} className="flex-1 overflow-y-auto px-4 py-4 space-y-3 text-sm" aria-live="polite">
              {statusError && (
                <p className="text-xs text-red-500 flex items-center gap-1.5">
                  <AlertTriangle size={13} /> {t('assistant.error')}
                </p>
              )}
              {status && !enabled && (
                <p className="text-xs rounded-xl bg-amber-500/10 text-amber-700 dark:text-amber-400 p-3">
                  {t('assistant.disabled')}
                </p>
              )}
              {enabled && noTools && (
                <p className="text-xs rounded-xl bg-wellq-gray/10 text-wellq-gray p-3">{t('assistant.noTools')}</p>
              )}

              {messages.length === 0 && enabled && !noTools && (
                <div className="space-y-3">
                  <p className="text-wellq-gray text-xs leading-relaxed">{t('assistant.welcome')}</p>
                  <div className="flex flex-wrap gap-2">
                    {SUGGESTIONS.map((key) => (
                      <button
                        key={key}
                        type="button"
                        onClick={() => ask(t(key))}
                        className="inline-flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded-full border border-wellq-cyan/30 text-wellq-dark dark:text-white hover:bg-wellq-cyan/10"
                      >
                        <Sparkles size={11} className="text-wellq-cyan" />
                        {t(key)}
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {messages.map((m, i) => (
                <div key={i} className={m.role === 'user' ? 'flex justify-end' : 'flex justify-start'}>
                  <div
                    className={
                      m.role === 'user'
                        ? 'max-w-[85%] rounded-2xl rounded-br-sm bg-wellq-cyan text-wellq-black px-3.5 py-2'
                        : 'max-w-[90%] rounded-2xl rounded-bl-sm bg-slate-100 dark:bg-white/5 text-wellq-dark dark:text-white/90 px-3.5 py-2.5'
                    }
                  >
                    {m.role === 'user' ? m.text : (
                      <>
                        {m.pending && !m.text && (
                          <span className="flex items-center gap-2 text-wellq-gray text-xs">
                            <Loader2 size={13} className="animate-spin" /> {t('assistant.thinking')}
                          </span>
                        )}
                        {m.text && <FormattedText text={m.text} />}
                        {m.error && (
                          <p className="text-xs text-red-500 flex items-center gap-1.5 mt-1">
                            <AlertTriangle size={12} /> {m.error}
                          </p>
                        )}
                        {m.tools?.length > 0 && (
                          <div className="flex flex-wrap gap-1.5 mt-2">
                            {m.tools.map((tool, j) => (
                              <span
                                key={`${tool.name}-${j}`}
                                className={`inline-flex items-center gap-1 text-[10px] font-semibold px-2 py-0.5 rounded-md ${
                                  tool.status === 'ok'
                                    ? 'bg-wellq-cyan/10 text-wellq-cyan'
                                    : 'bg-wellq-gray/10 text-wellq-gray'
                                }`}
                              >
                                <Database size={10} />
                                {t(`assistant.tools.${tool.name}`, tool.name)}
                                {tool.status === 'denied' && ` · ${t('assistant.toolDenied')}`}
                              </span>
                            ))}
                          </div>
                        )}
                      </>
                    )}
                  </div>
                </div>
              ))}
            </div>

            {/* Entrada */}
            <form
              onSubmit={(e) => { e.preventDefault(); ask(input); }}
              className="border-t border-wellq-gray/10 dark:border-white/10 p-3 flex items-end gap-2"
            >
              <textarea
                ref={inputRef}
                value={input}
                onChange={(e) => setInput(e.target.value.slice(0, 1000))}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ask(input); }
                }}
                rows={1}
                disabled={!enabled || noTools}
                placeholder={t('assistant.placeholder')}
                aria-label={t('assistant.placeholder')}
                className="flex-1 resize-none max-h-28 rounded-xl border border-wellq-gray/20 dark:border-white/10 bg-transparent px-3 py-2 text-sm text-wellq-dark dark:text-white placeholder:text-wellq-gray focus:outline-none focus:ring-2 focus:ring-wellq-cyan/40 disabled:opacity-50"
              />
              {streaming ? (
                <button
                  type="button"
                  onClick={stop}
                  aria-label={t('assistant.stop')}
                  className="shrink-0 w-9 h-9 rounded-xl flex items-center justify-center bg-red-500/10 text-red-500 hover:bg-red-500/20"
                >
                  <Square size={14} />
                </button>
              ) : (
                <button
                  type="submit"
                  disabled={!input.trim() || !enabled || noTools}
                  aria-label={t('assistant.send')}
                  className="shrink-0 w-9 h-9 rounded-xl flex items-center justify-center bg-wellq-cyan text-wellq-black hover:opacity-90 disabled:opacity-40"
                >
                  <Send size={15} />
                </button>
              )}
            </form>
          </motion.section>
        )}
      </AnimatePresence>

      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label={open ? t('assistant.close') : t('assistant.open')}
        aria-expanded={open}
        className="fixed bottom-6 right-6 z-[60] w-14 h-14 rounded-full flex items-center justify-center bg-wellq-cyan text-wellq-black shadow-lg shadow-wellq-cyan/30 hover:scale-105 transition-transform"
      >
        {open ? <X size={22} /> : <Bot size={24} />}
      </button>
    </>
  );
};

export default AssistantWidget;
