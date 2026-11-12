"""Configuración común de las pruebas (Mejora #23).

Las pruebas NO usan la base de datos real: se fija DATABASE_URL vacía (la API
arranca sin Neon) y la dependencia get_db se reemplaza por una sesión falsa.
Las variables de entorno tienen prioridad sobre backend/.env, así que correr
las pruebas en tu PC no toca la base de producción.
"""
import os
import sys
from datetime import timedelta

os.environ["DATABASE_URL"] = ""
os.environ["JWT_SECRET"] = "clave-solo-para-pruebas-1234567890"
os.environ.setdefault("KEYCLOAK_URL", "http://localhost:8080")
os.environ.setdefault("KEYCLOAK_CLIENT_ID", "wellq-tests")
os.environ["DEBUG"] = "false"

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.db.neon import get_db  # noqa: E402
from app.routers.auth import create_token  # noqa: E402


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


class FakeSession:
    """Sesión mínima: devuelve filas según el modelo consultado."""

    def __init__(self, data=None):
        self.data = data or {}
        self.added = []      # objetos pasados a add() (Mejoras #1, #2 y #12)
        self.deleted = []

    async def execute(self, stmt, *args, **kwargs):
        try:
            entity = stmt.column_descriptions[0]["entity"]
        except Exception:
            entity = None
        return _Result(self.data.get(entity, []))

    def add(self, obj):
        self.added.append(obj)

    async def delete(self, obj):
        self.deleted.append(obj)

    async def refresh(self, obj):
        pass

    async def commit(self):
        pass

    async def rollback(self):
        pass


@pytest.fixture
def fake_db():
    session = FakeSession()

    async def _override():
        yield session

    app.dependency_overrides[get_db] = _override
    yield session
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def client(fake_db):
    with TestClient(app) as c:
        yield c


@pytest.fixture
def auth_header():
    token = create_token(
        {"user_id": "usr-test", "email": "qa@wellq.test", "role": "super_admin", "type": "access"},
        timedelta(minutes=5),
    )
    return {"Authorization": f"Bearer {token}"}
