import React, { useCallback, useEffect, useState } from 'react';
import { motion } from 'framer-motion';
import {
  BellRing, Mail, MessageSquare, Send, Loader2, AlertTriangle, RefreshCw,
  CheckCircle2, XCircle, CircleSlash,
} from 'lucide-react';
import { toast } from 'sonner';
import {
  fetchIncidentNotificationConfig,
  fetchIncidentDeliveries,
  sendIncidentTest,
} from '../../api/client';
import { useLanguage } from '../../contexts/LanguageContext';
import useHasPermission from '../../hooks/useHasPermission';
import { Skeleton } from '../ui';

// ─────────────────────────────────────────────────────────────────────────────
// IncidentNotificationsPanel — Mejora #19
//
// Tarjeta «Notificaciones de incidentes» dentro de Operaciones de plataforma.
// Muestra qué canales están configurados (Email y Slack), permite enviar una
// prueba por cada canal (permiso platform.manage) y lista los últimos envíos.
// Consume:
//   GET  /api/v1/incident-notifications/config
//   GET  /api/v1/incident-notifications/deliveries
//   POST /api/v1/incident-notifications/test
// Los secretos (SMTP, webhook) viven en el .env del backend: aquí solo se ve
// si están configurados.
// ─────────────────────────────────────────────────────────────────────────────

const CHANNELS = [
  { key: 'email', icon: Mail, labelKey: 'platform.incidents.channelEmail', hintKey: 'platform.incidents.emailHint' },
  { key: 'slack', icon: MessageSquare, labelKey: 'platform.incidents.channelSlack', hintKey: 'platform.incidents.slackHint' },
];

const STATUS_META = {
  sent: { icon: CheckCircle2, labelKey: 'platform.incidents.statusSent', chip: 'bg-wellq-green/10 text-wellq-green' },
  failed: { icon: XCircle, labelKey: 'platform.incidents.statusFailed', chip: 'bg-red-500/10 text-red-600 dark:text-red-400' },
  skipped: { icon: CircleSlash, labelKey: 'platform.incidents.statusSkipped', chip: 'bg-wellq-gray/10 text-wellq-gray' },
};

const SEVERITY_KEYS = {
  low: 'platform.incidents.severity.low',
  medium: 'platform.incidents.severity.medium',
  high: 'platform.incidents.severity.high',
  critical: 'platform.incidents.severity.critical',
};

const fade = {
  hidden: { opacity: 0, y: 12 },
  show: { opacity: 1, y: 0, transition: { duration: 0.35, ease: 'easeOut' } },
};

const fmtDateTime = (value, locale) => {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return date.toLocaleString(locale === 'es' ? 'es-CL' : 'en-US', {
    day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit',
  });
};

