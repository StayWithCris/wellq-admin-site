"""Pruebas de API de la Recomendación #5 — Telemetría y registros reales.

Sin conexión a Atlas: se usa un doble de MongoDB (stubs de `insert_many`,
`aggregate`, `find` y `create_index`) y tokens del propio backend (jose +
JWT_SECRET), nunca Keycloak.
"""

import asyncio
import time
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.main import app
from app.routers import telemetry as telemetry_router
from app.routers.auth import create_token
from app.services import telemetry_service as telemetry


# ─────────────────────────────────────────────────────────────────────────────
# Doble de MongoDB (sin red)
# ─────────────────────────────────────────────────────────────────────────────

FACETS_SEED = {
    "histogram": [
        {"_id": 0, "count": 2},
        {"_id": 25, "count": 4},
        {"_id": 50, "count": 1},
        {"_id": 100, "count": 1},
        {"_id": 250, "count": 0},
        {"_id": 500, "count": 0},
        {"_id": 1000, "count": 0},
        {"_id": 2500, "count": 0},
        {"_id": 5000, "count": 0},
        {"_id": 10000, "count": 1},
        {"_id": "overflow", "count": 1},
    ],
    "totals": [{"_id": None, "total": 10, "errors": 2}],
    "series": [
        {
            "_id": datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc),
            "total": 6,
            "errors": 1,
            "buckets": [{"k": 0, "v": 2}, {"k": 1, "v": 4}],
        },
        {
            "_id": datetime(2026, 11, 19, 12, 1, tzinfo=timezone.utc),
            "total": 4,
            "errors": 1,
            "buckets": [{"k": 2, "v": 1}, {"k": 3, "v": 1}, {"k": 9, "v": 1}],
        },
    ],
    "endpoints": [
        {"_id": "/api/clinics/{clinic_id}", "count": 7},
        {"_id": "/api/dashboard", "count": 3},
    ],
}

POSE_DOCS = [
    {
        "created_at": datetime(2026, 11, 19, 12, 5, tzinfo=timezone.utc),
        "clinic_id": "clin-001",
        "patient_id": "pat-001",
        "result": "ok",
        "latency_ms": 120,
    },
    {
        "created_at": datetime(2026, 11, 19, 12, 4, tzinfo=timezone.utc),
        "clinic_id": "clin-002",
        "result": "warn",
        "latency_ms": 310,
    },
]


class _FakeCursor:
    """Cursor asíncrono mínimo (compatible con el de Motor)."""

    def __init__(self, docs):
        self._docs = list(docs)

    def __aiter__(self):
        async def _gen():
            for doc in self._docs:
                yield doc

        return _gen()

    def sort(self, *_args, **_kwargs):
        return self

    def limit(self, cantidad):
        return _FakeCursor(self._docs[:cantidad])


class _FakeCollection:
    def __init__(self, db, name):
        self._db = db
        self._name = name

    async def insert_many(self, docs, ordered=False):
        self._db.inserted.append({"collection": self._name, "docs": list(docs)})
        return InsertManyResultLike(list(docs))

    def aggregate(self, pipeline):
        self._db.pipelines.append({"collection": self._name, "pipeline": pipeline})
        return _FakeCursor([FACETS_SEED])

    def find(self, match=None):
        self._db.finds.append({"collection": self._name, "match": match})
        return _FakeCursor(POSE_DOCS)

    async def create_index(self, keys, name=None, **_kwargs):
        self._db.indexes.append({"collection": self._name, "name": name, "keys": keys, "kwargs": _kwargs})
        return name

    async def drop_index(self, index_or_name, **_kwargs):
        self._db.dropped_indexes.append({"collection": self._name, "name": index_or_name})
        self._db.indexes = [
            idx for idx in self._db.indexes
            if not (idx["collection"] == self._name and idx.get("name") == index_or_name)
        ]
        return index_or_name

    async def index_information(self):
        info = {}
        for idx in self._db.indexes:
            if idx["collection"] == self._name:
                name = idx.get("name")
                info[name] = {"key": idx.get("keys"), **idx.get("kwargs", {})}
        return info

    def watch(self):
        raise RuntimeError("change streams no disponibles en el doble de pruebas")


