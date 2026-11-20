"""Pruebas unitarias de la Recomendación #5 — Telemetría y registros reales.

Sin red, sin MongoDB y sin FastAPI: solo la lógica del módulo de telemetría.

Cubre:
  - Semántica del histograma por cubetas (límites exactos, longitud 10).
  - Percentiles con interpolación lineal (incluida la cubeta de overflow).
  - RollingHistogram: update / evict / snapshot / cota de memoria.
  - record_request: camino caliente O(1), derivación de 'result', buffer acotado.
  - TelemetryHub: fan-out O(1) por suscriptor y drop-oldest sin bloquear.
"""

import random
from datetime import timedelta
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from app.services import telemetry_service as telemetry


@pytest.fixture(autouse=True)
def _reset_estado_global():
    """Cada prueba arranca con el estado global limpio."""
    telemetry.LATENCY_BUFFER.clear()
    telemetry.rolling_hist = telemetry.RollingHistogram()
    telemetry.hub.close()
    telemetry.reset_flusher_wakeup()
    yield
    telemetry.LATENCY_BUFFER.clear()
    telemetry.hub.close()


def _doc(status_code=200, latency_ms=42.0, created_at=None, endpoint="/api/clinics/{clinic_id}"):
    return {
        "created_at": created_at or telemetry.utcnow(),
        "endpoint": endpoint,
        "method": "GET",
        "status_code": status_code,
        "latency_ms": latency_ms,
        "clinic_id": "clin-001",
    }


def _fake_request(path="/api/clinics/1", method="GET", query_string=b"", route=None):
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "root_path": "",
        "scheme": "http",
        "server": ("testserver", 80),
        "query_string": query_string,
        "headers": [],
    }
    if route is not None:
        scope["route"] = route
    return Request(scope)


# ─────────────────────────────────────────────────────────────────────────────
# bucket_index — límites EXACTOS de la semántica [BOUNDS[i], BOUNDS[i+1])
# ─────────────────────────────────────────────────────────────────────────────

def test_bucket_index_limites():
    # BOUNDS = (0, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000, 30000)
    assert telemetry.bucket_index(0) == 0
    assert telemetry.bucket_index(24.9) == 0
    assert telemetry.bucket_index(25) == 1
    assert telemetry.bucket_index(49.9) == 1
    assert telemetry.bucket_index(50) == 2
    assert telemetry.bucket_index(2499) == 6      # [1000, 2500)
    assert telemetry.bucket_index(2500) == 7      # [2500, 5000)
    assert telemetry.bucket_index(4999.9) == 7
    assert telemetry.bucket_index(5000) == 8      # [5000, 10000)
    assert telemetry.bucket_index(9999.9) == 8
    assert telemetry.bucket_index(10000) == 9     # [10000, 30000)
    assert telemetry.bucket_index(29999.9) == 9
    assert telemetry.bucket_index(30000) == 10    # overflow
    assert telemetry.bucket_index(45000) == 10    # overflow


def test_histograma_longitud_exacta_11():
    assert telemetry.N_BUCKETS == 11
    assert len(telemetry.BUCKET_BOUNDS_MS) == 11
    hist = telemetry.RollingHistogram()
    assert len(hist.counts) == 11
    telemetry.record_request(_doc(latency_ms=7))
    assert len(hist.counts) == 11


# ─────────────────────────────────────────────────────────────────────────────
# percentile_from_histogram
# ─────────────────────────────────────────────────────────────────────────────

def test_percentil_histograma_vacio_devuelve_0():
    assert telemetry.percentile_from_histogram([0] * 11, 0.5) == 0.0


def test_percentil_interpolacion_dentro_de_cubeta():
    # 10 eventos en la cubeta [25, 50) (índice 1) → la mediana cae a la mitad de la cubeta (37.5).
    assert telemetry.percentile_from_histogram([0, 10] + [0] * 9, 0.5) == 37.5


