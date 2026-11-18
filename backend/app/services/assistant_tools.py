"""Mejora #20 — Herramientas de solo lectura del asistente conversacional.

El modelo de lenguaje NUNCA escribe SQL ni accede a tablas libremente: solo
puede pedir una de estas 6 consultas fijas, con argumentos validados contra
listas cerradas. Cada herramienta exige el mismo permiso RBAC que la vista
equivalente del panel, así el asistente no muestra datos que el usuario no
podría ver.

| Herramienta            | Permiso         | Datos                                   |
|------------------------|-----------------|-----------------------------------------|
| resumen_panel          | overview.view   | Totales de clínicas, MRR, tickets, alertas |
| listar_clinicas        | clinics.view    | Clínicas filtradas por estado           |
| resumen_facturas       | billing.view    | Facturas por estado y vencidas          |
| alertas_activas        | platform.view   | Alertas sin gestionar por severidad     |
| tickets_soporte        | tickets.view    | Tickets por estado y los últimos abiertos |
| estado_sincronizacion  | platform.view   | Frecuencia y última sincronización (#7) |
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Awaitable, Callable, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models_db import Alert, Clinic, Invoice, SupportTicket

MAX_ROWS = 20

CLINIC_STATUSES = ["active", "warning", "critical", "churned"]
INVOICE_STATUSES = ["paid", "pending", "overdue"]
ALERT_SEVERITIES = ["low", "medium", "high", "critical"]
TICKET_STATUSES = ["Open", "Sent", "Closed"]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    permission: str
    parameters: Optional[dict]
    run: Callable[[AsyncSession, dict], Awaitable[dict]]

    def declaration(self) -> dict:
        decl = {"name": self.name, "description": self.description}
        if self.parameters:
            decl["parameters"] = self.parameters
        return decl


# ── Utilidades ──────────────────────────────────────────────────────────────

def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() + "Z" if value else None


def _enum_arg(args: dict, key: str, allowed: list[str]) -> Optional[str]:
    value = args.get(key)
    if value is None or value == "":
        return None
    for option in allowed:
        if str(value).strip().lower() == option.lower():
            return option
    raise ValueError(f"Valor no permitido para '{key}': {value}. Opciones: {', '.join(allowed)}")


def _limit_arg(args: dict, default: int = 10) -> int:
    try:
        value = int(args.get("limite", default))
    except (TypeError, ValueError):
        value = default
    return max(1, min(value, MAX_ROWS))


async def _all(db: AsyncSession, stmt) -> list:
    return list((await db.execute(stmt)).scalars().all())


def _visible_clinics(rows: list) -> list:
    return [c for c in rows if not getattr(c, "is_deleted", False)]


# ── Herramientas ────────────────────────────────────────────────────────────

async def resumen_panel(db: AsyncSession, args: dict) -> dict:
    clinics = _visible_clinics(await _all(db, select(Clinic).where(Clinic.is_deleted.is_(False))))
    tickets = await _all(db, select(SupportTicket))
    alerts = await _all(db, select(Alert).where(Alert.acknowledged_at.is_(None)))
    alerts = [a for a in alerts if getattr(a, "acknowledged_at", None) is None]
    by_status = Counter((c.status or "unknown") for c in clinics)
    return {
        "clinicas_total": len(clinics),
        "clinicas_por_estado": dict(by_status),
        "mrr_total": round(sum(float(c.mrr or 0) for c in clinics if c.status != "churned"), 2),
        "tickets_abiertos": sum(1 for t in tickets if (t.status or "") != "Closed"),
        "alertas_sin_gestionar": len(alerts),
        "alertas_criticas_o_altas": sum(1 for a in alerts if (a.severity or "") in ("high", "critical")),
    }


async def listar_clinicas(db: AsyncSession, args: dict) -> dict:
    status = _enum_arg(args, "estado", CLINIC_STATUSES)
    limit = _limit_arg(args)
    stmt = select(Clinic).where(Clinic.is_deleted.is_(False))
    if status:
        stmt = stmt.where(Clinic.status == status)
    rows = _visible_clinics(await _all(db, stmt))
    if status:
        rows = [c for c in rows if c.status == status]
    rows.sort(key=lambda c: (c.health_score if c.health_score is not None else 100, c.name or ""))
    return {
        "filtro_estado": status,
        "total_encontradas": len(rows),
        "clinicas": [
            {
                "nombre": c.name,
                "plan": c.tier,
                "estado": c.status,
                "salud": c.health_score,
                "mrr": c.mrr,
                "pacientes": f"{c.patients_used}/{c.patients_limit}",
                "ultimo_acceso": _iso(c.last_login),
            }
            for c in rows[:limit]
        ],
    }


async def resumen_facturas(db: AsyncSession, args: dict) -> dict:
    status = _enum_arg(args, "estado", INVOICE_STATUSES)
    stmt = select(Invoice)
    if status:
        stmt = stmt.where(Invoice.status == status)
    rows = await _all(db, stmt)
    if status:
        rows = [i for i in rows if i.status == status]
    summary: dict[str, dict] = {}
    for inv in rows:
        bucket = summary.setdefault(inv.status or "unknown", {"cantidad": 0, "monto": 0.0})
        bucket["cantidad"] += 1
        bucket["monto"] = round(bucket["monto"] + float(inv.amount or 0), 2)
    overdue = sorted((i for i in rows if i.status == "overdue"), key=lambda i: -(i.amount or 0))[:5]
    return {
        "filtro_estado": status,
        "por_estado": summary,
        "moneda": (rows[0].currency if rows else "USD"),
        "mayores_vencidas": [
            {"factura": i.invoice_id, "clinica_id": i.clinic_id, "monto": i.amount, "emitida": _iso(i.issued_at)}
            for i in overdue
        ],
    }


async def alertas_activas(db: AsyncSession, args: dict) -> dict:
    severity = _enum_arg(args, "severidad", ALERT_SEVERITIES)
    limit = _limit_arg(args)
    stmt = select(Alert).where(Alert.acknowledged_at.is_(None))
    if severity:
        stmt = stmt.where(Alert.severity == severity)
    rows = [a for a in await _all(db, stmt) if getattr(a, "acknowledged_at", None) is None]
    if severity:
        rows = [a for a in rows if a.severity == severity]
    rank = {s: i for i, s in enumerate(reversed(ALERT_SEVERITIES))}
    rows.sort(key=lambda a: (rank.get(a.severity, 9), -(a.created_at or datetime.min).timestamp()))
    return {
        "filtro_severidad": severity,
        "total": len(rows),
        "alertas": [
            {"titulo": a.title, "severidad": a.severity, "tipo": a.type, "creada": _iso(a.created_at)}
            for a in rows[:limit]
        ],
    }


async def tickets_soporte(db: AsyncSession, args: dict) -> dict:
    status = _enum_arg(args, "estado", TICKET_STATUSES)
    limit = _limit_arg(args)
    rows = await _all(db, select(SupportTicket))
    counts = Counter((t.status or "unknown") for t in rows)
    if status:
        rows = [t for t in rows if t.status == status]
    rows.sort(key=lambda t: t.reported_at or datetime.min, reverse=True)
    return {
        "por_estado": dict(counts),
        "filtro_estado": status,
        "tickets": [
            {
                "titulo": t.title,
                "estado": t.status,
                "categoria": t.category,
                "clinica_id": t.clinic_id,
                "asignado_a": t.responder_name,
                "reportado": _iso(t.reported_at),
            }
            for t in rows[:limit]
        ],
    }


async def estado_sincronizacion(db: AsyncSession, args: dict) -> dict:
    from app.services import sync_service

    config = await sync_service.load_config(db)
    failure = await sync_service.get_failure_alert_status(db)
    return {
        "frecuencia_horas": config.get("frequency_hours"),
        "ultima_ejecucion": config.get("last_run_at"),
        "estado_ultima_ejecucion": config.get("last_run_status"),
        "proxima_ejecucion": config.get("next_run_at"),
        "fallos_consecutivos": failure.get("consecutive_failures"),
        "alerta_por_fallos_activa": failure.get("alert_active"),
    }


def _obj(properties: dict) -> dict:
    return {"type": "object", "properties": properties}


TOOLS: list[Tool] = [
    Tool(
        "resumen_panel",
        "Resumen general del panel: total de clínicas por estado, MRR total, tickets abiertos y alertas sin gestionar.",
        "overview.view",
        None,
        resumen_panel,
    ),
    Tool(
        "listar_clinicas",
        "Lista clínicas (ordenadas de menor a mayor salud) con plan, estado, salud, MRR y uso de pacientes.",
        "clinics.view",
        _obj({
            "estado": {"type": "string", "enum": CLINIC_STATUSES, "description": "Filtrar por estado de la clínica."},
            "limite": {"type": "integer", "description": f"Máximo de filas (1 a {MAX_ROWS})."},
        }),
        listar_clinicas,
    ),
    Tool(
        "resumen_facturas",
        "Cantidad y monto de facturas por estado (paid, pending, overdue) y las 5 vencidas de mayor monto.",
        "billing.view",
        _obj({"estado": {"type": "string", "enum": INVOICE_STATUSES, "description": "Filtrar por estado."}}),
        resumen_facturas,
    ),
    Tool(
        "alertas_activas",
        "Alertas del sistema que aún no se gestionan, ordenadas por severidad.",
        "platform.view",
        _obj({
            "severidad": {"type": "string", "enum": ALERT_SEVERITIES, "description": "Filtrar por severidad."},
            "limite": {"type": "integer", "description": f"Máximo de filas (1 a {MAX_ROWS})."},
        }),
        alertas_activas,
    ),
    Tool(
        "tickets_soporte",
        "Conteo de tickets de soporte por estado y los más recientes (Open, Sent, Closed).",
        "tickets.view",
        _obj({
            "estado": {"type": "string", "enum": TICKET_STATUSES, "description": "Filtrar por estado."},
            "limite": {"type": "integer", "description": f"Máximo de filas (1 a {MAX_ROWS})."},
        }),
        tickets_soporte,
    ),
    Tool(
        "estado_sincronizacion",
        "Estado de la sincronización de datos: frecuencia, última y próxima ejecución, fallos consecutivos.",
        "platform.view",
        None,
        estado_sincronizacion,
    ),
]

TOOLS_BY_NAME = {t.name: t for t in TOOLS}


def user_can_use(tool: Tool, user: dict) -> bool:
    """Misma regla que `require_permission`: super_admin sin permisos = acceso total."""
    perms = (user or {}).get("permissions") or []
    if (user or {}).get("role") == "super_admin" and not perms:
        return True
    return tool.permission in perms


def allowed_tools(user: dict) -> list[Tool]:
    return [t for t in TOOLS if user_can_use(t, user)]


async def run_tool(db: AsyncSession, name: str, args: Any, user: dict) -> tuple[str, dict]:
    """Ejecuta una herramienta. Devuelve (estado, resultado) sin lanzar excepciones.

    Estado: ``ok``, ``denied`` (sin permiso), ``unknown`` o ``error``.
    """
    tool = TOOLS_BY_NAME.get(name)
    if tool is None:
        return "unknown", {"error": f"La herramienta '{name}' no existe."}
    if not user_can_use(tool, user):
        return "denied", {"error": f"El usuario no tiene el permiso '{tool.permission}' para esta consulta."}
    try:
        result = await tool.run(db, args if isinstance(args, dict) else {})
        return "ok", result
    except ValueError as exc:
        return "error", {"error": str(exc)}
    except Exception:  # noqa: BLE001 — no exponer detalles internos al modelo
        return "error", {"error": "No se pudo obtener el dato en este momento."}
