"""Pruebas de lógica de la Mejora #19 — Notificaciones de incidentes (Email y Slack).

No usan red ni SMTP: el transporte se reemplaza por un doble que registra
los envíos.
"""
import asyncio
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.config import settings
from app.models_db import AdminUser, Alert, IncidentNotificationDelivery, Permission, RolePermission
from app.services import incident_notifier as svc
from app.services import sync_service
from tests.conftest import FakeSession


class FakeTransport:
    def __init__(self, fail_email=False, fail_slack=False):
        self.emails, self.slacks = [], []
        self.fail_email, self.fail_slack = fail_email, fail_slack

    async def send_email(self, recipients, subject, text, html_body):
        if self.fail_email:
            raise RuntimeError("smtp caído")
        self.emails.append({"to": recipients, "subject": subject, "text": text, "html": html_body})

    async def send_slack(self, webhook_url, payload):
        if self.fail_slack:
            raise RuntimeError("slack 500")
        self.slacks.append({"url": webhook_url, "payload": payload})


@pytest.fixture
def transport(monkeypatch):
    fake = FakeTransport()
    monkeypatch.setattr(svc, "transport", fake)
    return fake


@pytest.fixture
def canales(monkeypatch):
    """Email y Slack configurados con valores de prueba."""
    monkeypatch.setattr(settings, "smtp_user", "alertas@wellq.cl")
    monkeypatch.setattr(settings, "smtp_app_password", "clave-prueba")
    monkeypatch.setattr(settings, "slack_webhook_url", "https://hooks.slack.com/services/T/B/X")
    monkeypatch.setattr(settings, "incident_email_recipients", "")
    monkeypatch.setattr(settings, "incident_min_severity", "high")
    monkeypatch.setattr(settings, "incident_notifications_enabled", True)


def _alert(severity="critical", **kw):
    data = dict(alert_id="alert-1", type="sync_failure", title="Sincronización caída",
                message="3 fallos seguidos", severity=severity, related_type="sync_run",
                related_id="sync-abc", created_at=datetime(2026, 11, 18, 10, 0))
    data.update(kw)
    return Alert(**data)


def _db_con_admins():
    perm = Permission(id=7, key="platform.manage", label="Manage", module="Platform Ops")
    link = RolePermission(role_id=3, permission_id=7)
    admins = [
        AdminUser(user_id="u1", full_name="Ops", email="ops@wellq.cl", role="admin", status="active", role_id=3),
        AdminUser(user_id="u2", full_name="Root", email="root@wellq.cl", role="super_admin", status="active"),
        AdminUser(user_id="u3", full_name="Fin", email="fin@wellq.cl", role="admin", status="active", role_id=9),
        AdminUser(user_id="u4", full_name="Baja", email="baja@wellq.cl", role="admin", status="inactive", role_id=3),
    ]
    return FakeSession({Permission: [perm], RolePermission: [link], AdminUser: admins})


# ── Reglas de severidad ─────────────────────────────────────────────────────

@pytest.mark.parametrize("severity,esperado", [
    ("critical", True), ("high", True), ("HIGH", True), ("medium", False), ("low", False), (None, False),
])
def test_umbral_por_defecto_high(canales, severity, esperado):
    assert svc.should_notify(SimpleNamespace(severity=severity)) is esperado


def test_umbral_configurable(canales, monkeypatch):
    monkeypatch.setattr(settings, "incident_min_severity", "medium")
    assert svc.should_notify(SimpleNamespace(severity="medium")) is True
    assert svc.should_notify(SimpleNamespace(severity="low")) is False


def test_umbral_invalido_vuelve_a_high(canales, monkeypatch):
    monkeypatch.setattr(settings, "incident_min_severity", "urgente")
    assert svc.min_severity() == "high"


def test_desactivado_no_notifica(canales, monkeypatch):
    monkeypatch.setattr(settings, "incident_notifications_enabled", False)
    assert svc.should_notify(SimpleNamespace(severity="critical")) is False


def test_canales_configurados(canales, monkeypatch):
    assert svc.email_configured() and svc.slack_configured()
    monkeypatch.setattr(settings, "slack_webhook_url", "http://inseguro")
    assert svc.slack_configured() is False


# ── Destinatarios ───────────────────────────────────────────────────────────

def test_destinatarios_admins_con_platform_manage(canales):
    emails, origen = asyncio.run(svc.resolve_email_recipients(_db_con_admins()))
    assert origen == "admins"
    assert emails == ["ops@wellq.cl", "root@wellq.cl"]   # ni fin (otro rol) ni baja (inactivo)


