import React, { useEffect, useState } from 'react';
import { LineChart, Line, XAxis, YAxis, Tooltip, CartesianGrid, ResponsiveContainer } from 'recharts';
import { Calculator } from 'lucide-react';
import { apiFetch } from '../../api/client';
import { useLanguage } from '../../contexts/LanguageContext';

// Rec #3 — Simulador de ingresos (proyección de MRR/ARR sobre datos reales)
const money = (n) => `$${Math.round(n ?? 0).toLocaleString()}`;

const Slider = ({ label, value, min, max, step, suffix, onChange }) => (
  <label className="block">
    <div className="flex justify-between text-xs font-bold text-wellq-gray mb-1">
      <span>{label}</span>
      <span className="text-wellq-dark dark:text-white">{value}{suffix}</span>
    </div>
    <input
      type="range" min={min} max={max} step={step} value={value}
      onChange={(e) => onChange(Number(e.target.value))}
      className="w-full accent-wellq-cyan"
    />
  </label>
);

export default function RevenueSimulator() {
  const { t } = useLanguage();
  const [params, setParams] = useState({ newClinics: 1, churn: 2, expansion: 0, months: 12 });
  const [state, setState] = useState({ loading: true, error: null, data: null });

  useEffect(() => {
    const timer = setTimeout(async () => {
      try {
        const q = new URLSearchParams({
          new_clinics_per_month: params.newClinics,
          churn_pct: params.churn,
          expansion_pct: params.expansion,
          months: params.months,
        });
        const res = await apiFetch(`/api/simulator/revenue?${q}`);
        setState({ loading: false, error: null, data: res.data });
      } catch (e) {
        setState({ loading: false, error: e.message || t('recs.simError'), data: null });
      }
    }, 250); // espera a que el usuario suelte el slider
    return () => clearTimeout(timer);
  }, [params]);

  const set = (k) => (v) => setParams((p) => ({ ...p, [k]: v }));
  const d = state.data;

  return (
    <div className="bg-white dark:bg-[#0b1017] rounded-2xl p-6 shadow-sm border border-wellq-gray/20 dark:border-[#1e293b]">
      <div className="flex items-center gap-2 mb-1">
        <Calculator size={18} className="text-wellq-cyan" />
        <h3 className="text-lg font-black text-wellq-dark dark:text-white">{t('recs.simTitle')}</h3>
      </div>
      <p className="text-xs text-wellq-gray mb-5">
        {t('recs.simSub')}
      </p>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="space-y-4">
          <Slider label={t('recs.newClinics')} value={params.newClinics} min={0} max={10} step={1} onChange={set('newClinics')} />
          <Slider label={t('recs.monthlyChurn')} value={params.churn} min={0} max={15} step={0.5} suffix="%" onChange={set('churn')} />
          <Slider label={t('recs.monthlyExpansion')} value={params.expansion} min={0} max={10} step={0.5} suffix="%" onChange={set('expansion')} />
          <Slider label={t('recs.horizon')} value={params.months} min={6} max={24} step={6} suffix={t('recs.monthsSuffix')} onChange={set('months')} />
          {d && (
            <p className="text-[11px] text-wellq-gray">
              {t('recs.simBase', { n: d.base.active_clinics, mrr: money(d.base.mrr), avg: money(d.assumptions.avg_mrr_per_new_clinic) })}
            </p>
          )}
        </div>

        <div className="lg:col-span-2">
          {state.error && <p className="text-sm text-red-500">{state.error}</p>}
          {!state.error && (
            <>
              <div className="grid grid-cols-3 gap-3 mb-4">
                {[
                  [t('recs.mrr6'), d?.summary.mrr_6m],
                  [t('recs.mrr12'), d?.summary.mrr_12m],
                  [t('recs.arr12'), d?.summary.arr_12m],
                ].map(([label, v]) => (
                  <div key={label} className="rounded-xl bg-wellq-cyan/5 border border-wellq-cyan/20 p-3">
                    <div className="text-[11px] font-bold text-wellq-gray">{label}</div>
                    <div className="text-xl font-black text-wellq-dark dark:text-white">{d ? money(v) : '…'}</div>
                  </div>
                ))}
              </div>
              <div className="h-56">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={d?.series ?? []}>
                    <CartesianGrid strokeDasharray="3 3" opacity={0.2} />
                    <XAxis dataKey="month" tickFormatter={(m) => `M${m}`} fontSize={11} />
                    <YAxis tickFormatter={money} fontSize={11} width={60} />
                    <Tooltip formatter={(v) => money(v)} labelFormatter={(m) => t('recs.monthN', { m })} />
                    <Line type="monotone" dataKey="mrr" name="MRR" stroke="#06b6d4" strokeWidth={2.5} dot={false} />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