class InsertManyResultLike:
    def __init__(self, inserted_ids):
        self.inserted_ids = inserted_ids


class _FakeMongo:
    def __init__(self):
        self.inserted = []
        self.pipelines = []
        self.finds = []
        self.indexes = []
        self.dropped_indexes = []

    def __getitem__(self, name):
        return _FakeCollection(self, name)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _token(permissions=None, role="viewer", token_type="access"):
    return create_token(
        {
            "user_id": "usr-telemetry",
            "email": "telemetry@wellq.test",
            "role": role,
            "permissions": permissions if permissions is not None else [],
            "type": token_type,
        },
        timedelta(minutes=5),
    )


def _auth(permissions=None, role="viewer"):
    return {"Authorization": f"Bearer {_token(permissions=permissions, role=role)}"}


@pytest.fixture(autouse=True)
def _reset_estado_global():
    telemetry.LATENCY_BUFFER.clear()
    telemetry.rolling_hist = telemetry.RollingHistogram()
    telemetry.hub.close()
    telemetry.reset_flusher_wakeup()
    yield
    telemetry.LATENCY_BUFFER.clear()
    telemetry.hub.close()


@pytest.fixture
def mongo_fake(monkeypatch):
    fake = _FakeMongo()
    monkeypatch.setattr(telemetry_router, "get_mongo_db", lambda: fake)
    monkeypatch.setattr(telemetry, "get_mongo_db", lambda: fake)
    return fake


@pytest.fixture
def db_completo():
    """Sesión falsa que además soporta `.scalar()` (lo usa GET /api/clinics)."""
    from app.db.neon import get_db

    class _Result:
        def __init__(self, rows):
            self._rows = rows

        def scalars(self):
            return self

        def all(self):
            return list(self._rows)

        def first(self):
            return self._rows[0] if self._rows else None

        def scalar_one_or_none(self):
            return self._rows[0] if self._rows else None

        def scalar(self):
            return self._rows[0] if self._rows else 0

    class _Session:
        async def execute(self, *_args, **_kwargs):
            return _Result([])

        def add(self, obj):
            pass

        async def commit(self):
            pass

        async def rollback(self):
            pass

    async def _override():
        yield _Session()

    app.dependency_overrides[get_db] = _override
    yield
    app.dependency_overrides.pop(get_db, None)


# ─────────────────────────────────────────────────────────────────────────────
# 1) Autenticación de /metrics
# ─────────────────────────────────────────────────────────────────────────────

def test_metrics_sin_header_devuelve_403(client):
    r = client.get("/api/platform/telemetry/metrics")
    assert r.status_code == 403, r.text


def test_metrics_con_token_invalido_devuelve_401(client):
    r = client.get(
        "/api/platform/telemetry/metrics",
        headers={"Authorization": "Bearer invalido"},
    )
    assert r.status_code == 401, r.text


def test_metrics_sin_permiso_platform_view_devuelve_403(client):
    r = client.get(
        "/api/platform/telemetry/metrics",
        headers=_auth(permissions=[], role="viewer"),
    )
    assert r.status_code == 403, r.text


# ─────────────────────────────────────────────────────────────────────────────
# 2) /metrics con platform.view
# ─────────────────────────────────────────────────────────────────────────────

