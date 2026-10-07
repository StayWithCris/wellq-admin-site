"""Recomendación #8 — Mapa de calor de cohortes de retención (cálculo real sobre Neon).

Cohorte = mes de creación de la clínica (clinics.created_at).
Una clínica cuenta como retenida en el mes N de su cohorte si:
  - sigue activa hoy (status != 'churned' y no eliminada): se considera retenida en todos los meses; o
  - ya no está activa: su última actividad (last_login, acotada por deleted_at) llega al inicio del mes N.
Los meses futuros de cada cohorte se devuelven como null (no se inventan datos).
"""
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.neon import get_db
from app.models_db import Clinic

router = APIRouter(prefix="/api/analytics", tags=["Analítica de Producto y App"])

MESES = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]


def _add_months(year: int, month: int, n: int) -> datetime:
    idx = year * 12 + (month - 1) + n
    return datetime(idx // 12, idx % 12 + 1, 1)


def _months_between(y1: int, m1: int, y2: int, m2: int) -> int:
    return (y2 * 12 + m2) - (y1 * 12 + m1)


@router.get("/retention/heatmap", summary="Mapa de calor de retención por cohorte (real)")
async def get_retention_heatmap(db: AsyncSession = Depends(get_db)):
    try:
        clinics = (await db.execute(select(Clinic))).scalars().all()
        now = datetime.utcnow()

        groups: dict[tuple[int, int], list] = {}
        for c in clinics:
            if not c.created_at:
                continue
            groups.setdefault((c.created_at.year, c.created_at.month), []).append(c)

        cohorts = []
        max_month = 0
        for (year, month) in sorted(groups):
            group = groups[(year, month)]
            elapsed = _months_between(year, month, now.year, now.month)
            max_month = max(max_month, elapsed)
            retention = []
            for k in range(elapsed + 1):
                boundary = _add_months(year, month, k)
                kept = 0
                for c in group:
                    active_now = c.status != "churned" and not c.is_deleted
                    if active_now:
                        kept += 1
                        continue
                    last = c.last_login or c.created_at
                    if c.is_deleted and c.deleted_at and c.deleted_at < last:
                        last = c.deleted_at
                    if k == 0 or last >= boundary:
                        kept += 1
                retention.append(round(kept / len(group) * 100))
            cohorts.append({
                "cohort": f"{MESES[month - 1]} {year}",
                "users": len(group),
                "retention": retention,
            })

        return {
            "status": "success",
            "max_month": max_month,
            "generated_at": now.isoformat(),
            "method": "Cohorte por mes de alta; retenida = activa hoy o con último acceso posterior al inicio del mes N.",
            "data": cohorts,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al calcular cohortes: {e}")
