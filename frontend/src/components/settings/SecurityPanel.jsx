import React, { useCallback, useEffect, useState } from 'react';
import { Lock, Smartphone, Copy, Download, Loader2, X } from 'lucide-react';
import { toast } from 'sonner';
import { apiFetch } from '../../api/client';
import { getStoredUser, clearTokens } from '../../services/auth';
import { useLanguage } from '../../contexts/LanguageContext';
import ConfirmDialog from '../ui/ConfirmDialog';

// Rec #18 — Seguridad de la cuenta: doble factor (TOTP) y sesiones abiertas

const inputCls = 'w-full rounded-lg border border-wellq-gray/30 dark:border-white/10 bg-white dark:bg-[#0b1017] px-3 py-2 text-sm text-wellq-dark dark:text-white';
const labelCls = 'text-xs font-bold text-wellq-gray';
const card = 'bg-white dark:bg-wellq-dark rounded-2xl p-6 shadow-sm border border-wellq-gray/20 dark:border-white/10';
const when = (iso) => (iso ? new Date(iso).toLocaleString('es-CL', { dateStyle: 'medium', timeStyle: 'short' }) : '—');

function BackupCodes({ codes, onDone }) {
  const { t } = useLanguage();
  const text = codes.join('\n');
  const copy = async () => {
    try { await navigator.clipboard.writeText(text); toast.success(t('recs.secCopied')); } catch { toast.error(t('recs.secCopyError')); }
  };
  const download = () => {
    const blob = new Blob([`${t('recs.secBackupFileHeader')}\n\n${text}\n`], { type: 'text/plain;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'wellq_codigos_de_respaldo.txt';
    a.click();
    URL.revokeObjectURL(url);
  };
  return (
    <div className="rounded-xl border border-amber-400/40 bg-amber-400/5 p-4 space-y-3" role="status">
      <p className="text-sm font-bold text-wellq-dark dark:text-white">{t('recs.secBackupTitle')}</p>
      <p className="text-xs text-wellq-gray">{t('recs.secBackupHint')}</p>
      <div className="grid grid-cols-2 gap-2 font-mono text-sm text-wellq-dark dark:text-white">
        {codes.map((c) => <span key={c} className="rounded-lg bg-wellq-gray/10 px-3 py-1.5 text-center tracking-wider">{c}</span>)}
      </div>
      <div className="flex flex-wrap gap-2">
        <button onClick={copy} className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-bold border border-wellq-gray/30 text-wellq-dark dark:text-white"><Copy size={14} /> {t('recs.secCopy')}</button>
        <button onClick={download} className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-bold border border-wellq-gray/30 text-wellq-dark dark:text-white"><Download size={14} /> {t('recs.secDownload')}</button>
        <button onClick={onDone} className="ml-auto px-4 py-2 rounded-lg text-xs font-bold bg-wellq-cyan text-wellq-black">{t('recs.secSavedCodes')}</button>
      </div>
    </div>
  );
}

function TwoFactorCard() {
  const { t } = useLanguage();
  const [status, setStatus] = useState(null);
  const [setup, setSetup] = useState(null);           // { secret, otpauthUri }
  const [code, setCode] = useState('');
  const [codes, setCodes] = useState(null);           // códigos de respaldo recién generados
  const [mode, setMode] = useState(null);             // 'disable' | 'regen'
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    apiFetch('/api/auth/2fa/status').then((res) => setStatus(res.data)).catch(() => setStatus({ error: true }));
  }, []);
  useEffect(() => { load(); }, [load]);

  const run = async (fn) => {
    setBusy(true);
    try { await fn(); } catch (e) { if (e.status !== 403) toast.error(e.message); } finally { setBusy(false); }
  };

  const startSetup = () => run(async () => {
    const res = await apiFetch('/api/auth/2fa/setup', { method: 'POST' });
    setSetup(res.data);
    setCode('');
  });

  const enable = () => run(async () => {
    const res = await apiFetch('/api/auth/2fa/enable', { method: 'POST', body: JSON.stringify({ code: code.trim() }) });
    setSetup(null); setCode('');
    setCodes(res.data.backupCodes);
    toast.success(t('recs.secEnabled'));
    load();
  });

  const submitSensitive = () => run(async () => {
    const body = JSON.stringify({ password, code: code.trim() });
    if (mode === 'disable') {
      await apiFetch('/api/auth/2fa/disable', { method: 'POST', body });
      toast.success(t('recs.secDisabled'));
    } else {
      const res = await apiFetch('/api/auth/2fa/backup-codes', { method: 'POST', body });
      setCodes(res.data.backupCodes);
    }
    setMode(null); setPassword(''); setCode('');
    load();
  });

  if (!status) return <section className={card}><Loader2 size={14} className="animate-spin text-wellq-gray" /></section>;
  if (status.error) return <section className={card}><p role="alert" className="text-sm text-red-500">{t('recs.secError')}</p></section>;

  return (
    <section className={`${card} space-y-4`} aria-labelledby="sec-2fa-title">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <Lock size={18} className="text-wellq-cyan" />
            <h3 id="sec-2fa-title" className="text-lg font-black text-wellq-dark dark:text-white">{t('recs.secTwoFactorTitle')}</h3>
            <span className={`px-2 py-0.5 rounded-full border text-[11px] font-bold ${status.enabled
              ? 'bg-wellq-green/10 text-wellq-green border-wellq-green/30' : 'bg-wellq-gray/10 text-wellq-gray border-wellq-gray/20'}`}>
              {t(status.enabled ? 'recs.secOn' : 'recs.secOff')}
            </span>
          </div>
          <p className="text-xs text-wellq-gray mt-1">{t('recs.secTwoFactorSub')}</p>
        </div>
        {!status.enabled && !setup && (
          <button onClick={startSetup} disabled={busy} className="flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-bold bg-wellq-cyan text-wellq-black disabled:opacity-50">
            {busy && <Loader2 size={14} className="animate-spin" />} {t('recs.secActivate')}
          </button>
        )}
      </div>

      {codes && <BackupCodes codes={codes} onDone={() => setCodes(null)} />}

      {setup && (
        <div className="rounded-xl border border-wellq-gray/20 dark:border-white/10 p-4 space-y-3">
          <p className="text-sm font-bold text-wellq-dark dark:text-white">{t('recs.secStep1')}</p>
          <p className="text-xs text-wellq-gray">{t('recs.secStep1Hint')}</p>
          <div className="rounded-lg bg-wellq-gray/10 p-3 font-mono text-sm tracking-wider text-wellq-dark dark:text-white break-all select-all" aria-label={t('recs.secKey')}>{setup.secret}</div>
          <a href={setup.otpauthUri} className="text-xs font-bold text-wellq-cyan hover:underline">{t('recs.secOpenApp')}</a>
          <p className="text-sm font-bold text-wellq-dark dark:text-white pt-1">{t('recs.secStep2')}</p>
          <div className="flex flex-wrap gap-2">
            <input id="sec-enable-code" aria-label={t('recs.secCode')} inputMode="numeric" autoComplete="one-time-code" maxLength={8} placeholder="000000"
              className={`${inputCls} max-w-[160px] text-center tracking-[0.3em] font-bold`} value={code} onChange={(e) => setCode(e.target.value)} />
            <button onClick={enable} disabled={busy || code.trim().length < 6} className="flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-bold bg-wellq-cyan text-wellq-black disabled:opacity-50">
              {busy && <Loader2 size={14} className="animate-spin" />} {t('recs.secConfirm')}
            </button>
            <button onClick={() => { setSetup(null); setCode(''); }} className="px-4 py-2 rounded-xl text-sm font-bold text-wellq-gray hover:text-wellq-dark dark:hover:text-white">{t('recs.secCancel')}</button>
          </div>
        </div>
      )}

      {status.enabled && !mode && (
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <span className="text-wellq-gray">{t('recs.secCodesLeft', { n: status.backupCodesRemaining })}</span>
          <button onClick={() => { setMode('regen'); setCode(''); setPassword(''); }} className="px-3 py-1.5 rounded-lg text-xs font-bold border border-wellq-gray/30 text-wellq-dark dark:text-white">{t('recs.secRegen')}</button>
          <button onClick={() => { setMode('disable'); setCode(''); setPassword(''); }} className="px-3 py-1.5 rounded-lg text-xs font-bold border border-red-300/50 text-red-500">{t('recs.secDisable')}</button>
        </div>
      )}

      {mode && (
        <div className="rounded-xl border border-wellq-gray/20 dark:border-white/10 p-4 space-y-3">
          <p className="text-sm font-bold text-wellq-dark dark:text-white">{t(mode === 'disable' ? 'recs.secDisableTitle' : 'recs.secRegenTitle')}</p>
          <p className="text-xs text-wellq-gray">{t('recs.secSensitiveHint')}</p>
          <div className="grid grid-cols-2 gap-3">
            <label className={labelCls}>{t('recs.secPassword')}
              <input id="sec-password" type="password" autoComplete="current-password" className={`${inputCls} mt-1`} value={password} onChange={(e) => setPassword(e.target.value)} />
            </label>
            <label className={labelCls}>{t('recs.secCode')}
              <input id="sec-code" inputMode="text" autoComplete="one-time-code" maxLength={12} className={`${inputCls} mt-1`} value={code} onChange={(e) => setCode(e.target.value)} />
            </label>
          </div>
          <div className="flex gap-2">
            <button onClick={submitSensitive} disabled={busy || !password || !code.trim()}
              className={`flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-bold disabled:opacity-50 ${mode === 'disable' ? 'bg-red-500 text-white' : 'bg-wellq-cyan text-wellq-black'}`}>
              {busy && <Loader2 size={14} className="animate-spin" />} {t(mode === 'disable' ? 'recs.secDisable' : 'recs.secRegen')}
            </button>
            <button onClick={() => setMode(null)} className="px-4 py-2 rounded-xl text-sm font-bold text-wellq-gray hover:text-wellq-dark dark:hover:text-white">{t('recs.secCancel')}</button>
          </div>
        </div>
      )}
    </section>
  );
}

