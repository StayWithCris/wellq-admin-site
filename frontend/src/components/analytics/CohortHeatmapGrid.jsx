import React, { useEffect, useState } from 'react';
import { Grid3x3 } from 'lucide-react';

// Rec #8 — Mapa de calor de cohortes de retención (datos reales desde Neon)
const cellColor = (pct) => {
  if (pct === null || pct === undefined) return 'transparent';
  if (pct >= 80) return 'rgba(34, 197, 94, 0.55)';
  if (pct >= 50) return 'rgba(234, 179, 8, 0.55)';
  return 'rgba(239, 68, 68, 0.55)';
};

export default function CohortHeatmapGrid() {
  const [state, setState] = useState({ loading: true, error: null, payload: null });

  useEffect(() => {
    const apiUrl = import.meta.env.VITE_API_URL || 'http://localhost:8000';
    fetch(`${apiUrl}/api/analytics/retention/heatmap`)
      .then((res) => {
        if (!res.ok) throw new Error('Error al consultar cohortes');
        return res.json();
      })
      .then((payload) => setState({ loading: false, error: null, payload }))
      .catch((err) => setState({ loading: false, error: err.message, payload: null }));
  }, []);

  const { loading, error, payload } = state;
  const rows = payload?.data ?? [];
  const cols = Array.from({ length: (payload?.max_month ?? 0) + 1 }, (_, i) => i);

  return (
    <div className="bg-white dark:bg-[#0b1017] rounded-2xl p-6 shadow-sm border border-wellq-gray/20 dark:border-[#1e293b]">
      <div className="flex items-center gap-2 mb-1">
        <Grid3x3 size={16} className="text-wellq-cyan" />
        <h3 className="font-bold text-wellq-dark dark:text-white text-sm">Mapa de calor de retención por cohorte</h3>
      </div>
      <p className="text-[11px] text-wellq-gray mb-4">
        Cada fila es el mes de alta de las clínicas; cada columna, los meses desde el alta. Los meses futuros quedan vacíos.
      </p>

      {loading && <div className="h-32 animate-pulse rounded-xl bg-wellq-gray/10" />}
      {error && <p className="text-xs text-red-500">No se pudo cargar el mapa de cohortes.</p>}
      {!loading && !error && rows.length === 0 && (
        <p className="text-xs text-wellq-gray">Aún no hay clínicas para formar cohortes.</p>
      )}

      {rows.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-xs border-separate border-spacing-1">
            <thead>
              <tr>
                <th className="text-left text-[10px] uppercase text-wellq-gray font-bold">Cohorte</th>
                <th className="text-right text-[10px] uppercase text-wellq-gray font-bold">Clínicas</th>
                {cols.map((m) => (
                  <th key={m} className="text-center text-[10px] uppercase text-wellq-gray font-bold">Mes {m}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.cohort}>
                  <td className="font-bold text-wellq-dark dark:text-white whitespace-nowrap">{r.cohort}</td>
                  <td className="text-right text-wellq-gray tabular-nums">{r.users}</td>
                  {cols.map((m) => {
                    const pct = r.retention[m];
                    return (
                      <td
                        key={m}
                        title={pct === undefined ? 'Sin datos aún' : `${pct}% retenido`}
                        className="h-9 min-w-[48px] text-center rounded-md font-black tabular-nums text-wellq-dark dark:text-white"
                        style={{ backgroundColor: cellColor(pct) }}
                      >
                        {pct === undefined ? '' : `${pct}%`}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {payload?.method && <p className="text-[10px] text-wellq-gray mt-3">{payload.method}</p>}
    </div>
  );
}
