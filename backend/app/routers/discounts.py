"""Recomendación #4 — Gestión de descuentos.

GET    /api/discounts                          Lista con estado, usos y resumen
POST   /api/discounts                          Crear descuento                 [plans.manage]
PATCH  /api/discounts/{discount_id}            Editar o activar/desactivar     [plans.manage]
DELETE /api/discounts/{discount_id}            Eliminar (solo si nunca se usó) [plans.manage]
GET    /api/discounts/{discount_id}/preview    Precio resultante para un plan
GET    /api/discounts/clinics/{clinic_id}      Descuentos de una clínica
POST   /api/discounts/{discount_id}/apply      Aplicar a una clínica           [plans.manage]
POST   /api/discounts/assignments/{assignment_id}/revoke   Quitar a una clínica [plans.manage]

Importante: este módulo gestiona y registra los descuentos y el precio pactado por clínica.
Todavía NO modifica el cálculo del MRR ni emite facturas: el "impacto mensual" que muestra
es informativo. La lógica de precios vive en app/services/discounts.py.
"""
import json
import uuid
from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.neon import get_db
from app.models_db import ActionLog, Clinic, ClinicDiscount, ClinicPlan, Discount, Plan
from app.services import discounts as svc
from .auth import require_permission
from .dependencies import get_current_user

router = APIRouter(prefix="/api/discounts", tags=["Descuentos"])

REASONS = {
    "inactive": "El descuento está desactivado.",
    "scheduled": "El descuento aún no comienza su vigencia.",
    "expired": "El descuento ya terminó su vigencia.",
    "exhausted": "El descuento alcanzó su tope de usos.",
    "plan_mismatch": "El descuento no corresponde al plan vigente de la clínica.",
}


def _iso(v: Optional[datetime]) -> Optional[str]:
    return v.isoformat() + "Z" if v else None


def _as_dict(d: Discount) -> dict:
    return {"is_active": d.is_active, "starts_at": d.starts_at, "ends_at": d.ends_at,
            "max_redemptions": d.max_redemptions, "redemptions": d.redemptions or 0, "plan_id": d.plan_id}


def _serialize(d: Discount, plan_names: dict[str, str], now: datetime) -> dict:
    return {
        "id": d.discount_id,
        "code": d.code,
        "name": d.name,
        "description": d.description or "",
        "kind": d.kind,
        "value": d.value,
        "planId": d.plan_id,
        "planName": plan_names.get(d.plan_id) if d.plan_id else None,
        "durationMonths": d.duration_months,
        "startsAt": _iso(d.starts_at),
        "endsAt": _iso(d.ends_at),
        "maxRedemptions": d.max_redemptions,
        "redemptions": d.redemptions or 0,
        "isActive": bool(d.is_active),
        "status": svc.status_of(_as_dict(d), now),
        "createdAt": _iso(d.created_at),
    }


def _serialize_assignment(a: ClinicDiscount, clinic_names: dict[str, str], now: datetime) -> dict:
    return {
        "id": a.assignment_id,
        "clinicId": a.clinic_id,
        "clinicName": clinic_names.get(a.clinic_id, a.clinic_id),
        "discountId": a.discount_id,
        "code": a.discount_code,
        "planId": a.plan_id,
        "basePrice": a.base_price,
        "discountedPrice": a.discounted_price,
        "monthlySaving": round(max((a.base_price or 0) - (a.discounted_price or 0), 0), 2),
        "appliedAt": _iso(a.applied_at),
        "endsAt": _iso(a.ends_at),
        "appliedBy": a.applied_by_email,
        "notes": a.notes or "",
        "revokedAt": _iso(a.revoked_at),
        "running": svc.assignment_running(
            {"revoked_at": a.revoked_at, "ends_at": a.ends_at}, now),
    }


def _log(db: AsyncSession, user: dict, action: str, entity_type: str, entity_id: str,
         name: Optional[str], changes: dict) -> None:
    db.add(ActionLog(
        log_id=f"log-{uuid.uuid4().hex[:10]}",
        admin_user_id=user.get("user_id", "unknown"),
        admin_email=user.get("email", "unknown"),
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        entity_name=name,
        changes=json.dumps(changes, ensure_ascii=False, default=str),
    ))


async def _plan_names(db: AsyncSession) -> dict[str, str]:
    return {p.plan_id: p.name for p in (await db.execute(select(Plan))).scalars().all()}


async def _get(db: AsyncSession, discount_id: str) -> Discount:
    d = (await db.execute(select(Discount).where(Discount.discount_id == discount_id))).scalars().first()
    if not d:
        raise HTTPException(status_code=404, detail="Descuento no encontrado")
    return d


