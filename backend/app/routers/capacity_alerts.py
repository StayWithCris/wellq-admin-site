"""Recomendación #21 — Alertas de límite de pacientes y oportunidad de upgrade.

GET /api/capacity/alerts?threshold=90
Lista clínicas activas cuyo uso de pacientes (patients_used / patients_limit)
alcanzó el umbral. Severidad: 'critical' si uso >= 100 %, 'warning' si >= umbral.
Sugiere el siguiente plan activo con precio mensual mayor al actual.
No envía correos: entrega los datos de contacto para que el equipo redacte el mensaje.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.neon import get_db
from app.models_db import Clinic, ClinicPlan, Plan
from .dependencies import get_current_user

router = APIRouter(prefix="/api/capacity", tags=["Alertas de Capacidad"])


def classify(used: int, limit: int, threshold: float) -> tuple[float, str | None]:
    """Devuelve (porcentaje de uso, severidad o None si no alcanza el umbral)."""
    if not limit or limit <= 0:
        return 0.0, None
    pct = used / limit * 100
    if pct >= 100:
        return round(pct, 1), "critical"
    if pct >= threshold:
        return round(pct, 1), "warning"
    return round(pct, 1), None


@router.get("/alerts", summary="Clínicas cerca o sobre su límite de pacientes")
async def capacity_alerts(
    threshold: float = Query(90, ge=50, le=100),
    db: AsyncSession = Depends(get_db),
    _user=Depends(get_current_user),
):
    try:
        clinics = (await db.execute(select(Clinic))).scalars().all()
        plans = {p.plan_id: p for p in (await db.execute(select(Plan))).scalars().all()}
        assigned = (await db.execute(
            select(ClinicPlan).where(ClinicPlan.effective_to.is_(None))
        )).scalars().all()
        current_plan = {}
        for a in sorted(assigned, key=lambda x: x.effective_from):
            current_plan[a.clinic_id] = a.plan_id  # el más reciente gana

        upgrades = sorted((p for p in plans.values() if p.status == "active"),
                          key=lambda p: p.monthly_price)

        items = []
        for c in clinics:
            if c.is_deleted or c.status == "churned":
                continue
            pct, severity = classify(c.patients_used or 0, c.patients_limit or 0, threshold)
            if not severity:
                continue
            cur = plans.get(current_plan.get(c.clinic_id))
            cur_price = cur.monthly_price if cur else (c.mrr or 0)
            nxt = next((p for p in upgrades if p.monthly_price > cur_price), None)
            items.append({
                "clinic_id": c.clinic_id,
                "name": c.name,
                "tier": c.tier,
                "patients_used": c.patients_used or 0,
                "patients_limit": c.patients_limit or 0,
                "usage_pct": pct,
                "severity": severity,
                "current_plan": cur.name if cur else None,
                "suggested_plan": nxt.name if nxt else None,
                "suggested_price": nxt.monthly_price if nxt else None,
                "currency": (nxt.currency if nxt else (cur.currency if cur else "USD")),
                "contact_email": c.billing_email or c.contact_email,
            })
        items.sort(key=lambda i: i["usage_pct"], reverse=True)
        return {
            "status": "success",
            "data": {
                "threshold": threshold,
                "total": len(items),
                "critical": sum(1 for i in items if i["severity"] == "critical"),
                "warning": sum(1 for i in items if i["severity"] == "warning"),
                "items": items,
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al calcular alertas de capacidad: {e}")
