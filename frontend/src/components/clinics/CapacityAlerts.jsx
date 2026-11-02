import React, { useEffect, useState } from 'react';
import { AlertTriangle, Mail, ChevronDown } from 'lucide-react';
import { apiFetch } from '../../api/client';
import { useLanguage } from '../../contexts/LanguageContext';

// Rec #21 — Alertas de límite de pacientes y oportunidad de upgrade
const draftLink = (c, t) => {
  const plan = c.suggested_plan ? t('recs.mailPlan', { plan: c.suggested_plan }) : '';
  const subject = t('recs.mailSubject', { name: c.name });
  const body = t('recs.mailBody', { name: c.name, used: c.patients_used, limit: c.patients_limit, pct: c.usage_pct, plan });
  return `mailto:${c.contact_email ?? ''}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
};

export default function CapacityAlerts() {
  const { t } = useLanguage();
  const [state, setState] = useState({ loading: true, error: null, data: null });
  const [open, setOpen] = useState(false);

  useEffect(() => {
    apiFetch('/api/capacity/alerts?threshold=90')
      .then((res) => setState({ loading: false, error: null, data: res.data }))
      .catch((e) => setState({ loading: false, error: e.message, data: null }));
  }, []);

  const d = state.data;
  if (state.loading || state.error || !d || d.total === 0) return null; // sin alertas: no ocupa espacio

  return (
    <div className="rounded-2xl border border-amber-400/40 bg-amber-50 dark:bg-amber-500/5 p-4">
      <button onClick={() => setOpen((o) => !o)} className="w-full flex items-center gap-3 text-left">
        <AlertTriangle size={20} className="text-amber-500 shrink-0" />
        <div className="flex-1">
          <div className="font-black text-wellq-dark dark:text-white">
            {d.total} {d.total === 1 ? t('recs.capClinic') : t('recs.capClinics')} {t('recs.capNear')}
          </div>
          <div className="text-xs text-wellq-gray">
            {t('recs.capSummary', { c: d.critical, w: d.warning, t: d.threshold })}
          </div>
        </div>
        <ChevronDown size={18} className={`transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>

      {open && (
        <div className="mt-4 space-y-3">
          {d.items.map((c) => (
            <div key={c.clinic_id} className="bg-white dark:bg-[#0b1017] rounded-xl p-3 border border-wellq-gray/20 dark:border-[#1e293b]">
              <div className="flex items-center justify-between gap-3 flex-wrap">
                <div>
                  <div className="font-bold text-sm text-wellq-dark dark:text-white">{c.name}</div>
                  <div className="text-xs text-wellq-gray">
                    {c.patients_used}/{c.patients_limit} {t('recs.capPatients')} ({c.usage_pct}%)
                    {c.current_plan ? ` · ${t('recs.capCurrentPlan')}: ${c.current_plan}` : ''}
                    {c.suggested_plan ? ` · ${t('recs.capSuggested')}: ${c.suggested_plan}` : ` · ${t('recs.capNoPlan')}`}
                  </div>
                </div>
                <a
                  href={draftLink(c, t)}
                  className="inline-flex items-center gap-1.5 text-xs font-bold px-3 py-1.5 rounded-lg bg-wellq-cyan/10 text-wellq-cyan hover:bg-wellq-cyan/20"
                >
                  <Mail size={14} /> {t('recs.capUpgrade')}
                </a>
              </div>
              <div className="mt-2 h-2 rounded-full bg-wellq-gray/15 overflow-hidden">
                <div
                  className={`h-full ${c.severity === 'critical' ? 'bg-red-500' : 'bg-amber-400'}`}
                  style={{ width: `${Math.min(c.usage_pct, 100)}%` }}
                />
              </div>
            </div>
          ))}
          <p className="text-[11px] text-wellq-gray">
            {t('recs.capNote')}
          </p>
        </div>
      )}
    </div>
  );
}
