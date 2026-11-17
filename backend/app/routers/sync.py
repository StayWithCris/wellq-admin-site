"""Recomendación #7 — Sincronización programable de datos.

GET  /api/sync-status           Estado por entidad (clínicas, pacientes, SOAP, tickets…)
GET  /api/sync/config           Frecuencia configurada y próxima ejecución
PUT  /api/sync/config           Cambiar frecuencia (6, 12 o 24 h)      [platform.manage]
POST /api/sync/run              Sincronizar ahora y registrar historial [platform.manage]
GET  /api/sync/history          Últimas ejecuciones registradas

La lógica vive en `app/services/sync_service.py`; la configuración se guarda
en `platform_settings` (clave `sync_config`) y cada ejecución en `sync_runs`.

NOTA: las fuentes Mongo (`patients`, `clinicians`) se omiten si MongoDB no
está configurado; la ejecución termina en estado `partial` y el panel lo
indica. Ver `app/services/sync_sources.py`.
"""

import json
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.neon import get_db
from app.services import sync_service as svc
from .auth import require_permission
from .dependencies import get_current_user

router = APIRouter(prefix="/api", tags=["Sincronización"])


class SyncFrequencyIn(BaseModel):
    frequency_hours: Literal[6, 12, 24]


def _email(user: Optional[dict]) -> Optional[str]:
    return (user or {}).get("email")


def _user_id(user: Optional[dict]) -> Optional[str]:
    return (user or {}).get("user_id")


# ==============================================================================
# ENDPOINT: #116 - GET /api/sync-status
# Descripción: Estado de sincronización por entidad, según la frecuencia guardada
# ==============================================================================
@router.get("/sync-status", summary="Estado de sincronización por entidad")
async def get_sync_status(db: AsyncSession = Depends(get_db)):
    config = await svc.load_config(db)
    sources = await svc.collect_sources(db, config["frequency_hours"])
    return {
        "sources": sources,
        "frequency_hours": config["frequency_hours"],
        "last_run_at": config["last_run_at"],
        "next_run_at": config["next_run_at"],
    }


# ==============================================================================
# ENDPOINT: GET /api/sync/config
# Descripción: Frecuencia de sincronización y próxima ejecución programada
# ==============================================================================
@router.get("/sync/config", summary="Configuración de sincronización")
async def get_sync_config(db: AsyncSession = Depends(get_db)):
    return await svc.load_config(db)


# ==============================================================================
# ENDPOINT: PUT /api/sync/config
# Descripción: Cambiar la frecuencia a 6, 12 o 24 horas
# ==============================================================================
@router.put(
    "/sync/config",
    summary="Configurar frecuencia de sincronización",
    dependencies=[Depends(require_permission("platform.manage"))],
)
async def update_sync_config(
    body: SyncFrequencyIn,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    before = await svc.load_config(db)
    config = await svc.save_frequency(db, body.frequency_hours, _email(user))
    await svc.record_audit(
        db,
        action="UPDATE",
        entity_type="sync_config",
        entity_id="sync_config",
        entity_name="Frecuencia de sincronización",
        admin_user_id=_user_id(user),
        admin_email=_email(user),
        changes={
            "before": {"frequency_hours": before["frequency_hours"]},
            "after": {"frequency_hours": config["frequency_hours"]},
        },
    )
    return {"status": "success", "data": config}


# ==============================================================================
# ENDPOINT: POST /api/sync/run
# Descripción: Disparar una sincronización manual y registrarla en el historial
# ==============================================================================
@router.post(
    "/sync/run",
    summary="Sincronizar ahora",
    dependencies=[Depends(require_permission("platform.manage"))],
)
async def run_sync_now(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    active = await svc.find_active_run(db)
    if active is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Ya hay una sincronización en curso ({active.run_id}). "
                "Espera a que termine antes de lanzar otra."
            ),
        )

    run = await svc.perform_sync(db, trigger="manual", created_by=_email(user))
    config = await svc.load_config(db)

    await svc.record_audit(
        db,
        action="CREATE",
        entity_type="sync_run",
        entity_id=run.run_id,
        entity_name="Sincronización manual",
        admin_user_id=_user_id(user),
        admin_email=_email(user),
        changes={
            "trigger": run.trigger,
            "status": run.status,
            "entities": json.loads(run.entities or "{}"),
        },
    )

    return {
        "status": "success",
        "data": svc.serialize_run(run),
        "next_run_at": config["next_run_at"],
    }


# ==============================================================================
# ENDPOINT: GET /api/sync/history
# Descripción: Historial de ejecuciones (manuales y programadas)
# ==============================================================================
@router.get("/sync/history", summary="Historial de sincronizaciones")
async def get_sync_history(
    limit: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    runs = await svc.get_history(db, limit)
    return {"data": runs}
