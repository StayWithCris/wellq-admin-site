import React, { useCallback, useEffect, useState } from 'react';
import { Shuffle, Loader2, ChevronDown, ChevronUp } from 'lucide-react';
import { toast } from 'sonner';
import { apiFetch } from '../../api/client';
import { useLanguage } from '../../contexts/LanguageContext';

// Rec #12 — Asignación inteligente de tickets por categoría
// Regla: la categoría define el equipo; dentro del equipo se elige a quien tenga menos tickets abiertos.
export default function TicketRoutingPanel({ refreshKey, canManage, onChanged, onRulesChanged }) {
  const { t } = useLanguage();
  const [state, setState] = useState({ loading: true, error: null, data: null });
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(false);
  const [lastRun, setLastRun] = useState(null);

  const load = useCallback(() => {
    apiFetch('/api/ticket-routing/rules')
      .then((res) => setState({ loading: false, error: null, data: res.data }))
      .catch((e) => setState({ loading: false, error: e.message, data: null }));
  }, []);
  useEffect(() => { load(); }, [load, refreshKey]);

  const patchRule = async (rule, body) => {
    setBusy(true);
    try {
      await apiFetch(`/api/ticket-routing/rules/${rule.category_id}`, { method: 'PATCH', body: JSON.stringify(body) });
      toast.success(t('recs.trRuleSaved', { category: rule.category }));
      load();
      onRulesChanged?.();
    } catch (e) {
      if (e.status !== 403) toast.error(e.message);
    } finally {
      setBusy(false);
    }
  };

  const assignPending = async () => {
    setBusy(true);
    try {
      const res = await apiFetch('/api/ticket-routing/assign-pending', { method: 'POST' });
      setLastRun(res.data);
      toast.success(t('recs.trAssignedN', { n: res.data.assigned.length }));
      load();
      onChanged?.();
    } catch (e) {
      if (e.status !== 403) toast.error(e.message);
    } finally {
      setBusy(false);
    }
  };

  if (state.loading) return null;
  if (state.error) return <p role="alert" className="text-sm text-red-500">{t('recs.trError')}: {state.error}</p>;

  const d = state.data;
  const reasons = { no_team: t('recs.trNoTeam'), no_responders: t('recs.trNoResponders'), unknown: t('recs.trUnknown') };

  return (
    <section className="bg-white dark:bg-wellq-dark rounded-2xl p-5 shadow-sm border border-wellq-gray/20 dark:border-[#1e293b]" aria-labelledby="tr-title">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open} className="flex items-center gap-2 text-left">
          <Shuffle size={18} className="text-wellq-cyan" />
          <span>
            <span id="tr-title" className="block text-base font-black text-wellq-dark dark:text-white">{t('recs.trTitle')}</span>
            <span className="block text-xs text-wellq-gray">{t('recs.trSub', { n: d.unassigned_total })}</span>
          </span>
          {open ? <ChevronUp size={16} className="text-wellq-gray" /> : <ChevronDown size={16} className="text-wellq-gray" />}
        </button>
        {canManage && d.unassigned_total > 0 && (
          <button onClick={assignPending} disabled={busy}
            className="flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-bold bg-wellq-cyan text-wellq-black disabled:opacity-50">
            {busy ? <Loader2 size={14} className="animate-spin" /> : <Shuffle size={14} />} {t('recs.trAssignPending', { n: d.unassigned_total })}
          </button>
        )}
      </div>

      {lastRun && (
        <div className="mt-3 rounded-xl bg-wellq-cyan/5 border border-wellq-cyan/20 p-3 text-xs text-wellq-dark dark:text-white" role="status">
          {lastRun.assigned.map((a) => (
            <p key={a.ticket_id}>{t('recs.trAssignedLine', { ticket: a.ticket_id, name: a.responder_name, team: a.team })}</p>
          ))}
          {lastRun.skipped.map((s) => (
            <p key={s.ticket_id} className="text-wellq-gray">{t('recs.trSkippedLine', { ticket: s.ticket_id, reason: reasons[s.reason] ?? s.reason })}</p>
          ))}
        </div>
      )}

      {open && (
        <div className="mt-4 overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-[10px] uppercase tracking-wider text-wellq-gray">
                <th className="py-2 pr-3">{t('recs.trColCategory')}</th>
                <th className="py-2 pr-3">{t('recs.trColTeam')}</th>
                <th className="py-2 pr-3">{t('recs.trColNext')}</th>
                <th className="py-2 pr-3">{t('recs.trColLoad')}</th>
                <th className="py-2 pr-3">{t('recs.trColAuto')}</th>
              </tr>
            </thead>
            <tbody>
              {d.rules.filter((r) => r.is_active).map((r) => (
                <tr key={r.category_id} className="border-t border-wellq-gray/10 dark:border-white/5 align-top">
                  <td className="py-2 pr-3 font-bold text-wellq-dark dark:text-white">
                    {r.category}
                    {r.unassigned_open > 0 && <span className="ml-1 text-[10px] text-amber-600 dark:text-amber-400">{t('recs.trUnassignedN', { n: r.unassigned_open })}</span>}
                  </td>
                  <td className="py-2 pr-3">
                    {canManage ? (
                      <select id={`tr-team-${r.category_id}`} aria-label={t('recs.trColTeam')} disabled={busy} value={r.team ?? ''}
                        onChange={(e) => patchRule(r, { team: e.target.value })}
                        className="rounded-lg border border-wellq-gray/30 dark:border-white/10 bg-white dark:bg-[#0b1017] px-2 py-1 text-xs text-wellq-dark dark:text-white">
                        <option value="">{t('recs.trNoTeamOption')}</option>
                        {[...new Set([...d.teams, ...(r.team ? [r.team] : [])])].map((tm) => <option key={tm} value={tm}>{tm}</option>)}
                      </select>
                    ) : (r.team ?? '—')}
                  </td>
                  <td className="py-2 pr-3 text-wellq-dark dark:text-white">
                    {r.next_responder ?? <span className="text-xs text-wellq-gray">{reasons[r.status] ?? '—'}</span>}
                  </td>
                  <td className="py-2 pr-3">
                    <div className="flex flex-wrap gap-1">
                      {r.responders.map((p) => (
                        <span key={p.id} className="px-2 py-0.5 rounded-full bg-wellq-gray/10 dark:bg-white/5 text-[11px] text-wellq-dark dark:text-white tabular-nums">
                          {p.name} · {p.open_tickets}
                        </span>
                      ))}
                    </div>
                  </td>
                  <td className="py-2 pr-3">
                    <label className="flex items-center gap-2 text-xs text-wellq-gray">
                      <input id={`tr-auto-${r.category_id}`} type="checkbox" checked={r.auto_assign} disabled={!canManage || busy || !r.team}
                        onChange={(e) => patchRule(r, { auto_assign: e.target.checked })} />
                      {t(r.auto_assign ? 'recs.trOn' : 'recs.trOff')}
                    </label>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="mt-3 text-[11px] text-wellq-gray">{t('recs.trNote')}</p>
        </div>
      )}
    </section>
  );
}
