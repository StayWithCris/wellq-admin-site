"""Programador de sincronizaciones con APScheduler (Recomendación #7).

Reemplaza el bucle ``while True`` / ``asyncio.sleep(300)`` por un cron job de
APScheduler que se dispara exactamente cuando corresponde:

- El scheduler arranca con un job ``check_sync`` cada 5 minutos.
- Cuando el operador cambia la frecuencia, ``reschedule()`` recalcula el
  ``IntervalTrigger`` y sustituye el job, eliminando el polling innecesario.
- El shutdown cancela el scheduler limpiamente via ``lifespan``.

APScheduler usa ``AsyncIOScheduler`` para correr dentro del event loop de
FastAPI sin bloquear hilos.
"""

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.db.neon import get_session_factory
from app.services import sync_service as svc

logger = structlog.get_logger(__name__)

# El scheduler es un singleton de módulo; se inicia y detiene desde lifespan.
scheduler = AsyncIOScheduler()

JOB_ID = "sync_scheduled_job"
DEFAULT_CHECK_MINUTES = 5
INITIAL_DELAY_SECONDS = 60


async def _check_and_run() -> None:
    """Callback del job: evalúa si toca sincronizar y ejecuta si corresponde.

    Usa pg_try_advisory_xact_lock dentro de perform_sync para que con múltiples workers
    (--workers 4) o réplicas de Cloud Run, solo un worker adquiera el lock en la transacción
    inicial y ejecute la corrida.
    """
    try:
        async with get_session_factory()() as session:
            # 1. Leer la configuración desde PostgreSQL (fuente única de verdad compartida)
            config = await svc.load_config(session)
            last_run = svc.parse_iso(config.get("last_run_at"))

            if not svc.is_due(
                last_run,
                config["frequency_hours"],
                has_config=config.get("configured", False),
            ):
                logger.debug("Sincronización programada: aún no corresponde")
                return

            try:
                run = await svc.perform_sync(
                    session, trigger="scheduled", created_by="scheduler", with_lock=True
                )
                logger.info(
                    "Sincronización programada terminada",
                    run_id=run.run_id,
                    status=run.status,
                    frequency_hours=run.frequency_hours,
                )
            except svc.SyncConflictError as exc:
                logger.debug("Sincronización programada omitida por conflicto", reason=str(exc))
                return
    except Exception as exc:  # pragma: no cover - depende de la BD real
        logger.warning("Falló la sincronización programada", error=str(exc))


def start_scheduler() -> None:
    """Arranca el scheduler con un job de intervalo de 5 minutos.

    Se llama desde ``main.lifespan`` después de conectar la BD.
    """
    if scheduler.running:
        return

    scheduler.add_job(
        _check_and_run,
        trigger=IntervalTrigger(minutes=DEFAULT_CHECK_MINUTES),
        id=JOB_ID,
        replace_existing=True,
        misfire_grace_time=120,
    )
    scheduler.start()
    logger.info(
        "APScheduler iniciado",
        job=JOB_ID,
        interval_minutes=DEFAULT_CHECK_MINUTES,
    )


def reschedule(frequency_hours: int) -> None:
    """Re-programa el job con un intervalo basado en la frecuencia guardada.

    Se llama desde el endpoint ``PUT /api/sync/config`` para que el scheduler
    sepa cuándo toca sin esperar al próximo tick de 5 min.

    Estrategia: si la frecuencia es ≤ 6h revisa cada 5 min; para 12h cada 10
    min; para 24h cada 15 min. Así no hay polling excesivo.
    """
    if not scheduler.running:
        return

    interval_map = {6: 5, 12: 10, 24: 15}
    minutes = interval_map.get(frequency_hours, DEFAULT_CHECK_MINUTES)

    scheduler.reschedule_job(
        JOB_ID,
        trigger=IntervalTrigger(minutes=minutes),
    )
    logger.info(
        "Scheduler reprogramado",
        frequency_hours=frequency_hours,
        check_interval_minutes=minutes,
    )


def shutdown_scheduler() -> None:
    """Detiene el scheduler limpiamente. Se llama al cerrar la app."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("APScheduler detenido")
