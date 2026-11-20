import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { motion } from 'framer-motion';
import { Activity, AlertTriangle, Radio, RefreshCw } from 'lucide-react';
import { toast } from 'sonner';
import { fetchTelemetryLogs, fetchTelemetryMetrics } from '../../api/client';
import { useLanguage } from '../../contexts/LanguageContext';
import useHasPermission from '../../hooks/useHasPermission';
import { Skeleton } from '../ui';
import { LatencyCharts } from './LatencyCharts';
import { LiveLogStream } from './LiveLogStream';

// ─────────────────────────────────────────────────────────────────────────────
// TelemetryPanel — Recomendación #5
//
// Tarjeta de «Telemetría y registros reales» dentro de Operaciones de
// plataforma. Consume:
//   GET /api/platform/telemetry/metrics?window=…   P50/P90/P99, errores, RPM
//   GET /api/platform/telemetry/logs?window=…      eventos recientes de IA
//   GET /api/platform/telemetry/stream              feed en vivo (SSE)
//
// RBAC: lectura → platform.view · acciones → platform.manage.
// ─────────────────────────────────────────────────────────────────────────────

const WINDOWS = [
  { key: '1h',  labelKey: 'platform.telemetry.window1h' },
  { key: '24h', labelKey: 'platform.telemetry.window24h' },
  { key: '7d',  labelKey: 'platform.telemetry.window7d' },
];

const fade = {
  hidden: { opacity: 0, y: 12 },
  show: { opacity: 1, y: 0, transition: { duration: 0.35, ease: 'easeOut' } },
};

const KPI_META = [
  { key: 'p50_ms',     labelKey: 'platform.telemetry.p50',      unit: 'ms', accent: 'from-wellq-blue/25' },
  { key: 'p90_ms',     labelKey: 'platform.telemetry.p90',      unit: 'ms', accent: 'from-wellq-cyan/25' },
  { key: 'p99_ms',     labelKey: 'platform.telemetry.p99',      unit: 'ms', accent: 'from-amber-500/25' },
  { key: 'error_rate', labelKey: 'platform.telemetry.errorRate', unit: '%',  accent: 'from-red-500/25',    percent: true },
  { key: 'rpm',        labelKey: 'platform.telemetry.rpm',      unit: '',   accent: 'from-wellq-green/25' },
];

