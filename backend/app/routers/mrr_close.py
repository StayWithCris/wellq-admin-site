"""Recomendación #1 — Carga e historial de MRR ("Cerrar y agregar mes").

GET  /api/financials/mrr/close/preview?year=&month=  Cálculo del mes desde la tabla clinics
POST /api/financials/mrr/close                        Guarda el cierre en mrr_snapshots
GET  /api/financials/mrr/closed                       Historial de meses cerrados

El cierre queda como cifra oficial del mes: GET /api/financials/mrr/snapshots
lo usa en lugar del cálculo en vivo (ver financials.py). Las filas antiguas de
mrr_snapshots sin "source" (datos de demostración) no se tocan ni se usan.
"""
import json
import uuid
from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.neon import get_db
from app.models_db import ActionLog, MrrSnapshot
from app.services.mrr_close import (
    MONTH_ABBR_ES, build_values, default_period, growth_pct, month_bounds,
    previous_month, validate_period,
)
from .auth import require_permission
from .dependencies import get_current_user
from .financials import calculate_mrr_breakdown_for_period

router = APIRouter(prefix="/api/financials/mrr", tags=["Cierre mensual de MRR"])


async def _closed_row(db: AsyncSession, year: int, month: int) -> MrrSnapshot | None:
    rows = (await db.execute(
        select(MrrSnapshot).where(
            MrrSnapshot.clinic_id.is_(None),
            MrrSnapshot.source.is_not(None),
            MrrSnapshot.period_year == year,
            MrrSnapshot.period_month == MONTH_ABBR_ES[month],
        )
    )).scalars().all()
    return rows[0] if rows else None


async def _previous_total(db: AsyncSession, year: int, month: int) -> float:
    """MRR total del mes anterior: el cierre oficial si existe, si no el cálculo."""
    py, pm = previous_month(year, month)
    prev = await _closed_row(db, py, pm)
    if prev:
        return prev.total_mrr or 0.0
    start, end = month_bounds(py, pm)
    return (await calculate_mrr_breakdown_for_period(db, start, end))["total_mrr"]


def _serialize(s: MrrSnapshot) -> dict:
    return {
        "period_month": s.period_month,
        "period_year": s.period_year,
        "total_mrr": s.total_mrr,
        "new_business": s.new_business,
        "expansion": s.expansion,
        "contraction": s.contraction,
        "churn": s.churn,
        "retained": s.retained,
        "monthly_growth_percentage": s.monthly_growth_percentage,
        "currency": s.currency,
        "source": s.source,
        "closed_by": s.closed_by_email,
        "notes": s.notes,
        "closed_at": s.created_at.isoformat() + "Z" if s.created_at else None,
    }


@router.get("/close/preview", summary="Previsualizar el cierre de un mes")
async def preview_close(
    year: Optional[int] = Query(None),
    month: Optional[int] = Query(None, ge=1, le=12),
    db: AsyncSession = Depends(get_db),
    _user=Depends(get_current_user),
):
    today = datetime.utcnow().date()
    if year is None or month is None:
        year, month = default_period(today)
    try:
        validate_period(year, month, today)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    start, end = month_bounds(year, month)
    calculated = await calculate_mrr_breakdown_for_period(db, start, end)
    prev_total = await _previous_total(db, year, month)
    existing = await _closed_row(db, year, month)
    return {
        "status": "success",
        "data": {
            "year": year,
            "month": month,
            "period_month": MONTH_ABBR_ES[month],
            "calculated": {**calculated, "monthly_growth_percentage": growth_pct(calculated["total_mrr"], prev_total)},
            "previous_total_mrr": prev_total,
            "already_closed": existing is not None,
            "existing": _serialize(existing) if existing else None,
        },
    }


class ManualValues(BaseModel):
    total_mrr: Optional[float] = Field(None, ge=0)
    new_business: Optional[float] = Field(None, ge=0)
    expansion: Optional[float] = Field(None, ge=0)
    contraction: Optional[float] = Field(None, ge=0)
    churn: Optional[float] = Field(None, ge=0)
    retained: Optional[float] = Field(None, ge=0)


class CloseMonthBody(BaseModel):
    year: int
    month: int = Field(..., ge=1, le=12)
    mode: Literal["auto", "manual"] = "auto"
    values: Optional[ManualValues] = None
    notes: Optional[str] = Field(None, max_length=500)
    replace: bool = False


@router.post(
    "/close",
    summary="Cerrar un mes y guardarlo en el historial de MRR",
    status_code=201,
    dependencies=[Depends(require_permission("billing.edit"))],
)
async def close_month(
    body: CloseMonthBody,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    today = datetime.utcnow().date()
    try:
        validate_period(body.year, body.month, today)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    if body.mode == "manual" and body.values is None:
        raise HTTPException(status_code=422, detail="En modo manual debes enviar los valores del mes.")

    start, end = month_bounds(body.year, body.month)
    calculated = await calculate_mrr_breakdown_for_period(db, start, end)
    manual = body.values.model_dump() if body.mode == "manual" else None
    try:
        values = build_values(calculated, manual)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    values["monthly_growth_percentage"] = growth_pct(values["total_mrr"], await _previous_total(db, body.year, body.month))

    existing = await _closed_row(db, body.year, body.month)
    if existing and not body.replace:
        raise HTTPException(
            status_code=409,
            detail=f"{MONTH_ABBR_ES[body.month]} {body.year} ya está cerrado. Marca 'reemplazar' para corregirlo.",
        )

    before = _serialize(existing) if existing else None
    snap = existing or MrrSnapshot(
        clinic_id=None, period_month=MONTH_ABBR_ES[body.month], period_year=body.year, currency="USD",
    )
    for k, v in values.items():
        setattr(snap, k, v)
    snap.source = body.mode
    snap.closed_by_email = user.get("email")
    snap.notes = (body.notes or "").strip() or None
    snap.created_at = datetime.utcnow()
    db.add(snap)

    db.add(ActionLog(
        log_id=f"log-{uuid.uuid4().hex[:10]}",
        admin_user_id=user.get("user_id", "unknown"),
        admin_email=user.get("email", "unknown"),
        action="UPDATE" if existing else "CREATE",
        entity_type="mrr_month",
        entity_id=f"{body.year}-{body.month:02d}",
        entity_name=f"MRR {MONTH_ABBR_ES[body.month]} {body.year}",
        changes=json.dumps({"before": before, "after": {**values, "source": body.mode}}, ensure_ascii=False, default=str),
    ))
    await db.commit()

    return {"status": "success", "data": _serialize(snap)}


@router.get("/closed", summary="Historial de meses cerrados")
async def list_closed(
    db: AsyncSession = Depends(get_db),
    _user=Depends(get_current_user),
):
    rows = (await db.execute(
        select(MrrSnapshot).where(MrrSnapshot.clinic_id.is_(None), MrrSnapshot.source.is_not(None))
    )).scalars().all()
    month_num = {v: k for k, v in MONTH_ABBR_ES.items()}
    rows = sorted(rows, key=lambda s: (s.period_year, month_num.get(s.period_month, 0)), reverse=True)
    return {"status": "success", "data": [_serialize(s) for s in rows]}
