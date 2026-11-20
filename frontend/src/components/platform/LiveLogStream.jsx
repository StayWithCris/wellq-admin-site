import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { ArrowDown, Pause, Play } from 'lucide-react';
import { toast } from 'sonner';
import { streamTelemetryEvents } from '../../api/client';
import { useLanguage } from '../../contexts/LanguageContext';
import { filterAndSortBySearch } from '../../utils/search';

// ─────────────────────────────────────────────────────────────────────────────
// LiveLogStream — Recomendación #5
//
// Feed en vivo de eventos (tráfico HTTP + análisis de postura) vía SSE.
//
// Cotas (el Virtual DOM no degrada con historia infinita):
//   · DOM:        máximo 200 filas visibles.
//   · Cola de pausa: máximo 500 eventos acumulados.
//
// Autoscroll inteligente: solo sigue el final si el usuario ya está al fondo;
// si hizo scroll hacia arriba NO se le fuerza el scroll (y aparece un botón
// flotante "ir al final").
// ─────────────────────────────────────────────────────────────────────────────

const MAX_ROWS = 200;
const MAX_PAUSE_QUEUE = 500;

const RESULT_META = {
  ok:    { chip: 'bg-wellq-green/10 text-wellq-green',   dot: 'bg-wellq-green' },
  warn:  { chip: 'bg-amber-500/10 text-amber-600 dark:text-amber-400', dot: 'bg-amber-500' },
  error: { chip: 'bg-red-500/10 text-red-600 dark:text-red-400', dot: 'bg-red-500' },
};

const fmtHora = (value, locale) => {
  if (!value) return '—';
  const fecha = new Date(value);
  if (Number.isNaN(fecha.getTime())) return '—';
  return fecha.toLocaleTimeString(locale === 'es' ? 'es-CL' : 'en-US', {
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  });
};

