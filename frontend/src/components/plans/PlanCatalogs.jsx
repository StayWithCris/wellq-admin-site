import React, { useCallback, useEffect, useState } from 'react';
import { FolderOpen, Plus, Trash2, Edit3, Save, X, Loader2, EyeOff } from 'lucide-react';
import { toast } from 'sonner';
import { apiFetch } from '../../api/client';
import { useLanguage } from '../../contexts/LanguageContext';

// Rec #2 — Catálogos de planes comerciales (agrupar y activar/desactivar en bloque)
export const CATALOG_COLORS = {
  slate:  'bg-wellq-gray/10 text-wellq-dark dark:text-white border-wellq-gray/20',
  cyan:   'bg-wellq-cyan/10 text-wellq-cyan border-wellq-cyan/30',
  green:  'bg-wellq-green/10 text-wellq-green border-wellq-green/30',
  amber:  'bg-amber-400/10 text-amber-600 dark:text-amber-400 border-amber-400/30',
  purple: 'bg-purple-500/10 text-purple-600 dark:text-purple-400 border-purple-500/30',
  rose:   'bg-rose-500/10 text-rose-600 dark:text-rose-400 border-rose-500/30',
  blue:   'bg-wellq-blue/10 text-wellq-blue border-wellq-blue/30',
};

