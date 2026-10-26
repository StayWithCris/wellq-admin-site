"""Recomendación #3 — Simulador de ingresos (proyección de MRR/ARR).

GET /api/simulator/revenue
Parte del MRR real de las clínicas activas (Neon) y proyecta mes a mes con
supuestos que el usuario mueve en la pantalla. Es solo lectura: no escribe nada.

Modelo (cada mes):
    mrr_siguiente = mrr * (1 - churn%) * (1 + expansión%) + nuevas_clínicas * mrr_por_clínica_nueva
Es una proyección ilustrativa, no una predicción.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.neon import get_db
from app.models_db import Clinic
from .dependencies import get_current_user

router = APIRouter(prefix="/api/simulator", tags=["Simulador de Ingresos"])


def project(base_mrr: float, new_clinics: float, mrr_per_new: float,
            churn_pct: float, expansion_pct: float, months: int) -> list[dict]:
    """Proyección pura (sin BD) para poder probarla fácilmente."""
    series = [{"month": 0, "mrr": round(base_mrr, 2), "arr": round(base_mrr * 12, 2)}]
    mrr = base_mrr
    for m in range(1, months + 1):
        mrr = mrr * (1 - churn_pct / 100) * (1 + expansion_pct / 100) + new_clinics * mrr_per_new
        series.append({"month": m, "mrr": round(mrr, 2), "arr": round(mrr * 12, 2)})
    return series


@router.get("/revenue", summary="Proyectar MRR/ARR con supuestos ajustables")
async def simulate_revenue(
    new_clinics_per_month: float = Query(1, ge=0, le=100),
    avg_mrr_per_new_clinic: float | None = Query(None, ge=0, le=1_000_000),
    churn_pct: float = Query(2, ge=0, le=100, description="Churn mensual %"),
    expansion_pct: float = Query(0, ge=0, le=100, description="Expansión mensual % sobre MRR existente"),
    months: int = Query(12, ge=1, le=36),
    db: AsyncSession = Depends(get_db),
    _user=Depends(get_current_user),
):
    try:
        clinics = (await db.execute(select(Clinic))).scalars().all()
        active = [c for c in clinics
                  if not c.is_deleted and c.status != "churned" and (c.mrr or 0) > 0]
        base_mrr = float(sum(c.mrr or 0 for c in active))
        avg_mrr = base_mrr / len(active) if active else 0.0
        mrr_new = avg_mrr_per_new_clinic if avg_mrr_per_new_clinic is not None else avg_mrr

        series = project(base_mrr, new_clinics_per_month, mrr_new, churn_pct, expansion_pct, months)
        at = lambda n: series[min(n, months)]
        return {
            "status": "success",
            "data": {
                "base": {
                    "active_clinics": len(active),
                    "mrr": round(base_mrr, 2),
                    "arr": round(base_mrr * 12, 2),
                    "avg_mrr_per_clinic": round(avg_mrr, 2),
                },
                "assumptions": {
                    "new_clinics_per_month": new_clinics_per_month,
                    "avg_mrr_per_new_clinic": round(mrr_new, 2),
                    "churn_pct": churn_pct,
                    "expansion_pct": expansion_pct,
                    "months": months,
                },
                "series": series,
                "summary": {
                    "mrr_6m": at(6)["mrr"],
                    "mrr_12m": at(12)["mrr"],
                    "arr_12m": at(12)["arr"],
                    "delta_12m": round(at(12)["mrr"] - base_mrr, 2),
                },
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al simular ingresos: {e}")