export const LiveLogStream = ({
  clinicId = '',
  endpoint = '',
  canManage = true,
  initialLogs = [],
  searchQuery = '',
}) => {
  const { t, locale } = useLanguage();

  const [logs, setLogs] = useState(() => (Array.isArray(initialLogs) ? initialLogs.slice(-MAX_ROWS) : []));
  const [paused, setPaused] = useState(false);
  const [liveState, setLiveState] = useState('live'); // 'live' | 'reconnecting' | 'paused_background'
  const [pendingCount, setPendingCount] = useState(0);

  const colaPausa = useRef([]);
  const contenedorRef = useRef(null);
  const alFondoRef = useRef(true);
  // `pausado` en un ref: pausar NO debe cerrar el stream si es pausa manual
  // (sigue abierto y los eventos se acumulan en la cola de pausa).
  const pausadoRef = useRef(paused);
  // Marca si veníamos de una desconexión (para no toastear en la 1ª conexión).
  const fueReconexionRef = useRef(false);
  const [mostrarIrAlFinal, setMostrarIrAlFinal] = useState(false);
  const [isActiveVisible, setIsActiveVisible] = useState(true);
  const visibilityTimerRef = useRef(null);

  useEffect(() => { pausadoRef.current = paused; }, [paused]);

  // ── Protección de costos en Cloud Run (document.visibilityState) ───────────
  useEffect(() => {
    const onVisibilityChange = () => {
      if (document.visibilityState === 'hidden') {
        // Al ocultarse la pestaña, programamos la pausa del stream tras 2 min
        visibilityTimerRef.current = setTimeout(() => {
          setIsActiveVisible(false);
          setLiveState('paused_background');
        }, 120000);
      } else {
        // Al volver a visible, cancelamos temporizador y reconectamos si estaba en pausa
        if (visibilityTimerRef.current) {
          clearTimeout(visibilityTimerRef.current);
          visibilityTimerRef.current = null;
        }
        setIsActiveVisible(true);
      }
    };

    document.addEventListener('visibilitychange', onVisibilityChange);
    return () => {
      document.removeEventListener('visibilitychange', onVisibilityChange);
      if (visibilityTimerRef.current) clearTimeout(visibilityTimerRef.current);
    };
  }, []);

  // ── Scroll inteligente ───────────────────────────────────────────────────
  const manejarScroll = useCallback(() => {
    const el = contenedorRef.current;
    if (!el) return;
    const alFondo = el.scrollTop + el.clientHeight >= el.scrollHeight - 8;
    alFondoRef.current = alFondo;
    setMostrarIrAlFinal(!alFondo);
  }, []);

  const irAlFinal = useCallback(() => {
    const el = contenedorRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
    alFondoRef.current = true;
    setMostrarIrAlFinal(false);
  }, []);

  // ── Recepción de eventos ─────────────────────────────────────────────────
  const agregarEventos = useCallback((nuevos) => {
    if (!nuevos.length) return;
    setLogs((prev) => {
      const siguiente = [...prev, ...nuevos];
      // Cota dura: máximo MAX_ROWS filas en el DOM.
      return siguiente.length > MAX_ROWS ? siguiente.slice(-MAX_ROWS) : siguiente;
    });
  }, []);

  useEffect(() => {
    // Si el usuario está al fondo, seguimos el scroll automáticamente.
    if (alFondoRef.current) {
      const el = contenedorRef.current;
      if (el) el.scrollTop = el.scrollHeight;
    }
  }, [logs]);

  useEffect(() => {
    if (!isActiveVisible) {
      setLiveState('paused_background');
      return;
    }

    const cerrar = streamTelemetryEvents({
      clinicId: clinicId || undefined,
      endpoint: endpoint || undefined,
      onOpen: () => {
        setLiveState('live');
        // Solo avisamos "reconectado" si veníamos de una desconexión: en la
        // PRIMERA conexión no hay nada que reconectar.
        if (fueReconexionRef.current) {
          fueReconexionRef.current = false;
          toast.success(t('platform.telemetry.reconnected'));
        }
      },
      onError: (err) => {
        if (err?.status === 401 || err?.status === 403) {
          setLiveState('auth_error');
          toast.error(err.message || t('platform.telemetry.authError'));
          return;
        }
        setLiveState('reconnecting');
        // El stream reintenta con backoff: el aviso se muestra UNA vez por
        // desconexión, no en cada reintento.
        if (!fueReconexionRef.current) {
          fueReconexionRef.current = true;
          toast.error(t('platform.telemetry.connectionLost'));
        }
      },
      onLog: (evento) => {
        if (pausadoRef.current) {
          // Pausa manual: los eventos se acumulan en la cola interna (máx 500).
          if (colaPausa.current.length < MAX_PAUSE_QUEUE) {
            colaPausa.current.push(evento);
            setPendingCount(colaPausa.current.length);
          }
          return;
        }
        agregarEventos([evento]);
      },
    });

    return () => {
      cerrar();
      colaPausa.current = [];
      setPendingCount(0);
    };
    // `paused` NO va en las dependencias: pausar no debe cerrar el stream.
    // El callback lee `pausadoRef`, que se actualiza en un efecto aparte.
  }, [clinicId, endpoint, agregarEventos, t, isActiveVisible]);

  // ── Pausa / reanudación ──────────────────────────────────────────────────
  const alternarPausa = () => {
    if (!canManage) return;
    if (paused) {
      const acumulado = colaPausa.current.splice(0, MAX_PAUSE_QUEUE);
      setPendingCount(0);
      agregarEventos(acumulado);
      setPaused(false);
    } else {
      setPaused(true);
    }
  };

  // ── Filtro por búsqueda global (mismo criterio que SyncPanel) ────────────
  const visibleLogs = useMemo(
    () => filterAndSortBySearch(
      logs,
      searchQuery,
      (l) => [l?.endpoint, l?.clinic_id, l?.patient_id, l?.result, l?.status_code, l?.method],
    ),
    [logs, searchQuery],
  );

  const estadoMeta = useMemo(() => {
    if (paused) {
      return {
        chip: 'bg-amber-500/10 text-amber-600 dark:text-amber-400',
        dot: 'bg-amber-500',
        label: t('platform.telemetry.paused'),
      };
    }
    if (liveState === 'paused_background') {
      return {
        chip: 'bg-blue-500/10 text-blue-600 dark:text-blue-400',
        dot: 'bg-blue-500',
        label: t('platform.telemetry.pausedInactivity'),
      };
    }
    if (liveState === 'auth_error') {
      return {
        chip: 'bg-rose-500/10 text-rose-600 dark:text-rose-400',
        dot: 'bg-rose-500',
        label: t('platform.telemetry.authError'),
      };
    }
    if (liveState === 'reconnecting') {
      return {
        chip: 'bg-amber-500/10 text-amber-600 dark:text-amber-400',
        dot: 'bg-amber-500',
        label: t('platform.telemetry.reconnecting'),
      };
    }
    return {
      chip: 'bg-wellq-green/10 text-wellq-green',
      dot: 'bg-wellq-green',
      label: t('platform.telemetry.live'),
    };
  }, [paused, liveState, t]);

  return (
    <div className="rounded-2xl border border-wellq-gray/10 dark:border-white/5 bg-wellq-gray/3 dark:bg-white/[0.02] overflow-hidden">
      {/* ── Cabecera ── */}
      <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3 border-b border-wellq-gray/10 dark:border-white/5">
        <div className="flex items-center gap-3">
          <h4 className="text-xs font-bold text-wellq-dark dark:text-white uppercase tracking-wider">
            {t('platform.telemetry.liveFeed')}
          </h4>
          <span className={`flex items-center gap-1.5 px-2 py-0.5 rounded-md text-[10px] font-bold uppercase tracking-wider ${estadoMeta.chip}`}>
            <span className={`w-1.5 h-1.5 rounded-full ${estadoMeta.dot} ${paused ? '' : 'animate-pulse'}`} />
            {estadoMeta.label}
          </span>
          <span className="text-[10px] font-semibold text-wellq-gray tabular-nums">
            {logs.length}
          </span>
        </div>

        <div className="flex items-center gap-2">
          {paused && pendingCount > 0 && (
            <span className="text-[10px] font-bold text-amber-600 dark:text-amber-400">
              {t('platform.telemetry.newEvents', { count: pendingCount })}
            </span>
          )}
          {canManage && (
            <button
              type="button"
              onClick={alternarPausa}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-bold bg-wellq-gray/10 text-wellq-gray hover:bg-wellq-gray/20 transition-colors"
            >
              {paused ? <Play size={13} /> : <Pause size={13} />}
              {paused ? t('platform.telemetry.resume') : t('platform.telemetry.pause')}
            </button>
          )}
        </div>
      </div>

      {/* ── Feed ── */}
      <div className="relative">
        <div
          ref={contenedorRef}
          onScroll={manejarScroll}
          className="max-h-[320px] overflow-y-auto divide-y divide-wellq-gray/5 dark:divide-white/5"
        >
          {visibleLogs.length === 0 ? (
            <p className="text-xs font-medium text-wellq-gray text-center py-8 px-4">
              {t('platform.telemetry.noEvents')}
            </p>
          ) : (
            visibleLogs.map((evento, idx) => {
              const meta = RESULT_META[evento?.result] ?? RESULT_META.ok;
              const etiqueta = evento?.clinic_id || evento?.endpoint || evento?.patient_id || '—';
              return (
                <div
                  key={`${evento?.created_at ?? idx}-${idx}`}
                  className="flex items-center gap-3 px-4 py-2 text-[11px] hover:bg-wellq-gray/5 dark:hover:bg-white/[0.03] transition-colors"
                >
                  <span className="text-wellq-gray tabular-nums shrink-0 font-medium">
                    {fmtHora(evento?.created_at, locale)}
                  </span>
                  <span
                    className={`w-1.5 h-1.5 rounded-full shrink-0 ${meta.dot}`}
                    title={evento?.result ?? 'ok'}
                  />
                  <span className="font-semibold text-wellq-dark dark:text-white/90 truncate max-w-[220px]">
                    {etiqueta}
                  </span>
                  {evento?.endpoint && evento?.clinic_id && (
                    <span className="text-wellq-gray/70 truncate max-w-[180px] hidden sm:inline">
                      {evento.endpoint}
                    </span>
                  )}
                  <span className="ml-auto flex items-center gap-2 shrink-0">
                    {evento?.latency_ms != null && (
                      <span className="text-wellq-gray tabular-nums font-semibold">
                        {Math.round(evento.latency_ms)} ms
                      </span>
                    )}
                    <span className={`px-1.5 py-0.5 rounded text-[9px] font-bold uppercase tracking-wider ${meta.chip}`}>
                      {evento?.result ?? 'ok'}
                    </span>
                  </span>
                </div>
              );
            })
          )}
        </div>

        {/* Botón flotante "ir al final" */}
        <AnimatePresence>
          {mostrarIrAlFinal && (
            <motion.button
              type="button"
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: 6 }}
              onClick={irAlFinal}
              className="absolute bottom-3 right-3 inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-[10px] font-bold bg-wellq-cyan text-wellq-black shadow-lg"
            >
              <ArrowDown size={12} />
              {t('platform.telemetry.jumpToEnd')}
            </motion.button>
          )}
        </AnimatePresence>
      </div>
    </div>
  );
};

export default LiveLogStream;
