"""Recomendación #7 — Sincronización programable de datos.

GET  /api/v1/sync-status       Estado por entidad (retrocompatible en /api/sync-status)
GET  /api/v1/sync/config       Frecuencia configurada y próxima ejecución
PUT  /api/v1/sync/config       Cambiar frecuencia (6, 12 o 24 h)      [platform.manage]
POST /api/v1/sync/run          Sincronizar ahora y registrar historial [platform.manage]
GET  /api/v1/sync/history      Últimas ejecuciones registradas

NOTA: todos los endpoints mantienen retrocompatibilidad con el prefijo /api/
original mediante un router legacy incluido en main.py.

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

# ── Router principal con versionado /api/v1 ─────────────────────────────────
router_v1 = APIRouter(prefix="/api/v1", tags=["Sincronización"])

# ── Router legacy sin version para retrocompatibilidad ──────────────────────
router_legacy = APIRouter(prefix="/api", tags=["Sincronización (legacy)"])

# Router combinado que se expone desde main.py
router = APIRouter()


class SyncFrequencyIn(BaseModel):
    frequency_hours: Literal[6, 12, 24]


def _email(user: Optional[dict]) -> Optional[str]:
    return (user or {}).get("email")


def _user_id(user: Optional[dict]) -> Optional[str]:
    return (user or {}).get("user_id")


# ==============================================================================
# Handlers compartidos (reutilizados por v1 y legacy)
# ==============================================================================

async def _handle_sync_status(db: AsyncSession):
    config = await svc.load_config(db)
    sources = await svc.collect_sources(db, config["frequency_hours"])
    failure_alert = await svc.get_failure_alert_status(db)
    return {
        "sources": sources,
        "frequency_hours": config["frequency_hours"],
        "last_run_at": config["last_run_at"],
        "next_run_at": config["next_run_at"],
        "failure_alert": failure_alert,
    }


async def _handle_get_config(db: AsyncSession):
    return await svc.load_config(db)


async def _handle_update_config(body: SyncFrequencyIn, db: AsyncSession, user: dict):
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

    # Reprogramar el APScheduler para que use la nueva frecuencia
    try:
        from app.services.sync_scheduler import reschedule
        reschedule(body.frequency_hours)
    except Exception:
        pass  # El scheduler puede no estar activo en tests

    return {"status": "success", "data": config}


async def _handle_run_sync(db: AsyncSession, user: dict):
    active = await svc.find_active_run(db)
    if active is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Ya hay una sincronización en curso ({active.run_id}). "
                "Espera a que termine antes de lanzar otra."
            ),
        )

    if not await svc.try_acquire_sync_lock(db):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ya hay una sincronización en curso (bloqueo distribuido activo en otro worker).",
        )

    try:
        run = await svc.perform_sync(db, trigger="manual", created_by=_email(user), with_lock=False)
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
    finally:
        await svc.release_sync_lock(db)


async def _handle_history(limit: int, db: AsyncSession):
    runs = await svc.get_history(db, limit)
    return {"data": runs}


# ==============================================================================
# ENDPOINTS v1 (/api/v1/sync-status, /api/v1/sync/*)
# ==============================================================================

@router_v1.get("/sync-status", summary="Estado de sincronización por entidad")
async def get_sync_status_v1(db: AsyncSession = Depends(get_db)):
    return await _handle_sync_status(db)


@router_v1.get("/sync/config", summary="Configuración de sincronización")
async def get_sync_config_v1(db: AsyncSession = Depends(get_db)):
    return await _handle_get_config(db)


@router_v1.put(
    "/sync/config",
    summary="Configurar frecuencia de sincronización",
    dependencies=[Depends(require_permission("platform.manage"))],
)
async def update_sync_config_v1(
    body: SyncFrequencyIn,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    return await _handle_update_config(body, db, user)


@router_v1.post(
    "/sync/run",
    summary="Sincronizar ahora",
    dependencies=[Depends(require_permission("platform.manage"))],
)
async def run_sync_now_v1(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    return await _handle_run_sync(db, user)


@router_v1.get("/sync/history", summary="Historial de sincronizaciones")
async def get_sync_history_v1(
    limit: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    return await _handle_history(limit, db)


# ==============================================================================
# ENDPOINTS legacy (/api/sync-status, /api/sync/*) — retrocompatibilidad 100%
# ==============================================================================

@router_legacy.get("/sync-status", summary="[Legacy] Estado de sincronización por entidad")
async def get_sync_status_legacy(db: AsyncSession = Depends(get_db)):
    return await _handle_sync_status(db)


@router_legacy.get("/sync/config", summary="[Legacy] Configuración de sincronización")
async def get_sync_config_legacy(db: AsyncSession = Depends(get_db)):
    return await _handle_get_config(db)


@router_legacy.put(
    "/sync/config",
    summary="[Legacy] Configurar frecuencia de sincronización",
    dependencies=[Depends(require_permission("platform.manage"))],
)
async def update_sync_config_legacy(
    body: SyncFrequencyIn,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    return await _handle_update_config(body, db, user)


@router_legacy.post(
    "/sync/run",
    summary="[Legacy] Sincronizar ahora",
    dependencies=[Depends(require_permission("platform.manage"))],
)
async def run_sync_now_legacy(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    return await _handle_run_sync(db, user)


@router_legacy.get("/sync/history", summary="[Legacy] Historial de sincronizaciones")
async def get_sync_history_legacy(
    limit: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    return await _handle_history(limit, db)


# Incluir ambos routers en el router combinado
router.include_router(router_v1)
router.include_router(router_legacy)