function SessionsCard() {
  const { t } = useLanguage();
  const [state, setState] = useState({ loading: true, error: null, rows: [] });
  const [confirm, setConfirm] = useState(null);   // { kind: 'one' | 'others', item }
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    apiFetch('/api/auth/sessions')
      .then((res) => setState({ loading: false, error: null, rows: res.data ?? [] }))
      .catch((e) => setState({ loading: false, error: e.message, rows: [] }));
  }, []);
  useEffect(() => { load(); }, [load]);

  const doConfirm = async () => {
    const { kind, item } = confirm;
    setConfirm(null);
    setBusy(true);
    try {
      if (kind === 'one') {
        await apiFetch(`/api/auth/sessions/${item.id}/revoke`, { method: 'POST' });
        if (item.current) {   // cerró su propia sesión: vuelve a la pantalla de ingreso
          clearTokens();
          window.location.reload();
          return;
        }
        toast.success(t('recs.secSessionClosed'));
      } else {
        const res = await apiFetch('/api/auth/sessions/revoke-others', { method: 'POST' });
        toast.success(t('recs.secOthersClosed', { n: res.data.revoked }));
      }
      load();
    } catch (e) {
      if (e.status !== 403) toast.error(e.message);
    } finally {
      setBusy(false);
    }
  };

  const hasCurrent = state.rows.some((s) => s.current);
  const others = state.rows.filter((s) => !s.current).length;

  return (
    <section className={card} aria-labelledby="sec-sessions-title">
      <div className="flex flex-wrap items-start justify-between gap-3 mb-3">
        <div>
          <div className="flex items-center gap-2">
            <Smartphone size={18} className="text-wellq-cyan" />
            <h3 id="sec-sessions-title" className="text-lg font-black text-wellq-dark dark:text-white">{t('recs.secSessionsTitle')}</h3>
          </div>
          <p className="text-xs text-wellq-gray mt-1">{t('recs.secSessionsSub')}</p>
        </div>
        {hasCurrent && others > 0 && (
          <button onClick={() => setConfirm({ kind: 'others' })} disabled={busy}
            className="px-3 py-2 rounded-lg text-xs font-bold border border-red-300/50 text-red-500 disabled:opacity-40">{t('recs.secCloseOthers')}</button>
        )}
      </div>

      {state.loading && <Loader2 size={14} className="animate-spin text-wellq-gray" />}
      {state.error && <p role="alert" className="text-sm text-red-500">{state.error}</p>}
      {!state.loading && !state.error && state.rows.length === 0 && <p className="text-sm text-wellq-gray">{t('recs.secNoSessions')}</p>}
      {!hasCurrent && state.rows.length > 0 && <p className="text-xs text-amber-600 dark:text-amber-400 mb-2">{t('recs.secLegacySession')}</p>}

      <ul className="divide-y divide-wellq-gray/10 dark:divide-white/5">
        {state.rows.map((s) => (
          <li key={s.id} className="py-3 flex flex-wrap items-center justify-between gap-2">
            <div>
              <div className="text-sm font-bold text-wellq-dark dark:text-white">
                {s.device}
                {s.current && <span className="ml-2 px-2 py-0.5 rounded-full text-[10px] font-bold bg-wellq-green/10 text-wellq-green border border-wellq-green/30">{t('recs.secThisDevice')}</span>}
                {s.twoFactor && <span className="ml-2 text-[10px] text-wellq-gray">· {t('recs.secWith2fa')}</span>}
              </div>
              <div className="text-xs text-wellq-gray">{s.ip || '—'} · {t('recs.secStarted', { when: when(s.createdAt) })} · {t('recs.secLastSeen', { when: when(s.lastSeenAt) })}</div>
            </div>
            <button onClick={() => setConfirm({ kind: 'one', item: s })} disabled={busy}
              aria-label={t('recs.secCloseSession')} title={t('recs.secCloseSession')}
              className="p-1.5 rounded-lg border border-red-300/50 text-red-500 disabled:opacity-40"><X size={14} /></button>
          </li>
        ))}
      </ul>
      <p className="mt-3 text-[11px] text-wellq-gray">{t('recs.secSessionsNote')}</p>

      <ConfirmDialog
        open={Boolean(confirm)}
        title={confirm?.kind === 'others' ? t('recs.secCloseOthersTitle') : t('recs.secCloseSessionTitle')}
        message={confirm?.kind === 'others' ? t('recs.secCloseOthersMsg') : t(confirm?.item?.current ? 'recs.secCloseCurrentMsg' : 'recs.secCloseSessionMsg')}
        onConfirm={doConfirm}
        onCancel={() => setConfirm(null)}
      />
    </section>
  );
}

export default function SecurityPanel() {
  const { t } = useLanguage();
  const user = getStoredUser();
  return (
    <div className="space-y-6">
      <p className="text-sm text-wellq-gray">{t('recs.secIntro', { email: user?.email ?? '' })}</p>
      <TwoFactorCard />
      <SessionsCard />
    </div>
  );
}
