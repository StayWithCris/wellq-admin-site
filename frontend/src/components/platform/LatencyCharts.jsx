import React, { useMemo } from 'react';
import {
  Area,
  AreaChart,
  Bar,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { useLanguage } from '../../contexts/LanguageContext';

// ─────────────────────────────────────────────────────────────────────────────
// LatencyCharts — Recomendación #5
//
// Series temporales de la API: percentiles de latencia (P50/P90/P99) y
// volumen + errores. Los datos YA VIENEN ACOTADOS por el backend (≤60 puntos),
// así que aquí no hay ningún slice adicional.
//
// Los colores usan las variables CSS de los tokens de diseño del repo
// (`--color-wellq-*`, definidas en index.css) — nunca hex hardcodeado.
// ─────────────────────────────────────────────────────────────────────────────

const COLOR_P50 = 'var(--color-wellq-blue)';
const COLOR_P90 = 'var(--color-wellq-cyan)';
const COLOR_P99 = 'var(--color-wellq-gray)';
const COLOR_RPM = 'var(--color-wellq-cyan)';
const COLOR_ERRORS = '#f59e0b'; // amber-500 — tono de estado, no token de marca

const GRID = 'rgba(140, 146, 153, 0.15)';
const AXIS = '#8c9299';

const tooltipStyle = {
  contentStyle: {
    background: '#0b1017',
    border: '1px solid rgba(255,255,255,0.1)',
    borderRadius: 12,
    fontSize: 12,
    color: '#fff',
  },
  labelStyle: { color: '#fff', fontWeight: 700, marginBottom: 4 },
  itemStyle: { color: '#fff' },
};

const fmtHora = (ts, locale) => {
  if (!ts) return '';
  const fecha = new Date(ts);
  if (Number.isNaN(fecha.getTime())) return String(ts);
  return fecha.toLocaleTimeString(locale === 'es' ? 'es-CL' : 'en-US', {
    hour: '2-digit',
    minute: '2-digit',
  });
};

export const LatencyCharts = ({ series = [] }) => {
  const { t, locale } = useLanguage();

  const datos = useMemo(
    () => (Array.isArray(series) ? series : []).map((p) => ({
      ...p,
      etiqueta: fmtHora(p.ts, locale),
    })),
    [series, locale],
  );

  if (datos.length === 0) {
    return (
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        <div className="rounded-2xl border border-wellq-gray/10 dark:border-white/5 bg-wellq-gray/3 dark:bg-white/[0.02] flex items-center justify-center h-56 px-4 text-center">
          <p className="text-xs font-medium text-wellq-gray">{t('platform.telemetry.noEvents')}</p>
        </div>
        <div className="rounded-2xl border border-wellq-gray/10 dark:border-white/5 bg-wellq-gray/3 dark:bg-white/[0.02] flex items-center justify-center h-56 px-4 text-center">
          <p className="text-xs font-medium text-wellq-gray">{t('platform.telemetry.noEvents')}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
      {/* ── Percentiles de latencia ── */}
      <div className="rounded-2xl border border-wellq-gray/10 dark:border-white/5 bg-wellq-gray/3 dark:bg-white/[0.02] p-4">
        <h4 className="text-xs font-bold text-wellq-dark dark:text-white uppercase tracking-wider mb-3">
          {t('platform.telemetry.latencyChart')}
        </h4>
        <ResponsiveContainer width="100%" height={230}>
          <AreaChart data={datos} margin={{ top: 5, right: 8, left: -18, bottom: 0 }}>
            <defs>
              <linearGradient id="gradP50" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor={COLOR_P50} stopOpacity={0.35} />
                <stop offset="95%" stopColor={COLOR_P50} stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke={GRID} vertical={false} />
            <XAxis
              dataKey="etiqueta"
              tick={{ fontSize: 10, fill: AXIS }}
              stroke={GRID}
              interval="preserveStartEnd"
              minTickGap={24}
            />
            <YAxis tick={{ fontSize: 10, fill: AXIS }} stroke={GRID} width={46} unit=" ms" />
            <Tooltip {...tooltipStyle} formatter={(value) => [`${value} ms`]} />
            <Legend
              wrapperStyle={{ fontSize: 11, color: AXIS }}
              formatter={(value) => (
                <span style={{ color: AXIS, fontSize: 11 }}>{value}</span>
              )}
            />
            <Area
              type="monotone"
              dataKey="p50_ms"
              name={t('platform.telemetry.p50')}
              stroke={COLOR_P50}
              strokeWidth={2}
              fill="url(#gradP50)"
              dot={false}
              isAnimationActive={false}
            />
            <Area
              type="monotone"
              dataKey="p90_ms"
              name={t('platform.telemetry.p90')}
              stroke={COLOR_P90}
              strokeWidth={2}
              fill="none"
              dot={false}
              isAnimationActive={false}
            />
            <Area
              type="monotone"
              dataKey="p99_ms"
              name={t('platform.telemetry.p99')}
              stroke={COLOR_P99}
              strokeWidth={2}
              strokeDasharray="4 3"
              fill="none"
              dot={false}
              isAnimationActive={false}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>

      {/* ── Volumen (RPM) y errores ── */}
      <div className="rounded-2xl border border-wellq-gray/10 dark:border-white/5 bg-wellq-gray/3 dark:bg-white/[0.02] p-4">
        <h4 className="text-xs font-bold text-wellq-dark dark:text-white uppercase tracking-wider mb-3">
          {t('platform.telemetry.volumeChart')}
        </h4>
        <ResponsiveContainer width="100%" height={230}>
          <ComposedChart data={datos} margin={{ top: 5, right: 8, left: -18, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke={GRID} vertical={false} />
            <XAxis
              dataKey="etiqueta"
              tick={{ fontSize: 10, fill: AXIS }}
              stroke={GRID}
              interval="preserveStartEnd"
              minTickGap={24}
            />
            <YAxis yAxisId="left" tick={{ fontSize: 10, fill: AXIS }} stroke={GRID} width={46} />
            <YAxis yAxisId="right" orientation="right" tick={{ fontSize: 10, fill: AXIS }} stroke={GRID} width={36} />
            <Tooltip {...tooltipStyle} />
            <Legend
              wrapperStyle={{ fontSize: 11, color: AXIS }}
              formatter={(value) => (
                <span style={{ color: AXIS, fontSize: 11 }}>{value}</span>
              )}
            />
            <Bar
              yAxisId="right"
              dataKey="errors"
              name={t('platform.telemetry.errorRate')}
              fill={COLOR_ERRORS}
              fillOpacity={0.65}
              radius={[3, 3, 0, 0]}
              isAnimationActive={false}
            />
            <Line
              yAxisId="left"
              type="monotone"
              dataKey="rpm"
              name={t('platform.telemetry.rpm')}
              stroke={COLOR_RPM}
              strokeWidth={2}
              dot={false}
              isAnimationActive={false}
            />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
};

export default LatencyCharts;
