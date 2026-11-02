"""Recomendación #13 — Temporizador de SLA para tickets de soporte.

GET /api/sla/tickets
Calcula, para cada ticket abierto (status distinto de "Closed"), cuánto tiempo
lleva abierto, su plazo según la categoría y si está en plazo, en riesgo o vencido.
También entrega el cumplimiento de SLA de los tickets cerrados en los últimos 30 días.

Solo lectura: no modifica tablas. Los plazos se definen en SLA_HOURS (horas corridas).
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.neon import get_db
from app.models_db import SupportTicket
from .dependencies import get_current_user

router = APIRouter(prefix="/api/sla", tags=["SLA de Soporte"])

# Plazo de resolución por categoría (horas). Ajustar aquí si Alloxentric define otros.
SLA_HOURS: dict[str, int] = {
    "Bug": 24,
    "Billing": 48,
    "Request": 72,
    "Feature": 120,
}
DEFAULT_SLA_HOURS = 72
AT_RISK_RATIO = 0.75  # desde el 75 % del plazo consumido se considera "en riesgo"


def sla_hours_for(category: str | None) -> int:
    return SLA_HOURS.get((category or "").strip(), DEFAULT_SLA_HOURS)


def _naive_utc(dt: datetime) -> datetime:
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def sla_state(reported_at: datetime, category: str | None, now: datetime) -> dict:
    """Cálculo puro (sin BD) para poder probarlo fácilmente."""
    reported_at = _naive_utc(reported_at)
    now = _naive_utc(now)
    hours = sla_hours_for(category)
    deadline = reported_at + timedelta(hours=hours)
    elapsed_h = max((now - reported_at).total_seconds() / 3600, 0.0)
    ratio = elapsed_h / hours if hours else 1.0
    if ratio >= 1:
        state = "breached"
    elif ratio >= AT_RISK_RATIO:
        state = "at_risk"
    else:
        state = "on_track"
    return {
        "sla_hours": hours,
        "deadline": deadline.isoformat() + "Z",
        "elapsed_hours": round(elapsed_h, 2),
        "remaining_hours": round(hours - elapsed_h, 2),
        "pct_used": round(ratio * 100, 1),
        "state": state,
    }


def met_sla(reported_at: datetime, closed_at: datetime, category: str | None) -> bool:
    took = (_naive_utc(closed_at) - _naive_utc(reported_at)).total_seconds() / 3600
    return took <= sla_hours_for(category)


@router.get("/tickets", summary="Estado de SLA de los tickets abiertos")
async def sla_tickets(
    db: AsyncSession = Depends(get_db),
    _user=Depends(get_current_user),
):
    try:
        now = datetime.utcnow()
        rows = (await db.execute(select(SupportTicket))).scalars().all()

        items = []
        for t in rows:
            if t.status == "Closed" or not t.reported_at:
                continue
            items.append({
                "ticket_id": t.ticket_id,
                "title": t.title,
                "category": t.category,
                "status": t.status,
                "clinic_id": t.clinic_id,
                "responder_name": t.responder_name,
                "reported_at": _naive_utc(t.reported_at).isoformat() + "Z",
                **sla_state(t.reported_at, t.category, now),
            })
        items.sort(key=lambda i: i["remaining_hours"])

        since = now - timedelta(days=30)
        closed = [t for t in rows
                  if t.status == "Closed" and t.closed_at and t.reported_at
                  and _naive_utc(t.closed_at) >= since]
        met = sum(1 for t in closed if met_sla(t.reported_at, t.closed_at, t.category))

        return {
            "status": "success",
            "data": {
                "generated_at": now.isoformat() + "Z",
                "policy": {"hours_by_category": SLA_HOURS, "default_hours": DEFAULT_SLA_HOURS,
                           "at_risk_pct": int(AT_RISK_RATIO * 100)},
                "summary": {
                    "open": len(items),
                    "on_track": sum(1 for i in items if i["state"] == "on_track"),
                    "at_risk": sum(1 for i in items if i["state"] == "at_risk"),
                    "breached": sum(1 for i in items if i["state"] == "breached"),
                    "closed_30d": len(closed),
                    "compliance_30d_pct": round(met / len(closed) * 100, 1) if closed else None,
                },
                "items": items,
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al calcular SLA: {e}")
