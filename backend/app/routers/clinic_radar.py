"""Recomendación #10 — Radar de salud multi-tenant de 5 ejes.

GET /api/clinics/{clinic_id}/health-radar
Cada eje se calcula de 0 a 100 con datos reales de Neon:
  - financiero: % de facturas pagadas de la clínica.
  - login:      cercanía del último acceso (<=7 días = 100, >=90 días = 0).
  - ia_soap:    notas SOAP generadas vs. meta por clínico activo.
  - camara:     sesiones de pacientes completadas vs. meta por clínico activo.
  - soporte:    100 menos 20 puntos por cada ticket abierto.
Si un eje no tiene datos, se devuelve has_data=false y valor 0 (no se inventa).
Las metas son constantes simples y editables, definidas abajo.
"""
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.neon import get_db
from app.models_db import Clinic, Invoice, ClinicUsageMetric, SupportTicket

router = APIRouter(prefix="/api/clinics", tags=["Salud de Clínicas"])

# Metas de referencia (ajustables)
LOGIN_FULL_DAYS = 7
LOGIN_ZERO_DAYS = 90
NOTES_TARGET_PER_CLINICIAN = 20      # notas SOAP por clínico activo al mes
SESSIONS_TARGET_PER_CLINICIAN = 30   # sesiones completadas por clínico activo al mes
POINTS_PER_OPEN_TICKET = 20


def _clamp(v: float) -> int:
    return int(max(0, min(100, round(v))))


@router.get("/{clinic_id}/health-radar", summary="Radar de salud de 5 ejes de una clínica")
async def get_clinic_health_radar(clinic_id: str = Path(...), db: AsyncSession = Depends(get_db)):
    clinic = (await db.execute(select(Clinic).where(Clinic.clinic_id == clinic_id))).scalar_one_or_none()
    if not clinic:
        raise HTTPException(status_code=404, detail="Clínica no encontrada")

    now = datetime.utcnow()

    # Financiero
    invoices = (await db.execute(select(Invoice).where(Invoice.clinic_id == clinic_id))).scalars().all()
    if invoices:
        paid = sum(1 for i in invoices if (i.status or "").lower() == "paid")
        financiero = {"value": _clamp(paid / len(invoices) * 100), "has_data": True,
                      "detail": f"{paid} de {len(invoices)} facturas pagadas"}
    else:
        financiero = {"value": 0, "has_data": False, "detail": "Sin facturas registradas"}

    # Login
    if clinic.last_login:
        days = max(0, (now - clinic.last_login).days)
        span = LOGIN_ZERO_DAYS - LOGIN_FULL_DAYS
        login = {"value": _clamp(100 - max(0, days - LOGIN_FULL_DAYS) / span * 100), "has_data": True,
                 "detail": f"Último acceso hace {days} días"}
    else:
        login = {"value": 0, "has_data": False, "detail": "Sin accesos registrados"}

    # Uso (IA/SOAP y cámara) desde la métrica más reciente
    usage = (await db.execute(
        select(ClinicUsageMetric).where(ClinicUsageMetric.clinic_id == clinic_id)
        .order_by(desc(ClinicUsageMetric.recorded_at)).limit(1)
    )).scalar_one_or_none()
    if usage:
        clinicians = max(1, usage.active_clinicians or 0)
        ia_soap = {"value": _clamp((usage.notes_generated or 0) / (clinicians * NOTES_TARGET_PER_CLINICIAN) * 100),
                   "has_data": True, "detail": f"{usage.notes_generated or 0} notas SOAP en el período"}
        camara = {"value": _clamp((usage.patient_sessions_completed or 0) / (clinicians * SESSIONS_TARGET_PER_CLINICIAN) * 100),
                  "has_data": True, "detail": f"{usage.patient_sessions_completed or 0} sesiones completadas"}
    else:
        ia_soap = {"value": 0, "has_data": False, "detail": "Sin métricas de uso"}
        camara = {"value": 0, "has_data": False, "detail": "Sin métricas de uso"}

    # Soporte
    tickets = (await db.execute(select(SupportTicket).where(SupportTicket.clinic_id == clinic_id))).scalars().all()
    open_count = sum(1 for t in tickets if (t.status or "").lower() != "closed")
    soporte = {"value": _clamp(100 - open_count * POINTS_PER_OPEN_TICKET), "has_data": True,
               "detail": f"{open_count} tickets abiertos de {len(tickets)}"}

    axes = {"financiero": financiero, "login": login, "ia_soap": ia_soap, "camara": camara, "soporte": soporte}
    with_data = [a["value"] for a in axes.values() if a["has_data"]]
    overall = round(sum(with_data) / len(with_data)) if with_data else None

    return {
        "clinic_id": clinic_id,
        "overall": overall,
        "axes": axes,
        "generated_at": now.isoformat(),
    }
