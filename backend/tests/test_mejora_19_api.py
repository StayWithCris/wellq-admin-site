"""Pruebas de API de la Mejora #19 — Notificaciones de incidentes."""
from datetime import datetime, timedelta

import pytest

from app.config import settings
from app.main import app
from app.models_db import ActionLog, Alert, IncidentNotificationDelivery
from app.routers.auth import create_token
from app.services import incident_notifier as svc
from tests.test_mejora_19_logica import FakeTransport

BASE = "/api/v1/incident-notifications"


@pytest.fixture
def transport(monkeypatch):
    fake = FakeTransport()
    monkeypatch.setattr(svc, "transport", fake)
    monkeypatch.setattr(settings, "smtp_user", "alertas@wellq.cl")
    monkeypatch.setattr(settings, "smtp_app_password", "clave-prueba")
    monkeypatch.setattr(settings, "slack_webhook_url", "https://hooks.slack.com/services/T/B/X")
    monkeypatch.setattr(settings, "incident_email_recipients", "ops@wellq.cl")
    return fake


def _token(perms):
    token = create_token(
        {"user_id": "usr-ops", "email": "ops@wellq.test", "role": "admin", "type": "access", "permissions": perms},
        timedelta(minutes=5),
    )
    return {"Authorization": f"Bearer {token}"}


def test_rutas_registradas():
    paths = {getattr(r, "path", "") for r in app.routes}
    for p in ["/config", "/deliveries", "/test", "/alerts/{alert_id}/dispatch"]:
        assert BASE + p in paths


def test_requieren_token(client):
    assert client.get(f"{BASE}/config").status_code in (401, 403)
    assert client.post(f"{BASE}/test", json={"channel": "slack"}).status_code in (401, 403)


def test_config_muestra_canales_sin_secretos(client, auth_header, transport):
    r = client.get(f"{BASE}/config", headers=auth_header)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["min_severity"] == "high"
    assert data["channels"]["email"] == {"configured": True, "recipients_source": "fixed", "recipients_count": 1}
    assert data["channels"]["slack"] == {"configured": True}
    assert "hooks.slack.com" not in r.text and "clave-prueba" not in r.text


def test_envio_de_prueba_y_auditoria(client, fake_db, auth_header, transport):
    r = client.post(f"{BASE}/test", json={"channel": "email"}, headers=auth_header)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["status"] == "sent"
    assert transport.emails[0]["to"] == ["ops@wellq.cl"]
    assert any(isinstance(o, ActionLog) and o.entity_type == "incident_notification" for o in fake_db.added)


def test_canal_invalido(client, auth_header, transport):
    assert client.post(f"{BASE}/test", json={"channel": "sms"}, headers=auth_header).status_code == 422


def test_solo_lectura_no_puede_enviar(client, transport):
    h = _token(["platform.view"])
    assert client.get(f"{BASE}/config", headers=h).status_code == 200
    assert client.post(f"{BASE}/test", json={"channel": "slack"}, headers=h).status_code == 403
    assert transport.slacks == []


def test_sin_platform_view_no_ve_historial(client):
    assert client.get(f"{BASE}/deliveries", headers=_token(["billing.view"])).status_code == 403


def test_reenviar_alerta_inexistente(client, auth_header, transport):
    assert client.post(f"{BASE}/alerts/no-existe/dispatch", headers=auth_header).status_code == 404


def test_reenviar_alerta_existente(client, fake_db, auth_header, transport):
    fake_db.data[Alert] = [Alert(alert_id="a-1", type="sync_failure", title="Caída", message="m",
                                 severity="high", created_at=datetime.utcnow())]
    r = client.post(f"{BASE}/alerts/a-1/dispatch", json={"channels": ["slack"]}, headers=auth_header)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert [d["channel"] for d in data] == ["slack"] and data[0]["status"] == "sent"
    assert data[0]["triggered_by"] == "qa@wellq.test"


def test_historial_de_envios(client, fake_db, auth_header):
    fake_db.data[IncidentNotificationDelivery] = [
        IncidentNotificationDelivery(delivery_id="inc-1", alert_id="a-1", channel="slack", status="sent",
                                     created_at=datetime(2026, 11, 18, 9, 0)),
    ]
    r = client.get(f"{BASE}/deliveries?limit=5", headers=auth_header)
    assert r.status_code == 200, r.text
    assert r.json()["data"][0]["delivery_id"] == "inc-1"


def test_limite_de_historial_validado(client, auth_header):
    assert client.get(f"{BASE}/deliveries?limit=500", headers=auth_header).status_code == 422
