import React, { useCallback, useEffect, useState } from 'react';
import { CalendarCheck, Lock, X, Loader2, AlertTriangle } from 'lucide-react';
import { toast } from 'sonner';
import { apiFetch } from '../../api/client';
import { useLanguage } from '../../contexts/LanguageContext';
import useHasPermission from '../../hooks/useHasPermission';

// Rec #1 — Carga e historial de MRR: "Cerrar y agregar mes"
export const MRR_CLOSED_EVENT = 'wellq:mrr-closed';

const money = (n) => `$${Number(n ?? 0).toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
const FIELDS = ['total_mrr', 'new_business', 'expansion', 'contraction', 'churn'];

const previousMonth = () => {
  const d = new Date();
  return d.getMonth() === 0 ? { year: d.getFullYear() - 1, month: 12 } : { year: d.getFullYear(), month: d.getMonth() };
};

function CloseMonthModal({ onClose, onDone }) {
  const { t } = useLanguage();
  const init = previousMonth();
  const [period, setPeriod] = useState(init);
  const [preview, setPreview] = useState({ loading: true, error: null, data: null });
  const [mode, setMode] = useState('auto');
  const [values, setValues] = useState({});
  const [notes, setNotes] = useState('');
  const [replace, setReplace] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let alive = true;
    setPreview({ loading: true, error: null, data: null });
    setReplace(false);
    apiFetch(`/api/financials/mrr/close/preview?year=${period.year}&month=${period.month}`)
      .then((res) => {
        if (!alive) return;
        setPreview({ loading: false, error: null, data: res.data });
        setValues(Object.fromEntries(FIELDS.map((k) => [k, res.data.calculated[k] ?? 0])));
      })
      .catch((e) => alive && setPreview({ loading: false, error: e.message, data: null }));
    return () => { alive = false; };
  }, [period]);

  const now = new Date();
  const years = [now.getFullYear() - 2, now.getFullYear() - 1, now.getFullYear()];
  const p = preview.data;

  const submit = async () => {
    setSaving(true);
    try {
      const body = { year: period.year, month: period.month, mode, notes: notes || undefined, replace };
      if (mode === 'manual') body.values = Object.fromEntries(FIELDS.map((k) => [k, Number(values[k] || 0)]));
      await apiFetch('/api/financials/mrr/close', { method: 'POST', body: JSON.stringify(body) });
      toast.success(t('recs.mcDone', { period: `${p?.period_month ?? ''} ${period.year}` }));
      window.dispatchEvent(new CustomEvent(MRR_CLOSED_EVENT));
      onDone();
    } catch (e) {
      if (e.status !== 403) toast.error(e.message);
    } finally {
      setSaving(false);
    }
  };

  const inputCls = 'w-full rounded-lg border border-wellq-gray/30 dark:border-white/10 bg-white dark:bg-[#0b1017] px-3 py-2 text-sm text-wellq-dark dark:text-white';

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" role="dialog" aria-modal="true" aria-labelledby="mc-title">
      <div className="w-full max-w-lg bg-white dark:bg-wellq-dark rounded-2xl shadow-xl border border-wellq-gray/20 dark:border-white/10 max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between px-6 py-4 border-b border-wellq-gray/10 dark:border-white/5">
          <h3 id="mc-title" className="text-lg font-black text-wellq-dark dark:text-white">{t('recs.mcModalTitle')}</h3>
          <button onClick={onClose} aria-label={t('recs.mcCancel')} className="p-1 rounded-lg text-wellq-gray hover:text-wellq-dark dark:hover:text-white"><X size={18} /></button>
        </div>

        <div className="p-6 space-y-5">
          <div className="grid grid-cols-2 gap-3">
            <label className="text-xs font-bold text-wellq-gray">{t('recs.mcMonth')}
              <select id="mc-month" className={`${inputCls} mt-1`} value={period.month}
                onChange={(e) => setPeriod((x) => ({ ...x, month: Number(e.target.value) }))}>
                {Array.from({ length: 12 }, (_, i) => i + 1).map((m) => (
                  <option key={m} value={m}>{new Date(2000, m - 1, 1).toLocaleString(undefined, { month: 'long' })}</option>
                ))}
              </select>
            </label>
            <label className="text-xs font-bold text-wellq-gray">{t('recs.mcYear')}
              <select id="mc-year" className={`${inputCls} mt-1`} value={period.year}
                onChange={(e) => setPeriod((x) => ({ ...x, year: Number(e.target.value) }))}>
                {years.map((y) => <option key={y} value={y}>{y}</option>)}
              </select>
            </label>
          </div>

          {preview.loading && <p className="flex items-center gap-2 text-sm text-wellq-gray"><Loader2 size={14} className="animate-spin" /> {t('recs.mcCalculating')}</p>}
          {preview.error && <p role="alert" className="text-sm text-red-500">{preview.error}</p>}

          {p && (
            <>
              {p.already_closed && (
                <div className="flex gap-2 rounded-xl bg-amber-400/10 border border-amber-400/30 p-3 text-xs text-amber-700 dark:text-amber-400">
                  <AlertTriangle size={14} className="flex-shrink-0 mt-0.5" />
                  <div>
                    <p className="font-bold">{t('recs.mcAlready', { total: money(p.existing?.total_mrr) })}</p>
                    <label className="flex items-center gap-2 mt-2 font-semibold">
                      <input id="mc-replace" type="checkbox" checked={replace} onChange={(e) => setReplace(e.target.checked)} />
                      {t('recs.mcReplace')}
                    </label>
                  </div>
                </div>
              )}

              <fieldset className="flex gap-2" aria-label={t('recs.mcMode')}>
                {['auto', 'manual'].map((m) => (
                  <button key={m} type="button" onClick={() => setMode(m)} aria-pressed={mode === m}
                    className={`flex-1 rounded-xl px-3 py-2 text-sm font-bold border transition ${mode === m
                      ? 'bg-wellq-cyan/10 border-wellq-cyan text-wellq-dark dark:text-white'
                      : 'border-wellq-gray/20 dark:border-white/10 text-wellq-gray'}`}>
                    {t(m === 'auto' ? 'recs.mcAuto' : 'recs.mcManual')}
                  </button>
                ))}
              </fieldset>
              <p className="text-[11px] text-wellq-gray">{t(mode === 'auto' ? 'recs.mcAutoHint' : 'recs.mcManualHint')}</p>

              <div className="grid grid-cols-2 gap-3">
                {FIELDS.map((k) => (
                  <label key={k} className="text-xs font-bold text-wellq-gray">{t(`recs.mcField_${k}`)}
                    {mode === 'auto' ? (
                      <div className="mt-1 text-base font-black text-wellq-dark dark:text-white tabular-nums">{money(p.calculated[k])}</div>
                    ) : (
                      <input id={`mc-${k}`} type="number" min="0" step="0.01" className={`${inputCls} mt-1`}
                        value={values[k] ?? ''} onChange={(e) => setValues((v) => ({ ...v, [k]: e.target.value }))} />
                    )}
                  </label>
                ))}
                <div className="text-xs font-bold text-wellq-gray">{t('recs.mcPrev')}
                  <div className="mt-1 text-base font-black text-wellq-dark dark:text-white tabular-nums">{money(p.previous_total_mrr)}</div>
                </div>
              </div>

              <label className="block text-xs font-bold text-wellq-gray">{t('recs.mcNotes')}
                <textarea id="mc-notes" rows={2} maxLength={500} className={`${inputCls} mt-1`} value={notes} onChange={(e) => setNotes(e.target.value)} />
              </label>
            </>
          )}
        </div>

        <div className="flex justify-end gap-2 px-6 py-4 border-t border-wellq-gray/10 dark:border-white/5">
          <button onClick={onClose} className="px-4 py-2 rounded-xl text-sm font-bold text-wellq-gray hover:text-wellq-dark dark:hover:text-white">{t('recs.mcCancel')}</button>
          <button onClick={submit} disabled={!p || saving || (p.already_closed && !replace)}
            className="flex items-center gap-2 px-5 py-2 rounded-xl text-sm font-bold bg-wellq-cyan text-wellq-black disabled:opacity-50 disabled:cursor-not-allowed">
            {saving ? <Loader2 size={14} className="animate-spin" /> : <Lock size={14} />} {t('recs.mcConfirm')}
          </button>
        </div>
      </div>
    </div>
  );
}

export default function MrrMonthClose() {
  const { t } = useLanguage();
  const canEdit = useHasPermission('billing.edit');
  const [state, setState] = useState({ loading: true, error: null, rows: [] });
  const [open, setOpen] = useState(false);

  const load = useCallback(() => {
    apiFetch('/api/financials/mrr/closed')
      .then((res) => setState({ loading: false, error: null, rows: res.data ?? [] }))
      .catch((e) => setState({ loading: false, error: e.message, rows: [] }));
  }, []);
  useEffect(() => { load(); }, [load]);

  return (
    <section className="bg-white dark:bg-wellq-dark rounded-2xl p-6 shadow-sm border border-wellq-gray/20 dark:border-white/10" aria-labelledby="mc-section-title">
      <div className="flex flex-wrap items-start justify-between gap-3 mb-4">
        <div>
          <div className="flex items-center gap-2">
            <CalendarCheck size={18} className="text-wellq-cyan" />
            <h3 id="mc-section-title" className="text-lg font-black text-wellq-dark dark:text-white">{t('recs.mcTitle')}</h3>
          </div>
          <p className="text-xs text-wellq-gray mt-1">{t('recs.mcSub')}</p>
        </div>
        {canEdit && (
          <button onClick={() => setOpen(true)} className="flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-bold bg-wellq-cyan text-wellq-black hover:bg-wellq-cyan/90">
            <Lock size={14} /> {t('recs.mcButton')}
          </button>
        )}
      </div>

      {state.error && <p role="alert" className="text-sm text-red-500">{state.error}</p>}
      {!state.loading && !state.error && state.rows.length === 0 && (
        <p className="text-sm text-wellq-gray">{t('recs.mcEmpty')}</p>
      )}
      {state.rows.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-[10px] uppercase tracking-wider text-wellq-gray">
                <th className="py-2 pr-4">{t('recs.mcColPeriod')}</th>
                <th className="py-2 pr-4 text-right">{t('recs.mcField_total_mrr')}</th>
                <th className="py-2 pr-4 text-right">{t('recs.mcColGrowth')}</th>
                <th className="py-2 pr-4">{t('recs.mcColSource')}</th>
                <th className="py-2 pr-4">{t('recs.mcColBy')}</th>
              </tr>
            </thead>
            <tbody>
              {state.rows.map((r) => (
                <tr key={`${r.period_month}-${r.period_year}`} className="border-t border-wellq-gray/10 dark:border-white/5">
                  <td className="py-2 pr-4 font-bold text-wellq-dark dark:text-white">{r.period_month} {r.period_year}</td>
                  <td className="py-2 pr-4 text-right tabular-nums">{money(r.total_mrr)}</td>
                  <td className={`py-2 pr-4 text-right tabular-nums ${r.monthly_growth_percentage < 0 ? 'text-red-500' : 'text-wellq-green'}`}>{r.monthly_growth_percentage}%</td>
                  <td className="py-2 pr-4">{t(r.source === 'manual' ? 'recs.mcManual' : 'recs.mcAuto')}</td>
                  <td className="py-2 pr-4 text-wellq-gray" title={r.notes || ''}>{r.closed_by || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {open && <CloseMonthModal onClose={() => setOpen(false)} onDone={() => { setOpen(false); load(); }} />}
    </section>
  );
}
