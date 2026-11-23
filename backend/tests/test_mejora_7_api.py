"""Pruebas de API de la Recomendación #7 — Sincronización programable."""
import json
from datetime import datetime, timedelta

from app.main import app
from app.models_db import (
    ActionLog,
    Clinic,
    ClinicianSummary,
    PatientHealthSummary,
    PlatformSetting,
    SyncRun,
)
from app.services import sync_sources


class _FakeCollection:
    """Colección Mongo de mentira: guarda los pipelines que recibió."""

    def __init__(self, name, docs, calls):
        self._name = name
        self._docs = docs
        self._calls = calls

    def aggregate(self, pipeline):
        self._calls.append(self._name)
        return list(self._docs)


class _FakeMongo:
    """Base de datos Mongo de mentira: sin red, sin credenciales."""

    def __init__(self, docs_by_collection=None):
        self._docs = docs_by_collection or {}
        self.calls = []

    def __getitem__(self, name):
        return _FakeCollection(name, self._docs.get(name, []), self.calls)


def test_rutas_registradas():
    paths = {getattr(r, "path", "") for r in app.routes}
    for p in [
        "/api/sync-status",
        "/api/sync/config",
        "/api/sync/run",
        "/api/sync/history",
    ]:
        assert p in paths, f"Falta la ruta {p}"


def test_config_por_defecto(client):
    r = client.get("/api/sync/config")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["frequency_hours"] == 24
    assert data["allowed_frequencies"] == [6, 12, 24]
    assert data["last_run_at"] is None
    assert data["configured"] is False


def test_status_por_defecto_sin_token(client):
    """GET /api/sync-status es público: SettingsView lo consume sin permisos."""
    r = client.get("/api/sync-status")
    assert r.status_code == 200, r.text
    data = r.json()
    assert len(data["sources"]) == 5
    for source in data["sources"]:
        assert {"key", "name", "last_sync", "status"} <= set(source)
        assert source["status"] == "error"  # FakeSession no devuelve fechas


def test_config_y_run_requieren_token(client):
    assert client.put("/api/sync/config", json={"frequency_hours": 6}).status_code in (401, 403)
    assert client.post("/api/sync/run").status_code in (401, 403)


def test_cambiar_frecuencia(client, fake_db, auth_header):
    r = client.put("/api/sync/config", json={"frequency_hours": 6}, headers=auth_header)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["frequency_hours"] == 6
    assert data["configured"] is True
    assert any(isinstance(o, PlatformSetting) and o.setting_key == "sync_config" for o in fake_db.added)


def test_cambiar_frecuencia_deja_auditoria(client, fake_db, auth_header):
    client.put("/api/sync/config", json={"frequency_hours": 12}, headers=auth_header)
    logs = [o for o in fake_db.added if isinstance(o, ActionLog)]
    assert logs, "No se registró la auditoría del cambio de frecuencia"
    assert logs[0].action == "UPDATE"
    assert logs[0].entity_type == "sync_config"
    assert "frequency_hours" in (logs[0].changes or "")


def test_frecuencia_invalida(client, fake_db, auth_header):
    assert client.put("/api/sync/config", json={"frequency_hours": 7}, headers=auth_header).status_code == 422


def test_sincronizar_ahora_registra_historial(client, fake_db, auth_header):
    r = client.post("/api/sync/run", headers=auth_header)
    assert r.status_code == 200, r.text
    run = r.json()["data"]
    # Sin MongoDB configurado (entorno de pruebas) las fuentes Mongo se omiten:
    # la ejecución es válida, pero parcial.
    assert run["status"] == "partial"
    assert run["trigger"] == "manual"
    assert set(run["entities"]) == {
        "clinics", "patients", "soap_notes", "tickets", "clinicians", "skipped",
    }
    assert run["entities"]["skipped"] == ["patients", "clinicians"]
    assert any(isinstance(o, SyncRun) for o in fake_db.added)
    assert any(isinstance(o, ActionLog) and o.entity_type == "sync_run" for o in fake_db.added)


def test_sincronizacion_sin_mongo_omite_fuentes_y_no_falla(client, fake_db, auth_header):
    """Sin `mongodb_uri` las 2 fuentes Mongo van a `skipped` y el run termina."""
    r = client.post("/api/sync/run", headers=auth_header)
    assert r.status_code == 200, r.text
    run = r.json()["data"]
    assert run["status"] != "failed"
    assert run["entities"]["skipped"] == ["patients", "clinicians"]
    # Las fuentes locales sí se ejecutaron: el run no se abortó.
    assert set(run["entities"]) >= {"clinics", "soap_notes", "tickets"}
    # No se escribió ningún resumen de pacientes ni de clínicos.
    assert not any(isinstance(o, PatientHealthSummary) for o in fake_db.added)
    assert not any(isinstance(o, ClinicianSummary) for o in fake_db.added)


def test_sincronizacion_con_mongo_ingiere_patients_y_clinicians(
    client, fake_db, auth_header, monkeypatch
):
    """Con Mongo disponible las fuentes agregan datos y el run queda `completed`."""
    clinic = Clinic(
        clinic_id="cl-1",
        name="Clínica Uno",
        mongo_clinic_id="507f1f77bcf86cd799439011",
    )
    fake_db.data[Clinic] = [clinic]

    mongo = _FakeMongo({
        "patients": [{
            "_id": None,
            "total_patients": 10,
            "at_risk": 3,
            "declining": 2,
            "stable": 4,
            "improving": 1,
        }],
        "clinicians": [{
            "_id": "cl-1",
            "total_clinicians": 5,
            "active_clinicians": 4,
            "specialties": ["Kinesiología"],
        }],
    })
    monkeypatch.setattr(sync_sources, "get_mongo_db_or_none", lambda: mongo)

    r = client.post("/api/sync/run", headers=auth_header)
    assert r.status_code == 200, r.text
    run = r.json()["data"]
    assert run["status"] == "completed"
    assert run["entities"]["skipped"] == []
    assert run["entities"]["patients"] == 1
    assert run["entities"]["clinicians"] == 1

    summaries = [o for o in fake_db.added if isinstance(o, PatientHealthSummary)]
    assert summaries, "No se escribió el resumen de pacientes"
    assert summaries[0].clinic_id == "cl-1"
    assert summaries[0].total_patients == 10
    assert summaries[0].at_risk == 3
    assert summaries[0].improving == 1

    clinicians = [o for o in fake_db.added if isinstance(o, ClinicianSummary)]
    assert clinicians, "No se escribió el resumen de clínicos"
    assert clinicians[0].total_clinicians == 5
    assert clinicians[0].active_clinicians == 4
    assert json.loads(clinicians[0].specialties) == ["Kinesiología"]

    # La agregación se hizo sobre la colección correcta.
    assert mongo.calls == ["patients", "clinicians"]


def test_mongo_roto_omite_fuentes_sin_romper_el_run(client, fake_db, auth_header, monkeypatch):
    """Si `get_mongo_db()` falla, la fuente se omite y el run continúa."""
    monkeypatch.setattr(
        sync_sources, "get_mongo_db_or_none", lambda: None
    )
    r = client.post("/api/sync/run", headers=auth_header)
    assert r.status_code == 200, r.text
    run = r.json()["data"]
    assert run["status"] == "partial"
    assert run["entities"]["skipped"] == ["patients", "clinicians"]


def test_sincronizacion_concurrente_devuelve_409(client, fake_db, auth_header):
    fake_db.data[SyncRun] = [
        SyncRun(
            run_id="sync-en-curso",
            trigger="manual",
            status="running",
            frequency_hours=24,
            started_at=datetime.utcnow(),
        )
    ]
    r = client.post("/api/sync/run", headers=auth_header)
    assert r.status_code == 409, r.text
    assert "sync-en-curso" in r.json()["detail"]


def test_ejecucion_huerfana_se_cierra_y_permite_sincronizar(client, fake_db, auth_header):
    fake_db.data[SyncRun] = [
        SyncRun(
            run_id="sync-huerfana",
            trigger="manual",
            status="running",
            frequency_hours=24,
            started_at=datetime.utcnow() - timedelta(minutes=30),
        )
    ]
    r = client.post("/api/sync/run", headers=auth_header)
    assert r.status_code == 200, r.text
    assert fake_db.data[SyncRun][0].status == "failed"
    assert fake_db.data[SyncRun][0].error == "ejecución huérfana"


def test_historial(client, fake_db):
    started = datetime.utcnow() - timedelta(hours=2)
    fake_db.data[SyncRun] = [
        SyncRun(
            run_id="sync-1", trigger="manual", status="completed", frequency_hours=24,
            entities='{"patients": 7}', created_by="qa@wellq.test",
            started_at=started, completed_at=started + timedelta(seconds=2),
        )
    ]
    r = client.get("/api/sync/history?limit=5")
    assert r.status_code == 200, r.text
    runs = r.json()["data"]
    assert len(runs) == 1
    assert runs[0]["id"] == "sync-1"
    assert runs[0]["duration_ms"] == 2000
    assert runs[0]["entities"]["patients"] == 7


def test_historial_limite_invalido(client):
    assert client.get("/api/sync/history?limit=0").status_code == 422


# ── Pruebas de mejoras (v1, alertas y compatibilidad) ────────────────────────

def test_rutas_v1_registradas():
    """Punto 5: Los endpoints están expuestos en /api/v1/ y en /api/ (legacy)."""
    paths = {getattr(r, "path", "") for r in app.routes}
    for p in [
        "/api/v1/sync-status",
        "/api/v1/sync/config",
        "/api/v1/sync/run",
        "/api/v1/sync/history",
        "/api/sync-status",
        "/api/sync/config",
        "/api/sync/run",
        "/api/sync/history",
    ]:
        assert p in paths, f"Falta la ruta {p}"


def test_endpoints_v1_funcionan_identicos(client):
    """Punto 5: /api/v1/sync/config y /api/v1/sync-status devuelven 200."""
    r_v1 = client.get("/api/v1/sync/config")
    r_legacy = client.get("/api/sync/config")
    assert r_v1.status_code == 200
    assert r_legacy.status_code == 200
    assert r_v1.json() == r_legacy.json()

    s_v1 = client.get("/api/v1/sync-status")
    s_legacy = client.get("/api/sync-status")
    assert s_v1.status_code == 200
    assert s_legacy.status_code == 200
    assert "failure_alert" in s_v1.json()
    assert s_v1.json()["failure_alert"]["alert_active"] is False


def test_alerta_fallos_consecutivos(client, fake_db):
    """Punto 4: 3 fallos consecutivos disparan flag de alerta activa."""
    now = datetime.utcnow()
    fake_db.data[SyncRun] = [
        SyncRun(run_id="run-3", trigger="manual", status="failed", error="timeout", started_at=now),
        SyncRun(run_id="run-2", trigger="scheduled", status="failed", error="conn error", started_at=now - timedelta(hours=1)),
        SyncRun(run_id="run-1", trigger="scheduled", status="failed", error="timeout", started_at=now - timedelta(hours=2)),
    ]
    r = client.get("/api/v1/sync-status")
    assert r.status_code == 200
    data = r.json()
    assert data["failure_alert"]["alert_active"] is True
    assert data["failure_alert"]["consecutive_failures"] == 3


def test_alerta_cascada_simultanea_no_dispara_falsa_alarma(client, fake_db):
    """Efecto cascada: 3 fallos simultáneos en el mismo segundo cuentan como 1 incidente."""
    now = datetime.utcnow()
    fake_db.data[SyncRun] = [
        SyncRun(run_id="run-w3", trigger="scheduled", status="failed", error="timeout", started_at=now),
        SyncRun(run_id="run-w2", trigger="scheduled", status="failed", error="timeout", started_at=now - timedelta(seconds=2)),
        SyncRun(run_id="run-w1", trigger="scheduled", status="failed", error="timeout", started_at=now - timedelta(seconds=4)),
    ]
    r = client.get("/api/v1/sync-status")
    assert r.status_code == 200
    data = r.json()
    # No dispara la alerta crítica (solo 1 ventana lógica de fallo)
    assert data["failure_alert"]["alert_active"] is False
    assert data["failure_alert"]["consecutive_failures"] == 1


def test_mongo_batch_ejecuta_un_solo_aggregate_con_multiples_clinicas(
    client, fake_db, auth_header, monkeypatch
):
    """Verifica que con 3 clínicas, MongoAggregateSource solo llama aggregate() 1 vez por colección."""
    c1 = Clinic(clinic_id="cl-1", name="Clínica Uno", mongo_clinic_id="507f1f77bcf86cd799439011")
    c2 = Clinic(clinic_id="cl-2", name="Clínica Dos", mongo_clinic_id="507f1f77bcf86cd799439012")
    c3 = Clinic(clinic_id="cl-3", name="Clínica Tres", mongo_clinic_id="507f1f77bcf86cd799439013")
    fake_db.data[Clinic] = [c1, c2, c3]

    mongo = _FakeMongo({
        "patients": [
            {"_id": "507f1f77bcf86cd799439011", "total_patients": 5, "at_risk": 1, "declining": 1, "stable": 2, "improving": 1},
            {"_id": "507f1f77bcf86cd799439012", "total_patients": 8, "at_risk": 2, "declining": 1, "stable": 3, "improving": 2},
            {"_id": "507f1f77bcf86cd799439013", "total_patients": 12, "at_risk": 3, "declining": 2, "stable": 4, "improving": 3},
        ],
        "clinicians": [
            {"_id": "cl-1", "total_clinicians": 2, "active_clinicians": 2, "specialties": ["Kinesio"]},
            {"_id": "cl-2", "total_clinicians": 4, "active_clinicians": 3, "specialties": ["Nutrición"]},
            {"_id": "cl-3", "total_clinicians": 6, "active_clinicians": 5, "specialties": ["Medicina"]},
        ],
    })
    monkeypatch.setattr(sync_sources, "get_mongo_db_or_none", lambda: mongo)

    r = client.post("/api/sync/run", headers=auth_header)
    assert r.status_code == 200, r.text
    run = r.json()["data"]
    assert run["status"] == "completed"
    assert run["entities"]["patients"] == 3
    assert run["entities"]["clinicians"] == 3

    # CRÍTICO: Exactamente 2 llamadas a Mongo en total (1 a patients, 1 a clinicians), NO 6
    assert mongo.calls == ["patients", "clinicians"]


def test_mongo_batch_acumula_tipos_mixtos_sin_pisar(
    client, fake_db, auth_header, monkeypatch
):
    """Verifica que si llegan docs con ObjectId y string para la misma clínica, se acumulan sin pisarse."""
    from bson import ObjectId

    clinic = Clinic(clinic_id="cl-1", name="Clínica Uno", mongo_clinic_id="507f1f77bcf86cd799439011")
    fake_db.data[Clinic] = [clinic]

    mongo = _FakeMongo({
        "patients": [
            {"_id": "507f1f77bcf86cd799439011", "total_patients": 100, "at_risk": 20, "declining": 10, "stable": 60, "improving": 10},
            {"_id": ObjectId("507f1f77bcf86cd799439011"), "total_patients": 2, "at_risk": 1, "declining": 0, "stable": 1, "improving": 0},
        ],
        "clinicians": [
            {"_id": "cl-1", "total_clinicians": 5, "active_clinicians": 4, "specialties": ["Kinesiología"]},
        ],
    })
    monkeypatch.setattr(sync_sources, "get_mongo_db_or_none", lambda: mongo)

    r = client.post("/api/sync/run", headers=auth_header)
    assert r.status_code == 200, r.text

    summaries = [o for o in fake_db.added if isinstance(o, PatientHealthSummary) and o.clinic_id == "cl-1"]
    assert len(summaries) == 1
    # Verifica que acumuló 100 + 2 = 102 en vez de pisar con 2
    assert summaries[0].total_patients == 102
    assert summaries[0].at_risk == 21
    assert summaries[0].stable == 61


def test_clinica_con_cero_pacientes_resetea_resumen_en_api(
    client, fake_db, auth_header, monkeypatch
):
    """Verifica que si una clínica tenía 10 pacientes y hoy Mongo devuelve 0, se resetea a 0."""
    clinic = Clinic(clinic_id="cl-1", name="Clínica Uno", mongo_clinic_id="507f1f77bcf86cd799439011")
    fake_db.data[Clinic] = [clinic]

    existing_summary = PatientHealthSummary(clinic_id="cl-1", total_patients=10, at_risk=3, stable=7)
    fake_db.data[PatientHealthSummary] = [existing_summary]

    mongo = _FakeMongo({"patients": [], "clinicians": []})
    monkeypatch.setattr(sync_sources, "get_mongo_db_or_none", lambda: mongo)

    r = client.post("/api/sync/run", headers=auth_header)
    assert r.status_code == 200, r.text
    run = r.json()["data"]
    assert run["status"] == "completed"
    assert run["entities"]["patients"] == 1
    assert run["entities"]["clinicians"] == 1

    assert existing_summary.total_patients == 0
    assert existing_summary.at_risk == 0
    assert existing_summary.recorded_at is not None