export const TelemetryPanel = ({ searchQuery = '' }) => {
  const { t } = useLanguage();
  const canView = useHasPermission('platform.view');
  const canManage = useHasPermission('platform.manage');

  const [windowKey, setWindowKey] = useState('1h');
  const [endpoint, setEndpoint] = useState('');
  const [clinicId, setClinicId] = useState('');

  const [metrics, setMetrics] = useState(null);
  const [logs, setLogs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState(null);
  const [unavailable, setUnavailable] = useState(null);

  const cargar = useCallback(async (ventana, silencioso = false) => {
    if (!silencioso) setLoading(true);
    else setRefreshing(true);
    setError(null);

    try {
      const [resMetrics, resLogs] = await Promise.all([
        fetchTelemetryMetrics({
          window: ventana,
          endpoint: endpoint || undefined,
          clinic_id: clinicId || undefined,
        }),
        fetchTelemetryLogs({
          window: ventana,
          clinic_id: clinicId || undefined,
          limit: 50,
        }),
      ]);

      // MongoDB no configurado → aviso informativo, no error destructivo.
      if (resMetrics?.status === 'unavailable' || resLogs?.status === 'unavailable') {
        setUnavailable(resMetrics?.reason || resLogs?.reason || null);
        setMetrics(null);
        setLogs([]);
      } else {
        setUnavailable(null);
        setMetrics(resMetrics ?? null);
        setLogs(Array.isArray(resLogs?.logs) ? resLogs.logs : []);
      }
    } catch (err) {
      setError(err.message || t('platform.telemetry.errorLoad'));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [t, endpoint, clinicId]);

  // Cada cambio de ventana, endpoint o clínica dispara exactamente UNA
  // recarga: `cargar` depende de los filtros, así que el efecto corre una vez
  // por cambio (y el `setState` del handler no vuelve a disparar el fetch).
  useEffect(() => {
    if (!canView) return;
    cargar(windowKey);
  }, [windowKey, canView, cargar]);

  // ── Filtros ──────────────────────────────────────────────────────────────
  const opcionesEndpoint = useMemo(() => {
    const lista = Array.isArray(metrics?.endpoints) ? metrics.endpoints : [];
    return lista
      .map((e) => e.endpoint)
      .filter(Boolean)
      .sort((a, b) => a.localeCompare(b));
  }, [metrics]);

  const opcionesClinica = useMemo(() => {
    const ids = new Set();
    (Array.isArray(logs) ? logs : []).forEach((log) => {
      if (log?.clinic_id) ids.add(log.clinic_id);
    });
    return Array.from(ids).sort((a, b) => a.localeCompare(b));
  }, [logs]);

  const aplicarFiltros = useCallback((next) => {
    if (next.windowKey !== undefined) setWindowKey(next.windowKey);
    if (next.endpoint !== undefined) setEndpoint(next.endpoint);
    if (next.clinicId !== undefined) setClinicId(next.clinicId);
    // El `useEffect` de arriba hace la recarga con los filtros ya actualizados.
    toast.success(t('platform.telemetry.filtersUpdated'));
  }, [t]);

  const recargar = useCallback(() => {
    cargar(windowKey, true);
    toast.success(t('platform.telemetry.filtersUpdated'));
  }, [cargar, windowKey, t]);

  if (!canView) {
    return (
      <div className="bg-white dark:bg-wellq-dark rounded-2xl p-6 shadow-sm border border-wellq-gray/20 dark:border-wellq-gray/30">
        <div className="flex items-center gap-2 text-wellq-gray text-sm font-semibold">
          <AlertTriangle size={16} />
          {t('platform.telemetry.errorLoad')}
        </div>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="bg-white dark:bg-wellq-dark rounded-2xl p-6 shadow-sm border border-wellq-gray/20 dark:border-wellq-gray/30 space-y-4">
        <Skeleton className="h-5 w-56" />
        <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
          {[1, 2, 3, 4, 5].map((i) => <Skeleton key={i} className="h-20 w-full rounded-2xl" />)}
        </div>
        <Skeleton className="h-56 w-full rounded-2xl" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="bg-white dark:bg-wellq-dark rounded-2xl p-6 shadow-sm border border-wellq-gray/20 dark:border-wellq-gray/30">
        <div className="flex items-center gap-2 text-red-500 text-sm font-semibold">
          <AlertTriangle size={16} />
          {t('platform.telemetry.errorLoad')}
        </div>
        <p className="text-xs text-wellq-gray mt-1">{error}</p>
      </div>
    );
  }

  return (
    <motion.div
      variants={fade}
      initial="hidden"
      animate="show"
      className="bg-white dark:bg-wellq-dark rounded-2xl p-6 shadow-sm border border-wellq-gray/20 dark:border-wellq-gray/30"
    >
      {/* ── Cabecera ── */}
      <div className="flex flex-wrap items-start justify-between gap-4 mb-5">
        <div className="flex items-start gap-3">
          <div className="w-10 h-10 rounded-xl flex items-center justify-center bg-wellq-cyan/10 ring-1 ring-wellq-cyan/20">
            <Radio size={18} className="text-wellq-cyan" strokeWidth={2.2} />
          </div>
          <div>
            <h3 className="font-bold text-wellq-dark dark:text-white text-base tracking-tight">
              {t('platform.telemetry.title')}
            </h3>
            <p className="text-xs text-wellq-gray mt-0.5">{t('platform.telemetry.subtitle')}</p>
          </div>
        </div>

        <button
          type="button"
          onClick={recargar}
          disabled={refreshing}
          title={t('topbar.refresh')}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-bold bg-wellq-gray/10 text-wellq-gray hover:bg-wellq-gray/20 disabled:opacity-50 transition-colors"
        >
          <RefreshCw size={13} className={refreshing ? 'animate-spin' : ''} />
          {t('topbar.refresh')}
        </button>
      </div>

      {/* ── Aviso informativo: MongoDB no configurado ── */}
      {unavailable && (
        <div className="mb-5 flex items-start gap-3 rounded-xl bg-amber-500/10 border border-amber-500/30 p-3.5 text-amber-600 dark:text-amber-400">
          <AlertTriangle size={18} className="shrink-0 mt-0.5" />
          <div className="min-w-0 flex-1">
            <p className="text-xs font-bold break-words">{unavailable}</p>
            <p className="text-[10px] font-semibold mt-1 uppercase tracking-wider">
              {t('platform.telemetry.title')}
            </p>
          </div>
        </div>
      )}

      {/* ── Filtros ── */}
      <div className="flex flex-wrap items-center gap-3 mb-5 pb-5 border-b border-wellq-gray/10 dark:border-white/5">
        <span className="text-xs font-bold text-wellq-gray uppercase tracking-wider">
          {t('platform.telemetry.window')}
        </span>
        <div className="flex items-center gap-2">
          {WINDOWS.map((w) => {
            const active = windowKey === w.key;
            return (
              <button
                key={w.key}
                type="button"
                onClick={() => aplicarFiltros({ windowKey: w.key })}
                title={t(w.labelKey)}
                className={`px-3 py-1.5 rounded-lg text-xs font-bold transition-colors ${
                  active
                    ? 'bg-wellq-cyan text-wellq-black'
                    : 'bg-wellq-gray/10 text-wellq-gray hover:bg-wellq-gray/20'
                }`}
              >
                {w.key}
              </button>
            );
          })}
        </div>

        <div className="flex flex-wrap items-center gap-3 ml-auto">
          <label className="flex items-center gap-2">
            <span className="text-xs font-bold text-wellq-gray uppercase tracking-wider">
              {t('platform.telemetry.endpoint')}
            </span>
            <select
              value={endpoint}
              onChange={(e) => aplicarFiltros({ endpoint: e.target.value })}
              className="px-2.5 py-1.5 text-xs font-semibold border border-wellq-gray/20 dark:border-white/10 rounded-lg focus:outline-none focus:ring-2 focus:ring-wellq-cyan bg-white dark:bg-wellq-dark dark:text-white cursor-pointer"
            >
              <option value="">{t('platform.telemetry.allEndpoints')}</option>
              {opcionesEndpoint.map((ep) => (
                <option key={ep} value={ep}>{ep}</option>
              ))}
            </select>
          </label>

          {opcionesClinica.length > 0 && (
            <label className="flex items-center gap-2">
              <span className="text-xs font-bold text-wellq-gray uppercase tracking-wider">
                {t('platform.telemetry.clinic')}
              </span>
              <select
                value={clinicId}
                onChange={(e) => aplicarFiltros({ clinicId: e.target.value })}
                className="px-2.5 py-1.5 text-xs font-semibold border border-wellq-gray/20 dark:border-white/10 rounded-lg focus:outline-none focus:ring-2 focus:ring-wellq-cyan bg-white dark:bg-wellq-dark dark:text-white cursor-pointer"
              >
                <option value="">{t('platform.telemetry.allClinics')}</option>
                {opcionesClinica.map((c) => (
                  <option key={c} value={c}>{c}</option>
                ))}
              </select>
            </label>
          )}
        </div>
      </div>

      {/* ── KPIs ── */}
      <div className="grid grid-cols-2 lg:grid-cols-5 gap-3 mb-5">
        {KPI_META.map((kpi) => {
          const raw = metrics?.[kpi.key];
          const valor = raw == null
            ? '—'
            : kpi.percent
              ? `${(Number(raw) * 100).toFixed(2)}%`
              : Number(raw).toLocaleString(undefined, { maximumFractionDigits: 2 });
          return (
            <div
              key={kpi.key}
              className="relative rounded-2xl border border-wellq-gray/20 dark:border-wellq-gray/30 bg-white dark:bg-wellq-dark overflow-hidden"
            >
              <div className={`absolute top-0 left-0 right-0 h-12 bg-gradient-to-b ${kpi.accent} to-transparent pointer-events-none`} />
              <div className="relative p-4">
                <p className="text-[10px] font-bold uppercase tracking-wider text-wellq-gray mb-1">
                  {t(kpi.labelKey)}
                </p>
                <p className="text-2xl font-black text-wellq-dark dark:text-white tabular-nums tracking-tight">
                  {valor}
                  {kpi.unit && (
                    <span className="text-xs font-bold text-wellq-gray ml-0.5">{kpi.unit}</span>
                  )}
                </p>
              </div>
            </div>
          );
        })}
      </div>

      {/* ── Gráficos ── */}
      <div className="mb-5">
        <LatencyCharts series={metrics?.series ?? []} />
      </div>

      {/* ── Feed en vivo ── */}
      <LiveLogStream
        clinicId={clinicId}
        endpoint={endpoint}
        canManage={canManage}
        initialLogs={logs}
        searchQuery={searchQuery}
      />

      {metrics?.total_requests != null && (
        <p className="mt-3 flex items-center gap-1.5 text-[10px] font-semibold text-wellq-gray">
          <Activity size={11} />
          {metrics.total_requests.toLocaleString()} · {t('platform.telemetry.rpm')}: {metrics.rpm}
        </p>
      )}
    </motion.div>
  );
};

export default TelemetryPanel;
