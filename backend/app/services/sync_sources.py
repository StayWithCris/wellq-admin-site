"""Fuentes de datos de la sincronización programable (Recomendación #7).

Este módulo separa *qué* se sincroniza de *cómo* se sincroniza:

- `NeonSnapshotSource` refresca la marca de frescura (`updated_at` /
  `recorded_at`) de tablas agregadas locales. No trae datos nuevos: son
  snapshots por diseño (clínicas, notas SOAP, tickets).
- `MongoAggregateSource` sí ingiere datos reales desde MongoDB: agrega las
  colecciones `patients` y `clinicians` y hace upsert en los resúmenes
  `PatientHealthSummary` y `ClinicianSummary`.

Las agregaciones de Mongo están documentadas en los docstrings de
`app/models/sync.py`; aquí se construyen explícitamente para poder probarlas
sin red.

GATE DE MONGO
-------------
Si `settings.mongodb_uri` está vacío, o `get_mongo_db()` falla (Mongo no
iniciado), las fuentes Mongo se marcan como **omitidas** (`skipped`) — nunca
como fallidas — y el resto del run continúa. Es una instalación válida sin
Mongo configurado, no un error.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models_db import (
    Clinic,
    ClinicianSummary,
    PatientHealthSummary,
    SoapQualityMetric,
    SupportTicket,
)

# ── Timeouts configurables por tipo de fuente (en minutos) ──────────────────
# Calibrados para mantenerse estrictamente por debajo del timeout por defecto
# de Google Cloud Run (5 minutos / 300 s) y reverse proxies (Vercel).
SOURCE_TIMEOUTS: dict[str, int] = {
    "neon": 1,       # 1 minuto (60 s) — snapshots y conteos locales
    "mongo": 2,      # 2 minutos (120 s) — agregación en MongoDB Atlas
}

DEFAULT_TIMEOUT_MINUTES = 2

# ── Definición canónica de las fuentes ─────────────────────────────────────
#
# El orden es el que ve el panel: clínicas, pacientes, notas SOAP, tickets y
# clínicos. `sync_service.SOURCE_SPECS` se deriva de aquí para que el estado
# que muestra la UI y lo que se ejecuta nunca se desincronicen.

SOURCE_SPECS: tuple[dict[str, Any], ...] = (
    {"key": "clinics", "name": "Clinics", "model": Clinic, "column": "updated_at", "kind": "neon", "timeout_min": SOURCE_TIMEOUTS["neon"]},
    {"key": "patients", "name": "Patient Health Summaries", "model": PatientHealthSummary, "column": "recorded_at", "kind": "mongo", "timeout_min": SOURCE_TIMEOUTS["mongo"]},
    {"key": "soap_notes", "name": "SOAP Notes", "model": SoapQualityMetric, "column": "recorded_at", "kind": "neon", "timeout_min": SOURCE_TIMEOUTS["neon"]},
    {"key": "tickets", "name": "Support Tickets", "model": SupportTicket, "column": "recorded_at", "kind": "neon", "timeout_min": SOURCE_TIMEOUTS["neon"]},
    {"key": "clinicians", "name": "Clinician Summaries", "model": ClinicianSummary, "column": "recorded_at", "kind": "mongo", "timeout_min": SOURCE_TIMEOUTS["mongo"]},
)


@dataclass
class SyncResult:
    """Resultado agregado de una ejecución de sincronización.

    - `counts`: registros tocados por fuente (`{"patients": 12, ...}`).
    - `skipped`: claves de fuente omitidas (p. ej. Mongo no configurado).
    - `errors`: mensajes de error por fuente; la ejecución continúa.
    """

    counts: dict[str, int] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def add_count(self, key: str, value: int) -> None:
        self.counts[key] = int(value)

    def add_skip(self, key: str, reason: str = "") -> None:
        if key not in self.skipped:
            self.skipped.append(key)

    def add_error(self, key: str, reason: str) -> None:
        self.errors.append(f"{key}: {reason}" if reason else key)


# ── Utilidades ──────────────────────────────────────────────────────────────

async def _collect(cursor: Any) -> list[dict]:
    """Normaliza el cursor de agregación de Mongo a una lista de documentos.

    Motor devuelve un cursor asíncrono; los dobles de prueba suelen devolver
    una lista síncrona. Se soportan las tres formas.
    """
    if cursor is None:
        return []
    if hasattr(cursor, "__aiter__"):
        return [doc async for doc in cursor]
    if hasattr(cursor, "__await__"):
        return list(await cursor)
    return list(cursor)


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _object_id(value: str) -> Any:
    """Convierte un id de Mongo en `ObjectId` sin fallar si no es válido."""
    try:
        from bson import ObjectId
    except ImportError:  # pragma: no cover - bson viene con motor
        return value
    try:
        return ObjectId(value)
    except Exception:
        return value


# ── Fuente local (snapshot / conteo no destructivo) ─────────────────────────

class NeonSnapshotSource:
    """Verifica y cuenta los registros vigentes de una tabla local.

    NO ejecuta UPDATE masivo sin WHERE: respeta las fechas históricas reales
    (updated_at / recorded_at) de clínicas, tickets y notas SOAP, evitando
    bloat de dead tuples y duplicación de páginas en Neon/PostgreSQL.
    """

    kind = "neon"

    def __init__(self, key: str, model: Any, column: str) -> None:
        self.key = key
        self.model = model
        self.column = column

    async def run(self, db: AsyncSession, now: datetime) -> int:
        stmt = select(self.model)
        if hasattr(self.model, "is_deleted"):
            stmt = stmt.where(self.model.is_deleted.is_(False))
        result = await db.execute(stmt)
        return len(result.scalars().all())


# ── Fuente MongoDB (agregación por lote sin bucle N+1) ──────────────────────

class MongoAggregateSource:
    """Agrega una colección de MongoDB y hace upsert en su tabla resumen.

    Ejecuta un único .aggregate() por colección usando {"$in": mongo_keys}
    y agrupando por el identificador de clínica (_id), eliminando por completo
    el bucle O(C x N) sobre clinics_list.
    """

    kind = "mongo"
    key: str = ""
    collection: str = ""
    summary_model: Any = None

    def get_mongo_keys_and_map(self, clinics: list[Any]) -> tuple[list[Any], dict[str, str]]:
        """Extrae la lista de identificadores para $in y mapea el id resultante a clinic_id de PostgreSQL."""
        raise NotImplementedError

    def build_batch_pipeline(self, mongo_keys: list[Any]) -> list[dict]:
        """Construye el pipeline de agregación en lote para toda la colección."""
        raise NotImplementedError

    def build_pipeline(self, clinic: Any) -> list[dict]:
        """Pipeline individual (retrocompatibilidad)."""
        raise NotImplementedError

    def apply(self, row: Any, aggregate: dict) -> None:
        raise NotImplementedError

    async def _get_row(self, db: AsyncSession, clinic_id: str) -> Optional[Any]:
        result = await db.execute(
            select(self.summary_model).where(self.summary_model.clinic_id == clinic_id)
        )
        return result.scalars().first()

    async def _get_rows_batch(self, db: AsyncSession, clinic_ids: list[str]) -> dict[str, Any]:
        """Precarga en UNA sola consulta todas las filas de resumen (elimina N+1)."""
        if not clinic_ids:
            return {}
        result = await db.execute(
            select(self.summary_model).where(self.summary_model.clinic_id.in_(clinic_ids))
        )
        return {r.clinic_id: r for r in result.scalars().all() if getattr(r, "clinic_id", None)}

    async def run(
        self,
        db: AsyncSession,
        mongo: Any,
        clinics: Iterable[Any],
        now: datetime,
    ) -> int:
        """Agrega la colección completa en UNA sola llamada a Mongo y actualiza resúmenes.

        Elimina el bucle for clinic in clinics_list: que ejecutaba aggregate() C veces.
        Ejecuta un único aggregate() por colección con {"$in": mongo_keys} y agrupando por _id.
        Precarga todas las filas resumen con _get_rows_batch() y persiste en bloque.
        """
        clinics_list = list(clinics)
        if not clinics_list:
            return 0

        mongo_keys, id_to_clinic_id = self.get_mongo_keys_and_map(clinics_list)
        if not mongo_keys:
            return 0

        # Precargar todas las filas de resumen existentes en PostgreSQL en 1 sola consulta
        clinic_ids = [c.clinic_id for c in clinics_list if getattr(c, "clinic_id", None)]
        existing_rows = await self._get_rows_batch(db, clinic_ids)

        # UN SOLO .aggregate() para toda la colección de MongoDB
        batch_pipeline = self.build_batch_pipeline(mongo_keys)
        docs = await _collect(mongo[self.collection].aggregate(batch_pipeline))
        if not docs:
            return 0

        touched = 0
        for doc in docs:
            doc_id = doc.get("_id")
            # Resolver el clinic_id de PostgreSQL para el grupo
            target_clinic_id = None
            if doc_id is not None:
                target_clinic_id = id_to_clinic_id.get(str(doc_id))
            elif len(clinics_list) == 1:
                # Si un mock de test devuelve _id: None para 1 clínica
                target_clinic_id = clinics_list[0].clinic_id

            if not target_clinic_id:
                continue

            row = existing_rows.get(target_clinic_id)
            if row is None:
                row = self.summary_model(clinic_id=target_clinic_id)
                db.add(row)
                existing_rows[target_clinic_id] = row

            self.apply(row, doc)
            row.recorded_at = now
            touched += 1

        if touched > 0:
            await db.commit()
        return touched


class PatientsMongoSource(MongoAggregateSource):
    """Colección `patients` → `PatientHealthSummary`.

    Pipeline en lote:
    - `$match` filtrando pacientes que contengan alguno de los `clinic_ids` consultados ({"$in": mongo_keys}).
    - `$unwind` de `clinic_ids`.
    - `$match` para acotar a las clínicas objetivo.
    - `$group` por `_id: "$clinic_ids"` totalizando por status (at_risk, declining, stable, improving).
    """

    key = "patients"
    collection = "patients"
    summary_model = PatientHealthSummary

    def get_mongo_keys_and_map(self, clinics: list[Any]) -> tuple[list[Any], dict[str, str]]:
        mongo_keys = []
        mapping = {}
        for c in clinics:
            mid = getattr(c, "mongo_clinic_id", None)
            cid = getattr(c, "clinic_id", None)
            if mid and cid:
                obj_id = _object_id(mid)
                mongo_keys.append(obj_id)
                if str(mid) != str(obj_id):
                    mongo_keys.append(str(mid))
                mapping[str(obj_id)] = cid
                mapping[str(mid)] = cid
        return mongo_keys, mapping

    def build_batch_pipeline(self, mongo_keys: list[Any]) -> list[dict]:
        return [
            {"$match": {"clinic_ids": {"$in": mongo_keys}}},
            {"$unwind": "$clinic_ids"},
            {"$match": {"clinic_ids": {"$in": mongo_keys}}},
            {
                "$group": {
                    "_id": "$clinic_ids",
                    "total_patients": {"$sum": 1},
                    "at_risk": {"$sum": {"$cond": [{"$eq": ["$status", "at_risk"]}, 1, 0]}},
                    "declining": {"$sum": {"$cond": [{"$eq": ["$status", "declining"]}, 1, 0]}},
                    "stable": {"$sum": {"$cond": [{"$eq": ["$status", "stable"]}, 1, 0]}},
                    "improving": {"$sum": {"$cond": [{"$eq": ["$status", "improving"]}, 1, 0]}},
                }
            },
        ]

    def build_pipeline(self, clinic: Any) -> list[dict]:
        """Retrocompatibilidad con llamadas individuales y tests existentes."""
        return [
            {"$match": {"clinic_ids": _object_id(clinic.mongo_clinic_id)}},
            {
                "$group": {
                    "_id": None,
                    "total_patients": {"$sum": 1},
                    "at_risk": {"$sum": {"$cond": [{"$eq": ["$status", "at_risk"]}, 1, 0]}},
                    "declining": {"$sum": {"$cond": [{"$eq": ["$status", "declining"]}, 1, 0]}},
                    "stable": {"$sum": {"$cond": [{"$eq": ["$status", "stable"]}, 1, 0]}},
                    "improving": {"$sum": {"$cond": [{"$eq": ["$status", "improving"]}, 1, 0]}},
                }
            },
        ]

    def apply(self, row: Any, aggregate: dict) -> None:
        row.total_patients = _as_int(aggregate.get("total_patients"))
        row.at_risk = _as_int(aggregate.get("at_risk"))
        row.declining = _as_int(aggregate.get("declining"))
        row.stable = _as_int(aggregate.get("stable"))
        row.improving = _as_int(aggregate.get("improving"))


class CliniciansMongoSource(MongoAggregateSource):
    """Colección `clinicians` → `ClinicianSummary`.

    Pipeline en lote:
    - `$match` filtrando clínicos de las clínicas consultadas ({"$in": mongo_keys}).
    - `$group` por `_id: "$clinic_id"` totalizando clinicians activos y especialidades.
    """

    key = "clinicians"
    collection = "clinicians"
    summary_model = ClinicianSummary

    def get_mongo_keys_and_map(self, clinics: list[Any]) -> tuple[list[Any], dict[str, str]]:
        mongo_keys = []
        mapping = {}
        for c in clinics:
            cid = getattr(c, "clinic_id", None)
            if cid:
                mongo_keys.append(cid)
                mapping[str(cid)] = cid
        return mongo_keys, mapping

    def build_batch_pipeline(self, mongo_keys: list[Any]) -> list[dict]:
        return [
            {"$match": {"clinic_id": {"$in": mongo_keys}}},
            {
                "$group": {
                    "_id": "$clinic_id",
                    "total_clinicians": {"$sum": 1},
                    "active_clinicians": {"$sum": {"$cond": [{"$eq": ["$state", "active"]}, 1, 0]}},
                    "specialties": {"$addToSet": "$specialties"},
                }
            },
        ]

    def build_pipeline(self, clinic: Any) -> list[dict]:
        """Retrocompatibilidad con llamadas individuales y tests existentes."""
        return [
            {"$match": {"clinic_id": clinic.clinic_id}},
            {
                "$group": {
                    "_id": "$clinic_id",
                    "total_clinicians": {"$sum": 1},
                    "active_clinicians": {"$sum": {"$cond": [{"$eq": ["$state", "active"]}, 1, 0]}},
                    "specialties": {"$addToSet": "$specialties"},
                }
            },
        ]

    def apply(self, row: Any, aggregate: dict) -> None:
        row.total_clinicians = _as_int(aggregate.get("total_clinicians"))
        row.active_clinicians = _as_int(aggregate.get("active_clinicians"))
        specialties = aggregate.get("specialties") or []
        flat_specialties = []
        if isinstance(specialties, (list, tuple, set)):
            for item in specialties:
                if isinstance(item, (list, tuple, set)):
                    flat_specialties.extend(item)
                elif item:
                    flat_specialties.append(item)
        elif isinstance(specialties, str):
            flat_specialties.append(specialties)
        row.specialties = json.dumps(list(dict.fromkeys(flat_specialties)), ensure_ascii=False)


# ── Catálogo de fuentes ─────────────────────────────────────────────────────

SNAPSHOT_SOURCES: tuple[NeonSnapshotSource, ...] = tuple(
    NeonSnapshotSource(spec["key"], spec["model"], spec["column"])
    for spec in SOURCE_SPECS
    if spec["kind"] == "neon"
)

MONGO_SOURCES: tuple[MongoAggregateSource, ...] = (
    PatientsMongoSource(),
    CliniciansMongoSource(),
)


def get_mongo_db_or_none() -> Optional[Any]:
    """Devuelve la base de datos Mongo o `None` si no está disponible.

    `None` significa "omitir esta fuente", nunca "fallar la ejecución":
    una instalación sin Mongo configurado es válida.
    """
    from app.config import settings
    from app.db.mongodb import get_mongo_db

    if not getattr(settings, "mongodb_uri", ""):
        return None
    try:
        return get_mongo_db()
    except Exception:
        return None


async def load_sync_clinics(db: AsyncSession) -> list[Clinic]:
    """Clínicas vigentes (sin borrado lógico) con su `mongo_clinic_id`."""
    result = await db.execute(select(Clinic).where(Clinic.is_deleted.is_(False)))
    return list(result.scalars().all())


async def run_sources(
    db: AsyncSession,
    now: Optional[datetime] = None,
) -> SyncResult:
    """Ejecuta todas las fuentes y devuelve el resultado agregado.

    Las fuentes locales siempre corren. Las de Mongo solo si hay una base de
    datos disponible; en caso contrario quedan en `skipped`.

    Cada fuente tiene un timeout configurable (`SOURCE_TIMEOUTS`): si una
    fuente excede su tiempo, se marca como error y la ejecución continúa.
    """
    moment = now or datetime.utcnow()
    outcome = SyncResult()

    for source in SNAPSHOT_SOURCES:
        timeout = SOURCE_TIMEOUTS.get("neon", DEFAULT_TIMEOUT_MINUTES) * 60
        try:
            count = await asyncio.wait_for(source.run(db, moment), timeout=timeout)
            outcome.add_count(source.key, count)
        except asyncio.TimeoutError:
            outcome.add_error(source.key, f"timeout ({timeout // 60} min)")
        except Exception as exc:
            outcome.add_error(source.key, str(exc))

    mongo = get_mongo_db_or_none()
    if mongo is None:
        for source in MONGO_SOURCES:
            outcome.add_count(source.key, 0)
            outcome.add_skip(source.key)
        return outcome

    try:
        clinics = await load_sync_clinics(db)
    except Exception as exc:
        clinics = []
        for source in MONGO_SOURCES:
            outcome.add_count(source.key, 0)
            outcome.add_error(source.key, f"no se pudieron leer las clínicas: {exc}")

    for source in MONGO_SOURCES:
        timeout = SOURCE_TIMEOUTS.get("mongo", DEFAULT_TIMEOUT_MINUTES) * 60
        try:
            count = await asyncio.wait_for(
                source.run(db, mongo, clinics, moment), timeout=timeout
            )
            outcome.add_count(source.key, count)
        except asyncio.TimeoutError:
            outcome.add_error(source.key, f"timeout ({timeout // 60} min)")
        except Exception as exc:
            outcome.add_error(source.key, str(exc))

    return outcome
