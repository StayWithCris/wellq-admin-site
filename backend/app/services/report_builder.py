"""Recomendación #11 — Compilador de reportes programados.

Servicio puro: recibe una sesión y una lista de secciones, calcula métricas
reales desde Neon y las entrega en tres formatos:

* ``build_payload``  → dict estructurado (vista previa y fuente de los demás formatos)
* ``render_html`` / ``render_text`` / ``render_slack`` → cuerpo del correo y de Slack
* ``render_csv``     → adjunto CSV UTF-8 con BOM, protegido contra fórmulas

Cada sección devuelve ``None`` cuando no hay datos: se omite del reporte y se
lista en ``omitted`` en vez de inventar números.
"""

from __future__ import annotations

import csv
import html
import io
import json
from collections import Counter
from datetime import datetime
from typing import Any, Awaitable, Callable, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models_db import (
    AdherenceSnapshot,
    AppUsageStat,
    Clinic,
    ClinicUsageMetric,
    FeatureAdoption,
    Invoice,
    MrrSnapshot,
    SoapQualityMetric,
    SupportTicket,
)
from app.routers.cohort_heatmap import compute_retention_cohorts

ALLOWED_SECTIONS = ["retention", "soap_quality", "clinic_health", "app_usage", "executive_mrr"]

SECTION_TITLES = {
    "retention": "Retención de clínicas",
    "soap_quality": "Calidad de notas SOAP",
    "clinic_health": "Salud de clínicas",
    "app_usage": "Uso de las aplicaciones",
    "executive_mrr": "Resumen ejecutivo de MRR",
}

CAPACITY_THRESHOLD = 0.9          # clínicas sobre el 90 % de su límite de pacientes
RECENT_COHORTS = 6                # cohortes usadas para la retención promedio
CSV_DANGEROUS_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def validate_sections(sections: list[str]) -> list[str]:
    """Devuelve las secciones en orden canónico; lanza ValueError si hay alguna inválida."""
    unknown = [s for s in sections if s not in ALLOWED_SECTIONS]
    if unknown:
        raise ValueError(f"Secciones no válidas: {', '.join(unknown)}")
    if not sections:
        raise ValueError("Debes elegir al menos una sección.")
    return [s for s in ALLOWED_SECTIONS if s in sections]


def _metric(label: str, value: Any, detail: str = "") -> dict:
    return {"label": label, "value": value, "detail": detail}


def _pct(value: float) -> str:
    return f"{round(float(value), 1)} %"


def _money(value: float, currency: str = "USD") -> str:
    return f"{float(value):,.2f} {currency}".replace(",", "X").replace(".", ",").replace("X", ".")


async def _rows(db: AsyncSession, model) -> list:
    return list((await db.execute(select(model))).scalars().all())


def _latest(rows: list, attr: str = "recorded_at"):
    rows = [r for r in rows if getattr(r, attr, None) is not None]
    return max(rows, key=lambda r: getattr(r, attr)) if rows else None


def _live_clinics(clinics: list) -> list:
    return [c for c in clinics if not getattr(c, "is_deleted", False)]


# ── Secciones ───────────────────────────────────────────────────────────────

async def _retention(db: AsyncSession, now: datetime) -> Optional[list[dict]]:
    clinics = await _rows(db, Clinic)
    if not clinics:
        return None
    cohorts, _ = compute_retention_cohorts(clinics, now)
    live = _live_clinics(clinics)
    active = [c for c in live if c.status != "churned"]
    recent = cohorts[-RECENT_COHORTS:]
    metrics = [
        _metric("Clínicas registradas", len(live)),
        _metric("Clínicas retenidas (activas)", len(active)),
    ]
    if recent:
        avg = sum(c["retention"][-1] for c in recent) / len(recent)
        metrics.append(_metric("Retención promedio de cohortes recientes", _pct(avg),
                               f"Últimas {len(recent)} cohortes por mes de alta"))
    return metrics


async def _soap_quality(db: AsyncSession, now: datetime) -> Optional[list[dict]]:
    latest = _latest(await _rows(db, SoapQualityMetric))
    usage = await _rows(db, ClinicUsageMetric)
    notes_period = sum(int(u.notes_generated or 0) for u in usage)
    if latest is None and not notes_period:
        return None
    metrics = []
    if notes_period:
        metrics.append(_metric("Notas generadas en el período", notes_period, "Suma por clínica (mes en curso)"))
    if latest is not None:
        metrics += [
            _metric("Tasa de aceptación", _pct(latest.acceptance_rate_percentage)),
            _metric("Notas con ediciones", _pct(latest.edits_required_percentage)),
            _metric("Tiempo ahorrado por nota", f"{round(latest.average_time_saved_minutes_per_note, 1)} min"),
        ]
    return metrics


