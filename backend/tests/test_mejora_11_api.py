"""Pruebas de API de la Recomendación #11 — Reportes programados."""
from datetime import datetime, timedelta

import pytest

from app.config import settings
from app.main import app
from app.models_db import ActionLog, ScheduledReport, ScheduledReportRun
from app.routers.auth import create_token
from app.services import scheduled_reports_service as svc
from tests.test_mejora_11_logica import FakeTransport, _datos

BASE = "/api/v1/reports"
BODY = {"name": "Resumen semanal", "frequency": "weekly", "day_of_week": 0, "send_hour_utc": 12,
        "sections": ["clinic_health", "executive_mrr"], "channels": ["email", "slack"],
        "recipients": ["ops@wellq.cl"], "include_csv": True}


def _token(perms, role="admin"):
    token = create_token({"user_id": "usr-x", "email": "x@wellq.test", "role": role, "type": "access",
                          "permissions": perms}, timedelta(minutes=5))
    return {"Authorization": f"Bearer {token}"}


VIEWER = ["analytics.view"]
MANAGER = ["analytics.view", "platform.manage"]


@pytest.fixture
def transport(monkeypatch):
    fake = FakeTransport()
    monkeypatch.setattr(svc, "transport", fake)
    monkeypatch.setattr(settings, "smtp_user", "reportes@wellq.cl")
    monkeypatch.setattr(settings, "smtp_app_password", "clave-prueba")
    monkeypatch.setattr(settings, "slack_webhook_url", "https://hooks.slack.com/services/T/B/X")
    return fake


@pytest.fixture
def reporte(fake_db):
    values = svc.validate_schedule(BODY)
    report = svc.new_report(values, "qa@wellq.test")
    datos = _datos().data
    fake_db.data.update(datos)
    fake_db.data[ScheduledReport] = [report]
    return report


def test_rutas_registradas():
    paths = {getattr(r, "path", "") for r in app.routes}
    for p in ["/meta", "/schedules", "/schedules/{report_id}", "/schedules/{report_id}/toggle",
              "/schedules/{report_id}/preview", "/schedules/{report_id}/export.csv",
              "/schedules/{report_id}/run", "/runs", "/dispatch-due"]:
        assert BASE + p in paths, p


# ── RBAC ────────────────────────────────────────────────────────────────────

def test_sin_analytics_view_es_403(client):
    h = _token(["billing.view"])
    assert client.get(f"{BASE}/schedules", headers=h).status_code == 403
    assert client.get(f"{BASE}/meta", headers=h).status_code == 403


def test_solo_lectura_puede_consultar(client, reporte):
    h = _token(VIEWER)
    rid = reporte.report_id
    for url in ["/meta", "/schedules", f"/schedules/{rid}/preview", f"/schedules/{rid}/export.csv", "/runs"]:
        assert client.get(BASE + url, headers=h).status_code == 200, url


def test_solo_lectura_no_puede_modificar(client, reporte, transport):
    h = _token(VIEWER)
    rid = reporte.report_id
    assert client.post(f"{BASE}/schedules", json=BODY, headers=h).status_code == 403
    assert client.put(f"{BASE}/schedules/{rid}", json=BODY, headers=h).status_code == 403
    assert client.patch(f"{BASE}/schedules/{rid}/toggle", json={"is_active": False}, headers=h).status_code == 403
    assert client.delete(f"{BASE}/schedules/{rid}", headers=h).status_code == 403
    assert client.post(f"{BASE}/schedules/{rid}/run", headers=h).status_code == 403
    assert transport.emails == []


def test_gestion_sin_analytics_view_es_403(client):
    assert client.post(f"{BASE}/schedules", json=BODY, headers=_token(["platform.manage"])).status_code == 403


def test_settings_manage_tambien_gestiona(client, fake_db):
    r = client.post(f"{BASE}/schedules", json=BODY, headers=_token(["analytics.view", "settings.manage"]))
    assert r.status_code == 201, r.text


# ── CRUD y auditoría ────────────────────────────────────────────────────────

def test_crear_calcula_next_run_y_audita(client, fake_db, auth_header):
    r = client.post(f"{BASE}/schedules", json=BODY, headers=auth_header)
    assert r.status_code == 201, r.text
    data = r.json()["data"]
    assert data["report_id"].startswith("REP-") and data["is_active"] is True
    assert datetime.fromisoformat(data["next_run_at"].rstrip("Z")).weekday() == 0
    assert any(isinstance(o, ScheduledReport) for o in fake_db.added)
    assert any(isinstance(o, ActionLog) and o.action == "CREATE" and o.entity_type == "scheduled_report"
               for o in fake_db.added)


@pytest.mark.parametrize("cambio", [{"frequency": "monthly", "day_of_month": 31}, {"channels": ["fax"]},
                                    {"recipients": ["no-es-correo"]}, {"sections": ["otra"]},
                                    {"frequency": "weekly", "day_of_week": None}])
def test_crear_invalido_es_422(client, auth_header, cambio):
    assert client.post(f"{BASE}/schedules", json={**BODY, **cambio}, headers=auth_header).status_code == 422


def test_listar(client, auth_header, reporte):
    r = client.get(f"{BASE}/schedules", headers=auth_header)
    assert r.json()["data"][0]["report_id"] == reporte.report_id


def test_editar_recalcula_y_audita(client, fake_db, auth_header, reporte):
    body = {**BODY, "frequency": "daily", "send_hour_utc": 7, "name": "Diario"}
    r = client.put(f"{BASE}/schedules/{reporte.report_id}", json=body, headers=auth_header)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["name"] == "Diario" and reporte.day_of_week is None
    assert reporte.next_run_at.hour == 7
    assert any(isinstance(o, ActionLog) and o.action == "UPDATE" for o in fake_db.added)


def test_pausar_y_reactivar(client, auth_header, reporte):
    r = client.patch(f"{BASE}/schedules/{reporte.report_id}/toggle", json={"is_active": False}, headers=auth_header)
    assert r.json()["data"]["is_active"] is False and r.json()["data"]["next_run_at"] is None
    r = client.patch(f"{BASE}/schedules/{reporte.report_id}/toggle", json={"is_active": True}, headers=auth_header)
    assert r.json()["data"]["next_run_at"] is not None


def test_eliminar_y_auditar(client, fake_db, auth_header, reporte):
    r = client.delete(f"{BASE}/schedules/{reporte.report_id}", headers=auth_header)
    assert r.status_code == 200
    assert reporte in fake_db.deleted
    assert any(isinstance(o, ActionLog) and o.action == "DELETE" for o in fake_db.added)


def test_inexistente_es_404(client, auth_header):
    assert client.get(f"{BASE}/schedules/REP-nada/preview", headers=auth_header).status_code == 404
    assert client.delete(f"{BASE}/schedules/REP-nada", headers=auth_header).status_code == 404


# ── Vista previa, CSV, envío e historial ────────────────────────────────────

def test_vista_previa(client, auth_header, reporte):
    r = client.get(f"{BASE}/schedules/{reporte.report_id}/preview", headers=auth_header)
    data = r.json()["data"]
    assert set(data["metrics"]["sections"]) == {"clinic_health", "executive_mrr"}
    assert "WellQ Admin" in data["html_preview"] and data["period_label"]


def test_exportar_csv(client, auth_header, reporte):
    r = client.get(f"{BASE}/schedules/{reporte.report_id}/export.csv", headers=auth_header)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"] and ".csv" in r.headers["content-disposition"]
    assert r.content.decode("utf-8-sig").splitlines()[0] == "Seccion,Metrica,Valor,Detalle"


def test_enviar_ahora(client, fake_db, auth_header, reporte, transport):
    r = client.post(f"{BASE}/schedules/{reporte.report_id}/run", headers=auth_header)
    assert r.status_code == 200, r.text
    run = r.json()["data"]
    assert run["status"] == "sent" and run["trigger"] == "manual"
    assert run["channels"] == {"email": "sent", "slack": "sent"}
    assert len(transport.emails) == 1 and len(transport.slacks) == 1


def test_meta_sin_secretos(client, auth_header, transport):
    r = client.get(f"{BASE}/meta", headers=auth_header)
    data = r.json()["data"]
    assert data["channels"] == {"email_configured": True, "slack_configured": True}
    assert "hooks.slack.com" not in r.text and "clave-prueba" not in r.text


def test_historial(client, fake_db, auth_header):
    fake_db.data[ScheduledReportRun] = [
        ScheduledReportRun(run_id="REPRUN-1", report_id="REP-a", report_name="A", trigger="manual",
                           status="sent", created_at=datetime(2026, 11, 25)),
        ScheduledReportRun(run_id="REPRUN-2", report_id="REP-b", report_name="B", trigger="scheduled",
                           status="failed", created_at=datetime(2026, 11, 26)),
    ]
    r = client.get(f"{BASE}/runs?report_id=REP-a", headers=auth_header)
    assert [x["run_id"] for x in r.json()["data"]] == ["REPRUN-1"]
    assert client.get(f"{BASE}/runs?limit=500", headers=auth_header).status_code == 422


# ── dispatch-due (Cloud Scheduler) ──────────────────────────────────────────

def test_dispatch_sin_secreto_ni_auth(client, monkeypatch):
    monkeypatch.setattr(settings, "reports_cron_secret", "s3creto-largo")
    assert client.post(f"{BASE}/dispatch-due").status_code == 401


def test_dispatch_secreto_invalido(client, monkeypatch):
    monkeypatch.setattr(settings, "reports_cron_secret", "s3creto-largo")
    assert client.post(f"{BASE}/dispatch-due", headers={"X-Scheduler-Secret": "otro"}).status_code == 403


def test_dispatch_secreto_vacio_no_habilita(client, monkeypatch):
    monkeypatch.setattr(settings, "reports_cron_secret", "")
    assert client.post(f"{BASE}/dispatch-due", headers={"X-Scheduler-Secret": ""}).status_code in (401, 403)


def test_dispatch_con_secreto_valido(client, reporte, transport, monkeypatch):
    monkeypatch.setattr(settings, "reports_cron_secret", "s3creto-largo")
    reporte.next_run_at = datetime.utcnow() - timedelta(minutes=1)
    r = client.post(f"{BASE}/dispatch-due", headers={"X-Scheduler-Secret": "s3creto-largo"})
    assert r.status_code == 200, r.text
    procesados = r.json()["data"]["processed"]
    assert len(procesados) == 1 and procesados[0]["status"] == "sent"
    assert len(transport.emails) == 1


def test_dispatch_con_usuario_gestor(client, transport):
    r = client.post(f"{BASE}/dispatch-due", headers=_token(MANAGER))
    assert r.status_code == 200 and r.json()["data"]["processed"] == []


def test_dispatch_usuario_solo_lectura(client):
    assert client.post(f"{BASE}/dispatch-due", headers=_token(VIEWER)).status_code == 403
