"""Recomendación #16 — Centro de auditoría global navegable.

GET /api/audit/logs
Explora la tabla append-only action_logs con filtros (acción, tipo de entidad,
administrador, texto de entidad, rango de fechas) y paginación. Solo super admin.
La exportación a CSV se arma en el frontend con los resultados filtrados.
"""
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, desc, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.neon import get_db
from app.models_db import ActionLog
from .dependencies import require_super_admin
from .clinic_audit import _diffs

router = APIRouter(prefix="/api/audit", tags=["Centro de Auditoría"])


def _parse_day(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value[:10])
    except ValueError:
        return None


@router.get("/logs", summary="Explorar el registro de auditoría global")
async def list_audit_logs(
    action: str | None = Query(None),
    entity_type: str | None = Query(None),
    admin_email: str | None = Query(None),
    q: str | None = Query(None, description="Texto en nombre o id de la entidad"),
    date_from: str | None = Query(None, description="YYYY-MM-DD"),
    date_to: str | None = Query(None, description="YYYY-MM-DD"),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    _admin: dict = Depends(require_super_admin),
):
    conditions = []
    if action:
        conditions.append(ActionLog.action == action.upper())
    if entity_type:
        conditions.append(ActionLog.entity_type == entity_type.lower())
    if admin_email:
        conditions.append(ActionLog.admin_email.ilike(f"%{admin_email}%"))
    if q:
        like = f"%{q}%"
        conditions.append(or_(ActionLog.entity_name.ilike(like), ActionLog.entity_id.ilike(like)))
    start = _parse_day(date_from)
    if start:
        conditions.append(ActionLog.created_at >= start)
    end = _parse_day(date_to)
    if end:
        conditions.append(ActionLog.created_at < end + timedelta(days=1))

    total = (await db.execute(select(func.count()).select_from(ActionLog).where(*conditions))).scalar_one()
    rows = (await db.execute(
        select(ActionLog).where(*conditions)
        .order_by(desc(ActionLog.created_at))
        .offset((page - 1) * page_size).limit(page_size)
    )).scalars().all()

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "data": [
            {
                "log_id": r.log_id,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "admin_email": r.admin_email,
                "action": r.action,
                "entity_type": r.entity_type,
                "entity_id": r.entity_id,
                "entity_name": r.entity_name,
                "changes": _diffs(r.action, r.changes),
            }
            for r in rows
        ],
    }
