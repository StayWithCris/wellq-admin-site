import React, { useEffect, useMemo, useState } from 'react';
import { Timer } from 'lucide-react';
import { apiFetch } from '../../api/client';
import { useLanguage } from '../../contexts/LanguageContext';

// Rec #13 — Temporizador de SLA: tiempo abierto de cada ticket vs. su plazo por categoría
const STATE_STYLE = {
  on_track: { bar: 'bg-green-500', text: 'text-green-600 dark:text-green-400', chip: 'bg-green-500/10 text-green-700 dark:text-green-400' },
  at_risk:  { bar: 'bg-amber-400', text: 'text-amber-600 dark:text-amber-400', chip: 'bg-amber-400/15 text-amber-700 dark:text-amber-400' },
  breached: { bar: 'bg-red-500',   text: 'text-red-600 dark:text-red-400',     chip: 'bg-red-500/10 text-red-700 dark:text-red-400' },
};

const stateFor = (pct, atRisk) => (pct >= 100 ? 'breached' : pct >= atRisk ? 'at_risk' : 'on_track');

const fmtDuration = (hours) => {
  const total = Math.round(Math.abs(hours) * 60);
  const d = Math.floor(total / 1440);
  const h = Math.floor((total % 1440) / 60);
  const m = total % 60;
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
};

export default function SlaMonitor({ refreshKey, onSelectTicket }) {
  const { t } = useLanguage();
  const [state, setState] = useState({ loading: true, error: null, data: null });
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    let alive = true;
    apiFetch('/api/sla/tickets')
      .then((res) => alive && setState({ loading: false, error: null, data: res.data }))
      .catch((e) => alive && setState({ loading: false, error: e.message, data: null }));
    return () => { alive = false; };
  }, [refreshKey]);

  // El reloj avanza en pantalla sin volver a consultar la API
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 30000);
    return () => clearInterval(id);
  }, []);

  const d = state.data;
  const atRisk = d?.policy?.at_risk_pct ?? 75;

  const items = useMemo(() => (d?.items ?? []).map((it) => {
    const deadline = Date.parse(it.deadline);
    const remaining = (deadline - now) / 3600000;
    const pct = ((it.sla_hours - remaining) / it.sla_hours) * 100;
    return { ...it, remaining, pct, live: stateFor(pct, atRisk) };
  }).sort((a, b) => a.remaining - b.remaining), [d, now, atRisk]);

  const counts = items.reduce((acc, i) => ({ ...acc, [i.live]: (acc[i.live] ?? 0) + 1 }), {});

  if (state.loading) return null;
  if (state.error) return <p role="alert" className="text-sm text-red-500">{t('recs.slaError')}: {state.error}</p>;

  return (
    <section
      aria-labelledby="sla-title"
      className="bg-white dark:bg-wellq-dark rounded-2xl p-5 shadow-sm border border-wellq-gray/20 dark:border-[#1e293b]"
    >
      <div className="flex items-start justify-between gap-4 flex-wrap mb-4">
        <div>
          <h3 id="sla-title" className="flex items-center gap-2 text-base font-black text-wellq-dark dark:text-white">
            <Timer size={18} className="text-wellq-cyan" aria-hidden="true" />
            {t('recs.slaTitle')}
          </h3>
          <p className="text-xs text-wellq-gray mt-0.5">{t('recs.slaSub')}</p>
        </div>
        <ul className="flex flex-wrap gap-2 text-xs font-bold" aria-label={t('recs.slaSummary')}>
          {['on_track', 'at_risk', 'breached'].map((k) => (
            <li key={k} className={`px-2.5 py-1 rounded-lg ${STATE_STYLE[k].chip}`}>
              {t(`recs.sla_${k}`)}: {counts[k] ?? 0}
            </li>
          ))}
          <li className="px-2.5 py-1 rounded-lg bg-wellq-gray/10 text-wellq-dark dark:text-white">
            {t('recs.slaCompliance')}:{' '}
            {d.summary.compliance_30d_pct === null ? '—' : `${d.summary.compliance_30d_pct}%`}
          </li>
        </ul>
      </div>

      {items.length === 0 ? (
        <p className="text-sm text-wellq-gray py-4 text-center">{t('recs.slaEmpty')}</p>
      ) : (
        <ul className="space-y-2 max-h-80 overflow-y-auto pr-1">
          {items.map((it) => {
            const st = STATE_STYLE[it.live];
            const label = it.remaining >= 0
              ? t('recs.slaLeft', { time: fmtDuration(it.remaining) })
              : t('recs.slaOverdue', { time: fmtDuration(it.remaining) });
            return (
              <li key={it.ticket_id}>
                <button
                  type="button"
                  onClick={() => onSelectTicket?.(it.ticket_id)}
                  className="w-full text-left rounded-xl p-3 border border-wellq-gray/15 dark:border-white/5 hover:bg-wellq-gray/5 dark:hover:bg-white/5 focus:outline-none focus-visible:ring-2 focus-visible:ring-wellq-cyan"
                  aria-label={`${it.title} — ${t(`recs.sla_${it.live}`)} — ${label}`}
                >
                  <div className="flex items-center justify-between gap-3">
                    <div className="min-w-0">
                      <div className="text-sm font-bold text-wellq-dark dark:text-white truncate">{it.title}</div>
                      <div className="text-[11px] text-wellq-gray">
                        {it.category ?? '—'} · {t('recs.slaPlan', { h: it.sla_hours })}
                        {it.responder_name ? ` · ${it.responder_name}` : ''}
                      </div>
                    </div>
                    <span className={`text-xs font-black whitespace-nowrap ${st.text}`}>{label}</span>
                  </div>
                  <div
                    className="mt-2 h-1.5 rounded-full bg-wellq-gray/15 overflow-hidden"
                    role="progressbar"
                    aria-valuemin={0}
                    aria-valuemax={100}
                    aria-valuenow={Math.min(Math.round(it.pct), 100)}
                    aria-label={t('recs.slaUsed')}
                  >
                    <div className={`h-full ${st.bar}`} style={{ width: `${Math.min(Math.max(it.pct, 0), 100)}%` }} />
                  </div>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