def test_destinatarios_lista_fija_tiene_prioridad(canales, monkeypatch):
    monkeypatch.setattr(settings, "incident_email_recipients", "a@x.cl, b@x.cl,no-es-correo")
    emails, origen = asyncio.run(svc.resolve_email_recipients(_db_con_admins()))
    assert origen == "fixed" and emails == ["a@x.cl", "b@x.cl"]


# ── Contenido ───────────────────────────────────────────────────────────────

def test_mensaje_incluye_datos_y_escapa_html():
    content = svc.build_message(_alert(title="<script>x</script>", message="línea"))
    assert content["subject"].startswith("[WellQ Admin][CRITICAL]")
    assert "<script>" not in content["html"] and "&lt;script&gt;" in content["html"]
    assert "Severidad: critical" in content["text"]
    assert content["slack"]["blocks"][0]["type"] == "header"


# ── Despacho ────────────────────────────────────────────────────────────────

def test_despacho_envia_por_ambos_canales_y_registra(canales, transport):
    db = _db_con_admins()
    deliveries = asyncio.run(svc.dispatch_alert(db, _alert()))
    assert {d.channel: d.status for d in deliveries} == {"email": "sent", "slack": "sent"}
    assert transport.emails[0]["to"] == ["ops@wellq.cl", "root@wellq.cl"]
    assert transport.slacks[0]["url"].startswith("https://hooks.slack.com/")
    assert sum(isinstance(o, IncidentNotificationDelivery) for o in db.added) == 2


def test_canal_sin_configurar_queda_omitido(canales, transport, monkeypatch):
    monkeypatch.setattr(settings, "slack_webhook_url", "")
    monkeypatch.setattr(settings, "smtp_app_password", "")
    deliveries = asyncio.run(svc.dispatch_alert(_db_con_admins(), _alert()))
    assert {d.status for d in deliveries} == {"skipped"}
    assert transport.emails == [] and transport.slacks == []


def test_sin_destinatarios_omite_email(canales, transport):
    deliveries = asyncio.run(svc.dispatch_alert(FakeSession(), _alert(), channels=["email"]))
    assert deliveries[0].status == "skipped"
    assert "destinatarios" in deliveries[0].detail


def test_fallo_de_envio_se_registra_como_failed(canales, monkeypatch):
    monkeypatch.setattr(svc, "transport", FakeTransport(fail_email=True, fail_slack=True))
    deliveries = asyncio.run(svc.dispatch_alert(_db_con_admins(), _alert()))
    assert {d.status for d in deliveries} == {"failed"}
    assert any("smtp caído" in d.detail for d in deliveries)


def test_notify_respeta_umbral(canales, transport):
    assert asyncio.run(svc.notify_new_alert(_db_con_admins(), _alert(severity="medium"))) == []
    assert transport.emails == [] and transport.slacks == []


def test_notify_nunca_lanza_excepciones(canales, monkeypatch):
    async def explota(*a, **k):
        raise RuntimeError("bd caída")
    monkeypatch.setattr(svc, "dispatch_alert", explota)
    assert asyncio.run(svc.notify_new_alert(FakeSession(), _alert())) == []


def test_envio_de_prueba_usa_un_solo_canal(canales, transport):
    delivery = asyncio.run(svc.send_test(_db_con_admins(), "slack", "qa@wellq.test"))
    assert delivery.channel == "slack" and delivery.status == "sent"
    assert delivery.alert_id == "test" and delivery.triggered_by == "qa@wellq.test"
    assert transport.emails == []


def test_serializa_delivery():
    d = IncidentNotificationDelivery(delivery_id="inc-1", alert_id="a", channel="email", status="sent",
                                     created_at=datetime(2026, 1, 1), sent_at=datetime(2026, 1, 1, 0, 1))
    data = svc.serialize_delivery(d)
    assert data["created_at"].endswith("Z") and data["sent_at"].endswith("Z")


# ── Enganche con la mejora #7 ───────────────────────────────────────────────

def test_alerta_de_sync_dispara_notificacion(monkeypatch):
    llamadas = []

    async def fake_notify(db, alert):
        llamadas.append(alert)
        return []

    async def tres_fallos(db):
        return 3

    monkeypatch.setattr(svc, "notify_new_alert", fake_notify)
    monkeypatch.setattr(sync_service, "check_consecutive_failures", tres_fallos)
    run = SimpleNamespace(status="failed", run_id="sync-x", error="timeout")
    creada = asyncio.run(sync_service.maybe_raise_failure_alert(FakeSession(), run))
    assert creada is True
    assert len(llamadas) == 1 and llamadas[0].severity == "critical"
