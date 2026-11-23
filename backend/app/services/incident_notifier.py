"""Mejora #19 — Notificaciones multicanal de incidentes (Email y Slack).

Cuando se crea una alerta con severidad igual o superior a
``INCIDENT_MIN_SEVERITY`` (por defecto ``high``), se avisa por:

* **Email**: SMTP ya configurado en el proyecto (``SMTP_*``). Destinatarios:
  la lista fija ``INCIDENT_EMAIL_RECIPIENTS`` o, si está vacía, los admins
  activos con el permiso ``platform.manage`` (y los ``super_admin``).
* **Slack**: webhook entrante (``SLACK_WEBHOOK_URL``).

Cada envío queda registrado por canal en ``incident_notification_deliveries``
con estado ``sent``, ``failed`` o ``skipped`` (canal sin configurar o sin
destinatarios). Un fallo de envío nunca interrumpe el flujo que creó la
alerta: se registra y se sigue.

El transporte real (SMTP y HTTP) vive en ``_Transport`` y se puede reemplazar
en las pruebas asignando ``incident_notifier.transport``.
"""

from __future__ import annotations

import asyncio
import html
import smtplib
import uuid
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from types import SimpleNamespace
from typing import Any, Iterable, Optional

import httpx
import structlog
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models_db import (
    AdminUser,
    Alert,
    IncidentNotificationDelivery,
    Permission,
    RolePermission,
)

logger = structlog.get_logger(__name__)

CHANNELS = ("email", "slack")
SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}
SEVERITY_EMOJI = {"low": "ℹ️", "medium": "⚠️", "high": "🔶", "critical": "🚨"}
NOTIFY_PERMISSION = "platform.manage"


# ── Reglas ──────────────────────────────────────────────────────────────────

def severity_rank(severity: Optional[str]) -> int:
    return SEVERITY_RANK.get((severity or "").strip().lower(), -1)


def min_severity() -> str:
    value = (settings.incident_min_severity or "high").strip().lower()
    return value if value in SEVERITY_RANK else "high"


def should_notify(alert: Any) -> bool:
    """True si las notificaciones están activas y la alerta llega al umbral."""
    if not settings.incident_notifications_enabled:
        return False
    return severity_rank(getattr(alert, "severity", None)) >= SEVERITY_RANK[min_severity()]


def email_configured() -> bool:
    return bool(settings.smtp_user and settings.smtp_app_password)


def slack_configured() -> bool:
    return (settings.slack_webhook_url or "").strip().startswith("https://")


def _fixed_recipients() -> list[str]:
    raw = settings.incident_email_recipients or ""
    return sorted({e.strip() for e in raw.split(",") if "@" in e})


async def resolve_email_recipients(db: AsyncSession) -> tuple[list[str], str]:
    """Devuelve (destinatarios, origen). Origen: ``fixed`` o ``admins``."""
    fixed = _fixed_recipients()
    if fixed:
        return fixed, "fixed"

    perm = (
        await db.execute(select(Permission).where(Permission.key == NOTIFY_PERMISSION))
    ).scalars().first()
    role_ids: set[int] = set()
    if perm is not None:
        links = (
            await db.execute(
                select(RolePermission).where(RolePermission.permission_id == perm.id)
            )
        ).scalars().all()
        role_ids = {l.role_id for l in links if getattr(l, "permission_id", None) == perm.id}

    admins = (
        await db.execute(select(AdminUser).where(AdminUser.status == "active"))
    ).scalars().all()
    emails = {
        a.email.strip()
        for a in admins
        if getattr(a, "email", None)
        and (getattr(a, "status", "active") or "active") == "active"
        and (a.role == "super_admin" or getattr(a, "role_id", None) in role_ids)
    }
    return sorted(emails), "admins"


# ── Contenido del mensaje ───────────────────────────────────────────────────

def _fmt_time(value: Optional[datetime]) -> str:
    return (value or datetime.utcnow()).strftime("%Y-%m-%d %H:%M UTC")


