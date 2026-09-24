import React, { useEffect, useState } from "react";
import { Sparkles, CheckCircle2, Clock, AlertTriangle } from "lucide-react";

export default function SoapQualityWidget() {
  const [metrics, setMetrics] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const apiUrl = import.meta.env.VITE_API_URL || "http://localhost:8000";
    fetch(`${apiUrl}/api/ai/soap-quality/`)
      .then((res) => {
        if (!res.ok) throw new Error("Error en la respuesta de la API");
        return res.json();
      })
      .then((data) => {
        setMetrics(data.latest);
        setLoading(false);
      })
      .catch((err) => {
        console.error("Error al cargar métricas de calidad SOAP:", err);
        setLoading(false);
      });
  }, []);

  if (loading) {
    return (
      <div className="bg-[#111c2e] p-6 rounded-xl border border-slate-800 animate-pulse">
        <div className="h-6 bg-slate-700 w-1/3 mb-4 rounded"></div>
        <div className="h-10 bg-slate-700 w-1/2 mb-6 rounded"></div>
        <div className="space-y-2">
          <div className="h-4 bg-slate-700 rounded"></div>
          <div className="h-4 bg-slate-700 rounded"></div>
        </div>
      </div>
    );
  }

  if (!metrics) {
    return (
      <div className="bg-[#111c2e] p-6 rounded-xl border border-slate-800 text-slate-400">
        No hay registros de calidad SOAP disponibles en el sistema.
      </div>
    );
  }

  return (
    <div className="bg-[#111c2e] p-6 rounded-xl border border-slate-800 text-white shadow-lg">
      <div className="flex items-center justify-between pb-4 border-b border-slate-800">
        <div className="flex items-center gap-2">
          <Sparkles className="text-emerald-400 w-5 h-5" />
          <h3 className="font-semibold text-base tracking-wide text-slate-200">
            AI SOAP Quality
          </h3>
        </div>
        <span className="text-xs uppercase px-2.5 py-0.5 rounded-full bg-slate-800 text-slate-400 border border-slate-700">
          {metrics.period || "Current Month"}
        </span>
      </div>

      <div className="my-6">
        <div className="flex items-baseline justify-between mb-2">
          <span className="text-3xl font-extrabold text-emerald-400">
            {metrics.acceptance_rate_percentage}%
          </span>
          <span className="text-xs font-semibold text-slate-400 uppercase tracking-wider">
            Acceptance Rate
          </span>
        </div>
        <div className="w-full bg-slate-800 h-2 rounded-full overflow-hidden">
          <div
            className="bg-emerald-400 h-full rounded-full transition-all duration-500"
            style={{ width: `${metrics.acceptance_rate_percentage}%` }}
          />
        </div>
      </div>

      <div className="grid grid-cols-1 gap-3 pt-2">
        <div className="flex items-center justify-between p-3 rounded-lg bg-slate-900/60 border border-slate-800">
          <div className="flex items-center gap-2 text-slate-400 text-sm">
            <CheckCircle2 className="w-4 h-4 text-emerald-400" />
            <span>Notas generadas</span>
          </div>
          <span className="font-semibold text-slate-100">
            {metrics.total_notes_generated?.toLocaleString()}
          </span>
        </div>

        <div className="flex items-center justify-between p-3 rounded-lg bg-slate-900/60 border border-slate-800">
          <div className="flex items-center gap-2 text-slate-400 text-sm">
            <AlertTriangle className="w-4 h-4 text-amber-400" />
            <span>Requieren edición</span>
          </div>
          <span className="font-semibold text-amber-400">
            {metrics.edits_required_percentage}%
          </span>
        </div>

        <div className="flex items-center justify-between p-3 rounded-lg bg-slate-900/60 border border-slate-800">
          <div className="flex items-center gap-2 text-slate-400 text-sm">
            <Clock className="w-4 h-4 text-blue-400" />
            <span>Tiempo ahorrado</span>
          </div>
          <span className="font-semibold text-blue-400">
            {metrics.average_time_saved_minutes_per_note} min/nota
          </span>
        </div>
      </div>

      {metrics.common_corrections && (
        <div className="mt-4 p-3 bg-slate-900/40 rounded-lg border border-slate-800/80">
          <span className="text-xs font-medium text-slate-400 block mb-1">
            Correcciones frecuentes:
          </span>
          <p className="text-xs text-slate-300 italic">
            {metrics.common_corrections}
          </p>
        </div>
      )}
    </div>
  );
}