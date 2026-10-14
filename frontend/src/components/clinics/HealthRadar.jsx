import { useEffect, useState } from 'react';
import { Radar as RadarIcon, Loader2 } from 'lucide-react';
import {
  RadarChart, PolarGrid, PolarAngleAxis, PolarRadiusAxis, Radar, ResponsiveContainer, Tooltip,
} from 'recharts';
import { apiFetch } from '../../api/client';

// Rec #10 — Radar de salud de 5 ejes por clínica (datos reales desde Neon)
const LABELS = {
  financiero: 'Financiero',
  login: 'Login',
  ia_soap: 'IA / SOAP',
  camara: 'Cámara',
  soporte: 'Soporte',
};

export function HealthRadar({ clinicId }) {
  const [state, setState] = useState({ loading: true, error: null, data: null });

  useEffect(() => {
    let alive = true;
    setState({ loading: true, error: null, data: null });
    apiFetch(`/api/clinics/${clinicId}/health-radar`)
      .then((data) => alive && setState({ loading: false, error: null, data }))
      .catch((e) => alive && setState({ loading: false, error: e.message || 'Error', data: null }));
    return () => { alive = false; };
  }, [clinicId]);

  const { loading, error, data } = state;
  const rows = data
    ? Object.entries(data.axes).map(([key, a]) => ({
        axis: LABELS[key] ?? key, value: a.value, detail: a.detail, hasData: a.has_data,
      }))
    : [];

  return (
    <div className="rounded-2xl border border-wellq-gray/20 dark:border-white/10 p-4">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <RadarIcon size={16} className="text-wellq-cyan" />
          <h4 className="font-bold text-sm text-wellq-dark dark:text-white">Radar de salud (5 ejes)</h4>
        </div>
        {data?.overall != null && (
          <span className="text-xs font-black text-wellq-dark dark:text-white">Promedio: {data.overall}</span>
        )}
      </div>

      {loading && <div className="flex justify-center py-10"><Loader2 className="animate-spin text-wellq-gray" size={20} /></div>}
      {error && <p className="text-xs text-red-500">No se pudo cargar el radar de salud.</p>}

      {data && (
        <>
          <div style={{ width: '100%', height: 240 }}>
            <ResponsiveContainer>
              <RadarChart data={rows} outerRadius="75%">
                <PolarGrid />
                <PolarAngleAxis dataKey="axis" tick={{ fontSize: 11 }} />
                <PolarRadiusAxis angle={90} domain={[0, 100]} tick={{ fontSize: 9 }} />
                <Radar dataKey="value" stroke="#16f8f9" fill="#16f8f9" fillOpacity={0.35} />
                <Tooltip formatter={(v, _n, p) => [`${v}/100`, p.payload.axis]} />
              </RadarChart>
            </ResponsiveContainer>
          </div>
          <ul className="mt-2 space-y-1">
            {rows.map((r) => (
              <li key={r.axis} className="flex justify-between text-[11px] text-wellq-gray">
                <span className="font-bold text-wellq-dark dark:text-white">{r.axis}</span>
                <span>{r.detail}</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

export default HealthRadar;
