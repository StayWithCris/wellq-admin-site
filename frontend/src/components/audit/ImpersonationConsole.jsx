import React, { useCallback, useEffect, useState } from 'react';
import { ShieldAlert, Download, Loader2, ChevronLeft, ChevronRight } from 'lucide-react';
import { toast } from 'sonner';
import { apiFetch } from '../../api/client';
import { useLanguage } from '../../contexts/LanguageContext';
import ConfirmDialog from '../ui/ConfirmDialog';

// Rec #14 — Consola de impersonación: historial, sesiones activas y revocación (solo super administrador)
const PAGE_SIZE = 25;
const STATUSES = ['', 'active', 'expired', 'revoked'];
const STATUS_STYLE = {
  active: 'bg-wellq-green/10 text-wellq-green border-wellq-green/30',
  expired: 'bg-wellq-gray/10 text-wellq-gray border-wellq-gray/20',
  revoked: 'bg-red-500/10 text-red-500 border-red-500/30',
};

const when = (iso) => (iso ? new Date(iso).toLocaleString('es-CL', { dateStyle: 'medium', timeStyle: 'short' }) : '—');
const inputCls = 'w-full rounded-lg border border-wellq-gray/20 dark:border-white/10 bg-transparent px-2.5 py-2 text-xs text-wellq-dark dark:text-white';
const EMPTY = { status: '', clinic: '', admin_email: '', date_from: '', date_to: '' };

const buildQuery = (f, page, pageSize) => {
  const p = new URLSearchParams();
  Object.entries(f).forEach(([k, v]) => { if (v) p.set(k, v); });
  p.set('page', String(page));
  p.set('page_size', String(pageSize));
  return p.toString();
};