def test_percentil_overflow_satura_a_30000():
    # 10 eventos en el overflow (>= 30000) → satura explícitamente en el límite superior conocido (30000.0).
    assert telemetry.percentile_from_histogram([0] * 10 + [10], 0.5) == 30000.0


def test_percentiles_monotonos_p50_menor_o_igual_p90_menor_o_igual_p99():
    rng = random.Random(20261119)
    counts = [0] * 11
    for _ in range(200):
        valor = rng.uniform(0, 35000)
        counts[telemetry.bucket_index(valor)] += 1

    p50 = telemetry.percentile_from_histogram(counts, 0.50)
    p90 = telemetry.percentile_from_histogram(counts, 0.90)
    p99 = telemetry.percentile_from_histogram(counts, 0.99)

    assert p50 <= p90 <= p99


# ─────────────────────────────────────────────────────────────────────────────
# counts_from_mongo_buckets
# ─────────────────────────────────────────────────────────────────────────────

def test_mapeo_ids_de_bucket_mongo():
    buckets = [
        {"_id": 0, "count": 1},
        {"_id": 25, "count": 2},
        {"_id": 50, "count": 3},
        {"_id": 100, "count": 4},
        {"_id": 250, "count": 5},
        {"_id": 500, "count": 6},
        {"_id": 1000, "count": 7},
        {"_id": 2500, "count": 8},
        {"_id": 5000, "count": 9},
        {"_id": 10000, "count": 10},
        {"_id": "overflow", "count": 11},
    ]
    counts = telemetry.counts_from_mongo_buckets(buckets)

    assert len(counts) == 11
    assert counts == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]
    # El id "overflow" SIEMPRE cae en el índice 10.
    assert counts[10] == 11


# ─────────────────────────────────────────────────────────────────────────────
# RollingHistogram
# ─────────────────────────────────────────────────────────────────────────────

def test_update_incrementa_cubeta_correcta():
    hist = telemetry.RollingHistogram()
    ahora = telemetry.utcnow()

    hist.update(ahora, 5)       # → cubeta 0 [0, 25)
    hist.update(ahora, 30)      # → cubeta 1 [25, 50)
    hist.update(ahora, 35000)   # → cubeta 10 (overflow)

    assert hist.counts[0] == 1
    assert hist.counts[1] == 1
    assert hist.counts[10] == 1
    assert hist.events == 3


def test_evict_expulsa_solo_vencidos_y_decrementa():
    hist = telemetry.RollingHistogram(window_s=300)
    ahora = telemetry.utcnow()
    viejo = ahora - timedelta(minutes=10)

    hist.update(viejo, 5)     # vencido
    hist.update(ahora, 5)     # vigente

    hist.evict(ahora)

    assert hist.counts[0] == 1       # solo queda el vigente (5 ms en [0, 25) -> mediana 12.5)
    assert hist.events == 1
    assert hist.percentile(0.5) == 12.5


def test_evict_descuenta_tambien_los_errores():
    hist = telemetry.RollingHistogram(window_s=300)
    ahora = telemetry.utcnow()

    hist.update(ahora - timedelta(minutes=10), 5, is_error=True)
    hist.update(ahora, 5, is_error=False)

    assert hist.errors == 1
    hist.evict(ahora)
    assert hist.errors == 0
    assert hist.counts[0] == 1


def test_len_acotado_por_maxlen():
    hist = telemetry.RollingHistogram(max_events=100)
    ahora = telemetry.utcnow()

    for _ in range(10_000):
        hist.update(ahora, 12)

    assert hist.events == 100          # el deque no crece
    assert sum(hist.counts) == 100     # los contadores siguen sincronizados


def test_snapshot_error_rate_en_ventana():
    hist = telemetry.RollingHistogram()
    ahora = telemetry.utcnow()

    for i in range(10):
        hist.update(ahora, 20, is_error=(i < 2))

    snapshot = hist.snapshot()
    assert snapshot["error_rate"] == 0.2
    assert snapshot["total_events"] == 10
    # 20 ms cae en la cubeta [0, 25): el percentil interpola dentro de ella (0 + 0.5 * 25 = 12.5).
    assert snapshot["p50_ms"] == 12.5
    assert snapshot["rpm"] > 0


# ─────────────────────────────────────────────────────────────────────────────
# record_request + buffer
# ─────────────────────────────────────────────────────────────────────────────

def test_record_request_alimenta_buffer_histograma_y_hub():
    sub = telemetry.hub.subscribe()

    for _ in range(5):
        telemetry.record_request(_doc(latency_ms=42))

    assert len(telemetry.LATENCY_BUFFER) == 5
    assert sum(telemetry.rolling_hist.counts) == 5
    assert telemetry.rolling_hist.counts[1] == 5      # 42 ms → cubeta [25, 50) (índice 1)
    assert sub["queue"].qsize() == 5


def test_record_request_despierta_al_flusher():
    telemetry.get_flusher_wakeup().clear()
    telemetry.record_request(_doc())
    assert telemetry.get_flusher_wakeup().is_set() is True


def test_record_request_deriva_result_de_status_code():
    telemetry.record_request(_doc(status_code=200))
    telemetry.record_request(_doc(status_code=404))
    telemetry.record_request(_doc(status_code=500))

    resultados = [d["result"] for d in telemetry.LATENCY_BUFFER]
    assert resultados == ["ok", "warn", "error"]


def test_deque_maxlen_descarta_el_mas_viejo():
    # El buffer tiene una cota dura: al llenarse se descarta el más viejo.
    for _ in range(telemetry.LATENCY_BUFFER_MAX + 50):
        telemetry.record_request(_doc(endpoint=f"/api/e/{_}"))

    assert len(telemetry.LATENCY_BUFFER) == telemetry.LATENCY_BUFFER_MAX
    # El primer endpoint insertado ya no está; el último sí.
    endpoints = [d["endpoint"] for d in telemetry.LATENCY_BUFFER]
    assert endpoints[0] != "/api/e/0"
    assert endpoints[-1] == f"/api/e/{telemetry.LATENCY_BUFFER_MAX + 49}"


def test_excluded_paths_no_generan_eventos():
    import asyncio

    from app.middleware.telemetry import telemetry_middleware

    class _Response:
        status_code = 200

    async def _call_next(_request):
        return _Response()

    async def _correr(request):
        return await telemetry_middleware(request, _call_next)

    # /health está excluido → no se registra nada.
    asyncio.run(_correr(_fake_request(path="/health")))
    assert len(telemetry.LATENCY_BUFFER) == 0

    # OPTIONS queda fuera de TRACKED_METHODS a propósito (preflight CORS).
    asyncio.run(_correr(_fake_request(path="/api/clinics", method="OPTIONS")))
    assert len(telemetry.LATENCY_BUFFER) == 0

    # Un GET normal se registra con la PLANTILLA de la ruta.
    asyncio.run(
        _correr(
            _fake_request(
                path="/api/clinics/1",
                route=SimpleNamespace(path="/api/clinics/{clinic_id}"),
            )
        )
    )
    assert len(telemetry.LATENCY_BUFFER) == 1
    doc = telemetry.LATENCY_BUFFER[0]
    assert doc["endpoint"] == "/api/clinics/{clinic_id}"
    assert doc["latency_ms"] >= 0
    assert doc["result"] == "ok"


def test_middleware_registra_excepciones_no_manejadas():
    """Un 5xx por excepción NO manejada también queda registrado.

    Sin esto, el error REAL (el más importante de observar) se escaparía de la
    telemetría: la excepción nunca produce una `response`."""
    import asyncio

    from app.middleware.telemetry import telemetry_middleware

    async def _call_next(_request):
        raise RuntimeError("boom")

    request = _fake_request(
        path="/api/clinics",
        route=SimpleNamespace(path="/api/clinics"),
    )

    async def _correr():
        return await telemetry_middleware(request, _call_next)

    with pytest.raises(RuntimeError):
        asyncio.run(_correr())

    assert len(telemetry.LATENCY_BUFFER) == 1
    doc = telemetry.LATENCY_BUFFER[0]
    assert doc["status_code"] == 500
    assert doc["result"] == "error"
    assert doc["endpoint"] == "/api/clinics"


