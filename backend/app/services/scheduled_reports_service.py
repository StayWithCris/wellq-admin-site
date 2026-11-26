"""Recomendación #11 — Servicio de reportes programados.

Responsabilidades:

* Validar la programación (frecuencia, día, hora, secciones, canales, emails).
* Calcular ``next_run_at`` en UTC de forma determinista.
* Enviar el reporte por Email (con CSV adjunto opcional) y Slack mediante un
  transporte inyectable (``transport``), igual que ``incident_notifier`` (#19).
* Registrar cada envío en ``scheduled_report_runs``.
* Despachar los reportes vencidos sin duplicados: bloqueo consultivo
  ``pg_try_advisory_xact_lock`` y actualización de ``next_run_at`` ANTES de
  enviar, así un segundo worker o un reintento ya no los ve como vencidos.
"""

from __future__ import annotations

import asyncio
import json
import re
import smtplib
import uuid
from calendar import monthrange
from datetime import datetime, timedelta
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Optional

import httpx
import structlog
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models_db import ScheduledReport, ScheduledReportRun
from app.services import incident_notifier
from app.services import report_builder as builder

logger = structlog.get_logger(__name__)

FREQUENCIES = ("daily", "weekly", "monthly")
CHANNELS = ("email", "slack")
TRIGGERS = ("scheduled", "manual", "cron_webhook")
REPORTS_ADVISORY_LOCK_ID = 84729104      # distinto al de sincronización (84729103)
MAX_RECIPIENTS = 50
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+'-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$")


class ReportValidationError(ValueError):
    pass


# ── Programación ────────────────────────────────────────────────────────────

def compute_next_run(frequency: str, send_hour_utc: int, *, day_of_week: Optional[int] = None,
                     day_of_month: Optional[int] = None, now: Optional[datetime] = None) -> datetime:
    """Próxima ejecución estrictamente posterior a ``now`` (UTC, sin tzinfo)."""
    now = (now or datetime.utcnow()).replace(microsecond=0)
    at_hour = now.replace(hour=send_hour_utc, minute=0, second=0)

    if frequency == "daily":
        return at_hour if at_hour > now else at_hour + timedelta(days=1)

    if frequency == "weekly":
        days_ahead = (day_of_week - now.weekday()) % 7
        candidate = at_hour + timedelta(days=days_ahead)
        return candidate if candidate > now else candidate + timedelta(days=7)

    if frequency == "monthly":
        candidate = at_hour.replace(day=day_of_month)
        if candidate > now:
            return candidate
        year, month = (now.year + 1, 1) if now.month == 12 else (now.year, now.month + 1)
        return candidate.replace(year=year, month=month, day=min(day_of_month, monthrange(year, month)[1]))

    raise ReportValidationError(f"Frecuencia no válida: {frequency}")


def validate_email(value: str) -> str:
    raw = value or ""
    if "\r" in raw or "\n" in raw:          # evita inyección de headers en el correo
        raise ReportValidationError(f"Email no válido: {value!r}")
    email = raw.strip()
    if any(ch in email for ch in (" ", ",", ";")) or not EMAIL_RE.match(email):
        raise ReportValidationError(f"Email no válido: {value!r}")
    return email.lower()


