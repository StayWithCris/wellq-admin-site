"""Recomendación #11 — Reportes programados (área Analítica).

GET    /api/v1/reports/meta                               catálogo y canales            [analytics.view]
GET    /api/v1/reports/schedules                          lista de reportes             [analytics.view]
POST   /api/v1/reports/schedules                          crear                         [gestión]
PUT    /api/v1/reports/schedules/{report_id}              editar                        [gestión]
PATCH  /api/v1/reports/schedules/{report_id}/toggle       activar / pausar              [gestión]
DELETE /api/v1/reports/schedules/{report_id}              eliminar                      [gestión]
GET    /api/v1/reports/schedules/{report_id}/preview      vista previa con datos reales [analytics.view]
GET    /api/v1/reports/schedules/{report_id}/export.csv   descarga CSV                  [analytics.view]
POST   /api/v1/reports/schedules/{report_id}/run          enviar ahora                  [gestión]
GET    /api/v1/reports/runs                               historial de envíos           [analytics.view]
POST   /api/v1/reports/dispatch-due                       despachar vencidos            [X-Scheduler-Secret o gestión]

"Gestión" (``require_reports_manage``) = analytics.view + (platform.manage o
settings.manage), o rol super_admin. No se crea un permiso nuevo porque no
existe en la base de producción y rompería los roles actuales.
"""

import hmac
from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.neon import get_db
from app.services import report_builder as builder
from app.services import scheduled_reports_service as svc
from app.services.sync_service import record_audit
from .auth import decode_token, require_permission
from .dependencies import get_current_user

router = APIRouter(prefix="/api/v1/reports", tags=["Reportes programados"])

VIEW_PERMISSION = "analytics.view"
MANAGE_PERMISSIONS = ("platform.manage", "settings.manage")

_optional_bearer = HTTPBearer(auto_error=False)


def _can_manage(user: dict) -> bool:
    perms = user.get("permissions") or []
    if user.get("role") == "super_admin":
        return True
    return VIEW_PERMISSION in perms and any(p in perms for p in MANAGE_PERMISSIONS)


def require_reports_manage(user: dict = Depends(get_current_user)) -> dict:
    """Permite gestionar reportes: super_admin, o analytics.view + platform.manage/settings.manage."""
    if not _can_manage(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acceso denegado: se requiere analytics.view y platform.manage o settings.manage.",
        )
    return user


class ScheduleIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: Optional[str] = Field(default=None, max_length=1000)
    frequency: Literal["daily", "weekly", "monthly"]
    day_of_week: Optional[int] = Field(default=None, ge=0, le=6)
    day_of_month: Optional[int] = Field(default=None, ge=1, le=28)
    send_hour_utc: int = Field(default=12, ge=0, le=23)
    sections: list[str] = Field(min_length=1)
    channels: list[Literal["email", "slack"]] = Field(min_length=1)
    recipients: list[str] = Field(default_factory=list, max_length=svc.MAX_RECIPIENTS)
    include_csv: bool = True


class ToggleIn(BaseModel):
    is_active: bool


def _email(user: Optional[dict]) -> str:
    return (user or {}).get("email") or "system"


def _validated(body: ScheduleIn) -> dict:
    try:
        return svc.validate_schedule(body.model_dump())
    except svc.ReportValidationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))


