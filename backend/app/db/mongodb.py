"""
db/mongodb.py — Conexión asíncrona a MongoDB con Motor
=======================================================
Motor es el driver oficial de MongoDB para Python asíncrono.
Se usa principalmente para consultas de analíticas pesadas y
agregaciones complejas que son más eficientes en MongoDB
que en Firestore (ventana de tiempo, group by, histogramas, etc.).
"""

import asyncio
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
import structlog

from app.config import settings

logger = structlog.get_logger(__name__)

# Cliente y base de datos globales (singleton)
_mongo_client: AsyncIOMotorClient | None = None
_mongo_db: AsyncIOMotorDatabase | None = None


def init_mongodb() -> None:
    """
    Crea el cliente Motor y selecciona la base de datos configurada.
    Motor es lazy: la conexión TCP real se establece en el primer request.
    Debe llamarse una vez al arrancar la app. Si hay un event loop activo,
    despacha la creación de índices en segundo plano.
    """
    global _mongo_client, _mongo_db

    try:
        # Crear cliente Motor con la URI de Atlas (incluye usuario, password, cluster)
        _mongo_client = AsyncIOMotorClient(
            settings.mongodb_uri,
            # Timeout de conexión: si el servidor no responde en 5 s, lanza error
            serverSelectionTimeoutMS=5000,
            # Mantener un máximo de 10 conexiones en el pool por proceso
            maxPoolSize=10,
        )

        # Seleccionar la base de datos (no crea ninguna conexión todavía)
        _mongo_db = _mongo_client[settings.mongodb_db_name]

        logger.info(
            "Cliente MongoDB (Motor) inicializado",
            base_de_datos=settings.mongodb_db_name,
        )

    except Exception as e:
        logger.error("Error al inicializar Motor/MongoDB", error=str(e))
        raise


async def close_mongodb() -> None:
    """
    Cierra el cliente Motor correctamente.
    Debe llamarse al detener la app para liberar el pool de conexiones.
    """
    global _mongo_client
    if _mongo_client is not None:
        _mongo_client.close()
        logger.info("Conexión MongoDB cerrada correctamente.")


def get_mongo_db() -> AsyncIOMotorDatabase:
    """
    Retorna la base de datos MongoDB lista para consultas asíncronas.

    Uso típico como dependencia de FastAPI:
        mongo: AsyncIOMotorDatabase = Depends(get_mongo_db)
    """
    if _mongo_db is None:
        raise RuntimeError(
            "La base de datos MongoDB no está inicializada. "
            "Asegúrate de llamar a init_mongodb() al arrancar la app."
        )
    return _mongo_db


async def ensure_mongodb_indexes(db: AsyncIOMotorDatabase | None = None) -> list[str]:
    """Crea los índices necesarios para optimizar las consultas y agregaciones de sincronización (#7).

    - patients: (clinic_ids, status) para acelerar $match en lote y $group por estado de salud.
    - clinicians: (clinic_id, state) para acelerar $match en lote y conteo de clínicos activos.
    """
    target_db = db if db is not None else _mongo_db
    if target_db is None:
        logger.warning("No se pueden crear índices en MongoDB: base de datos no inicializada")
        return []

    created_indexes: list[str] = []
    try:
        # Índice compuesto para agregación por clínica y estado en patients
        idx_patients = await target_db["patients"].create_index(
            [("clinic_ids", 1), ("status", 1)],
            name="idx_patients_clinic_ids_status",
            background=True,
        )
        created_indexes.append(f"patients.{idx_patients}")
        logger.info("Índice creado en colección patients", index=idx_patients)

        # Índice compuesto para agregación por clínica y estado en clinicians
        idx_clinicians = await target_db["clinicians"].create_index(
            [("clinic_id", 1), ("state", 1)],
            name="idx_clinicians_clinic_id_state",
            background=True,
        )
        created_indexes.append(f"clinicians.{idx_clinicians}")
        logger.info("Índice creado en colección clinicians", index=idx_clinicians)

    except Exception as exc:
        logger.warning("No se pudieron asegurar índices en MongoDB", error=str(exc))
        # Retornar los índices alcanzados sin romper el arranque de la app si Mongo está inalcanzable
        return created_indexes

    return created_indexes


# ── Nombres de colecciones en MongoDB ─────────────────────────────────────────
# Centralizamos los nombres de colecciones para evitar cadenas duplicadas
# dispersas por el código.

class MongoCollections:
    """Colecciones de MongoDB usadas para analíticas y reportes."""
    MRR_SNAPSHOTS = "mrr_snapshots"           # Snapshots diarios de MRR por clínica
    CHURN_EVENTS = "churn_events"             # Eventos de churn (cancelaciones, downgrades)
    POSE_ANALYSIS = "pose_analysis_logs"      # Logs de análisis de poses de la IA
    LATENCY_METRICS = "latency_metrics"       # Métricas de latencia por endpoint/modelo
    COST_METRICS = "cost_metrics"             # Costos de infraestructura y modelos de IA
    APP_VERSIONS = "app_version_distribution" # Distribución de versiones de la app por clínica
    PRODUCT_EVENTS = "product_events"         # Eventos de analítica de producto (clicks, funnels)