def _clean_code(code: str) -> str:
    return "".join(code.split()).upper()


class DiscountBody(BaseModel):
    code: str = Field(..., min_length=3, max_length=30, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(..., min_length=2, max_length=80)
    description: Optional[str] = Field(None, max_length=500)
    kind: Literal["percent", "fixed"] = "percent"
    value: float = Field(..., gt=0)
    plan_id: Optional[str] = None
    duration_months: Optional[int] = Field(None, ge=1, le=60)
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    max_redemptions: Optional[int] = Field(None, ge=1)


class DiscountPatch(BaseModel):
    """Solo se cambia lo que viene en el cuerpo; un null explícito borra el campo opcional."""
    name: Optional[str] = Field(None, min_length=2, max_length=80)
    description: Optional[str] = Field(None, max_length=500)
    kind: Optional[Literal["percent", "fixed"]] = None
    value: Optional[float] = Field(None, gt=0)
    plan_id: Optional[str] = None
    duration_months: Optional[int] = Field(None, ge=1, le=60)
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    max_redemptions: Optional[int] = Field(None, ge=1)
    is_active: Optional[bool] = None


class ApplyBody(BaseModel):
    clinic_id: str = Field(..., min_length=1)
    notes: Optional[str] = Field(None, max_length=500)


def _check_dates(starts: Optional[datetime], ends: Optional[datetime]) -> None:
    if starts and ends and ends <= starts:
        raise HTTPException(status_code=422, detail="La fecha de término debe ser posterior a la de inicio.")


@router.get("", summary="Lista de descuentos")
async def list_discounts(db: AsyncSession = Depends(get_db), _user=Depends(get_current_user)):
    now = datetime.utcnow()
    plan_names = await _plan_names(db)
    rows = (await db.execute(select(Discount))).scalars().all()
    rows = sorted(rows, key=lambda d: (d.created_at or now), reverse=True)
    assignments = (await db.execute(select(ClinicDiscount))).scalars().all()
    clinic_names = {c.clinic_id: c.name for c in (await db.execute(select(Clinic))).scalars().all()}

    items = [_serialize(d, plan_names, now) for d in rows]
    as_dicts = [{"revoked_at": a.revoked_at, "ends_at": a.ends_at, "base_price": a.base_price,
                 "discounted_price": a.discounted_price} for a in assignments]
    running = [a for a in assignments if svc.assignment_running({"revoked_at": a.revoked_at, "ends_at": a.ends_at}, now)]
    return {
        "status": "success",
        "data": {
            "discounts": items,
            "running_assignments": [_serialize_assignment(a, clinic_names, now) for a in
                                    sorted(running, key=lambda a: a.applied_at or now, reverse=True)],
            "summary": {
                "total": len(items),
                "active": sum(1 for i in items if i["status"] == svc.ACTIVE),
                "clinics_with_discount": len({a.clinic_id for a in running}),
                "monthly_impact": svc.monthly_impact(as_dicts, now),
            },
        },
    }


@router.post("", summary="Crear descuento", status_code=201,
             dependencies=[Depends(require_permission("plans.manage"))])
async def create_discount(body: DiscountBody, db: AsyncSession = Depends(get_db),
                          user: dict = Depends(get_current_user)):
    err = svc.validate_value(body.kind, body.value)
    if err:
        raise HTTPException(status_code=422, detail=err)
    _check_dates(body.starts_at, body.ends_at)
    code = _clean_code(body.code)
    if (await db.execute(select(Discount).where(Discount.code == code))).scalars().first():
        raise HTTPException(status_code=409, detail=f"Ya existe un descuento con el código {code}.")
    if body.plan_id and not (await db.execute(select(Plan).where(Plan.plan_id == body.plan_id))).scalars().first():
        raise HTTPException(status_code=422, detail="El plan indicado no existe.")

    d = Discount(
        discount_id=f"disc-{uuid.uuid4().hex[:8]}", code=code, name=body.name.strip(),
        description=(body.description or "").strip() or None, kind=body.kind, value=body.value,
        plan_id=body.plan_id or None, duration_months=body.duration_months,
        starts_at=body.starts_at, ends_at=body.ends_at, max_redemptions=body.max_redemptions,
        created_by_email=user.get("email", "unknown"),
    )
    db.add(d)
    _log(db, user, "CREATE", "discount", d.discount_id, d.name,
         {"after": {"code": code, "kind": d.kind, "value": d.value, "plan_id": d.plan_id}})
    await db.commit()
    now = datetime.utcnow()
    return {"status": "success", "data": _serialize(d, await _plan_names(db), now)}


@router.patch("/{discount_id}", summary="Editar o activar/desactivar un descuento",
              dependencies=[Depends(require_permission("plans.manage"))])
async def update_discount(body: DiscountPatch, discount_id: str = Path(...),
                          db: AsyncSession = Depends(get_db), user: dict = Depends(get_current_user)):
    d = await _get(db, discount_id)
    fs = body.model_fields_set          # campos que el cliente envió (incluye los null explícitos)
    used = (d.redemptions or 0) > 0
    before = {"name": d.name, "kind": d.kind, "value": d.value, "plan_id": d.plan_id,
              "is_active": d.is_active, "max_redemptions": d.max_redemptions}

    new_plan = (body.plan_id or None) if "plan_id" in fs else d.plan_id
    changes_terms = (body.kind is not None and body.kind != d.kind) or \
                    (body.value is not None and body.value != d.value) or new_plan != d.plan_id
    if used and changes_terms:
        raise HTTPException(status_code=409,
                            detail="El descuento ya se aplicó a clínicas: no se puede cambiar su valor, tipo ni plan. Crea uno nuevo.")

    kind = body.kind or d.kind
    value = body.value if body.value is not None else d.value
    err = svc.validate_value(kind, value)
    if err:
        raise HTTPException(status_code=422, detail=err)
    starts = body.starts_at if "starts_at" in fs else d.starts_at
    ends = body.ends_at if "ends_at" in fs else d.ends_at
    _check_dates(starts, ends)
    if "max_redemptions" in fs and body.max_redemptions is not None and body.max_redemptions < (d.redemptions or 0):
        raise HTTPException(status_code=422, detail="El tope de usos no puede ser menor a los usos ya registrados.")
    if new_plan and new_plan != d.plan_id and \
            not (await db.execute(select(Plan).where(Plan.plan_id == new_plan))).scalars().first():
        raise HTTPException(status_code=422, detail="El plan indicado no existe.")

    if body.name is not None:
        d.name = body.name.strip()
    if "description" in fs:
        d.description = (body.description or "").strip() or None
    d.kind, d.value, d.plan_id = kind, value, new_plan
    if "duration_months" in fs:
        d.duration_months = body.duration_months
    d.starts_at, d.ends_at = starts, ends
    if "max_redemptions" in fs:
        d.max_redemptions = body.max_redemptions
    if body.is_active is not None:
        d.is_active = body.is_active
    d.updated_at = datetime.utcnow()
    db.add(d)
    _log(db, user, "UPDATE", "discount", d.discount_id, d.name,
         {"before": before, "after": {"name": d.name, "kind": d.kind, "value": d.value,
                                      "plan_id": d.plan_id, "is_active": d.is_active,
                                      "max_redemptions": d.max_redemptions}})
    await db.commit()
    return {"status": "success", "data": _serialize(d, await _plan_names(db), datetime.utcnow())}


@router.delete("/{discount_id}", summary="Eliminar un descuento que nunca se usó",
               dependencies=[Depends(require_permission("plans.manage"))])
async def delete_discount(discount_id: str = Path(...), db: AsyncSession = Depends(get_db),
                          user: dict = Depends(get_current_user)):
    d = await _get(db, discount_id)
    if (d.redemptions or 0) > 0:
        raise HTTPException(status_code=409, detail="El descuento ya se aplicó a clínicas: desactívalo en lugar de eliminarlo.")
    _log(db, user, "HARD_DELETE", "discount", d.discount_id, d.name, {"before": {"code": d.code}})
    await db.delete(d)
    await db.commit()
    return {"status": "success", "data": {"id": discount_id}}


async def _current_plan(db: AsyncSession, clinic_id: str) -> tuple[Optional[ClinicPlan], Optional[Plan]]:
    assignment = (await db.execute(
        select(ClinicPlan).where(ClinicPlan.clinic_id == clinic_id, ClinicPlan.effective_to.is_(None))
    )).scalars().first()
    if not assignment:
        return None, None
    plan = (await db.execute(select(Plan).where(Plan.plan_id == assignment.plan_id))).scalars().first()
    return assignment, plan


@router.get("/clinics/{clinic_id}", summary="Descuentos de una clínica")
async def clinic_discounts(clinic_id: str = Path(...), db: AsyncSession = Depends(get_db),
                           _user=Depends(get_current_user)):
    now = datetime.utcnow()
    clinic = (await db.execute(select(Clinic).where(Clinic.clinic_id == clinic_id))).scalars().first()
    if not clinic:
        raise HTTPException(status_code=404, detail="Clínica no encontrada")
    rows = (await db.execute(select(ClinicDiscount).where(ClinicDiscount.clinic_id == clinic_id))).scalars().all()
    rows = sorted(rows, key=lambda a: a.applied_at or now, reverse=True)
    return {"status": "success",
            "data": [_serialize_assignment(a, {clinic.clinic_id: clinic.name}, now) for a in rows]}


@router.get("/{discount_id}/preview", summary="Precio resultante de un descuento para un plan")
async def preview(discount_id: str = Path(...), plan_id: str = Query(..., min_length=1),
                  db: AsyncSession = Depends(get_db), _user=Depends(get_current_user)):
    d = await _get(db, discount_id)
    plan = (await db.execute(select(Plan).where(Plan.plan_id == plan_id))).scalars().first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan no encontrado")
    ok, reason = svc.can_apply(_as_dict(d), plan.plan_id, datetime.utcnow())
    final = svc.apply_discount(plan.monthly_price, d.kind, d.value)
    return {"status": "success", "data": {
        "basePrice": plan.monthly_price, "discountedPrice": final,
        "monthlySaving": round(plan.monthly_price - final, 2), "currency": plan.currency,
        "applicable": ok, "reason": reason, "reasonText": REASONS.get(reason) if reason else None,
    }}


@router.post("/{discount_id}/apply", summary="Aplicar un descuento a una clínica", status_code=201,
             dependencies=[Depends(require_permission("plans.manage"))])
async def apply_to_clinic(body: ApplyBody, discount_id: str = Path(...),
                          db: AsyncSession = Depends(get_db), user: dict = Depends(get_current_user)):
    now = datetime.utcnow()
    d = await _get(db, discount_id)
    clinic = (await db.execute(select(Clinic).where(Clinic.clinic_id == body.clinic_id))).scalars().first()
    if not clinic or clinic.is_deleted:
        raise HTTPException(status_code=404, detail="Clínica no encontrada")
    _, plan = await _current_plan(db, clinic.clinic_id)
    if not plan:
        raise HTTPException(status_code=422, detail="La clínica no tiene un plan vigente al cual aplicar el descuento.")

    ok, reason = svc.can_apply(_as_dict(d), plan.plan_id, now)
    if not ok:
        raise HTTPException(status_code=422, detail=REASONS[reason])

    existing = (await db.execute(select(ClinicDiscount).where(ClinicDiscount.clinic_id == clinic.clinic_id))).scalars().all()
    if any(svc.assignment_running({"revoked_at": a.revoked_at, "ends_at": a.ends_at}, now) for a in existing):
        raise HTTPException(status_code=409, detail="La clínica ya tiene un descuento vigente. Quítalo antes de aplicar otro.")

    final = svc.apply_discount(plan.monthly_price, d.kind, d.value)
    a = ClinicDiscount(
        assignment_id=f"cd-{uuid.uuid4().hex[:8]}", clinic_id=clinic.clinic_id,
        discount_id=d.discount_id, discount_code=d.code, plan_id=plan.plan_id,
        base_price=plan.monthly_price, discounted_price=final, applied_at=now,
        ends_at=svc.assignment_end(now, d.duration_months),
        applied_by_email=user.get("email", "unknown"), notes=(body.notes or "").strip() or None,
    )
    d.redemptions = (d.redemptions or 0) + 1
    db.add(a)
    db.add(d)
    _log(db, user, "CREATE", "clinic_discount", a.assignment_id, clinic.name,
         {"after": {"code": d.code, "plan_id": plan.plan_id, "base_price": a.base_price,
                    "discounted_price": final, "ends_at": a.ends_at}})
    await db.commit()
    return {"status": "success", "data": _serialize_assignment(a, {clinic.clinic_id: clinic.name}, now)}


@router.post("/assignments/{assignment_id}/revoke", summary="Quitar un descuento a una clínica",
             dependencies=[Depends(require_permission("plans.manage"))])
async def revoke_assignment(assignment_id: str = Path(...), db: AsyncSession = Depends(get_db),
                            user: dict = Depends(get_current_user)):
    now = datetime.utcnow()
    a = (await db.execute(select(ClinicDiscount).where(ClinicDiscount.assignment_id == assignment_id))).scalars().first()
    if not a:
        raise HTTPException(status_code=404, detail="Asignación no encontrada")
    if a.revoked_at:
        raise HTTPException(status_code=409, detail="El descuento ya estaba revocado.")
    a.revoked_at, a.revoked_by_email = now, user.get("email", "unknown")
    db.add(a)
    _log(db, user, "UPDATE", "clinic_discount", a.assignment_id, a.discount_code,
         {"before": {"revoked_at": None}, "after": {"revoked_at": now}})
    await db.commit()
    return {"status": "success", "data": _serialize_assignment(a, {}, now)}
