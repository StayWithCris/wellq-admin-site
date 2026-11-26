import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import { motion } from 'framer-motion';
import {
  CalendarClock, Mail, MessageSquare, FileSpreadsheet, Play, Eye, Download, Plus, Trash2, Edit3,
  Pause, CheckCircle2, XCircle, CircleSlash, Loader2, AlertTriangle, X, History, ChevronDown, ChevronUp,
} from 'lucide-react';
import { toast } from 'sonner';
import {
  fetchReportMeta,
  fetchScheduledReports,
  createScheduledReport,
  updateScheduledReport,
  toggleScheduledReport,
  deleteScheduledReport,
  previewScheduledReport,
  downloadScheduledReportCsv,
  runScheduledReportNow,
  fetchScheduledReportRuns,
} from '../../api/client';
import { useLanguage } from '../../contexts/LanguageContext';
import useHasPermission from '../../hooks/useHasPermission';
import { filterAndSortBySearch } from '../../utils/search';
import { Skeleton } from '../ui';
import ConfirmDialog from '../ui/ConfirmDialog';

// ─────────────────────────────────────────────────────────────────────────────
// ScheduledReportsPanel — Recomendación #11
//
// Reportes programados dentro de Analítica de producto:
//   - lista de reportes con frecuencia, secciones, destinatarios y próximo envío
//   - vista previa (HTML aislado en un iframe sin permisos), descarga CSV
//   - crear / editar / pausar / eliminar / enviar ahora (permiso de gestión)
//   - historial de envíos (scheduled_report_runs)
// Gestión = super_admin, o analytics.view + platform.manage / settings.manage
// (la misma regla que aplica el backend en require_reports_manage).
// ─────────────────────────────────────────────────────────────────────────────

