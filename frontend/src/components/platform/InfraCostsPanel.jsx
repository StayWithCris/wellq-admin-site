import React, { useEffect, useState, useMemo } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  DollarSign, TrendingUp, AlertTriangle, CheckCircle, AlertCircle,
  Calculator, Loader2, Info,
} from 'lucide-react';
import { Skeleton } from '../ui';
import { apiFetch } from '../../api/client';

// ─────────────────────────────────────────────────────────────────────────────
// InfraCostsPanel — Proyector de costos de infraestructura cloud
//
// Implementa la mejora #6 del Informe de Recomendaciones (eje Infraestructura).
// Consume los endpoints /api/infrastructure/costs* expuestos en infra_costs.py.
//
// Decisiones de visualizacion:
//   · El titular es una cifra, no un grafico — un solo numero no necesita ejes.
//   · Desglose y proyeccion usan UNA sola serie: la identidad la da la etiqueta
//     de cada fila, no el color, asi que no hace falta leyenda ni paleta
//     categorica (los tonos de marca son todos cian y no se distinguirian).
//   · Cada barra lleva su valor visible. El verde no alcanza 3:1 sobre blanco,
//     y la etiqueta es lo que garantiza que el dato se pueda leer igual.
//   · El estado de presupuesto se comunica con icono + texto + color, nunca
//     solo con color.
//   · Modo oscuro con pasos elegidos a proposito (emerald-600 / emerald-400),
//     no invirtiendo el claro.
// ─────────────────────────────────────────────────────────────────────────────

const usd = (n) =>
  n == null ? '—'
    : n >= 1000 ? `$${(n / 1000).toFixed(1)}k`
    : `$${n.toFixed(2)}`;

