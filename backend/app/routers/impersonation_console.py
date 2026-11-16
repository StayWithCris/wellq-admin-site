"""Recomendación #14 — Consola de impersonación.

GET  /api/impersonation/summary                        Resumen: activas ahora, últimas 24 h y 30 días
GET  /api/impersonation/sessions                       Historial filtrable y paginado
POST /api/impersonation/sessions/{audit_log_id}/revoke Cortar una sesión activa
POST /api/impersonation/revoke-active                  Cortar todas las activas (opcional: de una clínica)

La sesión se crea en POST /api/clinics/{id}/impersonate (clinics.py). Esta consola la
observa y puede revocarla: el portal de clínica rechaza las sesiones con revoked_at.
Nunca se devuelve el token de la sesión. Solo super administrador.
"""
import json
import uuid
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.neon import get_db
from app.models_db import ActionLog, ImpersonateAuditLog
from app.services import impersonation as svc
from .dependencies import require_super_admin

router = APIRouter(prefix="/api/impersonation", tags=["Consola de Impersonación"])


def _parse_day(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value[:10])
    except ValueError:
        return None


def _iso(v: Optional[datetime]) -> Optional[str]:
    return v.isoformat() + "Z" if v else None


def _serialize(r: ImpersonateAuditLog, now: datetime) -> dict:
    status = svc.session_status(r.expires_at, r.revoked_at, now)
    return {
        "id": r.audit_log_id,
        "clinicId": r.clinic_id,
        "clinicName": r.clinic_name,
        "adminEmail": r.admin_email,
        "reason": r.reason,
        "status": status,
        "startedAt": _iso(r.created_at),
        "expiresAt": _iso(r.expires_at),
        "revokedAt": _iso(r.revoked_at),
        "minutesRemaining": svc.minutes_remaining(r.expires_at, now) if status == svc.ACTIVE else 0,
    }


def _status_condition(status: str, now: datetime):
    if status == svc.REVOKED:
        return ImpersonateAuditLog.revoked_at.is_not(None)
    if status == svc.EXPIRED:
        return and_(ImpersonateAuditLog.revoked_at.is_(None), ImpersonateAuditLog.expires_at <= now)
    if status == svc.ACTIVE:
        return and_(ImpersonateAuditLog.revoked_at.is_(None), ImpersonateAuditLog.expires_at > now)
    raise HTTPException(status_code=422, detail="status debe ser active, expired o revoked.")


def _log(db: AsyncSession, admin: dict, entity_id: str, name: str, changes: dict) -> None:
    db.add(ActionLog(
        log_id=f"log-{uuid.uuid4().hex[:10]}",
        admin_user_id=admin.get("user_id", "unknown"),
        admin_email=admin.get("email", "unknown"),
        action="UPDATE",
        entity_type="impersonation_session",
        entity_id=entity_id,
        entity_name=name,
        changes=json.dumps(changes, ensure_ascii=False, default=str),
    ))


@router.get("/summary", summary="Resumen de sesiones de soporte")
async def summary(db: AsyncSession = Depends(get_db), _admin: dict = Depends(require_super_admin)):
    now = datetime.utcnow()
    since_30d = now - timedelta(days=30)

    async def count(*conds) -> int:
        return (await db.execute(select(func.count()).select_from(ImpersonateAuditLog).where(*conds))).scalar_one()

    active = await count(_status_condition(svc.ACTIVE, now))
    last_24h = await count(ImpersonateAuditLog.created_at >= now - timedelta(hours=24))
    last_30d = await count(ImpersonateAuditLog.created_at >= since_30d)
    revoked_30d = await count(ImpersonateAuditLog.revoked_at.is_not(None), ImpersonateAuditLog.created_at >= since_30d)

    recent = (await db.execute(
        select(ImpersonateAuditLog).where(ImpersonateAuditLog.created_at >= since_30d)
    )).scalars().all()
    return {"status": "success", "data": {
        "activeNow": active,
        "last24h": last_24h,
        "last30d": last_30d,
        "revoked30d": revoked_30d,
        "topAdmins": svc.top_counts((r.admin_email for r in recent), 5),
        "topClinics": svc.top_counts((r.clinic_name for r in recent), 5),
    }}