# ─────────────────────────────────────────────────────────────────────────────
# TelemetryHub
# ─────────────────────────────────────────────────────────────────────────────

def test_publish_alcanza_a_todos_los_suscriptores_O1_por_suscriptor():
    sub_a = telemetry.hub.subscribe()
    sub_b = telemetry.hub.subscribe()

    telemetry.hub.publish({"type": "log", "endpoint": "/api/clinics"})

    assert sub_a["queue"].qsize() == 1
    assert sub_b["queue"].qsize() == 1


def test_cola_llena_descarta_el_mas_viejo_sin_bloquear():
    sub = telemetry.hub.subscribe()

    for i in range(600):
        telemetry.hub.publish({"type": "log", "n": i})

    assert sub["queue"].qsize() == 500
    # Drop-oldest: el primero se fue, el último sigue en la cola.
    assert sub["queue"].get_nowait()["n"] == 100


def test_suscriptor_filtrado_por_clinic_id():
    sub = telemetry.hub.subscribe(clinic_id="clin-001")

    telemetry.hub.publish({"type": "log", "clinic_id": "clin-002"})
    assert sub["queue"].qsize() == 0

    telemetry.hub.publish({"type": "log", "clinic_id": "clin-001"})
    assert sub["queue"].qsize() == 1


def test_unsubscribe_elimina_al_suscriptor():
    sub = telemetry.hub.subscribe()
    assert telemetry.hub.subscriber_count == 1
    telemetry.hub.unsubscribe(sub)
    assert telemetry.hub.subscriber_count == 0


def test_drain_all_pending_drena_completamente_multiples_lotes(monkeypatch):
    """Verifica que drain_all_pending drene > 200 items en varios lotes de hasta 200."""
    import asyncio

    insertados = []

    class _FakeCollection:
        async def insert_many(self, docs, ordered=False):
            insertados.append(list(docs))
            return len(docs)

    class _FakeDb:
        def __getitem__(self, name):
            return _FakeCollection()

    monkeypatch.setattr(telemetry, "get_mongo_db", lambda: _FakeDb())

    # Agregamos 450 items al buffer (más de 2 lotes de FLUSH_BATCH_SIZE=200)
    for i in range(450):
        telemetry.LATENCY_BUFFER.append({"i": i, "latency_ms": 10})

    assert len(telemetry.LATENCY_BUFFER) == 450

    total = asyncio.run(telemetry.drain_all_pending())

    assert total == 450
    assert len(telemetry.LATENCY_BUFFER) == 0
    # Se debieron procesar 3 lotes: 200, 200 y 50
    assert len(insertados) == 3
    assert len(insertados[0]) == 200
    assert len(insertados[1]) == 200
    assert len(insertados[2]) == 50


def test_watch_pose_logs_cierre_limpio_en_cancelacion(monkeypatch):
    """Verifica que watch_pose_logs atrape CancelledError limpiamente sin filtrar excepciones."""
    import asyncio

    class _FakeChangeStream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            return None

        def __aiter__(self):
            return self

        async def __anext__(self):
            await asyncio.sleep(10.0)
            raise StopAsyncIteration

    class _FakeCollection:
        def watch(self):
            return _FakeChangeStream()

    class _FakeDb:
        def __getitem__(self, name):
            return _FakeCollection()

    monkeypatch.setattr(telemetry, "get_mongo_db", lambda: _FakeDb())

    async def _test():
        tarea = asyncio.create_task(telemetry.watch_pose_logs())
        await asyncio.sleep(0.05)
        tarea.cancel()
        # Debe completarse limpiamente sin lanzar CancelledError hacia arriba
        await tarea
        assert tarea.done()

    asyncio.run(_test())


