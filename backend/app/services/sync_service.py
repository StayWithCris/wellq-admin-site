"""Servicio de sincronización programable (Recomendación #7).

QUÉ SE SINCRONIZA
-----------------
- `clinics`, `soap_notes` y `tickets` son **snapshots locales**: «sincronizar»
  refresca su timestamp de frescura (`updated_at` / `recorded_at`).
- `patients` y `clinicians` **sí ingieren datos de MongoDB**: las colecciones
  `patients` y `clinicians` se agregan y se hace upsert en
  `PatientHealthSummary` y `ClinicianSummary`. Ver `app/services/sync_sources.py`.

Si MongoDB no está configurado (`mongodb_uri` vacío) o no está iniciado, las
dos fuentes Mongo quedan **omitidas** (`skipped`) y la ejecución termina con
estado `partial`: es una instalación válida, no un fallo.

Configuración: fila de `platform_settings` con clave `sync_config`.
Historial: tabla `sync_runs`.
"""

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models_db import ActionLog, PlatformSetting, SyncRun
from app.services.sync_sources import SOURCE_SPECS, run_sources

SETTING_KEY = "sync_config"
DEFAULT_FREQUENCY_HOURS = 24
ALLOWED_FREQUENCY_HOURS = (6, 12, 24)

# Una ejecución en estado `running` más antigua que esto se considera huérfana.
STALE_RUN_MINUTES = 10

# `SOURCE_SPECS` (tabla y columna de frescura de cada fuente) vive en
# `app/services/sync_sources.py`: es la definición canónica que comparten el
# estado que muestra el panel y la ejecución real, para que no se desincronicen.


# ── Utilidades de fecha ──────────────────────────────────────────────────────

def to_iso(value: Optional[datetime]) -> Optional[str]:
    """Serializa una fecha como ISO-8601 UTC terminada en Z."""
    if value is None:
        return None
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.isoformat() + "Z"