const SECTIONS = ['retention', 'soap_quality', 'clinic_health', 'app_usage', 'executive_mrr'];
const FREQUENCIES = ['daily', 'weekly', 'monthly'];
const CHANNELS = ['email', 'slack'];
const WEEKDAYS = [0, 1, 2, 3, 4, 5, 6];
const EMAIL_RE = /^[A-Za-z0-9._%+'-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$/;

const RUN_STATUS = {
  sent: { icon: CheckCircle2, chip: 'bg-wellq-green/10 text-wellq-green' },
  partial: { icon: AlertTriangle, chip: 'bg-amber-500/10 text-amber-600 dark:text-amber-400' },
  failed: { icon: XCircle, chip: 'bg-red-500/10 text-red-600 dark:text-red-400' },
  skipped: { icon: CircleSlash, chip: 'bg-wellq-gray/10 text-wellq-gray' },
};

const EMPTY_FORM = {
  name: '',
  description: '',
  frequency: 'weekly',
  day_of_week: 0,
  day_of_month: 1,
  send_hour_utc: 12,
  sections: ['clinic_health', 'executive_mrr'],
  channels: ['email'],
  recipientsText: '',
  include_csv: true,
};

// Alias: la configuración de ESLint del repo no detecta `motion` usado como <motion.div>.
const MotionDiv = motion.div;

const fade = {
  hidden: { opacity: 0, y: 12 },
  show: { opacity: 1, y: 0, transition: { duration: 0.35, ease: 'easeOut' } },
};

const fmtDate = (value, locale) => {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return date.toLocaleString(locale === 'es' ? 'es-CL' : 'en-US', {
    day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit',
  });
};

const parseRecipients = (text) =>
  text.split(/[\s,;]+/).map((e) => e.trim()).filter(Boolean);

const Modal = ({ title, onClose, children, closeLabel, wide = false }) => createPortal(
  <div className="fixed inset-0 z-[9998] flex items-center justify-center p-4" role="dialog" aria-modal="true" aria-label={title}>
    <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />
    <div className={`relative w-full ${wide ? 'max-w-3xl' : 'max-w-xl'} max-h-[90vh] overflow-y-auto bg-white dark:bg-[#0b1017] rounded-2xl shadow-xl border border-wellq-gray/20 dark:border-[#1e293b] p-6`}>
      <div className="flex items-start justify-between gap-4 mb-4">
        <h3 className="text-base font-bold text-wellq-dark dark:text-white">{title}</h3>
        <button type="button" onClick={onClose} aria-label={closeLabel} className="p-1 rounded hover:bg-wellq-gray/10">
          <X size={18} className="text-wellq-gray" />
        </button>
      </div>
      {children}
    </div>
  </div>,
  document.body,
);

export const ScheduledReportsPanel = ({ searchQuery = '' }) => {
  const { t, locale } = useLanguage();
  const canPlatform = useHasPermission('platform.manage');
  const canSettings = useHasPermission('settings.manage');
  const canManage = canPlatform || canSettings;

  const [meta, setMeta] = useState(null);
  const [reports, setReports] = useState([]);
  const [runs, setRuns] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(null);           // `${accion}:${report_id}`
  const [form, setForm] = useState(null);           // {mode: 'create'|'edit', reportId, values}
  const [saving, setSaving] = useState(false);
  const [preview, setPreview] = useState(null);     // {report, data}
  const [confirmDelete, setConfirmDelete] = useState(null);
  const [historyOpen, setHistoryOpen] = useState(false);

  const load = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    setError(null);
    try {
      const [metaRes, reportsRes, runsRes] = await Promise.all([
        fetchReportMeta(),
        fetchScheduledReports(),
        fetchScheduledReportRuns({ limit: 10 }),
      ]);
      setMeta(metaRes?.data ?? null);
      setReports(reportsRes?.data ?? []);
      setRuns(runsRes?.data ?? []);
    } catch (err) {
      if (!silent) setError(err.message);
    } finally {
      if (!silent) setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const sectionLabel = useCallback((key) => t(`analytics.reports.sections.${key}`), [t]);
  const frequencyLabel = useCallback((r) => {
    const hour = `${String(r.send_hour_utc).padStart(2, '0')}:00 UTC`;
    if (r.frequency === 'weekly') return `${t('analytics.reports.freq.weekly')} · ${t(`analytics.reports.weekdays.d${r.day_of_week}`)} ${hour}`;
    if (r.frequency === 'monthly') return `${t('analytics.reports.freq.monthly')} · ${t('analytics.reports.dayN', { day: r.day_of_month })} ${hour}`;
    return `${t('analytics.reports.freq.daily')} · ${hour}`;
  }, [t]);

  const visibleReports = useMemo(
    () => filterAndSortBySearch(reports, searchQuery, (r) => [r.name, r.description, ...r.sections.map(sectionLabel), ...r.recipients]),
    [reports, searchQuery, sectionLabel],
  );

  const act = async (key, fn, okMsg) => {
    setBusy(key);
    try {
      const res = await fn();
      if (okMsg) toast.success(okMsg);
      await load(true);
      return res;
    } catch (err) {
      toast.error(err.message || t('analytics.reports.actionFailed'));
      return null;
    } finally {
      setBusy(null);
    }
  };

  const runNow = async (r) => {
    setBusy(`run:${r.report_id}`);
    try {
      const res = await runScheduledReportNow(r.report_id);
      const status = res?.data?.status;
      if (status === 'sent') toast.success(t('analytics.reports.runSent'));
      else if (status === 'partial') toast.warning(t('analytics.reports.runPartial'));
      else if (status === 'skipped') toast.warning(t('analytics.reports.runSkipped'));
      else toast.error(t('analytics.reports.runFailed'));
      await load(true);
    } catch (err) {
      toast.error(err.message || t('analytics.reports.runFailed'));
    } finally {
      setBusy(null);
    }
  };

  const openPreview = async (r) => {
    setBusy(`preview:${r.report_id}`);
    try {
      const res = await previewScheduledReport(r.report_id);
      setPreview({ report: r, data: res?.data });
    } catch (err) {
      toast.error(err.message || t('analytics.reports.actionFailed'));
    } finally {
      setBusy(null);
    }
  };

  const download = (r) => act(
    `csv:${r.report_id}`,
    () => downloadScheduledReportCsv(r.report_id, `WellQ_${r.name.replace(/[^A-Za-z0-9]+/g, '_')}.csv`),
  );

  const openForm = (r = null) => {
    setForm(r ? {
      mode: 'edit',
      reportId: r.report_id,
      values: {
        name: r.name,
        description: r.description ?? '',
        frequency: r.frequency,
        day_of_week: r.day_of_week ?? 0,
        day_of_month: r.day_of_month ?? 1,
        send_hour_utc: r.send_hour_utc,
        sections: r.sections,
        channels: r.channels,
        recipientsText: r.recipients.join(', '),
        include_csv: r.include_csv,
      },
    } : { mode: 'create', values: { ...EMPTY_FORM } });
  };

  const formErrors = useMemo(() => {
    if (!form) return [];
    const v = form.values;
    const errs = [];
    if (!v.name.trim()) errs.push(t('analytics.reports.errName'));
    if (!v.sections.length) errs.push(t('analytics.reports.errSections'));
    if (!v.channels.length) errs.push(t('analytics.reports.errChannels'));
    const emails = parseRecipients(v.recipientsText);
    const bad = emails.filter((e) => !EMAIL_RE.test(e));
    if (bad.length) errs.push(t('analytics.reports.errEmails', { emails: bad.join(', ') }));
    if (v.channels.includes('email') && !emails.length) errs.push(t('analytics.reports.errNoRecipients'));
    return errs;
  }, [form, t]);

  const saveForm = async () => {
    if (!form || formErrors.length || saving) return;
    const v = form.values;
    const body = {
      name: v.name.trim(),
      description: v.description.trim() || null,
      frequency: v.frequency,
      day_of_week: v.frequency === 'weekly' ? Number(v.day_of_week) : null,
      day_of_month: v.frequency === 'monthly' ? Number(v.day_of_month) : null,
      send_hour_utc: Number(v.send_hour_utc),
      sections: v.sections,
      channels: v.channels,
      recipients: parseRecipients(v.recipientsText),
      include_csv: v.include_csv,
    };
    setSaving(true);
    try {
      if (form.mode === 'edit') await updateScheduledReport(form.reportId, body);
      else await createScheduledReport(body);
      toast.success(t(form.mode === 'edit' ? 'analytics.reports.updated' : 'analytics.reports.created'));
      setForm(null);
      await load(true);
    } catch (err) {
      toast.error(err.message || t('analytics.reports.actionFailed'));
    } finally {
      setSaving(false);
    }
  };

  const setValue = (key, value) => setForm((f) => ({ ...f, values: { ...f.values, [key]: value } }));
  const toggleIn = (key, item) => setForm((f) => {
    const list = f.values[key];
    return { ...f, values: { ...f.values, [key]: list.includes(item) ? list.filter((x) => x !== item) : [...list, item] } };
  });

  const card = 'bg-white dark:bg-[#0b1017] rounded-2xl p-6 shadow-sm border border-wellq-gray/20 dark:border-[#1e293b]';

  if (loading) {
    return (
      <div className={`${card} space-y-4`}>
        <Skeleton className="h-5 w-56" />
        <Skeleton className="h-16 w-full" />
        <Skeleton className="h-16 w-full" />
      </div>
    );
  }

  if (error) {
    return (
      <div className={card}>
        <div className="flex items-center gap-2 text-red-500 text-sm font-semibold">
          <AlertTriangle size={16} />
          {t('analytics.reports.errorLoad')}
        </div>
        <p className="text-xs text-wellq-gray mt-1">{error}</p>
      </div>
    );
  }

  const channelBadge = (configured, Icon, label) => (
    <span className={`inline-flex items-center gap-1.5 px-2 py-1 rounded-md text-[10px] font-bold uppercase tracking-wider ${configured ? 'bg-wellq-green/10 text-wellq-green' : 'bg-wellq-gray/10 text-wellq-gray'}`}>
      <Icon size={12} />
      {label} · {configured ? t('analytics.reports.configured') : t('analytics.reports.notConfigured')}
    </span>
  );

  const iconBtn = 'p-2 rounded-lg text-wellq-gray hover:text-wellq-dark dark:hover:text-white hover:bg-wellq-gray/10 disabled:opacity-40';

  return (
    <MotionDiv variants={fade} initial="hidden" animate="show" className={card}>
      {/* Cabecera */}
      <div className="flex flex-wrap items-start justify-between gap-4 mb-5">
        <div className="flex items-start gap-3">
          <div className="w-10 h-10 rounded-xl flex items-center justify-center bg-wellq-cyan/10 ring-1 ring-wellq-cyan/20">
            <CalendarClock size={18} className="text-wellq-cyan" strokeWidth={2.2} />
          </div>
          <div>
            <h3 className="font-bold text-wellq-dark dark:text-white text-base tracking-tight">{t('analytics.reports.title')}</h3>
            <p className="text-xs text-wellq-gray mt-0.5">{t('analytics.reports.subtitle')}</p>
            <div className="flex flex-wrap gap-2 mt-2">
              {channelBadge(meta?.channels?.email_configured, Mail, t('analytics.reports.channels.email'))}
              {channelBadge(meta?.channels?.slack_configured, MessageSquare, t('analytics.reports.channels.slack'))}
            </div>
          </div>
        </div>
        {canManage && (
          <button
            type="button"
            onClick={() => openForm()}
            className="inline-flex items-center gap-2 px-3.5 py-2 rounded-lg text-xs font-bold bg-wellq-cyan text-wellq-black hover:opacity-90"
          >
            <Plus size={14} />
            {t('analytics.reports.new')}
          </button>
        )}
      </div>

      {/* Lista */}
      {visibleReports.length === 0 ? (
        <p className="text-xs text-wellq-gray py-4">
          {reports.length ? t('common.noResults') : t('analytics.reports.empty')}
        </p>
      ) : (
        <ul className="divide-y divide-wellq-gray/10 dark:divide-white/5">
          {visibleReports.map((r) => (
            <li key={r.report_id} className="py-4 flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <p className="text-sm font-bold text-wellq-dark dark:text-white">{r.name}</p>
                  <span className={`px-2 py-0.5 rounded-md text-[10px] font-bold uppercase tracking-wider ${r.is_active ? 'bg-wellq-green/10 text-wellq-green' : 'bg-wellq-gray/10 text-wellq-gray'}`}>
                    {r.is_active ? t('analytics.reports.active') : t('analytics.reports.paused')}
                  </span>
                </div>
                <p className="text-xs text-wellq-gray mt-1">
                  {frequencyLabel(r)} · {t('analytics.reports.recipientsCount', { count: r.recipients.length })}
                  {r.include_csv && <span className="inline-flex items-center gap-1 ml-2"><FileSpreadsheet size={11} />CSV</span>}
                </p>
                <div className="flex flex-wrap gap-1.5 mt-2">
                  {r.sections.map((s) => (
                    <span key={s} className="px-2 py-0.5 rounded-md text-[10px] font-semibold bg-wellq-cyan/10 text-wellq-cyan">{sectionLabel(s)}</span>
                  ))}
                </div>
                <p className="text-[11px] text-wellq-gray mt-2">
                  {t('analytics.reports.lastRun')}: {fmtDate(r.last_run_at, locale)} · {t('analytics.reports.nextRun')}: {r.is_active ? fmtDate(r.next_run_at, locale) : '—'}
                </p>
              </div>
              <div className="flex items-center gap-1">
                <button type="button" className={iconBtn} title={t('analytics.reports.preview')} aria-label={t('analytics.reports.preview')}
                  disabled={busy !== null} onClick={() => openPreview(r)}>
                  {busy === `preview:${r.report_id}` ? <Loader2 size={15} className="animate-spin" /> : <Eye size={15} />}
                </button>
                <button type="button" className={iconBtn} title={t('analytics.reports.downloadCsv')} aria-label={t('analytics.reports.downloadCsv')}
                  disabled={busy !== null} onClick={() => download(r)}>
                  {busy === `csv:${r.report_id}` ? <Loader2 size={15} className="animate-spin" /> : <Download size={15} />}
                </button>
                {canManage && (
                  <>
                    <button type="button" className={iconBtn} title={t('analytics.reports.runNow')} aria-label={t('analytics.reports.runNow')}
                      disabled={busy !== null} onClick={() => runNow(r)}>
                      {busy === `run:${r.report_id}` ? <Loader2 size={15} className="animate-spin" /> : <Play size={15} />}
                    </button>
                    <button type="button" className={iconBtn}
                      title={r.is_active ? t('analytics.reports.pause') : t('analytics.reports.activate')}
                      aria-label={r.is_active ? t('analytics.reports.pause') : t('analytics.reports.activate')}
                      disabled={busy !== null}
                      onClick={() => act(`toggle:${r.report_id}`, () => toggleScheduledReport(r.report_id, !r.is_active),
                        r.is_active ? t('analytics.reports.pausedOk') : t('analytics.reports.activatedOk'))}>
                      {r.is_active ? <Pause size={15} /> : <CheckCircle2 size={15} />}
                    </button>
                    <button type="button" className={iconBtn} title={t('analytics.reports.edit')} aria-label={t('analytics.reports.edit')}
                      disabled={busy !== null} onClick={() => openForm(r)}>
                      <Edit3 size={15} />
                    </button>
                    <button type="button" className={`${iconBtn} hover:text-red-500`} title={t('analytics.reports.delete')} aria-label={t('analytics.reports.delete')}
                      disabled={busy !== null} onClick={() => setConfirmDelete(r)}>
                      <Trash2 size={15} />
                    </button>
                  </>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}

      {/* Historial */}
      <div className="mt-4 pt-4 border-t border-wellq-gray/10 dark:border-white/5">
        <button type="button" onClick={() => setHistoryOpen((v) => !v)} aria-expanded={historyOpen}
          className="flex items-center gap-2 text-xs font-bold text-wellq-gray uppercase tracking-wider hover:text-wellq-dark dark:hover:text-white">
          <History size={13} />
          {t('analytics.reports.history')} ({runs.length})
          {historyOpen ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
        </button>
        {historyOpen && (
          runs.length === 0 ? (
            <p className="text-xs text-wellq-gray mt-3">{t('analytics.reports.noRuns')}</p>
          ) : (
            <ul className="mt-3 divide-y divide-wellq-gray/10 dark:divide-white/5">
              {runs.map((run) => {
                const meta2 = RUN_STATUS[run.status] ?? RUN_STATUS.skipped;
                const StatusIcon = meta2.icon;
                return (
                  <li key={run.run_id} className="py-2.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
                    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-md font-bold uppercase tracking-wider text-[10px] ${meta2.chip}`}>
                      <StatusIcon size={11} />
                      {t(`analytics.reports.runStatus.${run.status}`)}
                    </span>
                    <span className="font-semibold text-wellq-dark dark:text-white">{run.report_name}</span>
                    <span className="text-wellq-gray">{t(`analytics.reports.trigger.${run.trigger}`)}</span>
                    <span className="text-wellq-gray">{t('analytics.reports.recipientsCount', { count: run.recipients_count })}</span>
                    <span className="text-wellq-gray ml-auto">{fmtDate(run.created_at, locale)}</span>
                    {run.error_detail && run.status !== 'sent' && (
                      <p className="basis-full text-[11px] text-wellq-gray">{run.error_detail}</p>
                    )}
                  </li>
                );
              })}
            </ul>
          )
        )}
      </div>

      {/* Vista previa: el HTML va en un iframe sin permisos (sandbox="") */}
      {preview && (
        <Modal title={`${t('analytics.reports.preview')} · ${preview.report.name}`} onClose={() => setPreview(null)} closeLabel={t('common.close')} wide>
          <p className="text-xs text-wellq-gray mb-3">
            {preview.data?.period_label}
            {preview.data?.metrics?.omitted?.length > 0 && (
              <> · {t('analytics.reports.omitted', { sections: preview.data.metrics.omitted.map(sectionLabel).join(', ') })}</>
            )}
          </p>
          <iframe
            title={t('analytics.reports.preview')}
            srcDoc={preview.data?.html_preview ?? ''}
            sandbox=""
            className="w-full h-[60vh] rounded-xl border border-wellq-gray/20 bg-[#020817]"
          />
        </Modal>
      )}

      {/* Crear / editar */}
      {form && (
        <Modal title={form.mode === 'edit' ? t('analytics.reports.editTitle') : t('analytics.reports.newTitle')} onClose={() => setForm(null)} closeLabel={t('common.close')}>
          <div className="space-y-4 text-sm">
            <label className="block">
              <span className="text-xs font-bold text-wellq-gray">{t('analytics.reports.fieldName')}</span>
              <input value={form.values.name} maxLength={120} onChange={(e) => setValue('name', e.target.value)}
                className="mt-1 w-full rounded-lg border border-wellq-gray/20 dark:border-white/10 bg-transparent px-3 py-2 text-wellq-dark dark:text-white" />
            </label>
            <label className="block">
              <span className="text-xs font-bold text-wellq-gray">{t('analytics.reports.fieldDescription')}</span>
              <input value={form.values.description} maxLength={1000} onChange={(e) => setValue('description', e.target.value)}
                className="mt-1 w-full rounded-lg border border-wellq-gray/20 dark:border-white/10 bg-transparent px-3 py-2 text-wellq-dark dark:text-white" />
            </label>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
              <label className="block">
                <span className="text-xs font-bold text-wellq-gray">{t('analytics.reports.fieldFrequency')}</span>
                <select value={form.values.frequency} onChange={(e) => setValue('frequency', e.target.value)}
                  className="mt-1 w-full rounded-lg border border-wellq-gray/20 dark:border-white/10 bg-white dark:bg-[#0b1017] px-3 py-2 text-wellq-dark dark:text-white">
                  {FREQUENCIES.map((f) => <option key={f} value={f}>{t(`analytics.reports.freq.${f}`)}</option>)}
                </select>
              </label>
              {form.values.frequency === 'weekly' && (
                <label className="block">
                  <span className="text-xs font-bold text-wellq-gray">{t('analytics.reports.fieldWeekday')}</span>
                  <select value={form.values.day_of_week} onChange={(e) => setValue('day_of_week', Number(e.target.value))}
                    className="mt-1 w-full rounded-lg border border-wellq-gray/20 dark:border-white/10 bg-white dark:bg-[#0b1017] px-3 py-2 text-wellq-dark dark:text-white">
                    {WEEKDAYS.map((d) => <option key={d} value={d}>{t(`analytics.reports.weekdays.d${d}`)}</option>)}
                  </select>
                </label>
              )}
              {form.values.frequency === 'monthly' && (
                <label className="block">
                  <span className="text-xs font-bold text-wellq-gray">{t('analytics.reports.fieldMonthDay')}</span>
                  <input type="number" min={1} max={28} value={form.values.day_of_month}
                    onChange={(e) => setValue('day_of_month', Math.min(28, Math.max(1, Number(e.target.value) || 1)))}
                    className="mt-1 w-full rounded-lg border border-wellq-gray/20 dark:border-white/10 bg-transparent px-3 py-2 text-wellq-dark dark:text-white" />
                </label>
              )}
              <label className="block">
                <span className="text-xs font-bold text-wellq-gray">{t('analytics.reports.fieldHour')}</span>
                <input type="number" min={0} max={23} value={form.values.send_hour_utc}
                  onChange={(e) => setValue('send_hour_utc', Math.min(23, Math.max(0, Number(e.target.value) || 0)))}
                  className="mt-1 w-full rounded-lg border border-wellq-gray/20 dark:border-white/10 bg-transparent px-3 py-2 text-wellq-dark dark:text-white" />
              </label>
            </div>
            <fieldset>
              <legend className="text-xs font-bold text-wellq-gray mb-2">{t('analytics.reports.fieldSections')}</legend>
              <div className="flex flex-wrap gap-2">
                {SECTIONS.map((s) => (
                  <label key={s} className="inline-flex items-center gap-2 text-xs text-wellq-dark dark:text-white">
                    <input type="checkbox" checked={form.values.sections.includes(s)} onChange={() => toggleIn('sections', s)} />
                    {sectionLabel(s)}
                  </label>
                ))}
              </div>
            </fieldset>
            <fieldset>
              <legend className="text-xs font-bold text-wellq-gray mb-2">{t('analytics.reports.fieldChannels')}</legend>
              <div className="flex flex-wrap gap-4">
                {CHANNELS.map((c) => (
                  <label key={c} className="inline-flex items-center gap-2 text-xs text-wellq-dark dark:text-white">
                    <input type="checkbox" checked={form.values.channels.includes(c)} onChange={() => toggleIn('channels', c)} />
                    {t(`analytics.reports.channels.${c}`)}
                  </label>
                ))}
              </div>
            </fieldset>
            <label className="block">
              <span className="text-xs font-bold text-wellq-gray">{t('analytics.reports.fieldRecipients')}</span>
              <textarea rows={2} value={form.values.recipientsText} onChange={(e) => setValue('recipientsText', e.target.value)}
                placeholder="ops@wellq.cl, gerencia@wellq.cl"
                className="mt-1 w-full rounded-lg border border-wellq-gray/20 dark:border-white/10 bg-transparent px-3 py-2 text-wellq-dark dark:text-white" />
            </label>
            <label className="inline-flex items-center gap-2 text-xs text-wellq-dark dark:text-white">
              <input type="checkbox" checked={form.values.include_csv} onChange={(e) => setValue('include_csv', e.target.checked)} />
              {t('analytics.reports.fieldCsv')}
            </label>
            {formErrors.length > 0 && (
              <ul className="text-xs text-red-500 space-y-1">
                {formErrors.map((err) => <li key={err}>• {err}</li>)}
              </ul>
            )}
            <div className="flex justify-end gap-3 pt-2">
              <button type="button" onClick={() => setForm(null)}
                className="px-4 py-2 text-sm font-medium text-wellq-gray hover:bg-wellq-gray/10 rounded-lg">
                {t('common.cancel')}
              </button>
              <button type="button" onClick={saveForm} disabled={saving || formErrors.length > 0}
                className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-bold bg-wellq-cyan text-wellq-black hover:opacity-90 disabled:opacity-50">
                {saving && <Loader2 size={14} className="animate-spin" />}
                {t('analytics.reports.save')}
              </button>
            </div>
          </div>
        </Modal>
      )}

      <ConfirmDialog
        open={Boolean(confirmDelete)}
        title={t('analytics.reports.deleteTitle')}
        message={t('analytics.reports.deleteMsg', { name: confirmDelete?.name ?? '' })}
        onConfirm={async () => {
          const r = confirmDelete;
          setConfirmDelete(null);
          await act(`delete:${r.report_id}`, () => deleteScheduledReport(r.report_id), t('analytics.reports.deleted'));
        }}
        onCancel={() => setConfirmDelete(null)}
      />
    </MotionDiv>
  );
};

export default ScheduledReportsPanel;