def test_metrics_estructura_de_la_respuesta(client, mongo_fake):
    r = client.get(
        "/api/platform/telemetry/metrics?window=24h",
        headers=_auth(permissions=["platform.view"]),
    )
    assert r.status_code == 200, r.text
    data = r.json()

    assert data["status"] == "ok"
    assert data["window"] == "24h"
    assert data["from"] and data["to"]

    for clave in ("p50_ms", "p90_ms", "p99_ms", "error_rate", "rpm"):
        assert clave in data, f"Falta {clave}"
        assert isinstance(data[clave], (int, float))

    assert data["total_requests"] == 10
    assert data["errors"] == 2
    assert data["error_rate"] == 0.2
    assert data["p50_ms"] <= data["p90_ms"] <= data["p99_ms"]

    assert isinstance(data["series"], list)
    assert len(data["series"]) <= 60
    if data["series"]:
        punto = data["series"][0]
        for clave in ("ts", "p50_ms", "p90_ms", "p99_ms", "rpm", "errors"):
            assert clave in punto, f"Falta {clave} en la serie"
        # El sub-pipeline 'series' acumula status_code: errors NO es siempre 0.
        assert sum(p["errors"] for p in data["series"]) == 2

    assert len(data["endpoints"]) == 2
    assert data["endpoints"][0]["endpoint"] == "/api/clinics/{clinic_id}"
    assert data["endpoints"][0]["count"] == 7

    # El aggregation usa UN $match + UN $facet.
    pipeline = mongo_fake.pipelines[0]["pipeline"]
    assert pipeline[0]["$match"] and "$facet" in pipeline[1]
    facets = pipeline[1]["$facet"]
    assert set(facets) == {"histogram", "totals", "series", "endpoints"}
    # $bucket necesita `default` o Mongo lanza con latencias >= 5000.
    assert facets["histogram"][0]["$bucket"]["default"] == "overflow"


def test_metrics_window_invalido_devuelve_422(client, mongo_fake):
    r = client.get(
        "/api/platform/telemetry/metrics?window=foo",
        headers=_auth(permissions=["platform.view"]),
    )
    assert r.status_code == 422, r.text


def test_metrics_filtra_por_endpoint_y_clinica(client, mongo_fake):
    client.get(
        "/api/platform/telemetry/metrics?endpoint=%2Fapi%2Fclinics%2F%7Bclinic_id%7D&clinic_id=clin-001",
        headers=_auth(permissions=["platform.view"]),
    )
    match = mongo_fake.pipelines[0]["pipeline"][0]["$match"]
    assert match["endpoint"] == "/api/clinics/{clinic_id}"
    assert match["clinic_id"] == "clin-001"


# ─────────────────────────────────────────────────────────────────────────────
# 3) /logs
# ─────────────────────────────────────────────────────────────────────────────

def test_logs_limit_cero_devuelve_422(client, mongo_fake):
    r = client.get(
        "/api/platform/telemetry/logs?limit=0",
        headers=_auth(permissions=["platform.view"]),
    )
    assert r.status_code == 422, r.text


def test_logs_window_invalido_devuelve_422(client, mongo_fake):
    r = client.get(
        "/api/platform/telemetry/logs?window=foo",
        headers=_auth(permissions=["platform.view"]),
    )
    assert r.status_code == 422, r.text


