import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { motion } from 'framer-motion';
import {
  RefreshCw, Play, Loader2, CheckCircle2, AlertTriangle, XCircle, CircleSlash,
  History, Building2, Users, FileText, LifeBuoy, Stethoscope,
  CalendarClock, ChevronDown, ChevronUp,
} from 'lucide-react';
import { toast } from 'sonner';
import {
  fetchSyncStatus,
  fetchSyncConfig,
  fetchSyncHistory,
  updateSyncFrequency,
  triggerSyncRun,
} from '../../api/client';
import { useLanguage } from '../../contexts/LanguageContext';
import useHasPermission from '../../hooks/useHasPermission';
import { filterAndSortBySearch } from '../../utils/search';
import { Skeleton } from '../ui';

// ─────────────────────────────────────────────────────────────────────────────
// SyncPanel — Recomendación #7
//
// Tarjeta de «Sincronización de datos» dentro de Operaciones de plataforma.
// Consume:
//   GET  /api/sync-status   estado por entidad
//   GET  /api/sync/config   frecuencia y próxima ejecución
//   PUT  /api/sync/config   cambio de frecuencia (6 / 12 / 24 h)
//   POST /api/sync/run      sincronización manual
//   GET  /api/sync/history  historial de ejecuciones
//
// NOTA: las fuentes locales (clínicas, SOAP, tickets) son snapshots: solo se
// refresca su marca de tiempo. Las fuentes Mongo (pacientes, clínicos) sí
// ingieren datos; si MongoDB no está configurado quedan omitidas y la
// ejecución termina en estado «parcial».
// ─────────────────────────────────────────────────────────────────────────────

const FREQUENCIES = [6, 12, 24];

const STATUS_META = {
  ok: {
    icon: CheckCircle2,
    labelKey: 'platform.sync.statusOk',
    chip: 'bg-wellq-green/10 text-wellq-green',
    dot: 'bg-wellq-green',
  },
  warning: {
    icon: AlertTriangle,
    labelKey: 'platform.sync.statusWarning',
    chip: 'bg-amber-500/10 text-amber-600 dark:text-amber-400',
    dot: 'bg-amber-500',
  },
  error: {
    icon: XCircle,
    labelKey: 'platform.sync.statusError',
    chip: 'bg-red-500/10 text-red-600 dark:text-red-400',
    dot: 'bg-red-500',
  },
  skipped: {
    icon: CircleSlash,
    labelKey: 'platform.sync.statusSkipped',
    chip: 'bg-wellq-gray/10 text-wellq-gray',
    dot: 'bg-wellq-gray',
  },
};

// Estado de la última ejecución (distinto del estado de frescura por fuente).
const RUN_STATUS_META = {
  completed: {
    labelKey: 'platform.sync.runStatusCompleted',
    chip: 'bg-wellq-green/10 text-wellq-green',
    dot: 'bg-wellq-green',
  },
  partial: {
    labelKey: 'platform.sync.runStatusPartial',
    chip: 'bg-amber-500/10 text-amber-600 dark:text-amber-400',
    dot: 'bg-amber-500',
  },
  failed: {
    labelKey: 'platform.sync.runStatusFailed',
    chip: 'bg-red-500/10 text-red-600 dark:text-red-400',
    dot: 'bg-red-500',
  },
  running: {
    labelKey: 'platform.sync.runStatusRunning',
    chip: 'bg-wellq-cyan/10 text-wellq-cyan',
    dot: 'bg-wellq-cyan',
  },
};

const ENTITY_META = {
  clinics: { icon: Building2, labelKey: 'platform.sync.entities.clinics' },
  patients: { icon: Users, labelKey: 'platform.sync.entities.patients' },
  soap_notes: { icon: FileText, labelKey: 'platform.sync.entities.soapNotes' },
  tickets: { icon: LifeBuoy, labelKey: 'platform.sync.entities.tickets' },
  clinicians: { icon: Stethoscope, labelKey: 'platform.sync.entities.clinicians' },
};

const fade = {
  hidden: { opacity: 0, y: 12 },
  show: { opacity: 1, y: 0, transition: { duration: 0.35, ease: 'easeOut' } },
};