async def _get_or_404(db: AsyncSession, report_id: str):
    report = await svc.get_report(db, report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Reporte no encontrado")
    return report


async def _audit(db, user, action, report, changes=None):
    await record_audit(
        db,
        action=action,
        entity_type="scheduled_report",
        entity_id=report.report_id,
        entity_name=report.name,
        admin_user_id=(user or {}).get("user_id"),
        admin_email=_email(user),
        changes=changes,
    )


# ── Lectura ─────────────────────────────────────────────────────────────────

@router.get("/meta", dependencies=[Depends(require_permission(VIEW_PERMISSION))])
async def get_meta():
    return {"status": "success", "data": svc.meta()}


@router.get("/schedules", dependencies=[Depends(require_permission(VIEW_PERMISSION))])
async def list_schedules(db: AsyncSession = Depends(get_db)):
    return {"status": "success", "data": [svc.serialize_report(r) for r in await svc.list_reports(db)]}


@router.get("/schedules/{report_id}/preview", dependencies=[Depends(require_permission(VIEW_PERMISSION))])
async def preview(report_id: str, db: AsyncSession = Depends(get_db)):
    report = await _get_or_404(db, report_id)
    payload = await builder.build_payload(db, svc._list(report.sections), frequency=report.frequency)
    return {
        "status": "success",
        "data": {
            "report_id": report.report_id,
            "period_label": payload["period_label"],
            "metrics": payload,
            "html_preview": builder.render_html(report.name, payload),
        },
    }


@router.get("/schedules/{report_id}/export.csv", dependencies=[Depends(require_permission(VIEW_PERMISSION))])
async def export_csv(report_id: str, db: AsyncSession = Depends(get_db)):
    report = await _get_or_404(db, report_id)
    payload = await builder.build_payload(db, svc._list(report.sections), frequency=report.frequency)
    filename = svc.csv_filename(report)
    return Response(
        content=builder.render_csv(payload),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/runs", dependencies=[Depends(require_permission(VIEW_PERMISSION))])
async def list_runs(report_id: Optional[str] = None, limit: int = Query(20, ge=1, le=100),
                    db: AsyncSession = Depends(get_db)):
    return {"status": "success", "data": [svc.serialize_run(r) for r in await svc.list_runs(db, report_id, limit)]}


# ── Gestión ─────────────────────────────────────────────────────────────────

@router.post("/schedules", status_code=status.HTTP_201_CREATED)
async def create_schedule(body: ScheduleIn, db: AsyncSession = Depends(get_db),
                          user: dict = Depends(require_reports_manage)):
    values = _validated(body)
    report = svc.new_report(values, _email(user))
    db.add(report)
    await db.commit()
    await _audit(db, user, "CREATE", report, {"after": svc.serialize_report(report)})
    return {"status": "success", "data": svc.serialize_report(report)}


@router.put("/schedules/{report_id}")
async def update_schedule(report_id: str, body: ScheduleIn, db: AsyncSession = Depends(get_db),
                          user: dict = Depends(require_reports_manage)):
    report = await _get_or_404(db, report_id)
    before = svc.serialize_report(report)
    svc.apply_schedule(report, _validated(body))
    report.updated_by = _email(user)
    report.updated_at = datetime.utcnow()
    db.add(report)
    await db.commit()
    await _audit(db, user, "UPDATE", report, {"before": before, "after": svc.serialize_report(report)})
    return {"status": "success", "data": svc.serialize_report(report)}


@router.patch("/schedules/{report_id}/toggle")
async def toggle_schedule(report_id: str, body: ToggleIn, db: AsyncSession = Depends(get_db),
                          user: dict = Depends(require_reports_manage)):
    report = await _get_or_404(db, report_id)
    before = report.is_active
    report.is_active = body.is_active
    report.next_run_at = svc.next_run_for(report) if body.is_active else None
    report.updated_by = _email(user)
    report.updated_at = datetime.utcnow()
    db.add(report)
    await db.commit()
    await _audit(db, user, "UPDATE", report, {"before": {"is_active": before}, "after": {"is_active": body.is_active}})
    return {"status": "success", "data": svc.serialize_report(report)}


@router.delete("/schedules/{report_id}")
async def delete_schedule(report_id: str, db: AsyncSession = Depends(get_db),
                          user: dict = Depends(require_reports_manage)):
    report = await _get_or_404(db, report_id)
    snapshot = svc.serialize_report(report)
    await db.delete(report)
    await db.commit()
    await _audit(db, user, "DELETE", report, {"before": snapshot})
    return {"status": "success", "deleted_id": report_id}


@router.post("/schedules/{report_id}/run")
async def run_now(report_id: str, db: AsyncSession = Depends(get_db),
                  user: dict = Depends(require_reports_manage)):
    report = await _get_or_404(db, report_id)
    run = await svc.execute_report(db, report, trigger="manual", triggered_by=_email(user))
    await _audit(db, user, "CREATE", report, {"after": {"run_id": run.run_id, "status": run.status}})
    return {"status": "success", "data": svc.serialize_run(run)}


@router.post("/dispatch-due")
async def dispatch_due(
    db: AsyncSession = Depends(get_db),
    x_scheduler_secret: Optional[str] = Header(default=None),
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_optional_bearer),
):
    """Para Cloud Scheduler (X-Scheduler-Secret) o un admin con permiso de gestión."""
    secret = settings.reports_cron_secret
    if secret and x_scheduler_secret and hmac.compare_digest(x_scheduler_secret.encode(), secret.encode()):
        result = await svc.dispatch_due_reports(db, trigger="cron_webhook", triggered_by="cloud-scheduler")
        return {"status": "success", "data": result}

    if x_scheduler_secret:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Secreto de scheduler inválido.")
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No autenticado")

    payload = decode_token(credentials.credentials)
    if payload.get("type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido")
    require_reports_manage(payload)
    result = await svc.dispatch_due_reports(db, trigger="manual", triggered_by=_email(payload))
    return {"status": "success", "data": result}
