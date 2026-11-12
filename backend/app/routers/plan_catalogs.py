"""Recomendación #2 — Categorización y catálogos de planes comerciales.

GET    /api/plan-catalogs                     Catálogos con su cantidad de planes
POST   /api/plan-catalogs                     Crear catálogo
PATCH  /api/plan-catalogs/{catalog_id}        Editar nombre, descripción, color o estado
PUT    /api/plan-catalogs/{catalog_id}/plans  Definir qué planes pertenecen al catálogo
DELETE /api/plan-catalogs/{catalog_id}        Eliminar catálogo (sus planes quedan sin catálogo)

Desactivar un catálogo (is_active = false) oculta de una vez todos sus planes:
GET /api/plans?availableOnly=true ya no los entrega y no se pueden asignar a clínicas.
Los planes no se archivan ni se modifican, así que reactivar el catálogo los devuelve.
"""
import json
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.neon import get_db
from app.models_db import ActionLog, Plan, PlanCatalog
from .auth import require_permission
from .dependencies import get_current_user

router = APIRouter(prefix="/api/plan-catalogs", tags=["Catálogos de Planes"])

COLORS = {"slate", "cyan", "green", "amber", "purple", "rose", "blue"}


def _serialize(c: PlanCatalog, plan_ids: list[str]) -> dict:
    return {
        "id": c.catalog_id,
        "name": c.name,
        "description": c.description or "",
        "color": c.color or "slate",
        "isActive": bool(c.is_active),
        "planIds": plan_ids,
        "planCount": len(plan_ids),
        "createdAt": c.created_at.isoformat() + "Z" if c.created_at else None,
        "updatedAt": c.updated_at.isoformat() + "Z" if c.updated_at else None,
    }


async def _plans_by_catalog(db: AsyncSession) -> dict[str, list[str]]:
    rows = (await db.execute(
        select(Plan).where(Plan.catalog_id.is_not(None), Plan.status != "archived")
    )).scalars().all()
    out: dict[str, list[str]] = {}
    for p in rows:
        out.setdefault(p.catalog_id, []).append(p.plan_id)
    return out


async def _get_catalog(db: AsyncSession, catalog_id: str) -> PlanCatalog:
    c = (await db.execute(select(PlanCatalog).where(PlanCatalog.catalog_id == catalog_id))).scalars().first()
    if not c:
        raise HTTPException(status_code=404, detail="Catálogo no encontrado")
    return c


async def _name_taken(db: AsyncSession, name: str, exclude_id: str | None = None) -> bool:
    rows = (await db.execute(
        select(PlanCatalog).where(func.lower(PlanCatalog.name) == name.lower())
    )).scalars().all()
    return any(r.catalog_id != exclude_id for r in rows)


def _log(db: AsyncSession, user: dict, action: str, c: PlanCatalog, changes: dict) -> None:
    db.add(ActionLog(
        log_id=f"log-{uuid.uuid4().hex[:10]}",
        admin_user_id=user.get("user_id", "unknown"),
        admin_email=user.get("email", "unknown"),
        action=action,
        entity_type="plan_catalog",
        entity_id=c.catalog_id,
        entity_name=c.name,
        changes=json.dumps(changes, ensure_ascii=False, default=str),
    ))


@router.get("", summary="Listar catálogos de planes")
async def list_catalogs(db: AsyncSession = Depends(get_db), _user=Depends(get_current_user)):
    catalogs = (await db.execute(select(PlanCatalog))).scalars().all()
    by_cat = await _plans_by_catalog(db)
    data = [_serialize(c, by_cat.get(c.catalog_id, [])) for c in sorted(catalogs, key=lambda c: c.name.lower())]
    return {"status": "success", "data": data}


class CatalogBody(BaseModel):
    name: str = Field(..., min_length=2, max_length=60)
    description: Optional[str] = Field(None, max_length=300)
    color: str = "slate"
    isActive: bool = True


class CatalogPatch(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=60)
    description: Optional[str] = Field(None, max_length=300)
    color: Optional[str] = None
    isActive: Optional[bool] = None


@router.post("", summary="Crear un catálogo", status_code=201,
             dependencies=[Depends(require_permission("plans.manage"))])
