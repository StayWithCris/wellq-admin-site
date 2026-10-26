import React, { useEffect, useState } from 'react';
import { LineChart, Line, XAxis, YAxis, Tooltip, CartesianGrid, ResponsiveContainer } from 'recharts';
import { Calculator } from 'lucide-react';
import { apiFetch } from '../../api/client';

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
  const [params, setParams] = useState({ newClinics: 1, churn: 2, expansion: 0, months: 12 });
  const [state, setState] = useState({ loading: true, error: null, data: null });

  useEffect(() => {
    const t = setTimeout(async () => {
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
        setState({ loading: false, error: e.message || 'No se pudo simular', data: null });
      }
    }, 250); // espera a que el usuario suelte el slider
    return () => clearTimeout(t);
  }, [params]);

  const set = (k) => (v) => setParams((p) => ({ ...p, [k]: v }));
  const d = state.data;

  return (
    <div className="bg-white dark:bg-[#0b1017] rounded-2xl p-6 shadow-sm border border-wellq-gray/20 dark:border-[#1e293b]">
      <div className="flex items-center gap-2 mb-1">
        <Calculator size={18} className="text-wellq-cyan" />
        <h3 className="text-lg font-black text-wellq-dark dark:text-white">Simulador de ingresos</h3>
      </div>
      <p className="text-xs text-wellq-gray mb-5">
        Parte de tu MRR real y proyecta con los supuestos que muevas. Es una simulación, no una predicción.
      </p>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="space-y-4">
          <Slider label="Clínicas nuevas por mes" value={params.newClinics} min={0} max={10} step={1} onChange={set('newClinics')} />
          <Slider label="Churn mensual" value={params.churn} min={0} max={15} step={0.5} suffix="%" onChange={set('churn')} />
          <Slider label="Expansión mensual" value={params.expansion} min={0} max={10} step={0.5} suffix="%" onChange={set('expansion')} />
          <Slider label="Horizonte" value={params.months} min={6} max={24} step={6} suffix=" meses" onChange={set('months')} />
          {d && (
            <p className="text-[11px] text-wellq-gray">
              Base: {d.base.active_clinics} clínicas activas · MRR actual {money(d.base.mrr)} ·
              cada clínica nueva aporta {money(d.assumptions.avg_mrr_per_new_clinic)} (promedio actual).
            </p>
          )}
        </div>

        <div className="lg:col-span-2">
          {state.error && <p className="text-sm text-red-500">{state.error}</p>}
          {!state.error && (
            <>
              <div className="grid grid-cols-3 gap-3 mb-4">
                {[
                  ['MRR a 6 meses', d?.summary.mrr_6m],
                  ['MRR a 12 meses', d?.summary.mrr_12m],
                  ['ARR a 12 meses', d?.summary.arr_12m],
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
                    <Tooltip formatter={(v) => money(v)} labelFormatter={(m) => `Mes ${m}`} />
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