def build_message(alert: Any) -> dict:
    """Arma asunto, texto plano, HTML y payload de Slack para una alerta."""
    severity = (getattr(alert, "severity", "") or "").lower()
    title = getattr(alert, "title", "") or "Incidente"
    message = getattr(alert, "message", "") or ""
    alert_type = getattr(alert, "type", "") or "—"
    related = " ".join(
        str(v) for v in (getattr(alert, "related_type", None), getattr(alert, "related_id", None)) if v
    ) or "—"
    when = _fmt_time(getattr(alert, "created_at", None))
    emoji = SEVERITY_EMOJI.get(severity, "⚠️")

    subject = f"[WellQ Admin][{severity.upper() or 'ALERTA'}] {title}"
    text = (
        f"{title}\n\n{message}\n\n"
        f"Severidad: {severity or '—'}\nTipo: {alert_type}\nRelacionado: {related}\n"
        f"Fecha: {when}\nID de alerta: {getattr(alert, 'alert_id', '—')}\n\n"
        "Revisa el detalle en WellQ Admin > Operaciones de plataforma."
    )
    e = html.escape
    html_body = f"""
      <div style="font-family:Arial,sans-serif;background:#020817;padding:24px;color:#e5faff;">
        <div style="max-width:560px;margin:auto;background:#07101d;border:1px solid rgba(34,211,238,.35);border-radius:16px;padding:28px;">
          <p style="color:#67e8f9;font-weight:700;margin:0 0 12px;">WellQ Admin · Incidente</p>
          <h2 style="color:white;margin:0 0 12px;">{e(title)}</h2>
          <p style="color:#cbd5e1;white-space:pre-line;">{e(message)}</p>
          <table style="color:#a8b3c7;font-size:13px;margin-top:16px;">
            <tr><td style="padding-right:12px;">Severidad</td><td><b style="color:#fca5a5;">{e(severity or '—')}</b></td></tr>
            <tr><td style="padding-right:12px;">Tipo</td><td>{e(alert_type)}</td></tr>
            <tr><td style="padding-right:12px;">Relacionado</td><td>{e(related)}</td></tr>
            <tr><td style="padding-right:12px;">Fecha</td><td>{e(when)}</td></tr>
          </table>
          <p style="color:#64748b;font-size:12px;margin-top:20px;">Revisa el detalle en WellQ Admin &gt; Operaciones de plataforma.</p>
        </div>
      </div>
    """
    slack = {
        "text": f"{emoji} *{title}* ({severity or 'alerta'})",
        "blocks": [
            {"type": "header", "text": {"type": "plain_text", "text": f"{emoji} {title}"[:150]}},
            {"type": "section", "text": {"type": "mrkdwn", "text": message[:2900] or "—"}},
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": f"*Severidad:* {severity or '—'} · *Tipo:* {alert_type} · *Relacionado:* {related} · {when}"}
                ],
            },
        ],
    }
    return {"subject": subject, "text": text, "html": html_body, "slack": slack}


# ── Transporte (SMTP y webhook) ─────────────────────────────────────────────