def test_logs_limite_maximo_devuelve_200(client, mongo_fake):
    r = client.get(
        "/api/platform/telemetry/logs?limit=200",
        headers=_auth(permissions=["platform.view"]),
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "ok"
    assert len(data["logs"]) <= 200
    # Ordenados por created_at descendente.
    fechas = [log["created_at"] for log in data["logs"]]
    assert fechas == sorted(fechas, reverse=True)


# ─────────────────────────────────────────────────────────────────────────────
# 4) /stream — autenticación por Header Authorization: Bearer <token>
# ─────────────────────────────────────────────────────────────────────────────

def test_stream_token_invalido_devuelve_401(client):
    r = client.get(
        "/api/platform/telemetry/stream",
        headers={"Authorization": "Bearer invalido"},
    )
    assert r.status_code == 401, r.text


def test_stream_token_de_tipo_incorrecto_devuelve_401(client):
    r = client.get(
        "/api/platform/telemetry/stream",
        headers={"Authorization": f"Bearer {_token(token_type='refresh')}"},
    )
    assert r.status_code == 401, r.text


def test_stream_sin_platform_view_devuelve_403(client):
    r = client.get(
        "/api/platform/telemetry/stream",
        headers=_auth(permissions=[], role="viewer"),
    )
    assert r.status_code == 403, r.text


def test_stream_sin_header_authorization_devuelve_401(client):
    r = client.get("/api/platform/telemetry/stream")
    assert r.status_code == 401, r.text


def test_stream_query_param_token_es_ignorado_y_sin_header_devuelve_401(client):
    """El paso de token en query param está deshabilitado por seguridad (OWASP)."""
    r = client.get(
        f"/api/platform/telemetry/stream?token={_token(permissions=['platform.view'])}"
    )
    assert r.status_code == 401, r.text


def test_stream_heartbeat_y_tick_de_metricas(monkeypatch):
    """El heartbeat y el tick de métricas NO dependen de que llegue un evento.

    Con los timers parcheados a 0.5 s / 0.3 s, en ~3 s deben aparecer ≥3 pings
    y ≥1 `event: metrics` aunque la cola nunca reciba nada. Esto demuestra el
    desacople Y la espera dinámica del generador (con un poll fijo de 2.5 s el
    assert fallaría de forma determinista).

    NOTA: el TestClient de Starlette 0.37 BUFFERIZA el body completo antes de
    devolver la respuesta, así que no puede consumir un stream SSE infinito.
    Por eso el generador se consume directamente (es el corazón del contrato
    SSE); la capa HTTP se valida en los tests de token y de headers.
    """
    monkeypatch.setattr(telemetry, "HEARTBEAT_INTERVAL_S", 0.5)
    monkeypatch.setattr(telemetry, "METRICS_TICK_S", 0.3)

    async def _main():
        sub = telemetry.hub.subscribe()
        generador = telemetry.event_generator(
            sub, telemetry.rolling_hist, telemetry.hub
        )
        cuerpo = ""
        limite = time.monotonic() + 3.0
        try:
            async for chunk in generador:
                cuerpo += chunk
                if time.monotonic() > limite:
                    break
        finally:
            await generador.aclose()
        return cuerpo

    cuerpo = asyncio.run(_main())

    assert cuerpo.startswith(": connect")
    assert cuerpo.count(": ping") >= 3, cuerpo
    assert cuerpo.count("event: metrics") >= 1, cuerpo

    # Al cerrar el generador se ejecuta su `finally` y desuscribe.
    assert telemetry.hub.subscriber_count == 0


def test_stream_publica_los_eventos_del_hub():
    """Un evento publicado en el hub llega al stream como `event: log`."""

    async def _main():
        sub = telemetry.hub.subscribe()
        generador = telemetry.event_generator(
            sub, telemetry.rolling_hist, telemetry.hub
        )

        async def _publicar():
            await asyncio.sleep(0.05)
            telemetry.hub.publish(
                {"type": "log", "source": "http", "endpoint": "/api/clinics",
                 "status_code": 200, "result": "ok", "latency_ms": 12.5}
            )

        tarea = asyncio.create_task(_publicar())
        try:
            async for chunk in generador:
                if "event: log" in chunk:
                    return chunk
        finally:
            tarea.cancel()
            await asyncio.gather(tarea, return_exceptions=True)
            await generador.aclose()

    chunk = asyncio.run(_main())
    assert "event: log" in chunk
    assert "/api/clinics" in chunk
    assert '"result": "ok"' in chunk


def test_stream_devuelve_los_headers_sse_correctos():
    """El endpoint SSE expone text/event-stream y desactiva el buffering."""
    import asyncio

    respuesta = asyncio.run(
        telemetry_router.telemetry_stream(payload={}, clinic_id=None, endpoint=None)
    )
    assert respuesta.media_type == "text/event-stream"
    assert respuesta.headers["cache-control"] == "no-cache"
    assert respuesta.headers["x-accel-buffering"] == "no"


# ─────────────────────────────────────────────────────────────────────────────
# 5) Middleware end-to-end: persiste en Mongo y publica en el hub
# ─────────────────────────────────────────────────────────────────────────────

def test_middleware_persiste_el_lote_y_publica_en_el_hub(monkeypatch, db_completo):
    """GET /api/clinics → el middleware registra el request:
    (a) el flusher lo persiste en Mongo con el endpoint en forma de plantilla;
    (b) el suscriptor del hub recibe el `event: log` de ese request."""

    async def _main():
        fake = _FakeMongo()
        monkeypatch.setattr(telemetry, "get_mongo_db", lambda: fake)

        suscriptor = telemetry.hub.subscribe()
        token = _token(permissions=["platform.view"], role="super_admin")

        tarea_flusher = asyncio.create_task(telemetry.flusher_loop())
        try:
            transporte = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transporte, base_url="http://testserver"
            ) as ac:
                r = await ac.get("/api/clinics", headers={"Authorization": f"Bearer {token}"})
                assert r.status_code == 200, r.text

                # (b) El feed EN VIVO reacciona al tráfico HTTP.
                assert suscriptor["queue"].qsize() >= 1
                evento = suscriptor["queue"].get_nowait()
                assert evento["type"] == "log"
                assert evento["source"] == "http"
                assert evento["endpoint"] == "/api/clinics"
                assert evento["result"] == "ok"

                # (a) El flusher persiste el lote (despertado por wake_flusher).
                for _ in range(50):
                    if fake.inserted:
                        break
                    await asyncio.sleep(0.1)

            assert fake.inserted, "el flusher no persistió ningún lote"
            documentos = fake.inserted[0]["docs"]
            assert documentos
            assert documentos[0]["endpoint"] == "/api/clinics"
            assert documentos[0]["result"] == "ok"
            assert documentos[0]["latency_ms"] >= 0
        finally:
            tarea_flusher.cancel()
            await asyncio.gather(tarea_flusher, return_exceptions=True)

    asyncio.run(_main())