async def _clinic_health(db: AsyncSession, now: datetime) -> Optional[list[dict]]:
    clinics = _live_clinics(await _rows(db, Clinic))
    tickets = await _rows(db, SupportTicket)
    invoices = await _rows(db, Invoice)
    if not clinics and not tickets and not invoices:
        return None
    metrics = []
    if clinics:
        by_status = Counter((c.status or "unknown") for c in clinics)
        for status, label in (("active", "Clínicas activas"), ("warning", "Clínicas en alerta"),
                              ("critical", "Clínicas en estado crítico"), ("churned", "Clínicas dadas de baja")):
            metrics.append(_metric(label, by_status.get(status, 0)))
        near = [c for c in clinics if c.patients_limit and (c.patients_used or 0) >= CAPACITY_THRESHOLD * c.patients_limit]
        metrics.append(_metric("Clínicas sobre el 90 % de su límite de pacientes", len(near),
                               ", ".join(c.name for c in near[:5])))
    if tickets:
        metrics.append(_metric("Tickets de soporte abiertos", sum(1 for t in tickets if (t.status or "") != "Closed")))
    if invoices:
        overdue = [i for i in invoices if i.status == "overdue"]
        currency = invoices[0].currency or "USD"
        metrics.append(_metric("Facturas vencidas", len(overdue),
                               _money(sum(float(i.amount or 0) for i in overdue), currency)))
    return metrics


async def _app_usage(db: AsyncSession, now: datetime) -> Optional[list[dict]]:
    stats = await _rows(db, AppUsageStat)
    features = await _rows(db, FeatureAdoption)
    adherence = _latest(await _rows(db, AdherenceSnapshot))
    if not stats and not features and adherence is None:
        return None
    metrics = []
    for app_types, label in ((("patient", "patients"), "Usuarios activos mensuales · app de pacientes"),
                             (("tablet", "clinician"), "Usuarios activos mensuales · tablet clínica")):
        latest = _latest([s for s in stats if s.app_type in app_types])
        if latest is not None:
            metrics.append(_metric(label, latest.monthly_active_users))
    for f in sorted(features, key=lambda x: -(x.adoption_rate_percentage or 0))[:3]:
        metrics.append(_metric(f"Adopción · {f.feature_name}", _pct(f.adoption_rate_percentage),
                               f"{f.total_uses} usos"))
    if adherence is not None:
        metrics.append(_metric("Adherencia global de pacientes", _pct(adherence.overall_adherence_percentage),
                               f"Mayor abandono: {adherence.top_dropping_point}" if adherence.top_dropping_point else ""))
    return metrics or None


async def _executive_mrr(db: AsyncSession, now: datetime) -> Optional[list[dict]]:
    clinics = [c for c in _live_clinics(await _rows(db, Clinic)) if c.status != "churned"]
    snapshots = [s for s in await _rows(db, MrrSnapshot) if s.source and not s.clinic_id]
    if not clinics and not snapshots:
        return None
    metrics = []
    if clinics:
        metrics.append(_metric("MRR activo total", _money(sum(float(c.mrr or 0) for c in clinics))))
        for tier, count in sorted(Counter((c.tier or "—") for c in clinics).items()):
            metrics.append(_metric(f"Clínicas en plan {tier}", count))
    if snapshots:
        last = max(snapshots, key=lambda s: (s.period_year, s.created_at or datetime.min))
        metrics.append(_metric("Último cierre oficial de MRR", _money(last.total_mrr, last.currency or "USD"),
                               f"{last.period_month} {last.period_year} · crecimiento {_pct(last.monthly_growth_percentage)}"))
    return metrics


BUILDERS: dict[str, Callable[[AsyncSession, datetime], Awaitable[Optional[list[dict]]]]] = {
    "retention": _retention,
    "soap_quality": _soap_quality,
    "clinic_health": _clinic_health,
    "app_usage": _app_usage,
    "executive_mrr": _executive_mrr,
}


def period_label_for(frequency: str, now: datetime) -> str:
    if frequency == "monthly":
        return now.strftime("Mes %m/%Y")
    if frequency == "weekly":
        return f"Semana {now.isocalendar()[1]} de {now.year}"
    return now.strftime("Día %d/%m/%Y")


