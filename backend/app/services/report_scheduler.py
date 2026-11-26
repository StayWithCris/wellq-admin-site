"""Recomendación #11 — Job de APScheduler para reportes programados.

Reutiliza el scheduler singleton de la #7 (``sync_scheduler.scheduler``) y
agrega un job que cada 15 minutos despacha los reportes vencidos.

Con Cloud Run en ``--min-instances=0`` el proceso puede estar apagado a la
hora del envío: para ese caso existe ``POST /api/v1/reports/dispatch-due``,
pensado para Cloud Scheduler con el header ``X-Scheduler-Secret``. Como la
programación vive en Neon, el envío pendiente sale en el siguiente tick o
llamada, sin perderse.
"""

import structlog
from apscheduler.triggers.interval import IntervalTrigger

from app.config import settings
from app.db.neon import get_session_factory
from app.services import scheduled_reports_service as svc
from app.services.sync_scheduler import scheduler

logger = structlog.get_logger(__name__)

JOB_ID = "scheduled_reports_job"
CHECK_MINUTES = 15


async def _dispatch_job() -> None:
    try:
        async with get_session_factory()() as session:
            result = await svc.dispatch_due_reports(session, trigger="scheduled", triggered_by="scheduler")
            if result["processed"]:
                logger.info("Reportes programados despachados", procesados=result["processed"])
    except Exception as exc:  # pragma: no cover - depende de la BD real
        logger.warning("Falló el despacho de reportes programados", error=str(exc))


def register_reports_job() -> bool:
    """Agrega el job al scheduler. No hace nada si está desactivado o sin Neon."""
    if not settings.reports_scheduler_enabled or not settings.database_url:
        logger.info("Reportes programados #11 — job no registrado (desactivado o sin DATABASE_URL).")
        return False
    scheduler.add_job(
        _dispatch_job,
        trigger=IntervalTrigger(minutes=CHECK_MINUTES),
        id=JOB_ID,
        replace_existing=True,
        misfire_grace_time=300,
    )
    logger.info("Reportes programados #11 — job registrado", interval_minutes=CHECK_MINUTES)
    return True