const usdFull = (n) =>
  n == null ? '—' : `$${n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

// Estado del presupuesto: color + icono + etiqueta (nunca color solo)
const ESTADO = {
  normal:  { icon: CheckCircle,   label: 'En presupuesto', cls: 'text-emerald-600 dark:text-emerald-400 bg-emerald-500/10' },
  alerta:  { icon: AlertCircle,   label: 'Cerca del límite', cls: 'text-amber-600 dark:text-amber-400 bg-amber-500/10' },
  critico: { icon: AlertTriangle, label: 'Sobre presupuesto', cls: 'text-red-600 dark:text-red-400 bg-red-500/10' },
};

const fade = {
  hidden: { opacity: 0, y: 12 },
  show:   { opacity: 1, y: 0, transition: { duration: 0.35, ease: 'easeOut' } },
};

// ── Barra horizontal con etiqueta directa ────────────────────────────────────
const BarraServicio = ({ label, value, max, total, delay = 0 }) => {
  const [hover, setHover] = useState(false);
  const pct   = max > 0 ? (value / max) * 100 : 0;
  const share = total > 0 ? (value / total) * 100 : 0;

  return (
    <div
      className="relative group"
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
    >
      <div className="flex items-center justify-between mb-1.5">
        <span className="text-xs font-medium text-wellq-dark dark:text-slate-200 truncate pr-2">
          {label}
        </span>
        {/* Etiqueta directa: el valor siempre visible, no depende del color */}
        <span className="text-xs font-bold tabular-nums text-emerald-700 dark:text-emerald-300 shrink-0">
          {usdFull(value)}
        </span>
      </div>

      {/* Pista recesiva + marca fina con extremo redondeado anclado a la base */}
      <div className="h-2 w-full rounded-full bg-slate-100 dark:bg-white/5 overflow-hidden">
        <motion.div
          initial={{ width: 0 }}
          animate={{ width: `${pct}%` }}
          transition={{ duration: 0.5, delay, ease: 'easeOut' }}
          className="h-full rounded-full bg-emerald-600 dark:bg-emerald-400"
        />
      </div>

      <AnimatePresence>
        {hover && (
          <motion.div
            initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}
            className="absolute right-0 -top-9 z-20 px-2.5 py-1.5 rounded-lg bg-wellq-dark dark:bg-black
                       text-white text-[11px] font-medium shadow-lg whitespace-nowrap pointer-events-none"
          >
            {usdFull(value)} · {share.toFixed(1)}% del total
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
};

// ── Serie mensual de la proyeccion, con umbral de presupuesto ────────────────
const SerieProyeccion = ({ serie, presupuesto }) => {
  const [hover, setHover] = useState(null);
  const max = useMemo(
    () => Math.max(...serie.map((s) => s.costo_estimado_usd), presupuesto || 0) * 1.12,
    [serie, presupuesto]
  );
  const umbralPct = max > 0 && presupuesto > 0 ? (presupuesto / max) * 100 : null;

  return (
    <div className="relative pt-6 pb-1">
      {/* Umbral de presupuesto: linea discontinua recesiva con etiqueta directa */}
      {umbralPct != null && (
        <div className="absolute left-0 right-0 z-10 pointer-events-none"
             style={{ bottom: `calc(${umbralPct}% * 0.82 + 28px)` }}>
          <div className="border-t border-dashed border-slate-400/70 dark:border-slate-500/70" />
          <span className="absolute -top-4 right-0 text-[10px] font-semibold text-wellq-gray">
            Presupuesto {usdFull(presupuesto)}
          </span>
        </div>
      )}

      <div className="flex items-end justify-between gap-1.5 h-40">
        {serie.map((s, i) => {
          const h = max > 0 ? (s.costo_estimado_usd / max) * 100 : 0;
          const excede = presupuesto > 0 && s.costo_estimado_usd > presupuesto;
          return (
            <div key={s.mes} className="flex-1 flex flex-col items-center justify-end h-full relative"
                 onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
              <AnimatePresence>
                {hover === i && (
                  <motion.div
                    initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}
                    className="absolute -top-2 z-30 px-2.5 py-1.5 rounded-lg bg-wellq-dark dark:bg-black
                               text-white text-[11px] font-medium shadow-lg whitespace-nowrap pointer-events-none"
                  >
                    Mes {s.mes} · {s.clinicas_acumuladas} clínicas · {usdFull(s.costo_estimado_usd)}
                  </motion.div>
                )}
              </AnimatePresence>

              {/* separacion de 2px entre barras via gap del contenedor */}
              <motion.div
                initial={{ height: 0 }}
                animate={{ height: `${h}%` }}
                transition={{ duration: 0.45, delay: i * 0.05, ease: 'easeOut' }}
                className={`w-full rounded-t-md ${
                  excede
                    ? 'bg-red-600 dark:bg-red-400'
                    : 'bg-emerald-600 dark:bg-emerald-400'
                } ${hover === i ? 'ring-2 ring-white dark:ring-wellq-dark' : ''}`}
              />
              <span className="mt-1.5 text-[10px] font-medium text-wellq-gray tabular-nums">
                {s.mes}
              </span>
            </div>
          );
        })}
      </div>
      <p className="text-[10px] text-wellq-gray text-center mt-1">Mes de la proyección</p>
    </div>
  );
};

// ── Panel principal ──────────────────────────────────────────────────────────
export const InfraCostsPanel = () => {
  const [actual, setActual]   = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState(null);

  const [clinicas, setClinicas] = useState(100);
  const [meses, setMeses]       = useState(6);
  const [proy, setProy]         = useState(null);
  const [simulando, setSimulando] = useState(false);
  const [errorSim, setErrorSim]   = useState(null);

  useEffect(() => {
    let vivo = true;
    (async () => {
      try {
        setLoading(true);
        const json = await apiFetch('/api/infrastructure/costs');
        if (vivo) setActual(json?.status === 'success' ? json.data : null);
      } catch (err) {
        if (vivo) setError(err.message);
      } finally {
        if (vivo) setLoading(false);
      }
    })();
    return () => { vivo = false; };
  }, []);

  const simular = async () => {
    try {
      setSimulando(true);
      setErrorSim(null);
      const json = await apiFetch('/api/infrastructure/costs/project', {
        method: 'POST',
        body: JSON.stringify({ clinicas_adicionales: Number(clinicas), meses: Number(meses) }),
      });
      setProy(json);
    } catch (err) {
      setErrorSim(err.message);
    } finally {
      setSimulando(false);
    }
  };

  const desglose = useMemo(() => {
    const b = actual?.breakdown ?? [];
    return [...b].sort((x, y) => y.usd - x.usd);
  }, [actual]);

  const maxServicio = desglose.length ? desglose[0].usd : 0;
  const estado = ESTADO[actual?.estado_presupuesto] ?? ESTADO.normal;
  const EstadoIcon = estado.icon;

  // ── Carga ──
  if (loading) {
    return (
      <div className="bg-white dark:bg-wellq-dark rounded-2xl p-6 shadow-sm
                      border border-emerald-200/50 dark:border-emerald-500/20 space-y-4">
        <Skeleton className="h-5 w-56" />
        <Skeleton className="h-10 w-40" />
        <Skeleton className="h-2 w-full" />
        <Skeleton className="h-24 w-full" />
      </div>
    );
  }

  // ── Error ──
  if (error) {
    return (
      <div className="bg-white dark:bg-wellq-dark rounded-2xl p-6 shadow-sm
                      border border-red-200/60 dark:border-red-500/20">
        <div className="flex items-center gap-2 text-red-600 dark:text-red-400">
          <AlertTriangle size={18} strokeWidth={2.2} />
          <span className="text-sm font-semibold">No se pudo cargar el costo de infraestructura</span>
        </div>
        <p className="text-xs text-wellq-gray mt-1.5">{error}</p>
      </div>
    );
  }

  // ── Sin datos ──
  if (!actual) {
    return (
      <div className="bg-white dark:bg-wellq-dark rounded-2xl p-6 shadow-sm
                      border border-slate-200 dark:border-white/10">
        <div className="flex items-center gap-2 text-wellq-gray">
          <Info size={18} strokeWidth={2.2} />
          <span className="text-sm font-semibold">Sin registros de costo todavía</span>
        </div>
        <p className="text-xs text-wellq-gray mt-1.5">
          Ejecuta <code className="px-1 py-0.5 rounded bg-slate-100 dark:bg-white/10">python seed.py</code> para
          cargar los períodos de prueba.
        </p>
      </div>
    );
  }

  return (
    <motion.div variants={fade} initial="hidden" animate="show"
      className="bg-white dark:bg-wellq-dark rounded-2xl p-6 shadow-sm
                 border border-emerald-200/50 dark:border-emerald-500/20 overflow-hidden relative">

      <div className="absolute top-0 left-0 right-0 h-24 bg-gradient-to-b from-emerald-500/10 to-transparent
                      opacity-50 pointer-events-none" />

      {/* ── Cabecera ── */}
      <div className="relative flex items-start justify-between mb-5">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl flex items-center justify-center
                          bg-emerald-50 dark:bg-emerald-500/10 ring-1 ring-emerald-500/20">
            <DollarSign size={18} strokeWidth={2.2} className="text-emerald-600 dark:text-emerald-400" />
          </div>
          <div>
            <h3 className="text-sm font-bold text-wellq-dark dark:text-white">
              Costo de Infraestructura
            </h3>
            <p className="text-[11px] text-wellq-gray">{actual.period}</p>
          </div>
        </div>

        {/* Estado: icono + texto + color, nunca color solo */}
        <span className={`px-2.5 py-1 rounded-md text-[10px] font-bold uppercase tracking-wider
                          flex items-center gap-1.5 ${estado.cls}`}>
          <EstadoIcon size={12} strokeWidth={2.5} />
          {estado.label}
        </span>
      </div>

      {/* ── Titular: una cifra, sin ejes ── */}
      <div className="relative mb-5">
        <div className="flex items-baseline gap-2">
          <span className="text-3xl font-bold text-wellq-dark dark:text-white tabular-nums">
            {usdFull(actual.total_usd)}
          </span>
          <span className="text-sm text-wellq-gray">
            de {usdFull(actual.budget_usd)}
          </span>
        </div>

        {/* Medidor de presupuesto */}
        <div className="mt-2.5 h-2 w-full rounded-full bg-slate-100 dark:bg-white/5 overflow-hidden">
          <motion.div
            initial={{ width: 0 }}
            animate={{ width: `${Math.min(actual.budget_used_percent, 100)}%` }}
            transition={{ duration: 0.6, ease: 'easeOut' }}
            className={`h-full rounded-full ${
              actual.estado_presupuesto === 'critico' ? 'bg-red-600 dark:bg-red-400'
              : actual.estado_presupuesto === 'alerta' ? 'bg-amber-600 dark:bg-amber-400'
              : 'bg-emerald-600 dark:bg-emerald-400'}`}
          />
        </div>
        <p className="mt-1.5 text-[11px] font-semibold text-wellq-gray tabular-nums">
          {actual.budget_used_percent}% del presupuesto consumido
        </p>
      </div>

      {/* ── Desglose por servicio ── */}
      {desglose.length > 0 && (
        <div className="relative mb-6">
          <p className="text-[11px] font-bold text-wellq-gray uppercase tracking-wider mb-3">
            Desglose por servicio
          </p>
          <div className="space-y-3">
            {desglose.map((s, i) => (
              <BarraServicio key={s.servicio} label={s.servicio} value={s.usd}
                             max={maxServicio} total={actual.total_usd} delay={i * 0.05} />
            ))}
          </div>
        </div>
      )}

      {/* ── Simulador ── */}
      <div className="relative pt-5 border-t border-slate-100 dark:border-white/5">
        <div className="flex items-center gap-2 mb-3">
          <Calculator size={14} strokeWidth={2.2} className="text-wellq-gray" />
          <p className="text-[11px] font-bold text-wellq-gray uppercase tracking-wider">
            Proyección de costo
          </p>
        </div>

        <div className="flex flex-wrap items-end gap-3 mb-4">
          <label className="flex flex-col gap-1">
            <span className="text-[10px] font-semibold text-wellq-gray">Clínicas adicionales</span>
            <input type="number" min="0" max="10000" value={clinicas}
              onChange={(e) => setClinicas(e.target.value)}
              className="w-28 px-2.5 py-1.5 rounded-lg text-sm tabular-nums
                         bg-slate-50 dark:bg-white/5 border border-slate-200 dark:border-white/10
                         text-wellq-dark dark:text-white
                         focus:outline-none focus:ring-2 focus:ring-emerald-500/40" />
          </label>

          <label className="flex flex-col gap-1">
            <span className="text-[10px] font-semibold text-wellq-gray">Meses</span>
            <input type="number" min="1" max="36" value={meses}
              onChange={(e) => setMeses(e.target.value)}
              className="w-20 px-2.5 py-1.5 rounded-lg text-sm tabular-nums
                         bg-slate-50 dark:bg-white/5 border border-slate-200 dark:border-white/10
                         text-wellq-dark dark:text-white
                         focus:outline-none focus:ring-2 focus:ring-emerald-500/40" />
          </label>

          <button onClick={simular} disabled={simulando}
            className="px-4 py-1.5 rounded-lg text-sm font-semibold text-white
                       bg-emerald-600 hover:bg-emerald-700 disabled:opacity-60
                       transition-colors flex items-center gap-1.5">
            {simulando ? <Loader2 size={14} className="animate-spin" /> : <TrendingUp size={14} strokeWidth={2.4} />}
            {simulando ? 'Calculando…' : 'Simular'}
          </button>
        </div>

        {errorSim && (
          <p className="text-xs text-red-600 dark:text-red-400 mb-3">{errorSim}</p>
        )}

        <AnimatePresence>
          {proy && (
            <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}>
              <div className="grid grid-cols-3 gap-3 mb-4">
                {[
                  ['Costo actual',  usdFull(proy.linea_base.costo_usd)],
                  ['Incremental',   `+${usdFull(proy.proyeccion.costo_incremental_usd)}`],
                  ['Total proyectado', usdFull(proy.proyeccion.costo_total_usd)],
                ].map(([k, v]) => (
                  <div key={k} className="rounded-xl px-3 py-2.5 bg-slate-50 dark:bg-white/5">
                    <p className="text-[10px] font-semibold text-wellq-gray uppercase tracking-wide">{k}</p>
                    <p className="text-sm font-bold text-wellq-dark dark:text-white tabular-nums mt-0.5">{v}</p>
                  </div>
                ))}
              </div>

              {proy.alerta_presupuesto?.excede && (
                <div className="flex items-start gap-2 mb-3 px-3 py-2 rounded-lg
                                bg-red-500/10 text-red-600 dark:text-red-400">
                  <AlertTriangle size={14} strokeWidth={2.4} className="mt-0.5 shrink-0" />
                  <p className="text-xs font-medium">
                    La proyección supera el presupuesto a partir del mes {proy.alerta_presupuesto.mes_excede}.
                  </p>
                </div>
              )}

              <SerieProyeccion serie={proy.serie_mensual}
                               presupuesto={proy.linea_base.presupuesto_usd} />

              <p className="text-[10px] text-wellq-gray mt-3 leading-relaxed">
                Tarifas y supuestos de consumo son parámetros de calibración. Deben validarse
                contra la factura real de Google Cloud y Neon antes de usarse para decidir.
              </p>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </motion.div>
  );
};

export default InfraCostsPanel;