async def build_payload(db: AsyncSession, sections: list[str], *, frequency: str = "daily",
                        now: Optional[datetime] = None) -> dict:
    now = now or datetime.utcnow()
    sections = validate_sections(sections)
    compiled: dict[str, dict] = {}
    omitted: list[str] = []
    for key in sections:
        metrics = await BUILDERS[key](db, now)
        if metrics:
            compiled[key] = {"title": SECTION_TITLES[key], "metrics": metrics}
        else:
            omitted.append(key)
    return {
        "generated_at": now.isoformat() + "Z",
        "period_label": period_label_for(frequency, now),
        "sections": compiled,
        "omitted": omitted,
    }


# ── Renderizadores ──────────────────────────────────────────────────────────

def render_text(name: str, payload: dict) -> str:
    lines = [f"{name} — {payload['period_label']}", ""]
    for section in payload["sections"].values():
        lines.append(section["title"])
        for m in section["metrics"]:
            lines.append(f"  - {m['label']}: {m['value']}" + (f" ({m['detail']})" if m["detail"] else ""))
        lines.append("")
    if payload["omitted"]:
        lines.append("Secciones sin datos (omitidas): " + ", ".join(SECTION_TITLES[k] for k in payload["omitted"]))
    lines.append("Generado automáticamente por WellQ Admin.")
    return "\n".join(lines)


def render_html(name: str, payload: dict) -> str:
    e = html.escape
    blocks = []
    for section in payload["sections"].values():
        rows = "".join(
            f'<tr><td style="padding:6px 12px 6px 0;color:#a8b3c7;">{e(m["label"])}</td>'
            f'<td style="padding:6px 0;color:#ffffff;font-weight:700;">{e(str(m["value"]))}</td>'
            f'<td style="padding:6px 0 6px 12px;color:#64748b;font-size:12px;">{e(m["detail"])}</td></tr>'
            for m in section["metrics"]
        )
        blocks.append(
            f'<h3 style="color:#67e8f9;margin:22px 0 8px;font-size:15px;">{e(section["title"])}</h3>'
            f'<table style="border-collapse:collapse;font-size:13px;">{rows}</table>'
        )
    omitted = ""
    if payload["omitted"]:
        omitted = ('<p style="color:#64748b;font-size:12px;margin-top:18px;">Secciones sin datos (omitidas): '
                   + e(", ".join(SECTION_TITLES[k] for k in payload["omitted"])) + "</p>")
    empty = "" if blocks else '<p style="color:#a8b3c7;">No hay datos para las secciones elegidas en este período.</p>'
    return f"""
      <div style="font-family:Arial,sans-serif;background:#020817;padding:24px;color:#e5faff;">
        <div style="max-width:640px;margin:auto;background:#07101d;border:1px solid rgba(34,211,238,.35);border-radius:16px;padding:28px;">
          <p style="color:#67e8f9;font-weight:700;margin:0 0 8px;">WellQ Admin · Reporte programado</p>
          <h2 style="color:white;margin:0 0 4px;">{e(name)}</h2>
          <p style="color:#a8b3c7;margin:0;">{e(payload["period_label"])}</p>
          {''.join(blocks)}{empty}{omitted}
          <p style="color:#64748b;font-size:12px;margin-top:24px;">Generado automáticamente por WellQ Admin.</p>
        </div>
      </div>
    """


def render_slack(name: str, payload: dict) -> dict:
    blocks: list[dict] = [
        {"type": "header", "text": {"type": "plain_text", "text": f"📊 {name}"[:150]}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": payload["period_label"]}]},
    ]
    for section in payload["sections"].values():
        lines = "\n".join(f"• {m['label']}: *{m['value']}*" for m in section["metrics"][:6])
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"*{section['title']}*\n{lines}"[:2900]}})
    if not payload["sections"]:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "No hay datos para las secciones elegidas."}})
    return {"text": f"📊 {name} — {payload['period_label']}", "blocks": blocks}


def _csv_safe(value: Any) -> Any:
    """Evita la inyección de fórmulas al abrir el CSV en Excel."""
    if isinstance(value, str) and value.startswith(CSV_DANGEROUS_PREFIXES):
        return "'" + value
    return value


def render_csv(payload: dict) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Seccion", "Metrica", "Valor", "Detalle"])
    for section in payload["sections"].values():
        for m in section["metrics"]:
            writer.writerow([_csv_safe(section["title"]), _csv_safe(m["label"]),
                             _csv_safe(m["value"]), _csv_safe(m["detail"])])
    return ("﻿" + buffer.getvalue()).encode("utf-8")


def payload_summary(payload: dict) -> str:
    return json.dumps({k: len(v["metrics"]) for k, v in payload["sections"].items()}, ensure_ascii=False)
