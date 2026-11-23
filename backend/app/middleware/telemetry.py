"""
middleware/telemetry.py — Recomendación #5: recolección de telemetría HTTP
============================================================================

Mide la latencia de cada request y la entrega a `telemetry_service.record_request()`,
que es O(1) y SIN I/O (append al buffer acotado + histograma en memoria +
publish al hub SSE). La persistencia a MongoDB la hace el flusher en background.

El usuario no percibe overhead: el middleware solo mide con `time.perf_counter()`
y agrega un dict.
"""

from __future__ import annotations

import time

from fastapi import Request

from app.services import telemetry_service as telemetry

# Rutas excluidas: el propio stream (evita medir conexiones eternas), health,
# docs y el schema OpenAPI.
EXCLUDED_PATHS = {
    "/api/platform/telemetry/stream",
    "/health",
    "/api/health",
    "/docs",
    "/redoc",
    "/openapi.json",
}

# OPTIONS queda FUERA a propósito: `app.middleware("http")` se apila LIFO y
# envuelve TODO el stack, así que este middleware ve los preflight de CORS
# ANTES de que el CORSMiddleware los despache. No rastrearlos evita inflar
# métricas y RPM con handshakes que no son tráfico real.
TRACKED_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}


async def telemetry_middleware(request: Request, call_next):
    """Middleware HTTP — Recomendación #5.

    Mide la latencia de cada request y llama a
    `telemetry_service.record_request()`, que es O(1) y SIN I/O (append al
    buffer acotado + histograma en memoria + publish al hub SSE). La
    persistencia a MongoDB la hace el flusher en background.
    """
    inicio = time.perf_counter()

    try:
        response = await call_next(request)
    except Exception:
        # Una excepción NO manejada (5xx) nunca produce una `response`: si no
        # la registramos, el error REAL (el más importante de observar) se
        # escapa de la telemetría. Se registra y la excepción se re-lanza
        # intacta: el ServerErrorMiddleware sigue siendo quien la maneja.
        _registrar(request, 500, inicio)
        raise

    _registrar(request, response.status_code, inicio)
    return response


def _registrar(request: Request, status_code: int, inicio: float) -> None:
    """Mide y registra un request si corresponde al alcance de la telemetría."""
    if request.url.path in EXCLUDED_PATHS or request.method not in TRACKED_METHODS:
        return

    # FastAPI setea scope["route"] durante el routing
    # (child_scope["route"] = self en fastapi/routing.py): usar la PLANTILLA
    # de la ruta acota la cardinalidad — /api/clinics/1, /api/clinics/2…
    # quedan como /api/clinics/{id}.
    # Fallback "unmatched": los 404 de scanners tienen paths aleatorios y
    # explotarían los índices y el <select> de endpoints si se guardaran
    # crudos.
    # SEGURIDAD (OWASP): nunca se loguea ni almacena la query string completa ni
    # tokens/secretos; únicamente se extrae 'clinic_id' para filtrado operativo.
    route = request.scope.get("route")
    telemetry.record_request(
        {
            "created_at": telemetry.utcnow(),          # aware — helper propio
            "endpoint": route.path if route else "unmatched",
            "method": request.method,
            "status_code": status_code,
            "latency_ms": round((time.perf_counter() - inicio) * 1000, 2),
            "clinic_id": request.query_params.get("clinic_id"),
        }
    )