const fmtDateTime = (value, locale) => {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleString(locale === 'es' ? 'es-CL' : 'en-US', {
    day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit',
  });
};

const fmtRelative = (value, locale) => {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  const diffMs = date.getTime() - Date.now();
  const minutes = Math.round(diffMs / 60000);
  const rtf = new Intl.RelativeTimeFormat(locale === 'es' ? 'es' : 'en', { numeric: 'auto' });
  if (Math.abs(minutes) < 60) return rtf.format(minutes, 'minute');
  const hours = Math.round(minutes / 60);
  if (Math.abs(hours) < 24) return rtf.format(hours, 'hour');
  return rtf.format(Math.round(hours / 24), 'day');
};

const fmtDuration = (ms) => {
  if (ms == null) return '—';
  if (ms < 1000) return `${ms} ms`;
  return `${(ms / 1000).toFixed(1)} s`;
};

// `entities` mezcla contadores numéricos con la lista `skipped`: solo suman
// los valores numéricos.
const sumCounts = (entities) =>
  Object.values(entities ?? {}).reduce(
    (acc, value) => (typeof value === 'number' && Number.isFinite(value) ? acc + value : acc),
    0,
  );

export const SyncPanel = ({ searchQuery = '' }) => {
  const { t, locale } = useLanguage();
  const canEdit = useHasPermission('platform.manage');

  const [sources, setSources] = useState([]);
  const [config, setConfig] = useState(null);
  const [runs, setRuns] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [savingFrequency, setSavingFrequency] = useState(false);
  const [running, setRunning] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);

  const load = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    setError(null);
    try {
      const [statusRes, configRes, historyRes] = await Promise.all([
        fetchSyncStatus(),
        fetchSyncConfig(),
        fetchSyncHistory({ limit: 8 }),
      ]);
      setSources(statusRes?.sources ?? []);
      setConfig(configRes ?? null);
      setRuns(historyRes?.data ?? []);
    } catch (err) {
      if (!silent) setError(err.message);
    } finally {
      if (!silent) setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const visibleSources = useMemo(
    () => filterAndSortBySearch(sources, searchQuery, (s) => [s.name, s.key, s.status]),
    [sources, searchQuery],
  );

  const changeFrequency = async (hours) => {
    if (!canEdit || savingFrequency || config?.frequency_hours === hours) return;
    setSavingFrequency(true);
    try {
      const res = await updateSyncFrequency(hours);
      setConfig(res?.data ?? config);
      toast.success(t('platform.sync.frequencySaved'));
      await load(true);
    } catch (err) {
      toast.error(err.message || t('platform.sync.runFailed'));
    } finally {
      setSavingFrequency(false);
    }
  };

  const runNow = async () => {
    if (!canEdit || running) return;
    setRunning(true);
    try {
      const res = await triggerSyncRun();
      const total = sumCounts(res?.data?.entities);
      toast.success(t('platform.sync.runCompleted', { count: total }));
      await load(true);
    } catch (err) {
      toast.error(err.message || t('platform.sync.runFailed'));
    } finally {
      setRunning(false);
    }
  };

  const lastRun = runs[0] ?? null;
  const lastRunFailed = config?.last_run_status === 'failed' || lastRun?.status === 'failed';

  // Fuentes que la última ejecución omitió (p. ej. MongoDB no configurado).
  const skippedKeys = useMemo(() => {
    const skipped = lastRun?.entities?.skipped;
    return new Set(Array.isArray(skipped) ? skipped : []);
  }, [lastRun]);

  // Estado de la última ejecución: completado, parcial, fallido o en curso.
  const runStatus = config?.last_run_status ?? lastRun?.status ?? null;
  const runStatusMeta = runStatus ? RUN_STATUS_META[runStatus] : null;

  if (loading) {
    return (
      <div className="bg-white dark:bg-wellq-dark rounded-2xl p-6 shadow-sm border border-wellq-gray/20 dark:border-wellq-gray/30 space-y-4">
        <Skeleton className="h-5 w-48" />
        <Skeleton className="h-20 w-full" />
        <Skeleton className="h-12 w-full" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="bg-white dark:bg-wellq-dark rounded-2xl p-6 shadow-sm border border-wellq-gray/20 dark:border-wellq-gray/30">
        <div className="flex items-center gap-2 text-red-500 text-sm font-semibold">
          <AlertTriangle size={16} />
          {t('platform.sync.errorLoad')}
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
      {/* Cabecera */}
      <div className="flex flex-wrap items-start justify-between gap-4 mb-5">
        <div className="flex items-start gap-3">
          <div className="w-10 h-10 rounded-xl flex items-center justify-center bg-wellq-cyan/10 ring-1 ring-wellq-cyan/20">
            <RefreshCw size={18} className="text-wellq-cyan" strokeWidth={2.2} />
          </div>
          <div>
            <h3 className="font-bold text-wellq-dark dark:text-white text-base tracking-tight">
              {t('platform.sync.title')}
            </h3>
            <p className="text-xs text-wellq-gray mt-0.5">{t('platform.sync.subtitle')}</p>
          </div>
        </div>

        <div className="flex items-center gap-3">
          <div className="text-right">
            <p className="text-[10px] font-bold uppercase tracking-wider text-wellq-gray">
              {t('platform.sync.nextRun')}
            </p>
            <p className="text-xs font-semibold text-wellq-dark dark:text-white flex items-center gap-1.5 justify-end">
              <CalendarClock size={12} className="text-wellq-gray" />
              {config?.next_run_at ? fmtDateTime(config.next_run_at, locale) : t('platform.sync.neverRun')}
            </p>
            {runStatusMeta && (
              <p className="mt-1 flex items-center gap-1.5 justify-end">
                <span className="text-[10px] font-bold uppercase tracking-wider text-wellq-gray">
                  {t('platform.sync.lastRunStatus')}
                </span>
                <span className={`flex items-center gap-1.5 px-2 py-0.5 rounded-md text-[10px] font-bold uppercase tracking-wider ${runStatusMeta.chip}`}>
                  <span className={`w-1.5 h-1.5 rounded-full ${runStatusMeta.dot}`} />
                  {t(runStatusMeta.labelKey)}
                </span>
              </p>
            )}
          </div>
          {canEdit && (
            <button
              type="button"
              onClick={runNow}
              disabled={running}
              className="inline-flex items-center gap-2 px-3.5 py-2 rounded-lg text-xs font-bold bg-wellq-cyan text-wellq-black hover:opacity-90 disabled:opacity-50 transition-opacity"
            >
              {running ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
              {running ? t('platform.sync.running') : t('platform.sync.runNow')}
            </button>
          )}
        </div>
      </div>

      {/* Frecuencia */}
      <div className="flex flex-wrap items-center justify-between gap-3 mb-5 pb-5 border-b border-wellq-gray/10 dark:border-white/5">
        <span className="text-xs font-bold text-wellq-gray uppercase tracking-wider">
          {t('platform.sync.frequency')}
        </span>
        <div className="flex items-center gap-2">
          {FREQUENCIES.map((hours) => {
            const active = config?.frequency_hours === hours;
            return (
              <button
                key={hours}
                type="button"
                disabled={!canEdit || savingFrequency}
                onClick={() => changeFrequency(hours)}
                className={`px-3 py-1.5 rounded-lg text-xs font-bold transition-colors disabled:cursor-not-allowed ${
                  active
                    ? 'bg-wellq-cyan text-wellq-black'
                    : 'bg-wellq-gray/10 text-wellq-gray hover:bg-wellq-gray/20 disabled:opacity-50'
                }`}
              >
                {hours} h
              </button>
            );
          })}
        </div>
      </div>

      {/* Estado por entidad */}
      <div className="space-y-2">
        {visibleSources.map((source) => {
          const wasSkipped = skippedKeys.has(source.key);
          const statusMeta = wasSkipped
            ? STATUS_META.skipped
            : (STATUS_META[source.status] ?? STATUS_META.error);
          const entityMeta = ENTITY_META[source.key] ?? { icon: RefreshCw, labelKey: null };
          const Icon = entityMeta.icon;
          return (
            <div
              key={source.key}
              className="flex items-center justify-between gap-3 p-3 rounded-xl bg-wellq-gray/3 dark:bg-white/[0.02] border border-wellq-gray/5 dark:border-white/5"
            >
              <div className="flex items-center gap-3 min-w-0">
                <Icon size={14} className="text-wellq-gray shrink-0" />
                <div className="min-w-0">
                  <p className="text-sm font-semibold text-wellq-dark dark:text-white/90 truncate">
                    {entityMeta.labelKey ? t(entityMeta.labelKey) : source.name}
                  </p>
                  <p className="text-[11px] text-wellq-gray truncate">
                    {wasSkipped ? (
                      t('platform.sync.skippedLastRun')
                    ) : (
                      <>
                        {t('platform.sync.lastUpdate')}:{' '}
                        <span title={fmtDateTime(source.last_sync, locale) ?? '—'}>
                          {source.last_sync ? fmtRelative(source.last_sync, locale) : t('platform.sync.neverRun')}
                        </span>
                      </>
                    )}
                  </p>
                </div>
              </div>
              <span className={`flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[10px] font-bold uppercase tracking-wider shrink-0 ${statusMeta.chip}`}>
                <span className={`w-1.5 h-1.5 rounded-full ${statusMeta.dot}`} />
                {t(statusMeta.labelKey)}
              </span>
            </div>
          );
        })}
      </div>

      {/* Error de la última ejecución */}
      {lastRunFailed && (
        <div className="mt-4 flex items-start gap-2 rounded-xl bg-red-500/5 border border-red-500/20 px-3 py-2.5">
          <XCircle size={14} className="text-red-500 mt-0.5 shrink-0" />
          <p className="text-xs text-red-600 dark:text-red-400 break-words">
            {lastRun?.error || t('platform.sync.runFailed')}
          </p>
        </div>
      )}

      {/* Historial */}
      <div className="mt-5 pt-4 border-t border-wellq-gray/10 dark:border-white/5">
        <button
          type="button"
          onClick={() => setHistoryOpen((v) => !v)}
          className="flex w-full items-center justify-between text-xs font-bold text-wellq-gray uppercase tracking-wider hover:text-wellq-dark dark:hover:text-white transition-colors"
        >
          <span className="flex items-center gap-2">
            <History size={14} />
            {t('platform.sync.history')}
          </span>
          {historyOpen ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
        </button>

        {historyOpen && (
          <div className="mt-3 overflow-x-auto">
            {runs.length === 0 ? (
              <p className="text-xs text-wellq-gray py-3 text-center">{t('platform.sync.historyEmpty')}</p>
            ) : (
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-left text-[10px] uppercase tracking-wider text-wellq-gray">
                    <th className="py-2 pr-3 font-bold">{t('platform.sync.trigger')}</th>
                    <th className="py-2 pr-3 font-bold">{t('platform.sync.startedAt')}</th>
                    <th className="py-2 pr-3 font-bold">{t('platform.sync.duration')}</th>
                    <th className="py-2 pr-3 font-bold">{t('platform.sync.records')}</th>
                    <th className="py-2 font-bold">{t('platform.sync.runBy')}</th>
                  </tr>
                </thead>
                <tbody>
                  {runs.map((run) => {
                    const total = sumCounts(run.entities);
                    return (
                      <tr key={run.id} className="border-t border-wellq-gray/5 dark:border-white/5">
                        <td className="py-2 pr-3">
                          <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                            run.trigger === 'scheduled'
                              ? 'bg-wellq-cyan/10 text-wellq-cyan'
                              : 'bg-wellq-gray/10 text-wellq-gray'
                          }`}>
                            {run.trigger === 'scheduled'
                              ? t('platform.sync.triggerScheduled')
                              : t('platform.sync.triggerManual')}
                          </span>
                        </td>
                        <td className="py-2 pr-3 text-wellq-dark dark:text-white/80 whitespace-nowrap">
                          {fmtDateTime(run.started_at, locale) ?? '—'}
                        </td>
                        <td className="py-2 pr-3 text-wellq-gray tabular-nums">{fmtDuration(run.duration_ms)}</td>
                        <td className="py-2 pr-3 text-wellq-dark dark:text-white/80 tabular-nums">{total}</td>
                        <td className="py-2 text-wellq-gray truncate max-w-[160px]">{run.created_by ?? '—'}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </div>
        )}
      </div>
    </motion.div>
  );
};

export default SyncPanel;
