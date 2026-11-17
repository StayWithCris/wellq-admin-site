"""Programador de sincronizaciones (Recomendación #7).

Un único bucle revisa cada 5 minutos si la última ejecución superó la
frecuencia configurada. Si es así, ejecuta una sincronización programada y la
registra en `sync_runs`, igual que el botón «Sincronizar ahora».

Solo se inicia cuando la base de datos está configurada (ver `main.lifespan`).
No se ejecuta nada hasta que el operador guarde una frecuencia desde el panel
(`configured=True` en la configuración).

Si MongoDB no está configurado, las fuentes Mongo quedan omitidas y el tick
termina en estado `partial`. Ver `app/services/sync_sources.py`.
"""

import asyncio

import structlog

from app.db.neon import get_session_factory
from app.services import sync_service as svc

logger = structlog.get_logger(__name__)

CHECK_INTERVAL_SECONDS = 300
INITIAL_DELAY_SECONDS = 60


async def sync_scheduler_loop() -> None:
    """Bucle de fondo: ejecuta la sincronización cuando corresponde."""
    await asyncio.sleep(INITIAL_DELAY_SECONDS)

    while True:
        try:
            async with get_session_factory()() as session:
                config = await svc.load_config(session)
                last_run = svc.parse_iso(config.get("last_run_at"))

                if not svc.is_due(
                    last_run,
                    config["frequency_hours"],
                    has_config=config.get("configured", False),
                ):
                    logger.debug("Sincronización programada: aún no corresponde")
                else:
                    active = await svc.find_active_run(session)
                    if active is not None:
                        logger.warning(
                            "Sincronización programada omitida: ya hay una en curso",
                            run_id=active.run_id,
                        )
                    else:
                        run = await svc.perform_sync(
                            session, trigger="scheduled", created_by="scheduler"
                        )
                        logger.info(
                            "Sincronización programada terminada",
                            run_id=run.run_id,
                            status=run.status,
                            frequency_hours=run.frequency_hours,
                        )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - depende de la BD real
            logger.warning("Falló la sincronización programada", error=str(exc))

        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