export const IncidentNotificationsPanel = () => {
  const { t, locale } = useLanguage();
  const canManage = useHasPermission('platform.manage');

  const [config, setConfig] = useState(null);
  const [deliveries, setDeliveries] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [sending, setSending] = useState(null);

  const load = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    setError(null);
    try {
      const [configRes, deliveriesRes] = await Promise.all([
        fetchIncidentNotificationConfig(),
        fetchIncidentDeliveries(10),
      ]);
      setConfig(configRes?.data ?? null);
      setDeliveries(deliveriesRes?.data ?? []);
    } catch (err) {
      if (!silent) setError(err.message);
    } finally {
      if (!silent) setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const sendTest = async (channel) => {
    if (!canManage || sending) return;
    setSending(channel);
    const label = t(`platform.incidents.channel${channel === 'email' ? 'Email' : 'Slack'}`);
    try {
      const res = await sendIncidentTest(channel);
      const status = res?.data?.status;
      if (status === 'sent') toast.success(t('platform.incidents.testSent', { channel: label }));
      else if (status === 'skipped') toast.warning(t('platform.incidents.testSkipped', { channel: label }));
      else toast.error(t('platform.incidents.testFailed', { channel: label }));
      await load(true);
    } catch (err) {
      toast.error(err.message || t('platform.incidents.testFailed', { channel: label }));
    } finally {
      setSending(null);
    }
  };

  if (loading) {
    return (
      <div className="bg-white dark:bg-wellq-dark rounded-2xl p-6 shadow-sm border border-wellq-gray/20 dark:border-wellq-gray/30 space-y-4">
        <Skeleton className="h-5 w-56" />
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
          {t('platform.incidents.errorLoad')}
        </div>
        <p className="text-xs text-wellq-gray mt-1">{error}</p>
      </div>
    );
  }

  const severityLabel = t(SEVERITY_KEYS[config?.min_severity] ?? 'platform.incidents.severity.high');

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
          <div className="w-10 h-10 rounded-xl flex items-center justify-center bg-amber-500/10 ring-1 ring-amber-500/20">
            <BellRing size={18} className="text-amber-500" strokeWidth={2.2} />
          </div>
          <div>
            <h3 className="font-bold text-wellq-dark dark:text-white text-base tracking-tight">
              {t('platform.incidents.title')}
            </h3>
            <p className="text-xs text-wellq-gray mt-0.5">
              {t('platform.incidents.subtitle', { severity: severityLabel })}
            </p>
          </div>
        </div>
        <button
          type="button"
          onClick={() => load(true)}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold text-wellq-gray hover:text-wellq-dark dark:hover:text-white hover:bg-wellq-gray/10 transition-colors"
        >
          <RefreshCw size={13} />
          {t('platform.incidents.refresh')}
        </button>
      </div>

      {config && !config.enabled && (
        <div className="mb-5 flex items-start gap-2 rounded-xl bg-amber-500/10 border border-amber-500/30 p-3 text-xs text-amber-700 dark:text-amber-400">
          <AlertTriangle size={14} className="shrink-0 mt-0.5" />
          {t('platform.incidents.disabled')}
        </div>
      )}

      {/* Canales */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mb-5 pb-5 border-b border-wellq-gray/10 dark:border-white/5">
        {CHANNELS.map(({ key, icon: Icon, labelKey, hintKey }) => {
          const channel = config?.channels?.[key] ?? {};
          const configured = Boolean(channel.configured);
          const recipients = key === 'email'
            ? t(
              channel.recipients_source === 'fixed'
                ? 'platform.incidents.recipientsFixed'
                : 'platform.incidents.recipientsAdmins',
              { count: channel.recipients_count ?? 0 },
            )
            : t(hintKey);
          return (
            <div
              key={key}
              className="rounded-xl border border-wellq-gray/15 dark:border-white/10 p-4 flex items-start justify-between gap-3"
            >
              <div className="flex items-start gap-3 min-w-0">
                <Icon size={18} className="text-wellq-gray mt-0.5 shrink-0" />
                <div className="min-w-0">
                  <p className="text-sm font-bold text-wellq-dark dark:text-white flex items-center gap-2">
                    {t(labelKey)}
                    <span
                      className={`px-2 py-0.5 rounded-md text-[10px] font-bold uppercase tracking-wider ${
                        configured ? 'bg-wellq-green/10 text-wellq-green' : 'bg-wellq-gray/10 text-wellq-gray'
                      }`}
                    >
                      {configured ? t('platform.incidents.configured') : t('platform.incidents.notConfigured')}
                    </span>
                  </p>
                  <p className="text-xs text-wellq-gray mt-1 truncate">
                    {key === 'email' ? `${t(hintKey)} · ${recipients}` : recipients}
                  </p>
                </div>
              </div>
              {canManage && (
                <button
                  type="button"
                  onClick={() => sendTest(key)}
                  disabled={sending !== null}
                  className="shrink-0 inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-bold bg-wellq-cyan text-wellq-black hover:opacity-90 disabled:opacity-50 transition-opacity"
                >
                  {sending === key ? <Loader2 size={13} className="animate-spin" /> : <Send size={13} />}
                  {sending === key ? t('platform.incidents.sending') : t('platform.incidents.sendTest')}
                </button>
              )}
            </div>
          );
        })}
      </div>

      {/* Historial */}
      <h4 className="text-xs font-bold text-wellq-gray uppercase tracking-wider mb-3">
        {t('platform.incidents.history')}
      </h4>
      {deliveries.length === 0 ? (
        <p className="text-xs text-wellq-gray">{t('platform.incidents.noHistory')}</p>
      ) : (
        <ul className="divide-y divide-wellq-gray/10 dark:divide-white/5">
          {deliveries.map((d) => {
            const meta = STATUS_META[d.status] ?? STATUS_META.skipped;
            const StatusIcon = meta.icon;
            return (
              <li key={d.delivery_id} className="py-2.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
                <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-md font-bold uppercase tracking-wider text-[10px] ${meta.chip}`}>
                  <StatusIcon size={11} />
                  {t(meta.labelKey)}
                </span>
                <span className="font-semibold text-wellq-dark dark:text-white">
                  {d.channel === 'email' ? t('platform.incidents.channelEmail') : t('platform.incidents.channelSlack')}
                </span>
                <span className="text-wellq-dark dark:text-white/90 truncate max-w-[18rem]">
                  {d.alert_id === 'test' ? t('platform.incidents.testAlert') : (d.alert_title || d.alert_id)}
                </span>
                {d.severity && (
                  <span className="text-wellq-gray">{t(SEVERITY_KEYS[d.severity] ?? 'platform.incidents.severity.high')}</span>
                )}
                <span className="text-wellq-gray ml-auto">
                  {d.triggered_by === 'system' ? t('platform.incidents.triggeredBySystem') : d.triggered_by}
                  {' · '}
                  {fmtDateTime(d.created_at, locale)}
                </span>
                {d.status !== 'sent' && d.detail && (
                  <p className="basis-full text-[11px] text-wellq-gray">{d.detail}</p>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </motion.div>
  );
};

export default IncidentNotificationsPanel;
