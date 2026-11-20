"""
main.py — Punto de entrada de la aplicación FastAPI
====================================================
Para ejecutar:
    uvicorn app.main:app --reload --port 8000
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.routers import soap_quality
from app.routers import cohort_heatmap
from app.routers import clinic_radar
from app.routers import clinic_audit
from app.routers import audit_center
from app.routers import revenue_simulator
from app.routers import capacity_alerts
from app.routers import sla
from app.routers import mrr_close
from app.routers import plan_catalogs
from app.routers import ticket_routing
from app.routers import discounts
from app.routers import impersonation_console
from app.routers import two_factor
from app.routers import incident_notifications
from app.routers import assistant
from app.routers import telemetry            # Recomendación #5 — Telemetría y registros reales
import structlog
import logging
import asyncio
import app.models_db  # registra todos los modelos SQLModel


from app.config import settings
from app.db.neon import init_neon, close_neon, create_db_tables
from app.db.mongodb import init_mongodb, close_mongodb, ensure_mongodb_indexes
from app.services.sync_scheduler import start_scheduler, shutdown_scheduler
from app.services import telemetry_service as telemetry_service  # Recomendación #5
from app.middleware.telemetry import telemetry_middleware       # Recomendación #5

# Imports de todos los routers desde app.routers
from app.routers import (
    auth,
    password_reset,
    dashboard,
    clinics,
    clinic_portal,
    platform,
    financials,
    alerts,
    search,
    infrastructure,
    infra_costs,          # Mejora #6 — Proyector de costos de infraestructura
    analytics,
    users,
    notifications,
    jobs,
    settings as settings_router,
    features,
    plans,
    clinic_plans,
    support,
    clinic_health,
    sync,
    roles,                # RBAC: Roles, Permisos y asignación
    # kpis eliminado: sus endpoints fueron fusionados en dashboard.py
)

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer()
        if settings.debug
        else structlog.processors.JSONRenderer(),
    ],
    logger_factory=structlog.PrintLoggerFactory(),
)

logging.basicConfig(level=logging.DEBUG if settings.debug else logging.INFO)
logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Iniciando WellQ Admin API", entorno=settings.app_env)

    scheduler_task = None
    telemetry_tasks: list[asyncio.Task] = []
    if settings.database_url:
        init_neon()
        await create_db_tables()
        logger.info("Neon (PostgreSQL) conectado y tablas verificadas.")
        # Recomendación #7 — APScheduler con cron en vez de asyncio loop
        start_scheduler()
    else:
        logger.warning("DATABASE_URL no configurada — Neon desactivado.")

    # Recomendación #7 — Inicializar MongoDB e índices automáticos si está configurado
    if getattr(settings, "mongodb_uri", ""):
        try:
            init_mongodb()
            await ensure_mongodb_indexes()
            logger.info("MongoDB (Motor) conectado e índices asegurados.")
        except Exception as exc:
            logger.warning("No se pudieron asegurar índices en MongoDB al arrancar", error=str(exc))

        # Recomendación #5 — Tareas de telemetría: flusher (batch a Mongo) y
        # watcher de change streams. Si Mongo NO está configurado, NO arrancan:
        # `record_request()` sigue operando en memoria (buffer + histograma +
        # hub), así que el feed en vivo y los ticks SSE funcionan igual.
        telemetry_tasks.append(asyncio.create_task(telemetry_service.flusher_loop()))
        telemetry_tasks.append(asyncio.create_task(telemetry_service.watch_pose_logs()))
        logger.info("Telemetría #5 — flusher y watcher de pose_analysis_logs arrancados.")
    else:
        logger.info("MONGODB_URI no configurada — MongoDB desactivado.")
        logger.info(
            "Telemetría #5 — sin persistencia: el feed en vivo sigue activo "
            "(buffer + histograma en memoria)."
        )

    yield

    logger.info("Cerrando WellQ Admin API...")

    # Recomendación #5 — Cierre limpio y drain de telemetría ANTES de cerrar MongoDB.
    # En Cloud Run (SIGTERM con margen de 10s), asignamos un presupuesto estricto de 5s:
    # 3s para cancelación de tareas + 2s de drain de respaldo, dejando 5s libres
    # para desconexión limpia de pools de PostgreSQL y MongoDB antes de SIGKILL.
    for task in telemetry_tasks:
        task.cancel()
    if telemetry_tasks:
        try:
            await asyncio.wait_for(asyncio.gather(*telemetry_tasks, return_exceptions=True), timeout=3.0)
        except asyncio.TimeoutError:
            logger.warning("Timeout (3s) esperando cancelación de tareas de telemetría")

    # Respaldo de drenado final por si la tarea fue cancelada antes de vaciar todo el buffer
    try:
        await asyncio.wait_for(telemetry_service.drain_all_pending(), timeout=2.0)
    except Exception as exc:
        logger.warning("Fallo o timeout drenando buffer de telemetría en shutdown", error=str(exc))

    telemetry_service.hub.close()

    # Recomendación #7 — detener APScheduler limpiamente
    shutdown_scheduler()

    if getattr(settings, "mongodb_uri", ""):
        try:
            await close_mongodb()
        except Exception:
            pass

    if settings.database_url:
        await close_neon()

    logger.info("Conexiones closed. API detenida.")


app = FastAPI(
    title="WellQ Admin API",
    version="1.0.0",
    docs_url="/docs" if settings.debug else None,
    redoc_url="/redoc" if settings.debug else None,
    lifespan=lifespan,
)

# ─────────────────────────────────────────────────────────────────────────────
# Configuración de CORS blindada para Producción (Vercel) y Local
# ─────────────────────────────────────────────────────────────────────────────
origins = [
    "http://localhost:5173",
    "https://well-q-admin-1-2.vercel.app",
    "https://well-q-admin-1-2-2g0tyvkyl-carlitos2004s-projects.vercel.app"  # Tu enlace exacto de Vercel
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_origin_regex=r"https://.*\.vercel\.app",  # El comodín mágico que deja pasar subdominios
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─────────────────────────────────────────────────────────────────────────────
# Recomendación #5 — Middleware de telemetría HTTP
# ─────────────────────────────────────────────────────────────────────────────
# Se registra DESPUÉS del CORSMiddleware a propósito: `app.middleware("http")`
# se apila LIFO y envuelve TODO el stack, así que este middleware queda por
# fuera del CORS y ve los preflight de OPTIONS ANTES de que el CORSMiddleware
# los despache. Por eso OPTIONS está fuera de TRACKED_METHODS: no queremos
# inflar el RPM con handshakes que no son tráfico real.
app.middleware("http")(telemetry_middleware)

# Registro de routers existentes
app.include_router(auth.router)
app.include_router(password_reset.router)
app.include_router(dashboard.router)       # único owner de /api/kpis/*
app.include_router(clinics.router)
app.include_router(clinic_portal.router)
app.include_router(platform.router)
app.include_router(notifications.router)
app.include_router(jobs.router)
app.include_router(financials.router)
app.include_router(alerts.router)
app.include_router(search.router)
app.include_router(infrastructure.router)
app.include_router(infra_costs.router)     # Mejora #6 — /api/infrastructure/costs*
app.include_router(analytics.router)
app.include_router(users.router)
app.include_router(settings_router.router)
app.include_router(features.router)
app.include_router(plans.router)
app.include_router(clinic_plans.router)
app.include_router(soap_quality.router)
app.include_router(cohort_heatmap.router)  # Mejora #8 — /api/analytics/retention/heatmap
app.include_router(clinic_radar.router)  # Mejora #10 — /api/clinics/{id}/health-radar
app.include_router(clinic_audit.router)  # Mejora #15 — /api/clinics/{id}/audit-logs
app.include_router(audit_center.router)  # Mejora #16 — /api/audit/logs
app.include_router(revenue_simulator.router)  # Mejora #3 — /api/simulator/revenue
app.include_router(capacity_alerts.router)  # Mejora #21 — /api/capacity/alerts
app.include_router(sla.router)  # Mejora #13 — /api/sla/tickets
app.include_router(mrr_close.router)  # Mejora #1 — /api/financials/mrr/close*
app.include_router(plan_catalogs.router)  # Mejora #2 — /api/plan-catalogs
app.include_router(ticket_routing.router)  # Mejora #12 — /api/ticket-routing
app.include_router(discounts.router)  # Mejora #4 — /api/discounts
app.include_router(impersonation_console.router)  # Mejora #14 — /api/impersonation
app.include_router(two_factor.router)  # Mejora #18 — /api/auth/2fa/*, /api/auth/sessions
app.include_router(incident_notifications.router)  # Mejora #19 — /api/v1/incident-notifications/*
app.include_router(assistant.router)  # Mejora #20 — /api/v1/assistant/*
app.include_router(telemetry.router)  # Recomendación #5 — /api/platform/telemetry/*


# Registro de los nuevos componentes adaptados
# ─────────────────────────────────────────────────────────────────────────────
# NOTA: clinic_health usa prefix="/api/clinics" igual que clinics.
# FastAPI permite múltiples routers con el mismo prefix — se fusionan sin
# conflicto. El nuevo endpoint queda en:
#   GET /api/clinics/{clinic_id}/patient-health
# ─────────────────────────────────────────────────────────────────────────────
app.include_router(support.router)
app.include_router(clinic_health.router)
app.include_router(sync.router)
app.include_router(roles.router)       # RBAC: GET|POST /api/roles, GET /api/permissions


@app.get("/health", tags=["Sistema"])
async def health_check() -> dict:
    return {
        "status": "ok",
        "version": "1.0.0",
        "environment": settings.app_env,
        "database": "neon_connected" if settings.database_url else "not_configured",
    }
