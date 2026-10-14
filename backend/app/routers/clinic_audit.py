"""Recomendación #15 — Historial de auditoría de cambios por clínica.

GET /api/clinics/{clinic_id}/audit-logs
Lee la tabla append-only action_logs (ya poblada por clinics.py en cada
creación, edición, baja o reactivación) y devuelve la línea de tiempo de la clínica,
con los campos que cambiaron (antes -> después). Solo super admin.
"""
import json
from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.neon import get_db
from app.models_db import ActionLog
from .dependencies import require_super_admin

router = APIRouter(prefix="/api/clinics", tags=["Auditoría de Clínicas"])


def _diffs(action: str, raw: str | None) -> list[dict]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return []
    before = data.get("before") or {}
    after = data.get("after") or {}
    out = []
    for field in list(dict.fromkeys([*before.keys(), *after.keys()])):
        b, a = before.get(field), after.get(field)
        if action == "UPDATE" and b == a:
            continue
        out.append({"field": field, "before": b, "after": a})
    return out


@router.get("/{clinic_id}/audit-logs", summary="Historial de cambios de una clínica")
async def get_clinic_audit_logs(
    clinic_id: str = Path(...),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _admin: dict = Depends(require_super_admin),
):
    rows = (await db.execute(
        select(ActionLog)
        .where(ActionLog.entity_type == "clinic", ActionLog.entity_id == clinic_id)
        .order_by(desc(ActionLog.created_at))
        .limit(limit)
    )).scalars().all()

    return {
        "clinic_id": clinic_id,
        "total": len(rows),
        "data": [
            {
                "log_id": r.log_id,
                "action": r.action,
                "admin_email": r.admin_email,
                "entity_name": r.entity_name,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "changes": _diffs(r.action, r.changes),
            }
            for r in rows
        ],
    }