def parse_iso(value: Any) -> Optional[datetime]:
    """Convierte una fecha ISO-8601 (con o sin Z) a datetime UTC naive."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


# ── Configuración persistida ────────────────────────────────────────────────

def _load_setting(setting: Optional[PlatformSetting]) -> dict:
    if not setting or not setting.setting_value:
        return {}
    try:
        data = json.loads(setting.setting_value)
        return data if isinstance(data, dict) else {}
    except (TypeError, ValueError):
        return {}


def _normalize_frequency(value: Any) -> int:
    try:
        frequency = int(value)
    except (TypeError, ValueError):
        return DEFAULT_FREQUENCY_HOURS
    return frequency if frequency in ALLOWED_FREQUENCY_HOURS else DEFAULT_FREQUENCY_HOURS


def _config_payload(data: dict, setting: Optional[PlatformSetting] = None) -> dict:
    frequency = _normalize_frequency(data.get("frequency_hours"))
    last_run = parse_iso(data.get("last_run_at"))
    next_run = last_run + timedelta(hours=frequency) if last_run else None
    return {
        "frequency_hours": frequency,
        "allowed_frequencies": list(ALLOWED_FREQUENCY_HOURS),
        "last_run_at": to_iso(last_run),
        "next_run_at": to_iso(next_run),
        "last_run_id": data.get("last_run_id"),
        "last_run_status": data.get("last_run_status"),
        "updated_at": to_iso(setting.updated_at) if setting else None,
        "updated_by": setting.updated_by if setting else None,
        # `configured` es True cuando el operador ya guardó una frecuencia.
        # El scheduler solo ejecuta automáticamente si hay configuración.
        "configured": setting is not None,
    }


async def _get_setting(db: AsyncSession) -> Optional[PlatformSetting]:
    result = await db.execute(
        select(PlatformSetting).where(PlatformSetting.setting_key == SETTING_KEY)
    )
    return result.scalars().first()


async def _ensure_setting(db: AsyncSession) -> PlatformSetting:
    setting = await _get_setting(db)
    if setting is None:
        setting = PlatformSetting(
            setting_key=SETTING_KEY,
            setting_value="{}",
            updated_at=datetime.utcnow(),
            updated_by="system",
        )
        db.add(setting)
    return setting


async def load_config(db: AsyncSession) -> dict:
    setting = await _get_setting(db)
    return _config_payload(_load_setting(setting), setting)


async def save_frequency(
    db: AsyncSession,
    frequency_hours: int,
    updated_by: Optional[str] = None,
) -> dict:
    frequency = _normalize_frequency(frequency_hours)
    setting = await _ensure_setting(db)
    data = _load_setting(setting)
    data["frequency_hours"] = frequency
    setting.setting_value = json.dumps(data, ensure_ascii=False)
    setting.updated_at = datetime.utcnow()
    setting.updated_by = updated_by or "system"
    await db.commit()
    return _config_payload(data, setting)


# ── Estado por entidad ──────────────────────────────────────────────────────

def resolve_status(
    last_sync: Optional[datetime],
    frequency_hours: int = DEFAULT_FREQUENCY_HOURS,
) -> str:
    """`ok` dentro de la frecuencia, `warning` atrasada, `error` si nunca."""
    if last_sync is None:
        return "error"

    if last_sync.tzinfo is not None:
        now = datetime.now(timezone.utc)
    else:
        now = datetime.utcnow()

    if now - last_sync > timedelta(hours=_normalize_frequency(frequency_hours)):
        return "warning"
    return "ok"


def is_due(
    last_run_at: Optional[datetime],
    frequency_hours: int,
    now: Optional[datetime] = None,
    has_config: bool = False,
) -> bool:
    """Indica si corresponde una nueva ejecución programada.

    Sin ejecución previa solo se ejecuta si el operador ya guardó una
    frecuencia (`has_config=True`); así una instalación recién creada no
    sincroniza sola antes de que alguien configure el panel.
    """
    if last_run_at is None:
        return bool(has_config)

    if last_run_at.tzinfo is not None:
        now = now or datetime.now(timezone.utc)
    else:
        now = now or datetime.utcnow()

    return now - last_run_at >= timedelta(hours=_normalize_frequency(frequency_hours))


async def collect_sources(db: AsyncSession, frequency_hours: int) -> list[dict]:
    sources: list[dict] = []
    for spec in SOURCE_SPECS:
        column = getattr(spec["model"], spec["column"])
        result = await db.execute(select(func.max(column)))
        last_sync = result.scalar_one_or_none()
        sources.append(
            {
                "key": spec["key"],
                "name": spec["name"],
                "last_sync": to_iso(last_sync),
                "status": resolve_status(last_sync, frequency_hours),
                "frequency_hours": frequency_hours,
            }
        )
    return sources


# ── Ejecución e historial ───────────────────────────────────────────────────

def serialize_run(run: SyncRun) -> dict:
    try:
        entities = json.loads(run.entities) if run.entities else {}
    except (TypeError, ValueError):
        entities = {}

    duration_ms = None
    if run.started_at and run.completed_at:
        duration_ms = max(int((run.completed_at - run.started_at).total_seconds() * 1000), 0)

    return {
        "id": run.run_id,
        "trigger": run.trigger,
        "status": run.status,
        "frequency_hours": run.frequency_hours,
        "entities": entities,
        "created_by": run.created_by,
        "error": run.error,
        "started_at": to_iso(run.started_at),
        "completed_at": to_iso(run.completed_at),
        "duration_ms": duration_ms,
    }


async def get_history(db: AsyncSession, limit: int = 10) -> list[dict]:
    result = await db.execute(
        select(SyncRun).order_by(SyncRun.started_at.desc()).limit(limit)
    )
    return [serialize_run(run) for run in result.scalars().all()]


async def find_active_run(db: AsyncSession) -> Optional[SyncRun]:
    """Devuelve la ejecución en curso y cierra las huérfanas.

    Se apoya en la tabla `sync_runs` (no en memoria), así que funciona igual
    para el proceso web y para el scheduler. No hay bloqueo distribuido: con
    más de una instancia del backend la ventana de carrera sigue existiendo.
    """
    cutoff = datetime.utcnow() - timedelta(minutes=STALE_RUN_MINUTES)
    result = await db.execute(select(SyncRun).where(SyncRun.status == "running"))
    runs = result.scalars().all()

    active: Optional[SyncRun] = None
    stale: list[SyncRun] = []
    for run in runs:
        started = run.started_at or cutoff
        if started >= cutoff:
            if active is None or started > (active.started_at or started):
                active = run
        else:
            stale.append(run)

    if stale:
        now = datetime.utcnow()
        for run in stale:
            run.status = "failed"
            run.error = "ejecución huérfana"
            run.completed_at = now
        await db.commit()

    return active


async def perform_sync(
    db: AsyncSession,
    trigger: str = "manual",
    created_by: Optional[str] = None,
    frequency_hours: Optional[int] = None,
) -> SyncRun:
    """Ejecuta una sincronización y registra el resultado en `sync_runs`.

    Estado de la ejecución: `completed` (todas las fuentes corrieron),
    `partial` (algunas fuentes quedaron omitidas) o `failed` (alguna fuente
    falló). Ver `app/services/sync_sources.py`.
    """
    config = await load_config(db)
    frequency = _normalize_frequency(frequency_hours or config["frequency_hours"])
    started_at = datetime.utcnow()

    run = SyncRun(
        run_id=f"sync-{uuid.uuid4().hex[:12]}",
        trigger=trigger if trigger in ("manual", "scheduled") else "manual",
        status="running",
        frequency_hours=frequency,
        entities="{}",
        created_by=created_by,
        started_at=started_at,
    )
    db.add(run)
    await db.commit()

    try:
        outcome = await run_sources(db, now=started_at)
        counts = dict(outcome.counts)
        skipped = list(outcome.skipped)
        errors = list(outcome.errors)

        completed_at = datetime.utcnow()
        # `entities` conserva las claves por fuente y agrega `skipped`.
        run.entities = json.dumps({**counts, "skipped": skipped}, ensure_ascii=False)
        run.completed_at = completed_at

        if errors:
            run.status = "failed"
            run.error = "; ".join(errors)[:2000]
        elif skipped:
            # La ejecución sirvió, pero no todas las fuentes corrieron
            # (p. ej. MongoDB no está configurado).
            run.status = "partial"
        else:
            run.status = "completed"

        setting = await _ensure_setting(db)
        data = _load_setting(setting)
        data.update(
            {
                "frequency_hours": frequency,
                "last_run_at": completed_at.isoformat() + "Z",
                "last_run_id": run.run_id,
                "last_run_status": run.status,
            }
        )
        setting.setting_value = json.dumps(data, ensure_ascii=False)
        setting.updated_at = completed_at
        setting.updated_by = created_by or "system"
        await db.commit()
        return run
    except Exception as exc:  # pragma: no cover - depende de la BD real
        await db.rollback()
        run.status = "failed"
        run.error = str(exc)[:2000]
        run.completed_at = datetime.utcnow()
        db.add(run)
        await db.commit()
        raise


# ── Auditoría ───────────────────────────────────────────────────────────────

async def record_audit(
    db: AsyncSession,
    *,
    action: str,
    entity_type: str,
    entity_id: str,
    entity_name: Optional[str] = None,
    admin_user_id: Optional[str] = None,
    admin_email: Optional[str] = None,
    changes: Optional[dict] = None,
) -> ActionLog:
    """Registra la acción en `action_logs` (Centro de auditoría, #16).

    Se usan solo las acciones ya conocidas por el Audit Center (`UPDATE` y
    `CREATE`) para no romper sus filtros.
    """
    log = ActionLog(
        log_id=f"log-{uuid.uuid4().hex[:12]}",
        admin_user_id=admin_user_id or "system",
        admin_email=admin_email or "system",
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        entity_name=entity_name,
        changes=json.dumps(changes, ensure_ascii=False) if changes else None,
    )
    db.add(log)
    await db.commit()
    return log
