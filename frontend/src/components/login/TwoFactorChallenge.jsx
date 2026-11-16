import { useState } from 'react';
import { createPortal } from 'react-dom';
import { Loader2, ShieldCheck } from 'lucide-react';
import { verifyTwoFactor } from '../../services/auth';
import { useLanguage } from '../../contexts/LanguageContext';

// Rec #18 — Segundo paso del inicio de sesión: código de la app de autenticación o código de respaldo
export default function TwoFactorChallenge({ challengeToken, onSuccess, onCancel }) {
  const { t } = useLanguage();
  const [code, setCode] = useState('');
  const [useBackup, setUseBackup] = useState(false);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    if (!code.trim() || loading) return;
    setLoading(true);
    setError('');
    try {
      await verifyTwoFactor(challengeToken, code.trim());
      onSuccess();
    } catch (err) {
      setError(err.message || t('recs.tfError'));
      setLoading(false);
    }
  };

  return createPortal(
    <div className="fixed inset-0 z-[9999] flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm" role="dialog" aria-modal="true" aria-labelledby="tf-title">
      <form onSubmit={submit} className="w-full max-w-sm rounded-[14px] border border-cyan-300/30 bg-[#07101d] p-6 text-left shadow-[0_0_38px_rgba(34,211,238,.22)]">
        <div className="mb-4 flex items-center gap-2 text-cyan-300">
          <ShieldCheck size={20} />
          <h2 id="tf-title" className="text-lg font-black text-white">{t('recs.tfTitle')}</h2>
        </div>
        <p className="mb-4 text-sm text-slate-300">{t(useBackup ? 'recs.tfHintBackup' : 'recs.tfHint')}</p>
        <label htmlFor="tf-code" className="mb-1 block text-xs font-bold text-slate-300">{t(useBackup ? 'recs.tfBackupLabel' : 'recs.tfCodeLabel')}</label>
        <input
          id="tf-code" autoFocus value={code} onChange={(e) => setCode(e.target.value)}
          inputMode={useBackup ? 'text' : 'numeric'} autoComplete="one-time-code" maxLength={useBackup ? 12 : 8}
          placeholder={useBackup ? 'XXXX-XXXX' : '000000'}
          className="w-full rounded-lg border border-cyan-300/30 bg-[#0b1626] px-3 py-2.5 text-center text-lg font-bold tracking-[0.3em] text-white outline-none focus:border-cyan-300"
        />
        {error && <p role="alert" className="mt-3 text-sm text-red-400">{error}</p>}
        <button type="submit" disabled={loading || !code.trim()}
          className="mt-4 flex w-full items-center justify-center gap-2 rounded-lg bg-cyan-400 px-4 py-2.5 text-sm font-black text-slate-900 disabled:opacity-50">
          {loading && <Loader2 size={14} className="animate-spin" />} {t('recs.tfVerify')}
        </button>
        <div className="mt-3 flex items-center justify-between text-xs">
          <button type="button" onClick={() => { setUseBackup((v) => !v); setCode(''); setError(''); }} className="text-cyan-300 hover:underline">
            {t(useBackup ? 'recs.tfUseApp' : 'recs.tfUseBackup')}
          </button>
          <button type="button" onClick={onCancel} className="text-slate-400 hover:text-white">{t('recs.tfBack')}</button>
        </div>
      </form>
    </div>,
    document.body
  );
}