def validate_schedule(data: dict) -> dict:
    """Valida y normaliza los campos editables. Lanza ReportValidationError."""
    name = (data.get("name") or "").strip()
    if not name or len(name) > 120:
        raise ReportValidationError("El nombre es obligatorio y tiene máximo 120 caracteres.")

    frequency = data.get("frequency")
    if frequency not in FREQUENCIES:
        raise ReportValidationError("La frecuencia debe ser daily, weekly o monthly.")

    hour = data.get("send_hour_utc", 12)
    if not isinstance(hour, int) or not 0 <= hour <= 23:
        raise ReportValidationError("La hora UTC debe estar entre 0 y 23.")

    day_of_week = data.get("day_of_week")
    day_of_month = data.get("day_of_month")
    if frequency == "weekly":
        if not isinstance(day_of_week, int) or not 0 <= day_of_week <= 6:
            raise ReportValidationError("Para frecuencia semanal, day_of_week debe estar entre 0 (lunes) y 6 (domingo).")
        day_of_month = None
    elif frequency == "monthly":
        if not isinstance(day_of_month, int) or not 1 <= day_of_month <= 28:
            raise ReportValidationError("Para frecuencia mensual, day_of_month debe estar entre 1 y 28.")
        day_of_week = None
    else:
        day_of_week = day_of_month = None

    try:
        sections = builder.validate_sections(list(data.get("sections") or []))
    except ValueError as exc:
        raise ReportValidationError(str(exc)) from exc

    channels = [c for c in CHANNELS if c in (data.get("channels") or [])]
    if not channels or len(channels) != len(set(data.get("channels") or [])):
        raise ReportValidationError("Los canales válidos son email y slack (al menos uno).")

    recipients = sorted({validate_email(r) for r in (data.get("recipients") or [])})
    if "email" in channels and not recipients:
        raise ReportValidationError("Agrega al menos un destinatario para el canal email.")
    if len(recipients) > MAX_RECIPIENTS:
        raise ReportValidationError(f"Máximo {MAX_RECIPIENTS} destinatarios.")

    description = (data.get("description") or "").strip() or None
    return {
        "name": name,
        "description": description[:1000] if description else None,
        "frequency": frequency,
        "day_of_week": day_of_week,
        "day_of_month": day_of_month,
        "send_hour_utc": hour,
        "sections": sections,
        "channels": channels,
        "recipients": recipients,
        "include_csv": bool(data.get("include_csv", True)),
    }


def apply_schedule(report: ScheduledReport, values: dict, *, now: Optional[datetime] = None) -> None:
    report.name = values["name"]
    report.description = values["description"]
    report.frequency = values["frequency"]
    report.day_of_week = values["day_of_week"]
    report.day_of_month = values["day_of_month"]
    report.send_hour_utc = values["send_hour_utc"]
    report.sections = json.dumps(values["sections"])
    report.channels = json.dumps(values["channels"])
    report.recipients = json.dumps(values["recipients"])
    report.include_csv = values["include_csv"]
    report.next_run_at = next_run_for(report, now=now) if report.is_active else None


def next_run_for(report: ScheduledReport, *, now: Optional[datetime] = None) -> datetime:
    return compute_next_run(report.frequency, report.send_hour_utc, day_of_week=report.day_of_week,
                            day_of_month=report.day_of_month, now=now)


def _list(value: Optional[str]) -> list:
    try:
        data = json.loads(value or "[]")
        return data if isinstance(data, list) else []
    except (TypeError, ValueError):
        return []


# ── Transporte (SMTP y Slack) ───────────────────────────────────────────────

class _ReportTransport:
    """Envío real. Las pruebas lo reemplazan asignando ``transport``."""

    def _send_email_sync(self, recipients, subject, text, html_body, csv_bytes, csv_filename) -> None:
        msg = MIMEMultipart("mixed")
        msg["Subject"] = subject
        msg["From"] = f"{settings.smtp_from_name} <{settings.smtp_from_email or settings.smtp_user}>"
        msg["To"] = ", ".join(recipients)
        body = MIMEMultipart("alternative")
        body.attach(MIMEText(text, "plain", "utf-8"))
        body.attach(MIMEText(html_body, "html", "utf-8"))
        msg.attach(body)
        if csv_bytes:
            part = MIMEApplication(csv_bytes, _subtype="csv")
            part.add_header("Content-Disposition", "attachment", filename=csv_filename or "reporte.csv")
            msg.attach(part)
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as server:
            server.starttls()
            server.login(settings.smtp_user, settings.smtp_app_password)
            server.send_message(msg)

    async def send_email(self, recipients, subject, text, html_body, csv_bytes=None, csv_filename=None) -> None:
        await asyncio.to_thread(self._send_email_sync, recipients, subject, text, html_body, csv_bytes, csv_filename)

    async def send_slack(self, webhook_url: str, payload: dict) -> None:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(webhook_url, json=payload)
        if response.status_code >= 300:
            raise RuntimeError(f"Slack respondió {response.status_code}: {response.text[:200]}")


transport = _ReportTransport()


def csv_filename(report: ScheduledReport, now: Optional[datetime] = None) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", report.name).strip("_")[:50] or "reporte"
    return f"WellQ_{slug}_{(now or datetime.utcnow()).strftime('%Y-%m-%d')}.csv"


# ── Ejecución de un reporte ─────────────────────────────────────────────────

def _overall_status(summary: dict) -> str:
    states = list(summary.values())
    if states and all(s == "sent" for s in states):
        return "sent"
    if any(s == "sent" for s in states):
        return "partial"
    if any(s == "failed" for s in states):
        return "failed"
    return "skipped"


