"""Pruebas de API de la Mejora #20 — Asistente conversacional (SSE)."""
import json
from datetime import timedelta

import pytest

from app.config import settings
from app.main import app
from app.routers.auth import create_token
from app.services import assistant_service
from app.services.assistant_llm import get_llm_client
from tests.test_mejora_20_logica import FakeLLM

BASE = "/api/v1/assistant"


def _parse_sse(text):
    events = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        if "data" in lines:
            events.append(json.loads(lines["data"]))
    return events


@pytest.fixture
def llm():
    fake = FakeLLM([
        [{"functionCall": {"name": "resumen_panel", "args": {}}}],
        [{"text": "Tienes 0 clínicas registradas."}],
    ])
    app.dependency_overrides[get_llm_client] = lambda: fake
    assistant_service.rate_limiter.reset()
    yield fake
    app.dependency_overrides.pop(get_llm_client, None)
    assistant_service.rate_limiter.reset()


def _token(perms, role="admin"):
    token = create_token(
        {"user_id": "usr-fin", "email": "fin@wellq.test", "role": role, "type": "access", "permissions": perms},
        timedelta(minutes=5),
    )
    return {"Authorization": f"Bearer {token}"}


def test_rutas_registradas():
    paths = {getattr(r, "path", "") for r in app.routes}
    assert f"{BASE}/status" in paths and f"{BASE}/chat" in paths


def test_requieren_token(client):
    assert client.get(f"{BASE}/status").status_code in (401, 403)
    assert client.post(f"{BASE}/chat", json={"message": "hola"}).status_code in (401, 403)


def test_status_desactivado_sin_api_key(client, auth_header, monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "")
    r = client.get(f"{BASE}/status", headers=auth_header)
    assert r.status_code == 200
    assert r.json()["data"]["enabled"] is False


def test_chat_sin_api_key_responde_503(client, auth_header, monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "")
    r = client.post(f"{BASE}/chat", json={"message": "hola"}, headers=auth_header)
    assert r.status_code == 503


def test_status_lista_herramientas_del_usuario(client, llm):
    r = client.get(f"{BASE}/status", headers=_token(["billing.view"]))
    data = r.json()["data"]
    assert data["enabled"] is True and data["model"] == "gemini-falso"
    assert data["tools"] == ["resumen_facturas"]


def test_chat_responde_por_sse(client, auth_header, llm):
    r = client.post(f"{BASE}/chat", json={"message": "¿Cuántas clínicas hay?"}, headers=auth_header)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/event-stream")
    events = _parse_sse(r.text)
    assert [e["type"] for e in events] == ["meta", "tool", "delta", "done"]
    assert events[1]["name"] == "resumen_panel" and events[1]["status"] == "ok"
    assert events[2]["text"] == "Tienes 0 clínicas registradas."
    assert "event: delta" in r.text


def test_chat_con_historial(client, auth_header, llm):
    body = {"message": "¿y vencidas?", "history": [{"role": "user", "text": "hola"},
                                                   {"role": "assistant", "text": "Hola, ¿en qué te ayudo?"}]}
    assert client.post(f"{BASE}/chat", json=body, headers=auth_header).status_code == 200
    roles = [c["role"] for c in llm.calls[0]["contents"]]
    assert roles == ["user", "model", "user"]


@pytest.mark.parametrize("body", [{"message": ""}, {"message": "x" * 1001}, {},
                                  {"message": "hola", "history": [{"role": "system", "text": "x"}]}])
def test_validacion_de_entrada(client, auth_header, llm, body):
    assert client.post(f"{BASE}/chat", json=body, headers=auth_header).status_code == 422


def test_pregunta_solo_espacios(client, auth_header, llm):
    assert client.post(f"{BASE}/chat", json={"message": "   "}, headers=auth_header).status_code == 422


def test_limite_por_minuto(client, auth_header, llm, monkeypatch):
    monkeypatch.setattr(settings, "assistant_rate_limit_per_minute", 1)
    llm.script.extend([[{"text": "ok"}]])
    assert client.post(f"{BASE}/chat", json={"message": "uno"}, headers=auth_header).status_code == 200
    r = client.post(f"{BASE}/chat", json={"message": "dos"}, headers=auth_header)
    assert r.status_code == 429