async def create_catalog(body: CatalogBody, db: AsyncSession = Depends(get_db),
                         user: dict = Depends(get_current_user)):
    name = body.name.strip()
    if await _name_taken(db, name):
        raise HTTPException(status_code=409, detail=f"Ya existe un catálogo llamado '{name}'.")
    if body.color not in COLORS:
        raise HTTPException(status_code=422, detail=f"Color inválido. Usa uno de: {sorted(COLORS)}")
    c = PlanCatalog(
        catalog_id=f"pcat-{uuid.uuid4().hex[:8]}",
        name=name,
        description=(body.description or "").strip() or None,
        color=body.color,
        is_active=body.isActive,
    )
    db.add(c)
    _log(db, user, "CREATE", c, {"after": _serialize(c, [])})
    await db.commit()
    return {"status": "success", "data": _serialize(c, [])}


@router.patch("/{catalog_id}", summary="Editar o activar/desactivar un catálogo",
              dependencies=[Depends(require_permission("plans.manage"))])
async def update_catalog(body: CatalogPatch, catalog_id: str = Path(...),
                         db: AsyncSession = Depends(get_db), user: dict = Depends(get_current_user)):
    c = await _get_catalog(db, catalog_id)
    before = {"name": c.name, "description": c.description, "color": c.color, "isActive": c.is_active}

    if body.name is not None:
        name = body.name.strip()
        if await _name_taken(db, name, exclude_id=c.catalog_id):
            raise HTTPException(status_code=409, detail=f"Ya existe un catálogo llamado '{name}'.")
        c.name = name
    if body.description is not None:
        c.description = body.description.strip() or None
    if body.color is not None:
        if body.color not in COLORS:
            raise HTTPException(status_code=422, detail=f"Color inválido. Usa uno de: {sorted(COLORS)}")
        c.color = body.color
    if body.isActive is not None:
        c.is_active = body.isActive
    c.updated_at = datetime.utcnow()
    db.add(c)

    after = {"name": c.name, "description": c.description, "color": c.color, "isActive": c.is_active}
    _log(db, user, "UPDATE", c, {"before": before, "after": after})
    await db.commit()
    by_cat = await _plans_by_catalog(db)
    return {"status": "success", "data": _serialize(c, by_cat.get(c.catalog_id, []))}


class CatalogPlansBody(BaseModel):
    planIds: list[str]


@router.put("/{catalog_id}/plans", summary="Definir los planes del catálogo",
            dependencies=[Depends(require_permission("plans.manage"))])
async def set_catalog_plans(body: CatalogPlansBody, catalog_id: str = Path(...),
                            db: AsyncSession = Depends(get_db), user: dict = Depends(get_current_user)):
    c = await _get_catalog(db, catalog_id)
    wanted = set(body.planIds)
    plans = (await db.execute(select(Plan))).scalars().all()
    known = {p.plan_id for p in plans}
    missing = wanted - known
    if missing:
        raise HTTPException(status_code=422, detail=f"Planes inexistentes: {sorted(missing)}")

    before = sorted(p.plan_id for p in plans if p.catalog_id == c.catalog_id)
    now = datetime.utcnow()
    for p in plans:
        if p.plan_id in wanted and p.catalog_id != c.catalog_id:
            p.catalog_id = c.catalog_id          # un plan pertenece a un solo catálogo
            p.updated_at = now
            db.add(p)
        elif p.plan_id not in wanted and p.catalog_id == c.catalog_id:
            p.catalog_id = None
            p.updated_at = now
            db.add(p)

    _log(db, user, "UPDATE", c, {"before": {"planIds": before}, "after": {"planIds": sorted(wanted)}})
    await db.commit()
    by_cat = await _plans_by_catalog(db)
    return {"status": "success", "data": _serialize(c, by_cat.get(c.catalog_id, []))}


@router.delete("/{catalog_id}", summary="Eliminar un catálogo (los planes quedan sin catálogo)",
               dependencies=[Depends(require_permission("plans.manage"))])
async def delete_catalog(catalog_id: str = Path(...), db: AsyncSession = Depends(get_db),
                         user: dict = Depends(get_current_user)):
    c = await _get_catalog(db, catalog_id)
    plans = (await db.execute(select(Plan).where(Plan.catalog_id == c.catalog_id))).scalars().all()
    for p in plans:
        p.catalog_id = None
        db.add(p)
    _log(db, user, "HARD_DELETE", c, {"before": _serialize(c, [p.plan_id for p in plans])})
    await db.delete(c)
    await db.commit()
    return {"status": "success", "data": {"id": catalog_id, "releasedPlans": len(plans)}}
