"""
routers/telemetry.py — Recomendación #5: Telemetría y registros reales
========================================================================

Endpoints bajo /api/platform/telemetry:

    GET  /metrics   Métricas agregadas de la API (P50/P90/P99, errores, RPM)
    GET  /logs      Últimos eventos de análisis de postura
    GET  /stream    Feed en vivo por SSE (tráfico HTTP + eventos de IA)

AUTENTICACIÓN Y RBAC
--------------------
- Todos los endpoints (`/metrics`, `/logs`, `/stream`) están protegidos mediante
  el header estándar `Authorization: Bearer <token>`.
- El permiso RBAC exigido para consulta y suscripción al stream es `platform.view`.
- NUNCA se reciben tokens JWT por query param (`?token=`): OWASP prohíbe tokens en
  URLs debido a filtraciones en logs de proxies, balanceadores y navegadores.
- Los tokens son emitidos y validados por el propio backend (jose + JWT_SECRET).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.db.mongodb import get_mongo_db
from app.services import telemetry_service as telemetry

from .auth import decode_token, require_permission

router = APIRouter(prefix="/api/platform/telemetry", tags=["Telemetría y registros reales"])

WindowLiteral = Literal["1h", "24h", "7d"]
_stream_bearer = HTTPBearer(auto_error=False)


def _unavailable(reason: str) -> dict:
    """Respuesta HTTP 200 con `status: unavailable` (MongoDB no configurado).

    NO es un 503: el frontend pinta un aviso informativo en vez de un error
    destructivo. El feed en vivo sigue funcionando (buffer + histograma en
    memoria) aunque no haya persistencia.
    """
    return {
        "status": "unavailable",
        "reason": reason,
        "total_requests": 0,
        "rpm": 0.0,
        "error_rate": 0.0,
        "errors": 0,
        "p50_ms": 0.0,
        "p90_ms": 0.0,
        "p99_ms": 0.0,
        "series": [],
        "endpoints": [],
        "logs": [],
    }


async def require_stream_permission(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_stream_bearer),
) -> dict:
    """Valida el header Authorization: Bearer <token> para el stream SSE.

    - 401 si falta el header Authorization, si el token es inválido o no es de tipo 'access'.
    - 403 si el usuario carece del permiso 'platform.view'.
    - NUNCA lee credenciales de la query string.
    """
    if not credentials or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Header Authorization: Bearer requerido",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = decode_token(credentials.credentials)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido o expirado",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido: tipo incorrecto",
            headers={"WWW-Authenticate": "Bearer"},
        )

    perms = payload.get("permissions", [])
    role = payload.get("role", "")

    # Backward compat: super_admin legacy sin permissions en el JWT
    if role != "super_admin" or perms:
        if "platform.view" not in perms:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Acceso denegado: se requiere el permiso 'platform.view'",
            )

    return payload


@router.get(
    "/metrics",
    summary="Métricas agregadas de la API en la ventana seleccionada",
    dependencies=[Depends(require_permission("platform.view"))],
)
async def get_metrics(
    window: WindowLiteral = Query("1h", description="Ventana temporal: 1h, 24h o 7d"),
    endpoint: Optional[str] = Query(None, description="Filtra por endpoint (plantilla)"),
    clinic_id: Optional[str] = Query(None, description="Filtra por clínica"),
):
    """P50/P90/P99, tasa de error, RPM, serie temporal y top de endpoints."""
    fin = telemetry.utcnow()
    inicio = fin - timedelta(seconds=telemetry.WINDOW_SECONDS[window])

    try:
        mongo = get_mongo_db()
    except RuntimeError as exc:
        return _unavailable(str(exc))

    datos = await telemetry.aggregate_latency_metrics(
        mongo,
        inicio,
        fin,
        endpoint,
        clinic_id,
        telemetry.WINDOW_GRANULARITY[window],
    )

    return {
        "status": "ok",
        "window": window,
        "from": inicio.isoformat(),
        "to": fin.isoformat(),
        **datos,
    }


@router.get(
    "/logs",
    summary="Últimos eventos de análisis de postura",
    dependencies=[Depends(require_permission("platform.view"))],
)
async def get_logs(
    window: WindowLiteral = Query("1h", description="Ventana temporal: 1h, 24h o 7d"),
    clinic_id: Optional[str] = Query(None, description="Filtra por clínica"),
    limit: int = Query(50, ge=1, le=200, description="Máximo de eventos a devolver"),
):
    """Eventos recientes de `pose_analysis_logs`, ordenados por `created_at` desc."""
    inicio = telemetry.utcnow() - timedelta(seconds=telemetry.WINDOW_SECONDS[window])

    try:
        mongo = get_mongo_db()
    except RuntimeError as exc:
        return _unavailable(str(exc))

    logs = await telemetry.fetch_pose_logs(mongo, inicio, clinic_id, limit)

    return {
        "status": "ok",
        "window": window,
        "from": inicio.isoformat(),
        "logs": logs,
    }


@router.get(
    "/stream",
    summary="Feed en vivo de telemetría por SSE",
)
async def telemetry_stream(
    payload: dict = Depends(require_stream_permission),
    clinic_id: Optional[str] = Query(None, description="Filtra por clínica"),
    endpoint: Optional[str] = Query(None, description="Filtra por endpoint"),
):
    """Stream SSE con `event: log`, `event: metrics` (cada 15 s) y `: ping` (cada 25 s).

    El token se valida por header Authorization: Bearer antes de abrir el stream.
    Permiso RBAC requerido: `platform.view`.
    """
    async def _generador():
        sub = telemetry.hub.subscribe(clinic_id=clinic_id, endpoint=endpoint)
        try:
            async for chunk in telemetry.event_generator(
                sub, telemetry.rolling_hist, telemetry.hub
            ):
                yield chunk
        finally:
            telemetry.hub.unsubscribe(sub)

    return StreamingResponse(
        _generador(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )

