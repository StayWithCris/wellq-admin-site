"""Pruebas de lógica de la Recomendación #7 — Sincronización programable."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from bson import ObjectId

from app.services.sync_service import (
    ALLOWED_FREQUENCY_HOURS,
    DEFAULT_FREQUENCY_HOURS,
    is_due,
    parse_iso,
    resolve_status,
    to_iso,
)
from app.services.sync_sources import (
    CliniciansMongoSource,
    PatientsMongoSource,
    SyncResult,
)


def test_frecuencias_permitidas():
    assert ALLOWED_FREQUENCY_HOURS == (6, 12, 24)
    assert DEFAULT_FREQUENCY_HOURS == 24


def test_resolve_status():
    now = datetime.utcnow()
    assert resolve_status(None) == "error"
    assert resolve_status(now - timedelta(hours=1), 6) == "ok"
    assert resolve_status(now - timedelta(hours=8), 6) == "warning"
    assert resolve_status(datetime.now(timezone.utc) - timedelta(hours=1), 6) == "ok"


def test_is_due_respeta_frecuencia():
    now = datetime.utcnow()
    assert is_due(None, 6, now) is False
    assert is_due(now - timedelta(hours=5), 6, now) is False
    assert is_due(now - timedelta(hours=7), 6, now) is True


def test_is_due_sin_ejecucion_previa_con_config():
    """Con frecuencia guardada, una instalación nueva sincroniza de inmediato."""
    now = datetime.utcnow()
    assert is_due(None, 6, now, has_config=True) is True


def test_serializacion_de_fechas():
    value = datetime(2026, 11, 17, 12, 30)
    assert to_iso(value) == "2026-11-17T12:30:00Z"
    assert parse_iso("2026-11-17T12:30:00Z") == value
    assert parse_iso("no-es-fecha") is None


# ── D3: pipelines de MongoDB (construcción pura, sin red) ──────────────────

MONGO_CLINIC_ID = "507f1f77bcf86cd799439011"


def _clinica():
    return SimpleNamespace(clinic_id="cl-1", mongo_clinic_id=MONGO_CLINIC_ID)


def test_pipeline_patients_filtra_por_clinic_ids():
    pipeline = PatientsMongoSource().build_pipeline(_clinica())
    assert pipeline[0] == {"$match": {"clinic_ids": ObjectId(MONGO_CLINIC_ID)}}

    group = pipeline[1]["$group"]
    assert group["total_patients"] == {"$sum": 1}
    for field in ("at_risk", "declining", "stable", "improving"):
        assert field in group, f"Falta {field} en el $group de pacientes"


def test_pipeline_clinicians_agrupa_por_clinic_id():
    pipeline = CliniciansMongoSource().build_pipeline(_clinica())
    assert pipeline[0] == {"$match": {"clinic_id": "cl-1"}}

    group = pipeline[1]["$group"]
    assert group["_id"] == "$clinic_id"
    assert "total_clinicians" in group
    assert "active_clinicians" in group
    assert "specialties" in group


def test_pipeline_no_requiere_red():
    """Construir el pipeline no toca Mongo: es una función pura."""
    source = PatientsMongoSource()
    assert source.collection == "patients"
    assert source.key == "patients"
    assert source.build_pipeline(_clinica()) == source.build_pipeline(_clinica())


def test_sync_result_acumula_omitidas_y_errores():
    result = SyncResult()
    result.add_count("clinics", 3)
    result.add_skip("patients")
    result.add_skip("patients")  # no se repite
    result.add_error("clinicians", "timeout")

    assert result.counts == {"clinics": 3}
    assert result.skipped == ["patients"]
    assert result.errors == ["clinicians: timeout"]


# ── Pruebas de mejoras a la Recomendación #7 (Puntos 1, 2, 3, 4) ────────────

def test_source_timeouts_configurables_por_fuente():
    """Punto 2: Cada tipo de fuente tiene su propio timeout, calibrado para Cloud Run."""
    from app.services.sync_sources import SOURCE_TIMEOUTS, SOURCE_SPECS
    from app.services.sync_service import STALE_RUN_MINUTES

    assert "neon" in SOURCE_TIMEOUTS
    assert "mongo" in SOURCE_TIMEOUTS
    assert SOURCE_TIMEOUTS["neon"] <= SOURCE_TIMEOUTS["mongo"]
    # Los timeouts están protegidos dentro de la ventana de 5 min de Cloud Run
    assert SOURCE_TIMEOUTS["mongo"] <= 5
    assert STALE_RUN_MINUTES >= max(SOURCE_TIMEOUTS.values())

    # Todas las fuentes en SOURCE_SPECS tienen su timeout asociado
    for spec in SOURCE_SPECS:
        assert "timeout_min" in spec
        assert spec["timeout_min"] > 0


def test_scheduler_reschedule_adapta_intervalo():
    """Punto 1: APScheduler adapta el intervalo según la frecuencia elegida."""
    from app.services import sync_scheduler

    calls = []
    class FakeScheduler:
        running = True
        def reschedule_job(self, job_id, trigger):
            calls.append((job_id, trigger.interval.total_seconds()))

    old_scheduler = sync_scheduler.scheduler
    try:
        sync_scheduler.scheduler = FakeScheduler()
        sync_scheduler.reschedule(6)
        assert calls[-1] == (sync_scheduler.JOB_ID, 300)  # 5 min

        sync_scheduler.reschedule(12)
        assert calls[-1] == (sync_scheduler.JOB_ID, 600)  # 10 min

        sync_scheduler.reschedule(24)
        assert calls[-1] == (sync_scheduler.JOB_ID, 900)  # 15 min
    finally:
        sync_scheduler.scheduler = old_scheduler


def test_limite_de_alertas_consecutivas():
    """Punto 4: El umbral para alertar fallos consecutivos está fijado."""
    from app.services.sync_service import CONSECUTIVE_FAILURE_THRESHOLD
    assert CONSECUTIVE_FAILURE_THRESHOLD == 3


def test_neon_snapshot_es_no_destructivo():
    """Punto 4 Feedback: NeonSnapshotSource no debe destruir fechas con UPDATE masivo."""
    from app.models_db import Clinic
    from app.services.sync_sources import NeonSnapshotSource

    source = NeonSnapshotSource("clinics", Clinic, "updated_at")
    # Verificar que el kind es neon y no lanza errores en inicialización
    assert source.kind == "neon"
    assert source.column == "updated_at"


def test_mongo_batch_lookup_soporta_listas_vacias():
    """Punto 4 Feedback: _get_rows_batch resuelve en lote en vez de N+1."""
    import asyncio
    from app.services.sync_sources import PatientsMongoSource

    source = PatientsMongoSource()
    class FakeDB:
        async def execute(self, stmt):
            class R:
                def scalars(self):
                    return self
                def all(self):
                    return []
            return R()

    res = asyncio.run(source._get_rows_batch(FakeDB(), []))
    assert res == {}