async def execute_report(db: AsyncSession, report: ScheduledReport, *, trigger: str,
                         triggered_by: str, now: Optional[datetime] = None) -> ScheduledReportRun:
    """Compila y envía un reporte por sus canales y registra la corrida. No lanza excepciones."""
    now = now or datetime.utcnow()
    run = ScheduledReportRun(
        run_id=f"REPRUN-{uuid.uuid4().hex[:8]}",
        report_id=report.report_id,
        report_name=report.name,
        trigger=trigger if trigger in TRIGGERS else "manual",
        triggered_by=triggered_by or "scheduler",
        status="skipped",
        created_at=now,
    )
    summary: dict[str, str] = {}
    errors: list[str] = []
    try:
        payload = await builder.build_payload(db, _list(report.sections), frequency=report.frequency, now=now)
        run.period_label = payload["period_label"]
        subject = f"[WellQ Admin] {report.name} — {payload['period_label']}"
        recipients = _list(report.recipients)

        for channel in _list(report.channels):
            if channel == "email":
                if not incident_notifier.email_configured():
                    summary["email"] = "skipped"
                    errors.append("email: SMTP no configurado")
                    continue
                if not recipients:
                    summary["email"] = "skipped"
                    errors.append("email: sin destinatarios")
                    continue
                try:
                    attachment = builder.render_csv(payload) if report.include_csv else None
                    await transport.send_email(
                        recipients, subject, builder.render_text(report.name, payload),
                        builder.render_html(report.name, payload), attachment,
                        csv_filename(report, now) if attachment else None,
                    )
                    summary["email"] = "sent"
                    run.recipients_count = len(recipients)
                except Exception as exc:  # noqa: BLE001 — se registra y se sigue con el otro canal
                    summary["email"] = "failed"
                    errors.append(f"email: {exc}")
            elif channel == "slack":
                if not incident_notifier.slack_configured():
                    summary["slack"] = "skipped"
                    errors.append("slack: SLACK_WEBHOOK_URL no configurada")
                    continue
                try:
                    await transport.send_slack(settings.slack_webhook_url.strip(),
                                               builder.render_slack(report.name, payload))
                    summary["slack"] = "sent"
                except Exception as exc:  # noqa: BLE001
                    summary["slack"] = "failed"
                    errors.append(f"slack: {exc}")
        run.status = _overall_status(summary)
    except Exception as exc:  # noqa: BLE001 — fallo al compilar: la corrida queda como failed
        run.status = "failed"
        errors.append(f"compilación: {exc}")

    run.channels_summary = json.dumps(summary)
    run.error_detail = "; ".join(errors)[:2000] or None
    run.completed_at = datetime.utcnow()
    db.add(run)
    await db.commit()
    logger.info("Reporte programado ejecutado", report_id=report.report_id, status=run.status,
                canales=summary, trigger=run.trigger)
    return run


# ── Despacho de reportes vencidos ───────────────────────────────────────────

async def try_acquire_reports_lock(db: AsyncSession) -> bool:
    """Mismo patrón que ``sync_service.try_acquire_sync_xact_lock`` con otro ID de lock."""
    if db.__class__.__name__ == "FakeSession":
        return True
    if hasattr(db, "get_bind"):
        try:
            bind = db.get_bind()
            if bind is not None and getattr(bind, "dialect", None) is not None:
                if bind.dialect.name != "postgresql":
                    return True
        except Exception:
            pass
    from sqlalchemy import text
    res = await db.execute(text("SELECT pg_try_advisory_xact_lock(:lock_id)"),
                           {"lock_id": REPORTS_ADVISORY_LOCK_ID})
    row = res.first()
    return bool(row[0]) if row is not None else False


async def due_reports(db: AsyncSession, now: datetime) -> list[ScheduledReport]:
    rows = (
        await db.execute(
            select(ScheduledReport).where(
                ScheduledReport.is_active.is_(True),
                ScheduledReport.next_run_at.is_not(None),
                ScheduledReport.next_run_at <= now,
            )
        )
    ).scalars().all()
    return [r for r in rows if r.is_active and r.next_run_at is not None and r.next_run_at <= now]


async def dispatch_due_reports(db: AsyncSession, *, trigger: str = "scheduled",
                               triggered_by: str = "scheduler", now: Optional[datetime] = None) -> dict:
    """Envía los reportes vencidos una sola vez aunque haya varios workers."""
    now = now or datetime.utcnow()
    if not await try_acquire_reports_lock(db):
        return {"locked": True, "processed": []}

    claimed = await due_reports(db, now)
    # Se reclaman ANTES de enviar: al hacer commit se libera el lock y otro
    # worker ya ve el next_run_at futuro, así no hay doble envío.
    for report in claimed:
        report.last_run_at = now
        report.next_run_at = next_run_for(report, now=now)
        report.updated_at = now
        db.add(report)
    await db.commit()

    processed = []
    for report in claimed:
        run = await execute_report(db, report, trigger=trigger, triggered_by=triggered_by, now=now)
        processed.append({"report_id": report.report_id, "run_id": run.run_id, "status": run.status})
    return {"locked": False, "processed": processed}


# ── Consultas y serialización ───────────────────────────────────────────────

async def get_report(db: AsyncSession, report_id: str) -> Optional[ScheduledReport]:
    return (
        await db.execute(select(ScheduledReport).where(ScheduledReport.report_id == report_id))
    ).scalars().first()


async def list_reports(db: AsyncSession) -> list[ScheduledReport]:
    rows = (await db.execute(select(ScheduledReport).order_by(desc(ScheduledReport.created_at)))).scalars().all()
    return sorted(rows, key=lambda r: (not r.is_active, r.next_run_at or datetime.max))


async def list_runs(db: AsyncSession, report_id: Optional[str] = None, limit: int = 20) -> list[ScheduledReportRun]:
    stmt = select(ScheduledReportRun).order_by(desc(ScheduledReportRun.created_at))
    if report_id:
        stmt = stmt.where(ScheduledReportRun.report_id == report_id)
    rows = (await db.execute(stmt.limit(limit))).scalars().all()
    if report_id:
        rows = [r for r in rows if r.report_id == report_id]
    return sorted(rows, key=lambda r: r.created_at or datetime.min, reverse=True)[:limit]


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() + "Z" if value else None


def serialize_report(r: ScheduledReport) -> dict:
    return {
        "report_id": r.report_id,
        "name": r.name,
        "description": r.description,
        "frequency": r.frequency,
        "day_of_week": r.day_of_week,
        "day_of_month": r.day_of_month,
        "send_hour_utc": r.send_hour_utc,
        "sections": _list(r.sections),
        "channels": _list(r.channels),
        "recipients": _list(r.recipients),
        "include_csv": r.include_csv,
        "is_active": r.is_active,
        "last_run_at": _iso(r.last_run_at),
        "next_run_at": _iso(r.next_run_at),
        "created_by": r.created_by,
        "updated_by": r.updated_by,
        "created_at": _iso(r.created_at),
        "updated_at": _iso(r.updated_at),
    }


def serialize_run(r: ScheduledReportRun) -> dict:
    try:
        channels = json.loads(r.channels_summary or "{}")
    except ValueError:
        channels = {}
    return {
        "run_id": r.run_id,
        "report_id": r.report_id,
        "report_name": r.report_name,
        "trigger": r.trigger,
        "triggered_by": r.triggered_by,
        "status": r.status,
        "channels": channels,
        "recipients_count": r.recipients_count,
        "error_detail": r.error_detail,
        "period_label": r.period_label,
        "created_at": _iso(r.created_at),
        "completed_at": _iso(r.completed_at),
    }


def new_report(values: dict, created_by: str, *, now: Optional[datetime] = None) -> ScheduledReport:
    now = now or datetime.utcnow()
    report = ScheduledReport(
        report_id=f"REP-{uuid.uuid4().hex[:8]}",
        name=values["name"],
        frequency=values["frequency"],
        sections="[]",
        channels="[]",
        recipients="[]",
        is_active=True,
        created_by=created_by or "system",
        created_at=now,
        updated_at=now,
    )
    apply_schedule(report, values, now=now)
    return report


def meta() -> dict[str, Any]:
    return {
        "sections": [{"key": k, "title": builder.SECTION_TITLES[k]} for k in builder.ALLOWED_SECTIONS],
        "frequencies": list(FREQUENCIES),
        "channels": {
            "email_configured": incident_notifier.email_configured(),
            "slack_configured": incident_notifier.slack_configured(),
        },
        "scheduler_enabled": bool(settings.reports_scheduler_enabled),
        "cron_webhook_enabled": bool(settings.reports_cron_secret),
    }