export default function ImpersonationConsole() {
  const { t } = useLanguage();
  const [filters, setFilters] = useState(EMPTY);
  const [applied, setApplied] = useState(EMPTY);
  const [page, setPage] = useState(1);
  const [list, setList] = useState({ loading: true, error: null, total: 0, items: [] });
  const [summary, setSummary] = useState(null);
  const [confirm, setConfirm] = useState(null);   // { kind: 'one' | 'all', item }
  const [busy, setBusy] = useState(false);
  const [exporting, setExporting] = useState(false);

  const load = useCallback(() => {
    setList((s) => ({ ...s, loading: true, error: null }));
    apiFetch(`/api/impersonation/sessions?${buildQuery(applied, page, PAGE_SIZE)}`)
      .then((res) => setList({ loading: false, error: null, total: res?.total ?? 0, items: res?.data ?? [] }))
      .catch((e) => setList({ loading: false, error: e.message || 'Error', total: 0, items: [] }));
    apiFetch('/api/impersonation/summary').then((res) => setSummary(res.data)).catch(() => setSummary(null));
  }, [applied, page]);
  useEffect(() => { load(); }, [load]);

  const set = (k) => (e) => setFilters((f) => ({ ...f, [k]: e.target.value }));
  const apply = (e) => { e.preventDefault(); setPage(1); setApplied(filters); };
  const clear = () => { setFilters(EMPTY); setPage(1); setApplied(EMPTY); };

  const doConfirm = async () => {
    const { kind, item } = confirm;
    setConfirm(null);
    setBusy(true);
    try {
      if (kind === 'one') {
        await apiFetch(`/api/impersonation/sessions/${item.id}/revoke`, { method: 'POST' });
        toast.success(t('recs.icRevoked', { clinic: item.clinicName }));
      } else {
        const res = await apiFetch('/api/impersonation/revoke-active', { method: 'POST', body: JSON.stringify({}) });
        toast.success(t('recs.icRevokedN', { n: res.data.revoked }));
      }
      load();
    } catch (e) {
      if (e.status !== 403) toast.error(e.message);
    } finally {
      setBusy(false);
    }
  };

  const exportCsv = async () => {
    setExporting(true);
    try {
      const res = await apiFetch(`/api/impersonation/sessions?${buildQuery(applied, 1, 1000)}`);
      const esc = (v) => `"${String(v ?? '').replace(/"/g, '""')}"`;
      const header = [t('recs.icColStarted'), t('recs.icColAdmin'), t('recs.icColClinic'), t('recs.icColReason'), t('recs.icColStatus'), t('recs.icColExpires'), t('recs.icColRevoked')];
      const lines = (res?.data ?? []).map((r) => [when(r.startedAt), r.adminEmail, r.clinicName, r.reason, t(`recs.icStatus_${r.status}`), when(r.expiresAt), when(r.revokedAt)].map(esc).join(','));
      const blob = new Blob(['\uFEFF' + [header.join(','), ...lines].join('\n')], { type: 'text/csv;charset=utf-8;' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `sesiones_soporte_wellq_${new Date().toISOString().slice(0, 10)}.csv`;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      toast.error(t('recs.icExportError'));
    } finally {
      setExporting(false);
    }
  };

  const pages = Math.max(Math.ceil(list.total / PAGE_SIZE), 1);
  const cards = summary ? [
    { label: t('recs.icStatActive'), value: summary.activeNow, hot: summary.activeNow > 0 },
    { label: t('recs.icStat24h'), value: summary.last24h },
    { label: t('recs.icStat30d'), value: summary.last30d },
    { label: t('recs.icStatRevoked'), value: summary.revoked30d },
  ] : [];

  return (
    <div className="space-y-4">
      <section className="bg-white dark:bg-wellq-dark rounded-2xl p-5 shadow-sm border border-wellq-gray/20 dark:border-white/10" aria-labelledby="ic-title">
        <div className="flex flex-wrap items-start justify-between gap-3 mb-4">
          <div>
            <div className="flex items-center gap-2">
              <ShieldAlert size={18} className="text-wellq-cyan" />
              <h3 id="ic-title" className="text-lg font-black text-wellq-dark dark:text-white">{t('recs.icTitle')}</h3>
            </div>
            <p className="text-xs text-wellq-gray mt-1">{t('recs.icSub')}</p>
          </div>
          <div className="flex gap-2">
            <button onClick={exportCsv} disabled={exporting || list.total === 0}
              className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-bold bg-wellq-cyan/10 text-wellq-dark dark:text-white border border-wellq-cyan/30 disabled:opacity-50">
              {exporting ? <Loader2 size={14} className="animate-spin" /> : <Download size={14} />} {t('recs.icExport')}
            </button>
            <button onClick={() => setConfirm({ kind: 'all' })} disabled={busy || !summary || summary.activeNow === 0}
              className="px-3 py-2 rounded-lg text-xs font-bold border border-red-300/50 text-red-500 disabled:opacity-40">
              {t('recs.icRevokeAll')}
            </button>
          </div>
        </div>

        {summary && (
          <>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-3">
              {cards.map((c) => (
                <div key={c.label} className={`rounded-xl border p-3 ${c.hot ? 'border-wellq-green/40 bg-wellq-green/5' : 'border-wellq-gray/15 dark:border-white/10'}`}>
                  <div className="text-[10px] uppercase tracking-wider text-wellq-gray font-bold">{c.label}</div>
                  <div className="text-xl font-black text-wellq-dark dark:text-white tabular-nums">{c.value}</div>
                </div>
              ))}
            </div>
            {(summary.topAdmins.length > 0 || summary.topClinics.length > 0) && (
              <div className="grid md:grid-cols-2 gap-3 text-xs text-wellq-gray mb-3">
                <p><span className="font-bold text-wellq-dark dark:text-white">{t('recs.icTopAdmins')}:</span> {summary.topAdmins.map((a) => `${a.name} (${a.count})`).join(' · ') || '—'}</p>
                <p><span className="font-bold text-wellq-dark dark:text-white">{t('recs.icTopClinics')}:</span> {summary.topClinics.map((a) => `${a.name} (${a.count})`).join(' · ') || '—'}</p>
              </div>
            )}
          </>
        )}

        <form onSubmit={apply} className="grid grid-cols-2 md:grid-cols-6 gap-2">
          <select aria-label={t('recs.icColStatus')} className={inputCls} value={filters.status} onChange={set('status')}>
            {STATUSES.map((s) => <option key={s} value={s}>{s ? t(`recs.icStatus_${s}`) : t('recs.icAllStatuses')}</option>)}
          </select>
          <input aria-label={t('recs.icColClinic')} className={inputCls} placeholder={t('recs.icColClinic')} value={filters.clinic} onChange={set('clinic')} />
          <input aria-label={t('recs.icColAdmin')} className={inputCls} placeholder={t('recs.icColAdmin')} value={filters.admin_email} onChange={set('admin_email')} />
          <input aria-label={t('recs.icFrom')} type="date" className={inputCls} value={filters.date_from} onChange={set('date_from')} />
          <input aria-label={t('recs.icTo')} type="date" className={inputCls} value={filters.date_to} onChange={set('date_to')} />
          <div className="flex gap-2">
            <button type="submit" className="flex-1 px-3 py-2 rounded-lg text-xs font-bold bg-wellq-cyan text-wellq-black">{t('recs.icFilter')}</button>
            <button type="button" onClick={clear} className="px-3 py-2 rounded-lg text-xs font-bold border border-wellq-gray/30 text-wellq-gray">{t('recs.icClear')}</button>
          </div>
        </form>
      </section>

      <section className="bg-white dark:bg-wellq-dark rounded-2xl p-5 shadow-sm border border-wellq-gray/20 dark:border-white/10">
        {list.error && <p role="alert" className="text-sm text-red-500">{list.error}</p>}
        {!list.error && list.loading && <p className="flex items-center gap-2 text-sm text-wellq-gray"><Loader2 size={14} className="animate-spin" /> {t('recs.icLoading')}</p>}
        {!list.error && !list.loading && list.items.length === 0 && <p className="text-sm text-wellq-gray">{t('recs.icEmpty')}</p>}
        {list.items.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-[10px] uppercase tracking-wider text-wellq-gray">
                  <th className="py-2 pr-3">{t('recs.icColStarted')}</th>
                  <th className="py-2 pr-3">{t('recs.icColAdmin')}</th>
                  <th className="py-2 pr-3">{t('recs.icColClinic')}</th>
                  <th className="py-2 pr-3">{t('recs.icColReason')}</th>
                  <th className="py-2 pr-3">{t('recs.icColStatus')}</th>
                  <th className="py-2 text-right">{t('recs.icColActions')}</th>
                </tr>
              </thead>
              <tbody>
                {list.items.map((r) => (
                  <tr key={r.id} className="border-t border-wellq-gray/10 dark:border-white/5 align-top">
                    <td className="py-2 pr-3 text-xs text-wellq-gray whitespace-nowrap">{when(r.startedAt)}</td>
                    <td className="py-2 pr-3 text-wellq-dark dark:text-white">{r.adminEmail}</td>
                    <td className="py-2 pr-3 font-bold text-wellq-dark dark:text-white">{r.clinicName}</td>
                    <td className="py-2 pr-3 text-xs text-wellq-gray max-w-[280px]">{r.reason}</td>
                    <td className="py-2 pr-3">
                      <span className={`px-2 py-0.5 rounded-full border text-[11px] font-bold ${STATUS_STYLE[r.status]}`}>{t(`recs.icStatus_${r.status}`)}</span>
                      <div className="text-[11px] text-wellq-gray mt-1">
                        {r.status === 'active' && t('recs.icRemaining', { n: r.minutesRemaining })}
                        {r.status === 'expired' && t('recs.icExpiredAt', { when: when(r.expiresAt) })}
                        {r.status === 'revoked' && t('recs.icRevokedAt', { when: when(r.revokedAt) })}
                      </div>
                    </td>
                    <td className="py-2 text-right">
                      {r.status === 'active' && (
                        <button disabled={busy} onClick={() => setConfirm({ kind: 'one', item: r })}
                          className="px-2.5 py-1 rounded-lg text-xs font-bold border border-red-300/50 text-red-500 disabled:opacity-40">{t('recs.icRevoke')}</button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="flex items-center justify-between mt-3 text-xs text-wellq-gray">
          <span>{t('recs.icTotal', { n: list.total })}</span>
          <div className="flex items-center gap-2">
            <button disabled={page <= 1} onClick={() => setPage((p) => p - 1)} aria-label={t('recs.icPrev')} className="p-1.5 rounded-lg border border-wellq-gray/30 disabled:opacity-40"><ChevronLeft size={14} /></button>
            <span className="tabular-nums">{page} / {pages}</span>
            <button disabled={page >= pages} onClick={() => setPage((p) => p + 1)} aria-label={t('recs.icNext')} className="p-1.5 rounded-lg border border-wellq-gray/30 disabled:opacity-40"><ChevronRight size={14} /></button>
          </div>
        </div>
      </section>

      <ConfirmDialog
        open={Boolean(confirm)}
        title={confirm?.kind === 'all' ? t('recs.icRevokeAllTitle') : t('recs.icRevokeTitle')}
        message={confirm?.kind === 'all'
          ? t('recs.icRevokeAllMsg', { n: summary?.activeNow ?? 0 })
          : t('recs.icRevokeMsg', { clinic: confirm?.item?.clinicName, admin: confirm?.item?.adminEmail })}
        onConfirm={doConfirm}
        onCancel={() => setConfirm(null)}
      />
    </div>
  );
}
