"""Mejora #4 — Gestión de descuentos: reglas puras (sin base de datos).

Un descuento puede ser un porcentaje (0 < valor <= 100) o un monto fijo mensual (> 0),
aplicarse a un plan concreto o a todos, tener fechas de vigencia, un tope de usos y
una duración en meses una vez aplicado a una clínica.

El precio con descuento nunca baja de 0. Las asignaciones guardan el precio base y el
precio con descuento del momento en que se aplicó, así que cambiar después el plan o el
descuento no altera lo ya pactado.
"""
from datetime import datetime
from typing import Optional

KINDS = ("percent", "fixed")

# Estados de un descuento (en orden de prioridad)
INACTIVE, SCHEDULED, EXPIRED, EXHAUSTED, ACTIVE = "inactive", "scheduled", "expired", "exhausted", "active"


def validate_value(kind: str, value: float) -> Optional[str]:
    """Devuelve un mensaje de error, o None si el valor es válido."""
    if kind not in KINDS:
        return "El tipo de descuento debe ser 'percent' o 'fixed'."
    if value is None or value <= 0:
        return "El valor del descuento debe ser mayor que 0."
    if kind == "percent" and value > 100:
        return "Un descuento porcentual no puede superar 100 %."
    return None


def apply_discount(price: float, kind: str, value: float) -> float:
    """Precio mensual con descuento, redondeado a 2 decimales y nunca negativo."""
    price = max(float(price or 0), 0.0)
    if kind == "percent":
        result = price * (1 - float(value) / 100)
    elif kind == "fixed":
        result = price - float(value)
    else:
        raise ValueError(f"Tipo de descuento desconocido: {kind}")
    return round(max(result, 0.0), 2)


def add_months(moment: datetime, months: int) -> datetime:
    """Suma meses calendario; si el día no existe en el mes destino usa el último día."""
    total = moment.month - 1 + months
    year, month = moment.year + total // 12, total % 12 + 1
    days_in_month = [31, 29 if (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)) else 28,
                     31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1]
    return moment.replace(year=year, month=month, day=min(moment.day, days_in_month))


def status_of(d: dict, now: datetime) -> str:
    """Estado del descuento: inactive, scheduled, expired, exhausted o active."""
    if not d.get("is_active", True):
        return INACTIVE
    starts, ends = d.get("starts_at"), d.get("ends_at")
    if starts and now < starts:
        return SCHEDULED
    if ends and now >= ends:
        return EXPIRED
    cap = d.get("max_redemptions")
    if cap is not None and (d.get("redemptions") or 0) >= cap:
        return EXHAUSTED
    return ACTIVE


def applies_to_plan(discount_plan_id: Optional[str], plan_id: Optional[str]) -> bool:
    """Un descuento sin plan vale para todos; con plan, solo para ese."""
    return discount_plan_id in (None, "") or discount_plan_id == plan_id


def can_apply(d: dict, plan_id: Optional[str], now: datetime) -> tuple[bool, Optional[str]]:
    """(puede aplicarse, motivo). Motivos: inactive, scheduled, expired, exhausted, plan_mismatch."""
    st = status_of(d, now)
    if st != ACTIVE:
        return False, st
    if not applies_to_plan(d.get("plan_id"), plan_id):
        return False, "plan_mismatch"
    return True, None


def assignment_end(applied_at: datetime, duration_months: Optional[int]) -> Optional[datetime]:
    """Fecha en que termina el descuento de una clínica (None = sin término)."""
    return add_months(applied_at, duration_months) if duration_months else None


def assignment_running(a: dict, now: datetime) -> bool:
    """La asignación sigue vigente: no revocada y sin fecha de término vencida."""
    if a.get("revoked_at"):
        return False
    ends = a.get("ends_at")
    return ends is None or now < ends


def monthly_impact(assignments: list[dict], now: datetime) -> float:
    """Suma mensual de lo que se deja de cobrar por asignaciones vigentes (informativo)."""
    total = sum(
        max((a.get("base_price") or 0) - (a.get("discounted_price") or 0), 0)
        for a in assignments if assignment_running(a, now)
    )
    return round(total, 2)
