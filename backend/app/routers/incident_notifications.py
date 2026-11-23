"""Mejora #19 — Notificaciones multicanal de incidentes (Email y Slack).

GET  /api/v1/incident-notifications/config                  Estado de canales      [platform.view]
GET  /api/v1/incident-notifications/deliveries              Historial de envíos    [platform.view]
POST /api/v1/incident-notifications/test                    Enviar prueba          [platform.manage]
POST /api/v1/incident-notifications/alerts/{alert_id}/dispatch
                                                            Reenviar una alerta    [platform.manage]

El envío automático no pasa por aquí: lo hace ``incident_notifier.notify_new_alert``
cuando se crea una alerta con severidad >= INCIDENT_MIN_SEVERITY (por ahora,
la alerta de fallos consecutivos de sincronización de la mejora #7).
"""

from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.neon import get_db
from app.services import incident_notifier as svc
from app.services.sync_service import record_audit
from .auth import require_permission
from .dependencies import get_current_user

router = APIRouter(prefix="/api/v1/incident-notifications", tags=["Notificaciones de incidentes"])


class TestNotificationIn(BaseModel):
    channel: Literal["email", "slack"]


class DispatchIn(BaseModel):
    channels: Optional[list[Literal["email", "slack"]]] = None


def _email(user: Optional[dict]) -> str:
    return (user or {}).get("email") or "system"


@router.get(
    "/config",
    summary="Estado de los canales de notificación",
    dependencies=[Depends(require_permission("platform.view"))],
)
async def get_config(db: AsyncSession = Depends(get_db)):
    return {"status": "success", "data": await svc.get_config(db)}


@router.get(
    "/deliveries",
    summary="Historial de envíos de notificaciones",
    dependencies=[Depends(require_permission("platform.view"))],
)
async def list_deliveries(limit: int = Query(30, ge=1, le=100), db: AsyncSession = Depends(get_db)):
    return {"status": "success", "data": await svc.list_deliveries(db, limit)}


@router.post(
    "/test",
    summary="Enviar una notificación de prueba",
    dependencies=[Depends(require_permission("platform.manage"))],
)
async def send_test(
    body: TestNotificationIn,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    delivery = await svc.send_test(db, body.channel, _email(user))
    await record_audit(
        db,
        action="CREATE",
        entity_type="incident_notification",
        entity_id=delivery.delivery_id,
        entity_name=f"Prueba de notificación ({body.channel})",
        admin_user_id=(user or {}).get("user_id"),
        admin_email=_email(user),
        changes={"after": {"channel": body.channel, "status": delivery.status}},
    )
    return {"status": "success", "data": svc.serialize_delivery(delivery)}


@router.post(
    "/alerts/{alert_id}/dispatch",
    summary="Reenviar una alerta por los canales configurados",
    dependencies=[Depends(require_permission("platform.manage"))],
)
async def dispatch_alert(
    alert_id: str,
    body: Optional[DispatchIn] = None,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    alert = await svc.get_alert(db, alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail="Alerta no encontrada")

    deliveries = await svc.dispatch_alert(
        db, alert, triggered_by=_email(user), channels=(body.channels if body else None)
    )
    await record_audit(
        db,
        action="CREATE",
        entity_type="incident_notification",
        entity_id=alert_id,
        entity_name=f"Reenvío de alerta: {alert.title}"[:255],
        admin_user_id=(user or {}).get("user_id"),
        admin_email=_email(user),
        changes={"after": {d.channel: d.status for d in deliveries}},
    )
    return {"status": "success", "data": [svc.serialize_delivery(d) for d in deliveries]}