@router.get("/sessions", summary="Historial de sesiones de soporte")
async def list_sessions(
    status: Optional[str] = Query(None, description="active | expired | revoked"),
    clinic: Optional[str] = Query(None, description="Texto en nombre o id de la clínica"),
    admin_email: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None, description="YYYY-MM-DD"),
    date_to: Optional[str] = Query(None, description="YYYY-MM-DD"),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    _admin: dict = Depends(require_super_admin),
):
    now = datetime.utcnow()
    conds = []
    if status:
        conds.append(_status_condition(status.lower(), now))
    if clinic:
        like = f"%{clinic}%"
        conds.append(ImpersonateAuditLog.clinic_name.ilike(like) | ImpersonateAuditLog.clinic_id.ilike(like))
    if admin_email:
        conds.append(ImpersonateAuditLog.admin_email.ilike(f"%{admin_email}%"))
    start = _parse_day(date_from)
    if start:
        conds.append(ImpersonateAuditLog.created_at >= start)
    end = _parse_day(date_to)
    if end:
        conds.append(ImpersonateAuditLog.created_at < end + timedelta(days=1))

    total = (await db.execute(select(func.count()).select_from(ImpersonateAuditLog).where(*conds))).scalar_one()
    rows = (await db.execute(
        select(ImpersonateAuditLog).where(*conds)
        .order_by(desc(ImpersonateAuditLog.created_at))
        .offset((page - 1) * page_size).limit(page_size)
    )).scalars().all()
    return {"total": total, "page": page, "page_size": page_size, "data": [_serialize(r, now) for r in rows]}


@router.post("/sessions/{audit_log_id}/revoke", summary="Revocar una sesión de soporte activa")
async def revoke_session(audit_log_id: str = Path(...), db: AsyncSession = Depends(get_db),
                         admin: dict = Depends(require_super_admin)):
    now = datetime.utcnow()
    r = (await db.execute(
        select(ImpersonateAuditLog).where(ImpersonateAuditLog.audit_log_id == audit_log_id)
    )).scalars().first()
    if not r:
        raise HTTPException(status_code=404, detail="Sesión no encontrada")
    if svc.session_status(r.expires_at, r.revoked_at, now) != svc.ACTIVE:
        raise HTTPException(status_code=409, detail="La sesión ya no está activa.")
    r.revoked_at = now
    db.add(r)
    _log(db, admin, r.audit_log_id, r.clinic_name,
         {"before": {"revoked_at": None}, "after": {"revoked_at": now}, "admin_session": r.admin_email})
    await db.commit()
    return {"status": "success", "data": _serialize(r, now)}


class RevokeActiveBody(BaseModel):
    clinic_id: Optional[str] = Field(None, max_length=60)


@router.post("/revoke-active", summary="Revocar todas las sesiones de soporte activas")
async def revoke_active(body: RevokeActiveBody, db: AsyncSession = Depends(get_db),
                        admin: dict = Depends(require_super_admin)):
    now = datetime.utcnow()
    conds = [_status_condition(svc.ACTIVE, now)]
    if body.clinic_id:
        conds.append(ImpersonateAuditLog.clinic_id == body.clinic_id)
    rows = (await db.execute(select(ImpersonateAuditLog).where(*conds))).scalars().all()
    for r in rows:
        r.revoked_at = now
        db.add(r)
    if rows:
        _log(db, admin, "bulk", "Revocación masiva de sesiones de soporte",
             {"after": {"revoked": [r.audit_log_id for r in rows], "clinic_id": body.clinic_id}})
    await db.commit()
    return {"status": "success", "data": {"revoked": len(rows)}}
