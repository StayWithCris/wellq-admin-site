import { useEffect, useState } from 'react';
import { History, Loader2 } from 'lucide-react';
import { apiFetch } from '../../api/client';

// Rec #15 — Historial de auditoría por clínica (lee action_logs desde Neon)
const ACTION_LABEL = {
  CREATE: 'Creó la clínica',
  UPDATE: 'Modificó datos',
  SOFT_DELETE: 'Dio de baja la clínica',
  HARD_DELETE: 'Eliminó la clínica',
};
const ACTION_COLOR = {
  CREATE: 'bg-wellq-green',
  UPDATE: 'bg-wellq-cyan',
  SOFT_DELETE: 'bg-amber-500',
  HARD_DELETE: 'bg-red-500',
};

const fmt = (v) => (v === null || v === undefined || v === '' ? '—' : typeof v === 'object' ? JSON.stringify(v) : String(v));
const when = (iso) => {
  if (!iso) return '';
  const d = new Date(iso.endsWith('Z') ? iso : `${iso}Z`);
  return d.toLocaleString('es-CL', { dateStyle: 'medium', timeStyle: 'short' });
};

export function AuditTimeline({ clinicId }) {
  const [state, setState] = useState({ loading: true, error: null, items: [] });

  useEffect(() => {
    let alive = true;
    setState({ loading: true, error: null, items: [] });
    apiFetch(`/api/clinics/${clinicId}/audit-logs`)
      .then((res) => alive && setState({ loading: false, error: null, items: res?.data ?? [] }))
      .catch((e) => alive && setState({ loading: false, error: e.message || 'Error', items: [] }));
    return () => { alive = false; };
  }, [clinicId]);

  const { loading, error, items } = state;

  return (
    <div className="rounded-2xl border border-wellq-gray/20 dark:border-white/10 p-4">
      <div className="flex items-center gap-2 mb-3">
        <History size={16} className="text-wellq-cyan" />
        <h4 className="font-bold text-sm text-wellq-dark dark:text-white">Historial de cambios</h4>
      </div>

      {loading && <div className="flex justify-center py-6"><Loader2 className="animate-spin text-wellq-gray" size={20} /></div>}
      {error && <p className="text-xs text-red-500">No se pudo cargar el historial.</p>}
      {!loading && !error && items.length === 0 && (
        <p className="text-xs text-wellq-gray">Aún no hay cambios registrados para esta clínica.</p>
      )}

      {items.length > 0 && (
        <ol className="relative border-l border-wellq-gray/20 ml-2 space-y-4">
          {items.map((it) => (
            <li key={it.log_id} className="ml-4">
              <span className={`absolute -left-[5px] mt-1.5 w-2.5 h-2.5 rounded-full ${ACTION_COLOR[it.action] ?? 'bg-wellq-gray'}`} />
              <p className="text-xs font-bold text-wellq-dark dark:text-white">
                {ACTION_LABEL[it.action] ?? it.action}
              </p>
              <p className="text-[11px] text-wellq-gray">{it.admin_email} · {when(it.created_at)}</p>
              {it.changes.length > 0 && (
                <ul className="mt-1 space-y-0.5">
                  {it.changes.map((c) => (
                    <li key={c.field} className="text-[11px] text-wellq-gray">
                      <span className="font-semibold text-wellq-dark dark:text-white">{c.field}:</span>{' '}
                      {it.action === 'UPDATE' ? `${fmt(c.before)} → ${fmt(c.after)}` : fmt(c.after ?? c.before)}
                    </li>
                  ))}
                </ul>
              )}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

export default AuditTimeline;