export const usePlanCatalogs = () => {
  const [catalogs, setCatalogs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const load = useCallback(async () => {
    try {
      setLoading(true);
      const res = await apiFetch('/api/plan-catalogs');
      setCatalogs(res.data ?? []);
      setError(null);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => { load(); }, [load]);
  return { catalogs, loading, error, reload: load };
};

// Filtro por catálogo para la Biblioteca de Planes
export const filterPlansByCatalog = (plans, filter) => {
  if (filter === 'all') return plans;
  if (filter === 'none') return plans.filter((p) => !p.catalogId);
  return plans.filter((p) => p.catalogId === filter);
};

export function PlanCatalogFilter({ catalogs, plans, value, onChange }) {
  const { t } = useLanguage();
  if (!catalogs.length) return null;
  const chip = (id, label, count, extra = null, color = 'slate') => (
    <button key={id} type="button" onClick={() => onChange(id)} aria-pressed={value === id}
      className={`flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-bold border transition ${value === id
        ? CATALOG_COLORS[color] + ' ring-2 ring-wellq-cyan/40'
        : 'border-wellq-gray/20 dark:border-white/10 text-wellq-gray hover:text-wellq-dark dark:hover:text-white'}`}>
      {label} <span className="tabular-nums opacity-70">{count}</span> {extra}
    </button>
  );
  return (
    <div className="flex flex-wrap items-center gap-2" role="group" aria-label={t('recs.pcFilter')}>
      <FolderOpen size={14} className="text-wellq-gray" />
      {chip('all', t('recs.pcAll'), plans.length)}
      {catalogs.map((c) => chip(
        c.id, c.name, plans.filter((p) => p.catalogId === c.id).length,
        !c.isActive ? <EyeOff size={11} aria-label={t('recs.pcInactive')} /> : null, c.color,
      ))}
      {chip('none', t('recs.pcNone'), plans.filter((p) => !p.catalogId).length)}
    </div>
  );
}

const EMPTY = { name: '', description: '', color: 'slate' };

function CatalogForm({ initial = EMPTY, onSave, onCancel, saving }) {
  const { t } = useLanguage();
  const [f, setF] = useState(initial);
  const cls = 'w-full rounded-lg border border-wellq-gray/30 dark:border-white/10 bg-white dark:bg-[#0b1017] px-3 py-2 text-sm text-wellq-dark dark:text-white';
  return (
    <div className="grid gap-3 sm:grid-cols-[1fr_1fr_auto] items-end">
      <label className="text-xs font-bold text-wellq-gray">{t('recs.pcName')}
        <input id="pc-name" className={`${cls} mt-1`} value={f.name} maxLength={60} onChange={(e) => setF({ ...f, name: e.target.value })} />
      </label>
      <label className="text-xs font-bold text-wellq-gray">{t('recs.pcDescription')}
        <input id="pc-description" className={`${cls} mt-1`} value={f.description} maxLength={300} onChange={(e) => setF({ ...f, description: e.target.value })} />
      </label>
      <label className="text-xs font-bold text-wellq-gray">{t('recs.pcColor')}
        <select id="pc-color" className={`${cls} mt-1`} value={f.color} onChange={(e) => setF({ ...f, color: e.target.value })}>
          {Object.keys(CATALOG_COLORS).map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
      </label>
      <div className="sm:col-span-3 flex justify-end gap-2">
        <button type="button" onClick={onCancel} className="px-3 py-2 rounded-xl text-sm font-bold text-wellq-gray"><X size={14} className="inline" /> {t('recs.mcCancel')}</button>
        <button type="button" disabled={saving || f.name.trim().length < 2} onClick={() => onSave(f)}
          className="flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-bold bg-wellq-cyan text-wellq-black disabled:opacity-50">
          {saving ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />} {t('recs.pcSave')}
        </button>
      </div>
    </div>
  );
}

export function PlanCatalogsManager({ catalogs, plans, onChanged }) {
  const { t } = useLanguage();
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState(null);      // id del catálogo en edición
  const [assigning, setAssigning] = useState(null);  // id del catálogo cuyos planes se eligen
  const [selection, setSelection] = useState([]);
  const [confirmDelete, setConfirmDelete] = useState(null);
  const [busy, setBusy] = useState(false);

  const run = async (fn, okMsg) => {
    setBusy(true);
    try {
      await fn();
      if (okMsg) toast.success(okMsg);
      onChanged();
      return true;
    } catch (e) {
      if (e.status !== 403) toast.error(e.message);
      return false;
    } finally {
      setBusy(false);
    }
  };

  const create = (f) => run(() => apiFetch('/api/plan-catalogs', { method: 'POST', body: JSON.stringify(f) }), t('recs.pcCreated'))
    .then((ok) => ok && setCreating(false));
  const update = (id, body, msg) => run(() => apiFetch(`/api/plan-catalogs/${id}`, { method: 'PATCH', body: JSON.stringify(body) }), msg);
  const savePlans = (id) => run(() => apiFetch(`/api/plan-catalogs/${id}/plans`, { method: 'PUT', body: JSON.stringify({ planIds: selection }) }), t('recs.pcPlansSaved'))
    .then((ok) => ok && setAssigning(null));
  const remove = (id) => run(() => apiFetch(`/api/plan-catalogs/${id}`, { method: 'DELETE' }), t('recs.pcDeleted'))
    .then(() => setConfirmDelete(null));

  return (
    <section className="space-y-6" aria-labelledby="pc-title">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 id="pc-title" className="text-lg font-bold text-wellq-dark dark:text-white">{t('recs.pcTitle')}</h2>
          <p className="text-sm font-medium text-wellq-gray">{t('recs.pcSub')}</p>
        </div>
        {!creating && (
          <button onClick={() => setCreating(true)} className="flex items-center gap-2 px-5 py-2.5 bg-wellq-cyan text-wellq-black rounded-xl text-sm font-bold">
            <Plus size={16} /> {t('recs.pcNew')}
          </button>
        )}
      </div>

      {creating && (
        <div className="bg-white dark:bg-wellq-dark rounded-2xl p-5 border border-wellq-gray/20 dark:border-white/10">
          <CatalogForm onSave={create} onCancel={() => setCreating(false)} saving={busy} />
        </div>
      )}

      {catalogs.length === 0 && !creating && (
        <p className="py-12 text-center text-sm text-wellq-gray">{t('recs.pcEmpty')}</p>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {catalogs.map((c) => (
          <article key={c.id} className={`bg-white dark:bg-wellq-dark rounded-2xl p-5 border border-wellq-gray/20 dark:border-white/10 ${c.isActive ? '' : 'opacity-75'}`}>
            {editing === c.id ? (
              <CatalogForm initial={{ name: c.name, description: c.description, color: c.color }} saving={busy}
                onCancel={() => setEditing(null)}
                onSave={(f) => update(c.id, f, t('recs.pcUpdated')).then((ok) => ok && setEditing(null))} />
            ) : (
              <>
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <span className={`inline-block px-2.5 py-1 rounded-md text-[11px] font-bold uppercase tracking-wider border ${CATALOG_COLORS[c.color] || CATALOG_COLORS.slate}`}>{c.name}</span>
                    {c.description && <p className="text-xs text-wellq-gray mt-2">{c.description}</p>}
                    <p className="text-xs font-semibold text-wellq-gray mt-2">{t('recs.pcPlanCount', { n: c.planCount })}</p>
                  </div>
                  <label className="flex items-center gap-2 text-xs font-bold text-wellq-gray flex-shrink-0 cursor-pointer">
                    <input id={`pc-active-${c.id}`} type="checkbox" checked={c.isActive} disabled={busy}
                      onChange={(e) => update(c.id, { isActive: e.target.checked },
                        t(e.target.checked ? 'recs.pcActivated' : 'recs.pcDeactivated', { name: c.name }))} />
                    {t(c.isActive ? 'recs.pcActive' : 'recs.pcInactive')}
                  </label>
                </div>
                {!c.isActive && <p className="text-[11px] text-amber-600 dark:text-amber-400 mt-2">{t('recs.pcInactiveHint')}</p>}

                {assigning === c.id ? (
                  <div className="mt-4 space-y-2">
                    <p className="text-xs font-bold text-wellq-gray">{t('recs.pcChoosePlans')}</p>
                    <div className="max-h-56 overflow-y-auto space-y-1">
                      {plans.map((p) => (
                        <label key={p.id} className="flex items-center gap-2 text-sm text-wellq-dark dark:text-white">
                          <input id={`pc-${c.id}-plan-${p.id}`} type="checkbox" checked={selection.includes(p.id)}
                            onChange={(e) => setSelection((s) => (e.target.checked ? [...s, p.id] : s.filter((x) => x !== p.id)))} />
                          {p.name}
                          {p.catalogId && p.catalogId !== c.id && (
                            <span className="text-[10px] text-wellq-gray">({t('recs.pcMovesFrom', { name: p.catalogName })})</span>
                          )}
                        </label>
                      ))}
                    </div>
                    <div className="flex justify-end gap-2">
                      <button onClick={() => setAssigning(null)} className="px-3 py-1.5 rounded-lg text-xs font-bold text-wellq-gray">{t('recs.mcCancel')}</button>
                      <button disabled={busy} onClick={() => savePlans(c.id)} className="px-3 py-1.5 rounded-lg text-xs font-bold bg-wellq-cyan text-wellq-black disabled:opacity-50">{t('recs.pcSave')}</button>
                    </div>
                  </div>
                ) : (
                  <div className="flex flex-wrap gap-2 mt-4">
                    <button onClick={() => { setAssigning(c.id); setSelection(c.planIds); }} className="px-3 py-1.5 rounded-lg text-xs font-bold border border-wellq-gray/20 dark:border-white/10 text-wellq-dark dark:text-white">{t('recs.pcEditPlans')}</button>
                    <button onClick={() => setEditing(c.id)} className="flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-bold border border-wellq-gray/20 dark:border-white/10 text-wellq-dark dark:text-white"><Edit3 size={12} /> {t('recs.pcEdit')}</button>
                    {confirmDelete === c.id ? (
                      <span className="flex items-center gap-2 text-xs font-bold text-red-500">
                        {t('recs.pcConfirmDelete')}
                        <button disabled={busy} onClick={() => remove(c.id)} className="px-2 py-1 rounded-lg bg-red-500 text-white">{t('recs.pcYesDelete')}</button>
                        <button onClick={() => setConfirmDelete(null)} className="px-2 py-1 rounded-lg text-wellq-gray">{t('recs.mcCancel')}</button>
                      </span>
                    ) : (
                      <button onClick={() => setConfirmDelete(c.id)} className="flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-bold text-red-500 border border-red-500/20"><Trash2 size={12} /> {t('recs.pcDelete')}</button>
                    )}
                  </div>
                )}
              </>
            )}
          </article>
        ))}
      </div>
    </section>
  );
}
