import React, { useCallback, useEffect, useState } from 'react';
import { Tag, Plus, X, Loader2, Trash2, Edit3 } from 'lucide-react';
import { toast } from 'sonner';
import { apiFetch } from '../../api/client';
import { useLanguage } from '../../contexts/LanguageContext';
import ConfirmDialog from '../ui/ConfirmDialog';

// Rec #4 — Gestión de descuentos (porcentaje o monto fijo, vigencia, tope de usos, aplicación por clínica)

const STATUS_STYLE = {
  active: 'bg-wellq-green/10 text-wellq-green border-wellq-green/30',
  scheduled: 'bg-wellq-cyan/10 text-wellq-cyan border-wellq-cyan/30',
  expired: 'bg-wellq-gray/10 text-wellq-gray border-wellq-gray/20',
  exhausted: 'bg-amber-400/10 text-amber-600 dark:text-amber-400 border-amber-400/30',
  inactive: 'bg-wellq-gray/10 text-wellq-gray border-wellq-gray/20',
};

const money = (n) => `$${Number(n ?? 0).toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
const day = (iso) => (iso ? new Date(iso).toLocaleDateString('es-CL', { dateStyle: 'medium' }) : '—');
const toDateInput = (iso) => (iso ? iso.slice(0, 10) : '');
const valueLabel = (d) => (d.kind === 'percent' ? `${d.value} %` : money(d.value));

const inputCls = 'w-full rounded-lg border border-wellq-gray/30 dark:border-white/10 bg-white dark:bg-[#0b1017] px-3 py-2 text-sm text-wellq-dark dark:text-white';
const labelCls = 'text-xs font-bold text-wellq-gray';

const EMPTY = {
  code: '', name: '', description: '', kind: 'percent', value: '', plan_id: '',
  duration_months: '', starts_at: '', ends_at: '', max_redemptions: '',
};

function Modal({ title, onClose, children, footer, labelledBy }) {
  const { t } = useLanguage();
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" role="dialog" aria-modal="true" aria-labelledby={labelledBy}>
      <div className="w-full max-w-lg bg-white dark:bg-wellq-dark rounded-2xl shadow-xl border border-wellq-gray/20 dark:border-white/10 max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between px-6 py-4 border-b border-wellq-gray/10 dark:border-white/5">
          <h3 id={labelledBy} className="text-lg font-black text-wellq-dark dark:text-white">{title}</h3>
          <button onClick={onClose} aria-label={t('recs.dsCancel')} className="p-1 rounded-lg text-wellq-gray hover:text-wellq-dark dark:hover:text-white"><X size={18} /></button>
        </div>
        <div className="p-6 space-y-4">{children}</div>
        <div className="flex justify-end gap-2 px-6 py-4 border-t border-wellq-gray/10 dark:border-white/5">{footer}</div>
      </div>
    </div>
  );
}

function DiscountForm({ initial, plans, onClose, onSaved }) {
  const { t } = useLanguage();
  const editing = Boolean(initial?.id);
  const locked = editing && (initial.redemptions ?? 0) > 0;   // ya se usó: no se cambia valor, tipo ni plan
  const [f, setF] = useState(() => (editing ? {
    code: initial.code, name: initial.name, description: initial.description || '', kind: initial.kind,
    value: String(initial.value), plan_id: initial.planId || '', duration_months: initial.durationMonths ?? '',
    starts_at: toDateInput(initial.startsAt), ends_at: toDateInput(initial.endsAt), max_redemptions: initial.maxRedemptions ?? '',
  } : EMPTY));
  const [saving, setSaving] = useState(false);
  const set = (k) => (e) => setF((x) => ({ ...x, [k]: e.target.value }));

  const submit = async () => {
    setSaving(true);
    try {
      const num = (v) => (v === '' || v === null ? null : Number(v));
      const date = (v, end) => (v ? `${v}T${end ? '23:59:59' : '00:00:00'}` : null);
      const body = {
        name: f.name.trim(), description: f.description.trim() || null,
        kind: f.kind, value: Number(f.value), plan_id: f.plan_id || null,
        duration_months: num(f.duration_months), max_redemptions: num(f.max_redemptions),
        starts_at: date(f.starts_at, false), ends_at: date(f.ends_at, true),
      };
      if (editing) {
        await apiFetch(`/api/discounts/${initial.id}`, { method: 'PATCH', body: JSON.stringify(body) });
        toast.success(t('recs.dsSaved', { code: f.code }));
      } else {
        await apiFetch('/api/discounts', { method: 'POST', body: JSON.stringify({ ...body, code: f.code.trim() }) });
        toast.success(t('recs.dsCreated', { code: f.code.trim().toUpperCase() }));
      }
      onSaved();
    } catch (e) {
      if (e.status !== 403) toast.error(e.message);
    } finally {
      setSaving(false);
    }
  };

  const valid = f.name.trim().length >= 2 && Number(f.value) > 0 && (editing || /^[A-Za-z0-9_-]{3,30}$/.test(f.code.trim()))
    && (f.kind !== 'percent' || Number(f.value) <= 100);

  return (
    <Modal title={t(editing ? 'recs.dsEditTitle' : 'recs.dsNewTitle')} onClose={onClose} labelledBy="ds-form-title"
      footer={(
        <>
          <button onClick={onClose} className="px-4 py-2 rounded-xl text-sm font-bold text-wellq-gray hover:text-wellq-dark dark:hover:text-white">{t('recs.dsCancel')}</button>
          <button onClick={submit} disabled={!valid || saving}
            className="flex items-center gap-2 px-5 py-2 rounded-xl text-sm font-bold bg-wellq-cyan text-wellq-black disabled:opacity-50 disabled:cursor-not-allowed">
            {saving && <Loader2 size={14} className="animate-spin" />} {t('recs.dsSave')}
          </button>
        </>
      )}>
      {locked && <p className="text-xs rounded-lg bg-amber-400/10 border border-amber-400/30 text-amber-700 dark:text-amber-400 p-3">{t('recs.dsLockedNote')}</p>}
      <div className="grid grid-cols-2 gap-3">
        <label className={labelCls}>{t('recs.dsCode')}
          <input id="ds-code" className={`${inputCls} mt-1 uppercase`} value={f.code} onChange={set('code')} disabled={editing} maxLength={30} />
        </label>
        <label className={labelCls}>{t('recs.dsName')}
          <input id="ds-name" className={`${inputCls} mt-1`} value={f.name} onChange={set('name')} maxLength={80} />
        </label>
        <label className={labelCls}>{t('recs.dsKind')}
          <select id="ds-kind" className={`${inputCls} mt-1`} value={f.kind} onChange={set('kind')} disabled={locked}>
            <option value="percent">{t('recs.dsKindPercent')}</option>
            <option value="fixed">{t('recs.dsKindFixed')}</option>
          </select>
        </label>
        <label className={labelCls}>{t('recs.dsValue')}
          <input id="ds-value" type="number" min="0" step="0.01" className={`${inputCls} mt-1`} value={f.value} onChange={set('value')} disabled={locked} />
        </label>
        <label className={`${labelCls} col-span-2`}>{t('recs.dsPlan')}
          <select id="ds-plan" className={`${inputCls} mt-1`} value={f.plan_id} onChange={set('plan_id')} disabled={locked}>
            <option value="">{t('recs.dsAllPlans')}</option>
            {plans.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </label>
        <label className={labelCls}>{t('recs.dsStarts')}
          <input id="ds-starts" type="date" className={`${inputCls} mt-1`} value={f.starts_at} onChange={set('starts_at')} />
        </label>
        <label className={labelCls}>{t('recs.dsEnds')}
          <input id="ds-ends" type="date" className={`${inputCls} mt-1`} value={f.ends_at} onChange={set('ends_at')} />
        </label>
        <label className={labelCls}>{t('recs.dsDuration')}
          <input id="ds-duration" type="number" min="1" max="60" className={`${inputCls} mt-1`} value={f.duration_months} onChange={set('duration_months')} placeholder={t('recs.dsNoLimit')} />
        </label>
        <label className={labelCls}>{t('recs.dsMax')}
          <input id="ds-max" type="number" min="1" className={`${inputCls} mt-1`} value={f.max_redemptions} onChange={set('max_redemptions')} placeholder={t('recs.dsNoLimit')} />
        </label>
      </div>
      <label className={`${labelCls} block`}>{t('recs.dsDescription')}
        <textarea id="ds-description" rows={2} maxLength={500} className={`${inputCls} mt-1`} value={f.description} onChange={set('description')} />
      </label>
    </Modal>
  );
}

function ApplyModal({ discount, onClose, onApplied }) {
  const { t } = useLanguage();
  const [clinics, setClinics] = useState({ loading: true, rows: [] });
  const [clinicId, setClinicId] = useState('');
  const [notes, setNotes] = useState('');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    apiFetch('/api/clinics?page_size=500&sort_by=name')
      .then((res) => setClinics({ loading: false, rows: res?.data ?? [] }))
      .catch(() => setClinics({ loading: false, rows: [] }));
  }, []);

  const submit = async () => {
    setSaving(true);
    try {
      const res = await apiFetch(`/api/discounts/${discount.id}/apply`, {
        method: 'POST', body: JSON.stringify({ clinic_id: clinicId, notes: notes.trim() || undefined }),
      });
      const a = res.data;
      toast.success(t('recs.dsApplied', { code: discount.code, clinic: a.clinicName, price: money(a.discountedPrice) }));
      onApplied();
    } catch (e) {
      if (e.status !== 403) toast.error(e.message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal title={t('recs.dsApplyTitle', { code: discount.code })} onClose={onClose} labelledBy="ds-apply-title"
      footer={(
        <>
          <button onClick={onClose} className="px-4 py-2 rounded-xl text-sm font-bold text-wellq-gray hover:text-wellq-dark dark:hover:text-white">{t('recs.dsCancel')}</button>
          <button onClick={submit} disabled={!clinicId || saving}
            className="flex items-center gap-2 px-5 py-2 rounded-xl text-sm font-bold bg-wellq-cyan text-wellq-black disabled:opacity-50 disabled:cursor-not-allowed">
            {saving && <Loader2 size={14} className="animate-spin" />} {t('recs.dsApply')}
          </button>
        </>
      )}>
      <p className="text-xs text-wellq-gray">{t('recs.dsApplyHint', { value: valueLabel(discount) })}</p>
      <label className={`${labelCls} block`}>{t('recs.dsClinic')}
        <select id="ds-apply-clinic" className={`${inputCls} mt-1`} value={clinicId} onChange={(e) => setClinicId(e.target.value)} disabled={clinics.loading}>
          <option value="">{clinics.loading ? t('recs.dsLoading') : t('recs.dsChooseClinic')}</option>
          {clinics.rows.map((c) => <option key={c.clinic_id} value={c.clinic_id}>{c.name}</option>)}
        </select>
      </label>
      <label className={`${labelCls} block`}>{t('recs.dsNotes')}
        <textarea id="ds-apply-notes" rows={2} maxLength={500} className={`${inputCls} mt-1`} value={notes} onChange={(e) => setNotes(e.target.value)} />
      </label>
    </Modal>
  );
}

export function DiscountsManager({ plans = [] }) {
  const { t } = useLanguage();
  const [state, setState] = useState({ loading: true, error: null, data: null });
  const [form, setForm] = useState(null);          // null | {} (nuevo) | descuento (editar)
  const [applyTo, setApplyTo] = useState(null);
  const [confirm, setConfirm] = useState(null);    // { kind: 'delete' | 'revoke', item }
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    apiFetch('/api/discounts')
      .then((res) => setState({ loading: false, error: null, data: res.data }))
      .catch((e) => setState({ loading: false, error: e.message, data: null }));
  }, []);
  useEffect(() => { load(); }, [load]);

  const act = async (fn, okMsg) => {
    setBusy(true);
    try {
      await fn();
      if (okMsg) toast.success(okMsg);
      load();
    } catch (e) {
      if (e.status !== 403) toast.error(e.message);
    } finally {
      setBusy(false);
    }
  };

  const toggle = (d) => act(
    () => apiFetch(`/api/discounts/${d.id}`, { method: 'PATCH', body: JSON.stringify({ is_active: !d.isActive }) }),
    t(d.isActive ? 'recs.dsDeactivated' : 'recs.dsActivated', { code: d.code }),
  );

  const doConfirm = async () => {
    const { kind, item } = confirm;
    setConfirm(null);
    if (kind === 'delete') {
      await act(() => apiFetch(`/api/discounts/${item.id}`, { method: 'DELETE' }), t('recs.dsDeleted', { code: item.code }));
    } else {
      await act(() => apiFetch(`/api/discounts/assignments/${item.id}/revoke`, { method: 'POST' }), t('recs.dsRevoked', { clinic: item.clinicName }));
    }
  };

  if (state.loading) return <p className="flex items-center gap-2 text-sm text-wellq-gray"><Loader2 size={14} className="animate-spin" /> {t('recs.dsLoading')}</p>;
  if (state.error) return <p role="alert" className="text-sm text-red-500">{t('recs.dsError')}: {state.error}</p>;

  const { discounts, running_assignments: running, summary } = state.data;
  const cards = [
    { label: t('recs.dsStatActive'), value: summary.active },
    { label: t('recs.dsStatClinics'), value: summary.clinics_with_discount },
    { label: t('recs.dsStatImpact'), value: money(summary.monthly_impact) },
    { label: t('recs.dsStatTotal'), value: summary.total },
  ];

  return (
    <div className="space-y-6">
      <section className="bg-white dark:bg-wellq-dark rounded-2xl p-6 shadow-sm border border-wellq-gray/20 dark:border-white/10" aria-labelledby="ds-title">
        <div className="flex flex-wrap items-start justify-between gap-3 mb-4">
          <div>
            <div className="flex items-center gap-2">
              <Tag size={18} className="text-wellq-cyan" />
              <h3 id="ds-title" className="text-lg font-black text-wellq-dark dark:text-white">{t('recs.dsTitle')}</h3>
            </div>
            <p className="text-xs text-wellq-gray mt-1">{t('recs.dsSub')}</p>
          </div>
          <button onClick={() => setForm({})} className="flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-bold bg-wellq-cyan text-wellq-black hover:bg-wellq-cyan/90">
            <Plus size={14} /> {t('recs.dsNew')}
          </button>
        </div>

        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
          {cards.map((c) => (
            <div key={c.label} className="rounded-xl border border-wellq-gray/15 dark:border-white/10 p-3">
              <div className="text-[10px] uppercase tracking-wider text-wellq-gray font-bold">{c.label}</div>
              <div className="text-xl font-black text-wellq-dark dark:text-white tabular-nums">{c.value}</div>
            </div>
          ))}
        </div>
        <p className="text-[11px] text-wellq-gray mb-4">{t('recs.dsImpactNote')}</p>

        {discounts.length === 0 ? (
          <p className="text-sm text-wellq-gray">{t('recs.dsEmpty')}</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-[10px] uppercase tracking-wider text-wellq-gray">
                  <th className="py-2 pr-3">{t('recs.dsCode')}</th>
                  <th className="py-2 pr-3">{t('recs.dsColDiscount')}</th>
                  <th className="py-2 pr-3">{t('recs.dsPlan')}</th>
                  <th className="py-2 pr-3">{t('recs.dsColValidity')}</th>
                  <th className="py-2 pr-3 text-right">{t('recs.dsColUses')}</th>
                  <th className="py-2 pr-3">{t('recs.dsColStatus')}</th>
                  <th className="py-2 text-right">{t('recs.dsColActions')}</th>
                </tr>
              </thead>
              <tbody>
                {discounts.map((d) => (
                  <tr key={d.id} className="border-t border-wellq-gray/10 dark:border-white/5 align-top">
                    <td className="py-2 pr-3 font-black text-wellq-dark dark:text-white">{d.code}</td>
                    <td className="py-2 pr-3 text-wellq-dark dark:text-white">
                      <div className="font-bold">{valueLabel(d)}</div>
                      <div className="text-xs text-wellq-gray">{d.name}{d.durationMonths ? ` · ${t('recs.dsMonths', { n: d.durationMonths })}` : ''}</div>
                    </td>
                    <td className="py-2 pr-3 text-wellq-dark dark:text-white">{d.planName ?? t('recs.dsAllPlans')}</td>
                    <td className="py-2 pr-3 text-xs text-wellq-gray">{day(d.startsAt)} → {d.endsAt ? day(d.endsAt) : t('recs.dsNoLimit')}</td>
                    <td className="py-2 pr-3 text-right tabular-nums text-wellq-dark dark:text-white">{d.redemptions}{d.maxRedemptions ? ` / ${d.maxRedemptions}` : ''}</td>
                    <td className="py-2 pr-3">
                      <span className={`px-2 py-0.5 rounded-full border text-[11px] font-bold ${STATUS_STYLE[d.status] ?? STATUS_STYLE.inactive}`}>{t(`recs.dsStatus_${d.status}`)}</span>
                    </td>
                    <td className="py-2">
                      <div className="flex flex-wrap justify-end gap-1.5">
                        <button disabled={busy || d.status !== 'active'} onClick={() => setApplyTo(d)}
                          className="px-2.5 py-1 rounded-lg text-xs font-bold bg-wellq-cyan/10 border border-wellq-cyan/30 text-wellq-dark dark:text-white disabled:opacity-40">{t('recs.dsApply')}</button>
                        <button disabled={busy} onClick={() => toggle(d)}
                          className="px-2.5 py-1 rounded-lg text-xs font-bold border border-wellq-gray/30 text-wellq-dark dark:text-white disabled:opacity-40">
                          {t(d.isActive ? 'recs.dsDeactivate' : 'recs.dsActivate')}
                        </button>
                        <button disabled={busy} onClick={() => setForm(d)} aria-label={t('recs.dsEdit')} title={t('recs.dsEdit')}
                          className="p-1.5 rounded-lg border border-wellq-gray/30 text-wellq-gray hover:text-wellq-dark dark:hover:text-white disabled:opacity-40"><Edit3 size={14} /></button>
                        {d.redemptions === 0 && (
                          <button disabled={busy} onClick={() => setConfirm({ kind: 'delete', item: d })} aria-label={t('recs.dsDelete')} title={t('recs.dsDelete')}
                            className="p-1.5 rounded-lg border border-red-300/50 text-red-500 disabled:opacity-40"><Trash2 size={14} /></button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="bg-white dark:bg-wellq-dark rounded-2xl p-6 shadow-sm border border-wellq-gray/20 dark:border-white/10" aria-labelledby="ds-running-title">
        <h3 id="ds-running-title" className="text-base font-black text-wellq-dark dark:text-white mb-1">{t('recs.dsRunningTitle')}</h3>
        <p className="text-xs text-wellq-gray mb-3">{t('recs.dsRunningSub')}</p>
        {running.length === 0 ? (
          <p className="text-sm text-wellq-gray">{t('recs.dsRunningEmpty')}</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-[10px] uppercase tracking-wider text-wellq-gray">
                  <th className="py-2 pr-3">{t('recs.dsClinic')}</th>
                  <th className="py-2 pr-3">{t('recs.dsCode')}</th>
                  <th className="py-2 pr-3 text-right">{t('recs.dsColBase')}</th>
                  <th className="py-2 pr-3 text-right">{t('recs.dsColFinal')}</th>
                  <th className="py-2 pr-3">{t('recs.dsColSince')}</th>
                  <th className="py-2 pr-3">{t('recs.dsColUntil')}</th>
                  <th className="py-2 text-right">{t('recs.dsColActions')}</th>
                </tr>
              </thead>
              <tbody>
                {running.map((a) => (
                  <tr key={a.id} className="border-t border-wellq-gray/10 dark:border-white/5" title={a.notes || ''}>
                    <td className="py-2 pr-3 font-bold text-wellq-dark dark:text-white">{a.clinicName}</td>
                    <td className="py-2 pr-3 text-wellq-dark dark:text-white">{a.code}</td>
                    <td className="py-2 pr-3 text-right tabular-nums text-wellq-gray">{money(a.basePrice)}</td>
                    <td className="py-2 pr-3 text-right tabular-nums font-bold text-wellq-dark dark:text-white">{money(a.discountedPrice)}</td>
                    <td className="py-2 pr-3 text-xs text-wellq-gray">{day(a.appliedAt)} · {a.appliedBy}</td>
                    <td className="py-2 pr-3 text-xs text-wellq-gray">{a.endsAt ? day(a.endsAt) : t('recs.dsNoLimit')}</td>
                    <td className="py-2 text-right">
                      <button disabled={busy} onClick={() => setConfirm({ kind: 'revoke', item: a })}
                        className="px-2.5 py-1 rounded-lg text-xs font-bold border border-red-300/50 text-red-500 disabled:opacity-40">{t('recs.dsRevoke')}</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {form && <DiscountForm initial={form} plans={plans} onClose={() => setForm(null)} onSaved={() => { setForm(null); load(); }} />}
      {applyTo && <ApplyModal discount={applyTo} onClose={() => setApplyTo(null)} onApplied={() => { setApplyTo(null); load(); }} />}
      <ConfirmDialog
        open={Boolean(confirm)}
        title={confirm?.kind === 'delete' ? t('recs.dsDeleteTitle') : t('recs.dsRevokeTitle')}
        message={confirm?.kind === 'delete'
          ? t('recs.dsDeleteMsg', { code: confirm?.item?.code })
          : t('recs.dsRevokeMsg', { code: confirm?.item?.code, clinic: confirm?.item?.clinicName })}
        onConfirm={doConfirm}
        onCancel={() => setConfirm(null)}
      />
    </div>
  );
}