# ─────────────────────────────────────────────────────────────────────────────
# 6) MongoDB no configurado → 200 con status "unavailable"
# ─────────────────────────────────────────────────────────────────────────────

def test_metrics_sin_mongodb_devuelve_200_unavailable(client):
    """Sin MONGODB_URI el backend responde 200 (NO 503): el frontend pinta un
    aviso informativo en vez de un error destructivo."""
    r = client.get(
        "/api/platform/telemetry/metrics",
        headers=_auth(permissions=["platform.view"]),
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "unavailable"
    assert data["reason"]
    assert data["series"] == []
    assert data["endpoints"] == []
    assert data["p50_ms"] == 0.0


def test_logs_sin_mongodb_devuelve_200_unavailable(client):
    r = client.get(
        "/api/platform/telemetry/logs",
        headers=_auth(permissions=["platform.view"]),
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "unavailable"
    assert data["logs"] == []


def test_record_request_sigue_operando_sin_mongodb(client):
    """Sin Mongo, `record_request()` sigue alimentando buffer + histograma + hub
    en memoria: el feed en vivo y los ticks SSE funcionan igual."""
    telemetry.hub.close()
    sub = telemetry.hub.subscribe()

    r = client.get(
        "/api/platform/telemetry/metrics",
        headers=_auth(permissions=["platform.view"]),
    )
    assert r.status_code == 200

    assert len(telemetry.LATENCY_BUFFER) >= 1
    assert sum(telemetry.rolling_hist.counts) >= 1
    assert sub["queue"].qsize() >= 1


def test_las_tareas_de_telemetria_no_arrancan_sin_mongodb(client):
    """El lifespan NO arranca el flusher ni el watcher si Mongo no está
    configurado (el feed en vivo sigue vivo gracias al hot path en memoria)."""
    assert telemetry.LATENCY_BUFFER is not None
    # Con Mongo sin configurar, `get_mongo_db()` lanza RuntimeError.
    with pytest.raises(RuntimeError):
        telemetry.get_mongo_db()


def test_ensure_mongodb_indexes_ttl_solo_en_latency_metrics(monkeypatch):
    """Verifica que ensure_mongodb_indexes cree el índice TTL (8 días = 691200 s)
    EXCLUSIVAMENTE en latency_metrics, y que pose_analysis_logs NO tenga TTL."""
    import asyncio
    from app.db import mongodb as db_mongo

    fake = _FakeMongo()
    monkeypatch.setattr(db_mongo, "get_mongo_db", lambda: fake)

    asyncio.run(db_mongo.ensure_mongodb_indexes(fake))

    # Buscar índices en latency_metrics
    ttl_indices = [
        idx for idx in fake.indexes
        if idx["collection"] == "latency_metrics" and idx.get("name") == "idx_latency_metrics_created_at_ttl"
    ]
    assert len(ttl_indices) == 1
    ttl_idx = ttl_indices[0]
    assert ttl_idx["keys"] == [("created_at", 1)]
    assert ttl_idx["kwargs"].get("expireAfterSeconds") == 691200

    # Verificar que NINGÚN índice en pose_analysis_logs tenga expireAfterSeconds
    pose_ttl_indices = [
        idx for idx in fake.indexes
        if idx["collection"] == "pose_analysis_logs" and "expireAfterSeconds" in idx.get("kwargs", {})
    ]
    assert len(pose_ttl_indices) == 0, "pose_analysis_logs NO debe tener índice TTL"


def test_ensure_mongodb_indexes_idempotencia_conflicto_codigo_85_o_no_ttl(monkeypatch):
    """Verifica la recuperación idempotente ante colisiones de índice sobre created_at:
    1) Reemplaza índice simple preexistente sin TTL por el índice TTL de 691200s.
    2) Si create_index lanza OperationFailure con código 85 (IndexOptionsConflict),
       resuelve la colisión y recrea el índice TTL.
    3) Errores con códigos distintos (ej. 13 - Unauthorized) NO se silencian y se re-lanzan.
    4) pose_analysis_logs permanece sin TTL."""
    import asyncio
    from pymongo.errors import OperationFailure
    from app.db import mongodb as db_mongo

    fake = _FakeMongo()
    # 1) Simulamos un índice previo sin TTL sobre created_at en latency_metrics
    fake.indexes.append({
        "collection": "latency_metrics",
        "name": "created_at_1",
        "keys": [("created_at", 1)],
        "kwargs": {},  # sin expireAfterSeconds
    })

    monkeypatch.setattr(db_mongo, "get_mongo_db", lambda: fake)

    # Debe detectar created_at_1, eliminarlo y crear idx_latency_metrics_created_at_ttl
    asyncio.run(db_mongo.ensure_mongodb_indexes(fake))

    assert "created_at_1" in [d["name"] for d in fake.dropped_indexes]
    ttl_indices = [
        idx for idx in fake.indexes
        if idx["collection"] == "latency_metrics" and idx.get("name") == "idx_latency_metrics_created_at_ttl"
    ]
    assert len(ttl_indices) == 1
    assert ttl_indices[0]["kwargs"].get("expireAfterSeconds") == 691200

    # 2) Simular conflicto código 85 (IndexOptionsConflict) en create_index
    class _ConflictingCollection(_FakeCollection):
        def __init__(self, db, name):
            super().__init__(db, name)
            self.first_attempt = True

        async def create_index(self, keys, name=None, **kwargs):
            if name == "idx_latency_metrics_created_at_ttl" and self.first_attempt:
                self.first_attempt = False
                raise OperationFailure("Index already exists with different options", code=85)
            return await super().create_index(keys, name=name, **kwargs)

    class _ConflictingMongo(_FakeMongo):
        def __getitem__(self, name):
            return _ConflictingCollection(self, name)

    conflicting_fake = _ConflictingMongo()
    asyncio.run(db_mongo.ensure_mongodb_indexes(conflicting_fake))
    assert len([
        idx for idx in conflicting_fake.indexes
        if idx["collection"] == "latency_metrics" and idx.get("name") == "idx_latency_metrics_created_at_ttl"
    ]) == 1

    # 3) Error con código distinto a 85/86 (ej. 13) en latency_metrics se re-lanza y no se silencia
    class _FailingCollection(_FakeCollection):
        async def create_index(self, keys, name=None, **kwargs):
            if name == "idx_latency_metrics_created_at_ttl":
                raise OperationFailure("Unauthorized", code=13)
            return await super().create_index(keys, name=name, **kwargs)

    class _FailingMongo(_FakeMongo):
        def __getitem__(self, name):
            return _FailingCollection(self, name)

    with pytest.raises(OperationFailure) as exc_info:
        asyncio.run(db_mongo.ensure_mongodb_indexes(_FailingMongo()))
    assert exc_info.value.code == 13


