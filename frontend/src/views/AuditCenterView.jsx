import { useCallback, useEffect, useState } from 'react';
import { History, Download, Loader2, ChevronLeft, ChevronRight } from 'lucide-react';
import { apiFetch } from '../api/client';

// Rec #16 — Centro de auditoría global (lee action_logs desde Neon)
const PAGE_SIZE = 25;
const ACTIONS = ['', 'CREATE', 'UPDATE', 'SOFT_DELETE', 'HARD_DELETE'];
const ENTITIES = ['', 'clinic', 'plan', 'feature'];

const fmt = (v) => (v === null || v === undefined || v === '' ? '—' : typeof v === 'object' ? JSON.stringify(v) : String(v));
const when = (iso) => {
  if (!iso) return '';
  const d = new Date(iso.endsWith('Z') ? iso : `${iso}Z`);
  return d.toLocaleString('es-CL', { dateStyle: 'medium', timeStyle: 'short' });
};
const summary = (it) =>
  it.changes.map((c) => (it.action === 'UPDATE' ? `${c.field}: ${fmt(c.before)} → ${fmt(c.after)}` : `${c.field}: ${fmt(c.after ?? c.before)}`)).join('; ');

const buildQuery = (f, page, pageSize) => {
  const p = new URLSearchParams();
  Object.entries(f).forEach(([k, v]) => { if (v) p.set(k, v); });
  p.set('page', String(page));
  p.set('page_size', String(pageSize));
  return p.toString();
};

const inputCls = 'w-full rounded-lg border border-wellq-gray/20 dark:border-white/10 bg-transparent px-2.5 py-2 text-xs text-wellq-dark dark:text-white';

export function AuditCenterView() {
  const [filters, setFilters] = useState({ action: '', entity_type: '', admin_email: '', q: '', date_from: '', date_to: '' });
  const [applied, setApplied] = useState(filters);
  const [page, setPage] = useState(1);
  const [state, setState] = useState({ loading: true, error: null, total: 0, items: [] });
  const [exporting, setExporting] = useState(false);

  const load = useCallback(() => {
    setState((s) => ({ ...s, loading: true, error: null }));
    apiFetch(`/api/audit/logs?${buildQuery(applied, page, PAGE_SIZE)}`)
      .then((res) => setState({ loading: false, error: null, total: res?.total ?? 0, items: res?.data ?? [] }))
      .catch((e) => setState({ loading: false, error: e.message || 'Error', total: 0, items: [] }));
  }, [applied, page]);

  useEffect(() => { load(); }, [load]);

  const set = (k) => (e) => setFilters((f) => ({ ...f, [k]: e.target.value }));
  const apply = (e) => { e.preventDefault(); setPage(1); setApplied(filters); };
  const clear = () => {
    const empty = { action: '', entity_type: '', admin_email: '', q: '', date_from: '', date_to: '' };
    setFilters(empty); setPage(1); setApplied(empty);
  };

  const exportCsv = async () => {
    setExporting(true);
    try {
      const res = await apiFetch(`/api/audit/logs?${buildQuery(applied, 1, 1000)}`);
      const esc = (v) => `"${String(v ?? '').replace(/"/g, '""')}"`;
      const header = ['Fecha', 'Administrador', 'Accion', 'Entidad', 'ID', 'Nombre', 'Cambios'];
      const lines = (res?.data ?? []).map((it) =>
        [when(it.created_at), it.admin_email, it.action, it.entity_type, it.entity_id, it.entity_name, summary(it)].map(esc).join(','));
      const blob = new Blob(['﻿' + [header.join(','), ...lines].join('\n')], { type: 'text/csv;charset=utf-8;' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `auditoria_wellq_${new Date().toISOString().slice(0, 10)}.csv`;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      setState((s) => ({ ...s, error: 'No se pudo exportar.' }));
    } finally {
      setExporting(false);
    }
  };

  const { loading, error, total, items } = state;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="space-y-4">
      <div className="bg-white dark:bg-[#0b1017] rounded-2xl p-5 border border-wellq-gray/20 dark:border-[#1e293b]">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <History size={18} className="text-wellq-cyan" />
            <h3 className="font-bold text-sm text-wellq-dark dark:text-white">Centro de auditoría</h3>
            <span className="text-xs text-wellq-gray">{total} registros</span>
          </div>
          <button onClick={exportCsv} disabled={exporting || total === 0}
            className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-bold bg-wellq-cyan/10 text-wellq-dark dark:text-white border border-wellq-cyan/30 disabled:opacity-50">
            {exporting ? <Loader2 size={14} className="animate-spin" /> : <Download size={14} />} Exportar CSV
          </button>
        </div>

        <form onSubmit={apply} className="grid grid-cols-2 md:grid-cols-6 gap-2">
          <select className={inputCls} value={filters.action} onChange={set('action')}>
            {ACTIONS.map((a) => <option key={a} value={a}>{a || 'Toda acción'}</option>)}
          </select>
          <select className={inputCls} value={filters.entity_type} onChange={set('entity_type')}>
            {ENTITIES.map((a) => <option key={a} value={a}>{a || 'Toda entidad'}</option>)}
          </select>
          <input className={inputCls} placeholder="Administrador (correo)" value={filters.admin_email} onChange={set('admin_email')} />
          <input className={inputCls} placeholder="Nombre o ID" value={filters.q} onChange={set('q')} />
          <input type="date" className={inputCls} value={filters.date_from} onChange={set('date_from')} />
          <input type="date" className={inputCls} value={filters.date_to} onChange={set('date_to')} />
          <div className="col-span-2 md:col-span-6 flex gap-2">
            <button type="submit" className="px-3 py-2 rounded-lg text-xs font-black bg-gradient-to-r from-wellq-cyan to-wellq-blue text-wellq-dark">Filtrar</button>
            <button type="button" onClick={clear} className="px-3 py-2 rounded-lg text-xs font-bold text-wellq-gray border border-wellq-gray/20">Limpiar</button>
          </div>
        </form>
      </div>

      <div className="bg-white dark:bg-[#0b1017] rounded-2xl p-5 border border-wellq-gray/20 dark:border-[#1e293b]">
        {loading && <div className="flex justify-center py-8"><Loader2 className="animate-spin text-wellq-gray" size={20} /></div>}
        {error && <p className="text-xs text-red-500">No se pudo cargar el registro de auditoría.</p>}
        {!loading && !error && items.length === 0 && <p className="text-xs text-wellq-gray">No hay registros con esos filtros.</p>}

        {items.length > 0 && !loading && (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-left text-[10px] uppercase text-wellq-gray">
                  <th className="py-2 pr-3">Fecha</th><th className="pr-3">Administrador</th><th className="pr-3">Acción</th>
                  <th className="pr-3">Entidad</th><th>Cambios</th>
                </tr>
              </thead>
              <tbody>
                {items.map((it) => (
                  <tr key={it.log_id} className="border-t border-wellq-gray/10 align-top">
                    <td className="py-2 pr-3 whitespace-nowrap text-wellq-gray">{when(it.created_at)}</td>
                    <td className="pr-3 text-wellq-dark dark:text-white">{it.admin_email}</td>
                    <td className="pr-3 font-bold text-wellq-dark dark:text-white">{it.action}</td>
                    <td className="pr-3 text-wellq-dark dark:text-white">{it.entity_type} · {it.entity_name || it.entity_id}</td>
                    <td className="text-wellq-gray">{summary(it) || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {total > PAGE_SIZE && (
          <div className="flex items-center justify-end gap-2 mt-3 text-xs text-wellq-gray">
            <button disabled={page <= 1} onClick={() => setPage((p) => p - 1)} className="p-1 disabled:opacity-40"><ChevronLeft size={16} /></button>
            <span>Página {page} de {pages}</span>
            <button disabled={page >= pages} onClick={() => setPage((p) => p + 1)} className="p-1 disabled:opacity-40"><ChevronRight size={16} /></button>
          </div>
        )}
      </div>
    </div>
  );
}

export default AuditCenterView;
