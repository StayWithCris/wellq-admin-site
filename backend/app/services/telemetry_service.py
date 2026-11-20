"""
services/telemetry_service.py — Recomendación #5: Telemetría y registros reales
================================================================================

Módulo de observabilidad real (API + IA) para Operaciones de Plataforma.

ARQUITECTURA Y DECISIÓN DE DESPLIEGUE EN CLOUD RUN (OPCIÓN A - COSTO MÍNIMO REAL):
-----------------------------------------------------------------------------------
- Esta versión opera bajo topología de instancia única con escala a cero en Cloud Run:
  `--max-instances=1`, `--min-instances=0` y CPU throttling estándar activado.
- El estado en tiempo real (buffer de peticiones, histograma y TelemetryHub SSE) reside
  en la memoria RAM del proceso único para máxima velocidad ($O(1)$) y cero costo adicional.
- Bajo `--min-instances=0` y CPU throttling estándar:
  1) `flusher_loop()` drena lotes hacia MongoDB Atlas mientras hay peticiones HTTP activas
     (que mantienen la CPU asignada al contenedor).
  2) Si cesa el tráfico (ej. el operador cierra o minimiza la pestaña por >2 min activando
     `document.visibilityState`), Cloud Run entra en reposo y escala a cero, eliminando
     el 100% de la facturación ociosa 24/7.
  3) Ante el apagado del contenedor (`SIGTERM`), el ciclo de vida ejecuta `drain_all_pending()`
     dentro del presupuesto de 5 segundos, garantizando que hasta 2.000 documentos pendientes
     en el buffer se persistan en MongoDB Atlas antes de que la instancia termine.
- La verdad histórica acumulada se persiste en lotes asíncronos en MongoDB Atlas (`latency_metrics`),
  con un índice TTL estricto de 8 días (691.200 s) para evitar saturación de almacenamiento.
- Si en el futuro se habilitase autoescalado horizontal multi-instancia (>1 contenedor),
  el streaming en vivo requeriría un broker centralizado (ej. Redis Pub/Sub o NATS) o
  exportación mediante OpenTelemetry a Google Cloud Monitoring.

Flujo de datos
--------------
    requests ──▶ middleware ──record_request() O(1), sin I/O──┬─▶ deque(maxlen=2000) ──▶ flusher batch ──▶ Mongo latency_metrics
                                                              ├─▶ RollingHistogram (O(1) update / O(B) percentiles)
                                                              └─▶ TelemetryHub ──▶ SSE
    pose_analysis_logs (Atlas) ──change stream*──▶ TelemetryHub ┘

    (*con degradación elegante si el change stream no está disponible)

Reglas de rendimiento (Cloud Run en producción)
-----------------------------------------------
- El hot path (`record_request`) NO hace I/O: es O(1) y sin esperas.
- La persistencia a Mongo la hace `flusher_loop()` en background, por lotes de 200.
- Ningún buffer sin cota: todos usan `maxlen` / `maxsize` fijos.
- El stream SSE nunca mantiene abierta una sesión de Neon (el pool es 5+5).

Fechas
------
TODO el módulo usa datetimes AWARE en UTC. `datetime.utcnow()` está DEPRECADO
en Python 3.12, por eso existe el helper `utcnow()`. Al leer datetimes de
Mongo (que llegan naive UTC) se normaliza SIEMPRE con
`.replace(tzinfo=timezone.utc)` antes de comparar.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import structlog

from app.db.mongodb import MongoCollections, get_mongo_db

logger = structlog.get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Constantes de módulo
# ─────────────────────────────────────────────────────────────────────────────

# Semántica ÚNICA y compartida del histograma, alineada 1:1 con `$bucket` de MongoDB.
# Cubre desde latencias CRUD rápidas (<25 ms) hasta inferencias pesadas de visión artificial (hasta 30.000 ms).
# Cubeta `i` cubre [BOUNDS[i], BOUNDS[i+1]) y la última posición es el overflow [30000, ∞).
BUCKET_BOUNDS_MS = (0, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000, 30000)
N_BUCKETS = len(BUCKET_BOUNDS_MS)  # 11 posiciones: 10 rangos acotados + 1 overflow

# Buffer de requests a persistir (cota dura de RAM).
LATENCY_BUFFER_MAX = 2000
# Máximo de documentos escritos por tanda (presión de escritura acotada).
FLUSH_BATCH_SIZE = 200
# Intervalo mínimo entre dos pasadas del flusher (segundos).
FLUSH_IDLE_INTERVAL_S = 1.0

# Ventana deslizante del histograma en memoria (para el tick SSE en vivo).
ROLLING_WINDOW_S = 300
ROLLING_MAX_EVENTS = 2000

# Temporizadores LOCALES del generador SSE (los tests los parchean).
HEARTBEAT_INTERVAL_S = 25   # ": ping"
METRICS_TICK_S = 15         # "event: metrics"
STREAM_POLL_S = 2.5         # cota dura de espera del bucle

# Tamaño de la cola SSE por suscriptor (drop-oldest).
SUBSCRIBER_QUEUE_MAX = 500

# Ventanas temporales soportadas por los endpoints REST.
WINDOW_SECONDS: dict[str, int] = {
    "1h": 3600,
    "24h": 86400,
    "7d": 604800,
}

# Granularidad de la serie temporal por ventana.
WINDOW_GRANULARITY: dict[str, dict[str, Any]] = {
    "1h": {"unit": "minute", "binSize": 1},   # ~60 puntos
    "24h": {"unit": "hour", "binSize": 1},    # ~24 puntos
    "7d": {"unit": "hour", "binSize": 3},     # ~56 puntos
}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers de fecha
# ─────────────────────────────────────────────────────────────────────────────

def utcnow() -> datetime:
    """datetime.now(timezone.utc) — `datetime.utcnow()` está DEPRECADO en 3.12.

    TODO el módulo usa datetimes AWARE; al leer de Mongo (que devuelve naive
    UTC) se normaliza con `.replace(tzinfo=timezone.utc)`. NO mezclar con las
    fechas naive de los modelos SQLModel del resto del repo (TypeError)."""
    return datetime.now(timezone.utc)


def _as_aware(value: Any) -> Optional[datetime]:
    """Normaliza un datetime de Mongo (naive UTC) a aware UTC."""
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


# ─────────────────────────────────────────────────────────────────────────────
# Histograma por cubetas (O(1) por evento, O(B) por percentil)
# ─────────────────────────────────────────────────────────────────────────────

def bucket_index(latency_ms: float) -> int:
    """Cubeta de una latencia — O(B) con B=11 constante → O(1) efectivo.

    Valores de referencia EXACTOS con BUCKET_BOUNDS_MS =
    (0, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000, 30000):
    0 → 0 · 24.9 → 0 · 25 → 1 · 49.9 → 1 · 50 → 2 ·
    9999.9 → 8 · 10000 → 9 · 29999.9 → 9 · 30000 → 10 (overflow).

    Coincide con los rangos [lower, upper) que produce `$bucket` en MongoDB."""
    for i in range(N_BUCKETS - 1):
        if latency_ms < BUCKET_BOUNDS_MS[i + 1]:
            return i
    return N_BUCKETS - 1  # índice 10: overflow (>= 30000 ms)


def percentile_from_histogram(counts: list[int], q: float) -> float:
    """Percentil q∈[0,1] desde un histograma de N_BUCKETS cubetas — O(B).

    counts[i] = eventos en [BOUNDS[i], BOUNDS[i+1]); counts[10] = overflow (>= 30000 ms).
    Interpola linealmente dentro de la cubeta finita que contiene el q-ésimo evento.
    Si el percentil cae en la cubeta de overflow, satura explícitamente en el
    límite superior conocido (BUCKET_BOUNDS_MS[-1] = 30000.0 ms) en lugar de inventar
    un ancho nominal arbitrario que distorsione P90/P99.
    Devuelve 0.0 si total == 0."""
    total = sum(counts)                      # O(B)
    if total == 0:
        return 0.0
    target = q * total
    acumulado = 0
    for i, c in enumerate(counts):           # O(B)
        if c > 0 and acumulado + c >= target:
            if i >= N_BUCKETS - 1:
                # Cubeta de overflow abierta: saturación documentada en 30000 ms.
                return float(BUCKET_BOUNDS_MS[-1])
            inferior = float(BUCKET_BOUNDS_MS[i])
            superior = float(BUCKET_BOUNDS_MS[i + 1])
            frac = (target - acumulado) / c
            ancho = superior - inferior
            return round(inferior + frac * ancho, 2)
        acumulado += c
    return float(BUCKET_BOUNDS_MS[-1])


def counts_from_mongo_buckets(buckets: list[dict]) -> list[int]:
    """Mapea la salida del sub-pipeline 'histogram' de $facet a counts[11].

    `$bucket` etiqueta cada cubeta con SU LÍMITE INFERIOR (0, 25, 50… 10000) y
    las latencias fuera de boundaries caen al id dado en 'default' ("overflow").

    Mapping: 0→0, 25→1, 50→2, …, 10000→9, "overflow"→10."""
    counts = [0] * N_BUCKETS
    for b in buckets or []:
        bucket_id = b.get("_id")
        if bucket_id == "overflow":
            idx = N_BUCKETS - 1
        else:
            try:
                idx = BUCKET_BOUNDS_MS.index(int(bucket_id))
            except (ValueError, TypeError):
                continue
        counts[idx] += b.get("count", 0)
    return counts


class RollingHistogram:
    """Histograma con ventana temporal deslizante (p. ej. últimos 5 min para el
    tick en vivo).

    Interno: counts[N_BUCKETS] + contador de errores + deque(maxlen=K) de
    (ts, idx, is_error).

    update(ts, latency_ms, is_error=False): O(1) — bucket_index + counts[idx] += 1
            + errores += is_error + append (ts, idx, is_error).
    percentile(q): O(B) — delega en percentile_from_histogram.
    evict(ahora):  O(1) amortizado — expulsa por la izquierda los vencidos
                   descontando counts[idx] Y el contador de errores; el deque
                   tiene maxlen como cota dura.
    snapshot():    O(B) — {'p50_ms','p90_ms','p99_ms','rpm','error_rate'} de la
                   ventana; alimenta el `event: metrics` del generador SSE.

    Complejidad espacial: O(K) con K = maxlen — RAM acotada siempre."""

    def __init__(self, window_s: int = ROLLING_WINDOW_S, max_events: int = ROLLING_MAX_EVENTS):
        self.window_s = window_s
        self.max_events = max_events
        self._counts: list[int] = [0] * N_BUCKETS
        self._errors = 0
        self._events: deque[tuple[datetime, int, bool]] = deque(maxlen=max_events)

    # ── Métricas internas (para tests y diagnóstico) ────────────────────────
    @property
    def counts(self) -> list[int]:
        return list(self._counts)

    @property
    def errors(self) -> int:
        return self._errors

    @property
    def events(self) -> int:
        return len(self._events)

    def update(self, ts: datetime, latency_ms: float, is_error: bool = False) -> None:
        """Inserta una muestra en la ventana — O(1)."""
        ts_aware = _as_aware(ts) or utcnow()
        idx = bucket_index(latency_ms)

        # Si el deque está lleno, expulsamos el más viejo ANTES de insertar
        # para que los contadores (counts y errores) no se desincronicen.
        if len(self._events) >= self.max_events:
            _, old_idx, old_err = self._events.popleft()
            self._counts[old_idx] = max(0, self._counts[old_idx] - 1)
            if old_err:
                self._errors = max(0, self._errors - 1)

        self._counts[idx] += 1
        if is_error:
            self._errors += 1
        self._events.append((ts_aware, idx, bool(is_error)))

    def evict(self, ahora: Optional[datetime] = None) -> int:
        """Expulsa de la ventana los eventos vencidos — O(1) amortizado."""
        ahora = _as_aware(ahora) or utcnow()
        corte = ahora - timedelta(seconds=self.window_s)
        expulsados = 0
        while self._events:
            ts, idx, is_error = self._events[0]
            if ts >= corte:
                break
            self._events.popleft()
            self._counts[idx] = max(0, self._counts[idx] - 1)
            if is_error:
                self._errors = max(0, self._errors - 1)
            expulsados += 1
        return expulsados

    def percentile(self, q: float) -> float:
        return percentile_from_histogram(self._counts, q)

    def snapshot(self) -> dict[str, float]:
        """Resumen O(B) de la ventana deslizante."""
        self.evict(utcnow())
        total = sum(self._counts)
        minutos = self.window_s / 60.0
        return {
            "p50_ms": self.percentile(0.5),
            "p90_ms": self.percentile(0.9),
            "p99_ms": self.percentile(0.99),
            "rpm": round(total / minutos, 2) if total else 0.0,
            "error_rate": round(self._errors / total, 4) if total else 0.0,
            "total_events": total,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Estado global del módulo
# ─────────────────────────────────────────────────────────────────────────────

# Buffer acotado de requests pendientes de persistir (lo drena el flusher).
LATENCY_BUFFER: deque[dict] = deque(maxlen=LATENCY_BUFFER_MAX)

# Histograma deslizante en memoria (alimenta los ticks SSE).
rolling_hist = RollingHistogram()

# Evento que despierta al flusher. asyncio.Event.set() NO es thread-safe, por eso
# `wake_flusher()` enruta el set() al loop del flusher cuando el llamador está
# en otro hilo.
_flusher_wakeup: Optional[asyncio.Event] = None
_flusher_loop_ref: Optional[asyncio.AbstractEventLoop] = None


# ─────────────────────────────────────────────────────────────────────────────
# Hub de eventos (fan-out SSE)
# ─────────────────────────────────────────────────────────────────────────────

class TelemetryHub:
    """Fan-out de eventos 'log' en vivo. Cada suscriptor tiene SU PROPIA cola
    acotada: asyncio.Queue(maxsize=500) → publish() es O(1) por suscriptor
    (put_nowait; si está llena se descarta el evento más viejo con
    get_nowait()+put_nowait() — drop-oldest, sin bloquear al publisher).

    subscribe(filtro) / unsubscribe() en O(#suscriptores).
    NUNCA guarda referencias a sesiones de Neon."""

    def __init__(self, queue_size: int = SUBSCRIBER_QUEUE_MAX):
        self._queue_size = queue_size
        self._subscribers: dict[int, dict] = {}
        self._next_id = 1

    # ── Suscripción ────────────────────────────────────────────────────────
    def subscribe(
        self,
        clinic_id: Optional[str] = None,
        endpoint: Optional[str] = None,
    ) -> dict:
        """Registra un suscriptor y devuelve su descriptor (con su cola)."""
        sub = {
            "id": self._next_id,
            "queue": asyncio.Queue(maxsize=self._queue_size),
            "clinic_id": clinic_id,
            "endpoint": endpoint,
        }
        self._next_id += 1
        self._subscribers[sub["id"]] = sub
        return sub

    def unsubscribe(self, sub: dict) -> None:
        """Elimina un suscriptor — O(1)."""
        if not sub:
            return
        self._subscribers.pop(sub.get("id"), None)

    def close(self) -> None:
        """Cierra todos los suscriptores (shutdown del lifespan)."""
        self._subscribers.clear()

    @property
    def subscribers(self) -> list[dict]:
        return list(self._subscribers.values())

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    # ── Publicación ────────────────────────────────────────────────────────
    @staticmethod
    def _matches(sub: dict, event: dict) -> bool:
        """Aplica los filtros del suscriptor (clinic_id / endpoint)."""
        if sub.get("clinic_id") and event.get("clinic_id") != sub["clinic_id"]:
            return False
        if sub.get("endpoint") and event.get("endpoint") != sub["endpoint"]:
            return False
        return True

    def publish(self, event: dict) -> None:
        """Publica un evento a todos los suscriptors — O(1) por suscriptor."""
        for sub in list(self._subscribers.values()):
            if not self._matches(sub, event):
                continue
            queue = sub["queue"]
            if queue.full():
                # Drop-oldest: el evento más viejo se sacrifica para no
                # bloquear al publisher ni al subscriber.
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:  # pragma: no cover - carrera improbable
                    pass
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:  # pragma: no cover - carrera improbable
                pass


# El hub real (la clase se define arriba; se instancia una sola vez para que
# el middleware, el flusher y los generadores SSE compartan el mismo fan-out).
hub = TelemetryHub()


# ─────────────────────────────────────────────────────────────────────────────
# Camino caliente: record_request
# ─────────────────────────────────────────────────────────────────────────────

def record_request(doc: dict) -> None:
    """Camino caliente del middleware — O(1), SIN I/O.

    1) doc['result'] = 'error' si status_code >= 500, 'warn' si >= 400, 'ok'
       si no. Campo UNIFICADO con pose_analysis_logs: el LiveLogStream pinta
       su badge con 'result' (los eventos HTTP NO traen 'result' nativo — sin
       este paso el badge saldría undefined).
    2) LATENCY_BUFFER.append(doc)  → deque(maxlen=2000), descarta el más viejo.
    3) rolling_hist.update(...)     → alimenta los ticks SSE.
    4) hub.publish({'type': 'log', ...}) → el feed EN VIVO muestra el tráfico HTTP.
    5) wake_flusher()                → DESPIERTA al flusher; sin este paso
       flusher_loop() duerme eternamente en su asyncio.Event y Mongo nunca
       recibe documentos.

    La PERSISTENCIA a Mongo la hace flusher_loop en background.

    HILO DE EJECUCIÓN: record_request() SOLO se llama desde el event loop (como
    el middleware). asyncio.Event.set() NO es thread-safe; si algún día se
    llamara desde otro thread, wake_flusher() usa
    loop.call_soon_threadsafe(event.set)."""
    status_code = int(doc.get("status_code") or 0)
    doc["result"] = "error" if status_code >= 500 else ("warn" if status_code >= 400 else "ok")
    doc.setdefault("source", "http")

    LATENCY_BUFFER.append(doc)

    rolling_hist.update(
        doc.get("created_at") or utcnow(),
        float(doc.get("latency_ms") or 0.0),
        is_error=status_code >= 400,
    )

    hub.publish({"type": "log", **doc})

    wake_flusher()


def get_flusher_wakeup() -> asyncio.Event:
    """Devuelve (o crea) el asyncio.Event que despierta al flusher."""
    global _flusher_wakeup
    if _flusher_wakeup is None:
        _flusher_wakeup = asyncio.Event()
    return _flusher_wakeup


def reset_flusher_wakeup() -> asyncio.Event:
    """Crea un Event NUEVO (lo llama el lifespan al arrancar).

    Necesario porque asyncio.Event queda ligado al event loop en el primer
    `wait()`: sin esto, una segunda instancia de la app en el mismo proceso
    (tests) fallaría con "bound to a different event loop"."""
    global _flusher_wakeup
    _flusher_wakeup = asyncio.Event()
    return _flusher_wakeup


def wake_flusher() -> None:
    """Despierta al flusher — O(1).

    `asyncio.Event.set()` NO es thread-safe: si el llamador está en un hilo sin
    event loop, el set() se encola con `call_soon_threadsafe` sobre el loop del
    flusher."""
    event = get_flusher_wakeup()
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # Hilo sin event loop: enrutar al loop del flusher.
        loop = _flusher_loop_ref
        if loop is not None:
            try:
                loop.call_soon_threadsafe(event.set)
                return
            except RuntimeError:  # pragma: no cover - loop cerrado
                pass
    event.set()


# ─────────────────────────────────────────────────────────────────────────────
# Flusher (tarea del lifespan): batch a Mongo
# ─────────────────────────────────────────────────────────────────────────────

async def flush_pending() -> int:
    """Drena hasta FLUSH_BATCH_SIZE docs del buffer y los escribe en Mongo.

    UNA sola `insert_many(ordered=False)`. Si Mongo falla: log estructurado y
    los documentos vuelven al FRENTE del buffer para reintentar en el siguiente
    ciclo (si el buffer se llena, se pierden los más viejos — backpressure
    explícito para proteger la RAM)."""
    docs: list[dict] = []
    while LATENCY_BUFFER and len(docs) < FLUSH_BATCH_SIZE:
        docs.append(LATENCY_BUFFER.popleft())

    if not docs:
        return 0

    try:
        mongo = get_mongo_db()
        await mongo[MongoCollections.LATENCY_METRICS].insert_many(docs, ordered=False)
        logger.info("Lote de telemetría persistido", documentos=len(docs))
        return len(docs)
    except Exception as exc:
        # Devolver los documentos al frente del buffer (conservando el orden).
        LATENCY_BUFFER.extendleft(reversed(docs))
        logger.warning("No se pudo persistir el lote de telemetría", error=str(exc))
        return 0


async def drain_all_pending(max_batches: int = 50) -> int:
    """Drena completamente el LATENCY_BUFFER en lotes sucesivos de FLUSH_BATCH_SIZE.

    Protección ante shutdown en Cloud Run: si hay más de 200 documentos
    acumulados en el buffer (hasta maxlen=2000), una sola llamada a flush_pending()
    dejaría hasta 1.800 eventos huérfanos sin persistir. Este bucle ejecuta hasta
    max_batches para persistir todo el contenido pendiente en MongoDB de forma acotada.

    Retorna el total de documentos persistidos con éxito.
    """
    total_drained = 0
    batches = 0
    while LATENCY_BUFFER and batches < max_batches:
        persisted = await flush_pending()
        if persisted == 0:
            # Si flush_pending falló (ej. error de conexión a Mongo o buffer vacío),
            # salimos para evitar ciclos infinitos en el shutdown.
            break
        total_drained += persisted
        batches += 1
    return total_drained


async def flusher_loop() -> None:
    """Duerme en un asyncio.Event (wake_flusher() lo setea, idempotente).

    Al despertar drena hasta FLUSH_BATCH_SIZE docs del LATENCY_BUFFER
    (popleft O(1) c/u) y hace UNA insert_many(ordered=False) sobre
    latency_metrics.

    Cota: máx 200 docs cada ≥1 s → presión de escritura acotada."""
    global _flusher_loop_ref

    wakeup = reset_flusher_wakeup()
    try:
        _flusher_loop_ref = asyncio.get_running_loop()
    except RuntimeError:  # pragma: no cover
        _flusher_loop_ref = None

    try:
        while True:
            try:
                await asyncio.wait_for(wakeup.wait(), timeout=FLUSH_IDLE_INTERVAL_S)
            except asyncio.TimeoutError:
                pass
            wakeup.clear()
            if LATENCY_BUFFER:
                await flush_pending()
    except asyncio.CancelledError:
        # Shutdown en Cloud Run: drenamos TODO el buffer remanente (hasta 2.000 docs).
        try:
            drained = await drain_all_pending()
            logger.info("Drenado final de buffer completado en shutdown", persistidos=drained)
        except Exception as exc:  # pragma: no cover - cierre best-effort
            logger.warning("Fallo durante drenado final de buffer", error=str(exc))
        logger.info("Flusher de telemetría detenido")
        raise
    finally:
        _flusher_loop_ref = None


# ─────────────────────────────────────────────────────────────────────────────
# Agregaciones REST
# Búsqueda inicial en índice B-Tree O(log N) + escaneo y procesamiento O(K)
# en memoria sobre los K documentos dentro del rango temporal seleccionado.
# ─────────────────────────────────────────────────────────────────────────────

def _bucket_switch_stage() -> dict:
    """Etapa `$addFields` que calcula la cubeta de cada documento.

    MISMA semántica que `bucket_index()`:
    índices 0..N_BUCKETS-2 para los rangos [BOUNDS[i], BOUNDS[i+1]),
    e índice N_BUCKETS-1 (10) para overflow (>= 30000 ms)."""
    branches = [
        {"case": {"$lt": ["$latency_ms", BUCKET_BOUNDS_MS[i + 1]]}, "then": i}
        for i in range(N_BUCKETS - 1)
    ]
    return {
        "$switch": {
            "branches": branches,
            "default": N_BUCKETS - 1,
        }
    }


def _build_facet_pipeline(
    start: datetime,
    end: datetime,
    endpoint: Optional[str],
    clinic_id: Optional[str],
    granularidad: dict,
) -> list[dict]:
    """Arma el pipeline de agregación: UN $match + UN $facet.

    COMPLEJIDAD REAL:
    El $match inicial se apoya en los índices compuestos de MongoDB para realizar
    un seek B-Tree en O(log N). Luego, el pipeline realiza un escaneo y agregación
    en memoria en O(K) sobre los K documentos que caen dentro de la ventana de tiempo.

    ADVERTENCIA CRÍTICA: `$bucket` DESTRUYE los campos del documento — un
    pipeline lineal ($match → $bucket → $group sobre created_at/status_code)
    recibiría null. Por eso todo va dentro del `$facet` y cada sub-pipeline
    reparte por su cuenta.
    """
    match: dict[str, Any] = {"created_at": {"$gte": start, "$lte": end}}
    if endpoint:
        match["endpoint"] = endpoint
    if clinic_id:
        match["clinic_id"] = clinic_id

    return [
        {"$match": match},
        {
            "$facet": {
                # 1) Histograma global → counts[10] vía counts_from_mongo_buckets
                "histogram": [
                    {
                        "$bucket": {
                            "groupBy": "$latency_ms",
                            "boundaries": list(BUCKET_BOUNDS_MS),
                            # OBLIGATORIO: sin default, Mongo lanza
                            # MongoServerError con latencias >= 5000.
                            "default": "overflow",
                        }
                    }
                ],
                # 2) Totales y tasa de error
                "totals": [
                    {
                        "$group": {
                            "_id": None,
                            "total": {"$sum": 1},
                            "errors": {
                                "$sum": {
                                    "$cond": [{"$gte": ["$status_code", 400]}, 1, 0]
                                }
                            },
                        }
                    }
                ],
                # 3) Serie temporal con histograma POR PUNTO.
                #    CRÍTICO: acumular status_code AQUÍ — el sub-pipeline
                #    'histogram' es otro hilo del $facet y NO comparte campos;
                #    sin 'err' en estos $group, series[i].errors sería siempre 0
                #    y la Bar de errores del volumeChart nunca pintaría.
                "series": [
                    {"$addFields": {"bucket": _bucket_switch_stage()}},
                    {
                        "$group": {
                            "_id": {
                                "ts": {"$dateTrunc": {"date": "$created_at", **granularidad}},
                                "b": "$bucket",
                            },
                            "n": {"$sum": 1},
                            "err": {
                                "$sum": {
                                    "$cond": [{"$gte": ["$status_code", 400]}, 1, 0]
                                }
                            },
                        }
                    },
                    {
                        "$group": {
                            "_id": "$_id.ts",
                            "total": {"$sum": "$n"},
                            "errors": {"$sum": "$err"},
                            "buckets": {"$push": {"k": "$_id.b", "v": "$n"}},
                        }
                    },
                    {"$sort": {"_id": 1}},
                ],
                # 4) Top endpoints por volumen
                "endpoints": [
                    {"$group": {"_id": "$endpoint", "count": {"$sum": 1}}},
                    {"$sort": {"count": -1}},
                    {"$limit": 20},
                ],
            }
        },
    ]


def _minutes_per_point(granularidad: dict) -> float:
    """Minutos que cubre cada punto de la serie temporal."""
    unit = granularidad.get("unit", "minute")
    bin_size = float(granularidad.get("binSize", 1))
    return bin_size * (1.0 if unit == "minute" else 60.0)


async def aggregate_latency_metrics(
    mongo,
    start: datetime,
    end: datetime,
    endpoint: Optional[str],
    clinic_id: Optional[str],
    granularidad: dict,
) -> dict:
    """Agrega las métricas de latencia de la ventana.

    Devuelve el contrato de `/api/platform/telemetry/metrics`:
    total_requests, rpm, error_rate, p50_ms, p90_ms, p99_ms, series, endpoints.

    Post-proceso en Python (O(B) por vector de cubetas, NUNCA sorted()):
    - histogram → counts_from_mongo_buckets → percentile_from_histogram(q).
    - series → pivotar buckets [{k, v}] a counts por ts (≤60 puntos × 10
      cubetas) → p50/p90/p99 por punto; rpm del punto = total / minutos del
      intervalo; errors ya viene.
    - totals → rpm global = total / minutos de la ventana; error_rate = errors
      / total."""
    pipeline = _build_facet_pipeline(start, end, endpoint, clinic_id, granularidad)

    facets: dict = {}
    cursor = mongo[MongoCollections.LATENCY_METRICS].aggregate(pipeline)
    async for doc in cursor:
        facets = doc or {}
        break

    # 1) Percentiles globales
    counts = counts_from_mongo_buckets(facets.get("histogram") or [])
    p50 = percentile_from_histogram(counts, 0.50)
    p90 = percentile_from_histogram(counts, 0.90)
    p99 = percentile_from_histogram(counts, 0.99)

    # 2) Totales
    totals_rows = facets.get("totals") or []
    totals = totals_rows[0] if totals_rows else {}
    total_requests = int(totals.get("total") or 0)
    errors = int(totals.get("errors") or 0)

    minutos_ventana = max((end - start).total_seconds() / 60.0, 1.0 / 60.0)
    rpm = round(total_requests / minutos_ventana, 2)
    error_rate = round(errors / total_requests, 4) if total_requests else 0.0

    # 3) Serie temporal
    minutos_punto = _minutes_per_point(granularidad)
    series: list[dict] = []
    for punto in facets.get("series") or []:
        ts = punto.get("_id")
        counts_punto = [0] * N_BUCKETS
        for cubeta in punto.get("buckets") or []:
            try:
                idx = int(cubeta.get("k"))
            except (TypeError, ValueError):
                continue
            if 0 <= idx < N_BUCKETS:
                counts_punto[idx] += int(cubeta.get("v") or 0)

        total_punto = int(punto.get("total") or 0)
        series.append({
            "ts": ts.isoformat() if isinstance(ts, datetime) else ts,
            "p50_ms": percentile_from_histogram(counts_punto, 0.50),
            "p90_ms": percentile_from_histogram(counts_punto, 0.90),
            "p99_ms": percentile_from_histogram(counts_punto, 0.99),
            "rpm": round(total_punto / minutos_punto, 2) if minutos_punto else 0.0,
            "total": total_punto,
            "errors": int(punto.get("errors") or 0),
        })

    # 4) Top endpoints
    endpoints = [
        {"endpoint": row.get("_id"), "count": int(row.get("count") or 0)}
        for row in (facets.get("endpoints") or [])
    ]

    return {
        "total_requests": total_requests,
        "rpm": rpm,
        "error_rate": error_rate,
        "errors": errors,
        "p50_ms": p50,
        "p90_ms": p90,
        "p99_ms": p99,
        "series": series,
        "endpoints": endpoints,
    }


async def fetch_pose_logs(
    mongo,
    start: datetime,
    clinic_id: Optional[str],
    limit: int,
) -> list[dict]:
    """Últimos eventos de `pose_analysis_logs` por `created_at` descendente."""
    match: dict[str, Any] = {"created_at": {"$gte": start}}
    if clinic_id:
        match["clinic_id"] = clinic_id

    cursor = mongo[MongoCollections.POSE_ANALYSIS].find(match).sort("created_at", -1).limit(limit)

    logs: list[dict] = []
    async for doc in cursor:
        item = dict(doc or {})
        created = _as_aware(item.get("created_at"))
        if created is not None:
            item["created_at"] = created.isoformat()
        item.setdefault("source", "pose")
        item.setdefault("result", item.get("status") or "ok")
        logs.append(item)
    return logs


# ─────────────────────────────────────────────────────────────────────────────
# Stream SSE
# ─────────────────────────────────────────────────────────────────────────────

def _sse(event: str, data: dict) -> str:
    """Empaqueta un evento SSE. El cuerpo de un stream solo acepta str/bytes."""
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


async def event_generator(sub: dict, rolling_hist: RollingHistogram, hub: TelemetryHub):
    """Generador del stream SSE.

    REGLAS CLAVE:
    (a) la cola SOLO lleva dicts de 'log' y el generador los envuelve SIEMPRE
        con _sse("log", …) — hacer yield de un dict crudo lanza TypeError en
        Starlette (el cuerpo de un stream solo acepta str/bytes);
    (b) el tick de métricas (15 s) y el heartbeat (25 s) son temporizadores
        LOCALES con time.monotonic();
    (c) el timeout de wait_for se calcula DINÁMICAMENTE como el tiempo hasta el
        PRÓXIMO timer: con STREAM_POLL_S fijo (2.5 s) y cola vacía, el bucle
        duerme la espera completa y el heartbeat atrasa hasta el límite del
        poll.
    """
    ultimo_ping = ultimo_tick = time.monotonic()
    yield ": connect\n\n"
    try:
        while True:
            ahora = time.monotonic()
            espera = min(
                STREAM_POLL_S,                                     # cota dura de CPU
                max(0.01, (ultimo_tick + METRICS_TICK_S) - ahora),
                max(0.01, (ultimo_ping + HEARTBEAT_INTERVAL_S) - ahora),
            )
            try:
                evento = await asyncio.wait_for(sub["queue"].get(), timeout=espera)
                yield _sse("log", evento)      # la cola SOLO lleva dicts
            except asyncio.TimeoutError:
                pass                           # sin eventos → cae a los temporizadores

            ahora = time.monotonic()
            if ahora - ultimo_tick >= METRICS_TICK_S:
                yield _sse("metrics", rolling_hist.snapshot())
                ultimo_tick = ahora
            if ahora - ultimo_ping >= HEARTBEAT_INTERVAL_S:
                yield ": ping\n\n"             # garantizado: sale en su deadline
                ultimo_ping = ahora
    finally:
        hub.unsubscribe(sub)                  # SIEMPRE


# ─────────────────────────────────────────────────────────────────────────────
# Watcher de pose_analysis_logs (change streams de Atlas)
# ─────────────────────────────────────────────────────────────────────────────

def _normalize_pose_event(doc: dict) -> dict:
    """Normaliza un documento de pose_analysis_logs al contrato del feed."""
    evento = {
        "type": "log",
        "source": "pose",
        "created_at": doc.get("created_at"),
        "clinic_id": doc.get("clinic_id"),
        "patient_id": doc.get("patient_id"),
        "result": doc.get("result") or doc.get("status") or "ok",
        "latency_ms": doc.get("latency_ms"),
        "error": doc.get("error"),
    }
    created = _as_aware(doc.get("created_at"))
    if created is not None:
        evento["created_at"] = created.isoformat()
    return evento


async def watch_pose_logs() -> None:
    """Tarea del lifespan. Se cancela en el shutdown.

    DEGRADACIÓN ELEGANTE: try/except al arrancar; si el change stream no está
    disponible (rol sin permisos, entorno sin replica set), log warning y el
    live feed queda solo con el tráfico HTTP (que el middleware ya publica)."""
    try:
        mongo = get_mongo_db()
        async with mongo[MongoCollections.POSE_ANALYSIS].watch() as stream:
            async for change in stream:
                doc = (change or {}).get("fullDocument") or {}
                if not doc:
                    continue
                hub.publish(_normalize_pose_event(doc))
    except asyncio.CancelledError:
        logger.info("Watcher de pose_analysis_logs detenido limpiamente")
    except Exception as exc:
        # Degradación elegante: change stream no disponible.
        logger.warning("Change stream no disponible", error=str(exc))