class _Transport:
    """Envío real. En pruebas se reemplaza por un doble sin red."""

    def _send_email_sync(self, recipients: list[str], subject: str, text: str, html_body: str) -> None:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"{settings.smtp_from_name} <{settings.smtp_from_email or settings.smtp_user}>"
        msg["To"] = ", ".join(recipients)
        msg.attach(MIMEText(text, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as server:
            server.starttls()
            server.login(settings.smtp_user, settings.smtp_app_password)
            server.send_message(msg)

    async def send_email(self, recipients: list[str], subject: str, text: str, html_body: str) -> None:
        await asyncio.to_thread(self._send_email_sync, recipients, subject, text, html_body)

    async def send_slack(self, webhook_url: str, payload: dict) -> None:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(webhook_url, json=payload)
        if response.status_code >= 300:
            raise RuntimeError(f"Slack respondió {response.status_code}: {response.text[:200]}")


transport = _Transport()


# ── Despacho ────────────────────────────────────────────────────────────────

def _new_delivery(alert: Any, channel: str, triggered_by: str) -> IncidentNotificationDelivery:
    return IncidentNotificationDelivery(
        delivery_id=f"inc-{uuid.uuid4().hex[:12]}",
        alert_id=str(getattr(alert, "alert_id", "") or "—"),
        alert_title=(getattr(alert, "title", None) or "")[:255] or None,
        severity=getattr(alert, "severity", None),
        channel=channel,
        triggered_by=triggered_by or "system",
        created_at=datetime.utcnow(),
    )


async def _deliver_email(db: AsyncSession, alert: Any, content: dict, delivery: IncidentNotificationDelivery) -> None:
    if not email_configured():
        delivery.status, delivery.detail = "skipped", "SMTP no configurado (SMTP_USER / SMTP_APP_PASSWORD)."
        return
    recipients, origin = await resolve_email_recipients(db)
    if not recipients:
        delivery.status, delivery.detail = "skipped", "Sin destinatarios: no hay admins activos con platform.manage."
        return
    delivery.recipients = ", ".join(recipients)
    try:
        await transport.send_email(recipients, content["subject"], content["text"], content["html"])
        delivery.status, delivery.sent_at = "sent", datetime.utcnow()
        delivery.detail = f"{len(recipients)} destinatario(s) ({origin})."
    except Exception as exc:  # noqa: BLE001 — se registra y se sigue
        delivery.status, delivery.detail = "failed", f"Error SMTP: {exc}"[:500]


async def _deliver_slack(alert: Any, content: dict, delivery: IncidentNotificationDelivery) -> None:
    if not slack_configured():
        delivery.status, delivery.detail = "skipped", "SLACK_WEBHOOK_URL no configurada."
        return
    delivery.recipients = "slack-webhook"
    try:
        await transport.send_slack(settings.slack_webhook_url.strip(), content["slack"])
        delivery.status, delivery.sent_at = "sent", datetime.utcnow()
        delivery.detail = "Mensaje publicado en Slack."
    except Exception as exc:  # noqa: BLE001
        delivery.status, delivery.detail = "failed", f"Error Slack: {exc}"[:500]


async def dispatch_alert(
    db: AsyncSession,
    alert: Any,
    *,
    triggered_by: str = "system",
    channels: Optional[Iterable[str]] = None,
) -> list[IncidentNotificationDelivery]:
    """Envía la alerta por los canales pedidos y registra cada intento.

    No aplica el umbral de severidad: eso lo decide ``notify_new_alert``.
    El reenvío manual desde el panel usa esta función directamente.
    """
    selected = [c for c in (channels or CHANNELS) if c in CHANNELS]
    content = build_message(alert)
    deliveries: list[IncidentNotificationDelivery] = []

    for channel in selected:
        delivery = _new_delivery(alert, channel, triggered_by)
        if channel == "email":
            await _deliver_email(db, alert, content, delivery)
        else:
            await _deliver_slack(alert, content, delivery)
        db.add(delivery)
        deliveries.append(delivery)

    await db.commit()
    logger.info(
        "Notificación de incidente procesada",
        alert_id=getattr(alert, "alert_id", None),
        resultados={d.channel: d.status for d in deliveries},
    )
    return deliveries


async def notify_new_alert(db: AsyncSession, alert: Any) -> list[IncidentNotificationDelivery]:
    """Punto de enganche para alertas nuevas. Nunca lanza excepciones."""
    try:
        if not should_notify(alert):
            return []
        return await dispatch_alert(db, alert, triggered_by="system")
    except Exception as exc:  # noqa: BLE001 — una notificación no debe romper el flujo
        logger.error("No se pudo notificar el incidente", error=str(exc),
                     alert_id=getattr(alert, "alert_id", None))
        return []


async def send_test(db: AsyncSession, channel: str, triggered_by: str) -> IncidentNotificationDelivery:
    """Envía un mensaje de prueba por un canal y lo deja en el historial."""
    fake_alert = SimpleNamespace(
        alert_id="test",
        type="notification_test",
        title="Prueba de notificaciones de WellQ Admin",
        message="Este es un mensaje de prueba enviado desde Operaciones de plataforma. "
                "Si lo recibes, el canal está bien configurado.",
        severity="high",
        related_type=None,
        related_id=None,
        created_at=datetime.utcnow(),
    )
    deliveries = await dispatch_alert(db, fake_alert, triggered_by=triggered_by, channels=[channel])
    return deliveries[0]


# ── Consultas para el panel ─────────────────────────────────────────────────

async def get_alert(db: AsyncSession, alert_id: str) -> Optional[Alert]:
    return (await db.execute(select(Alert).where(Alert.alert_id == alert_id))).scalars().first()


async def get_config(db: AsyncSession) -> dict:
    recipients, origin = await resolve_email_recipients(db)
    return {
        "enabled": bool(settings.incident_notifications_enabled),
        "min_severity": min_severity(),
        "channels": {
            "email": {
                "configured": email_configured(),
                "recipients_source": origin,
                "recipients_count": len(recipients),
            },
            "slack": {"configured": slack_configured()},
        },
    }


async def list_deliveries(db: AsyncSession, limit: int = 30) -> list[dict]:
    limit = max(1, min(int(limit or 30), 100))
    rows = (
        await db.execute(
            select(IncidentNotificationDelivery)
            .order_by(desc(IncidentNotificationDelivery.created_at))
            .limit(limit)
        )
    ).scalars().all()
    return [serialize_delivery(d) for d in rows[:limit]]


def serialize_delivery(d: IncidentNotificationDelivery) -> dict:
    def iso(v):
        return v.isoformat() + "Z" if v else None

    return {
        "delivery_id": d.delivery_id,
        "alert_id": d.alert_id,
        "alert_title": d.alert_title,
        "severity": d.severity,
        "channel": d.channel,
        "recipients": d.recipients,
        "status": d.status,
        "detail": d.detail,
        "triggered_by": d.triggered_by,
        "created_at": iso(d.created_at),
        "sent_at": iso(d.sent_at),
    }
