"""Mejora #20 — Cliente de Google Gemini (API REST, con streaming SSE).

Se usa la API REST directamente con ``httpx`` (ya es dependencia del proyecto)
para no sumar SDKs. El cliente expone una sola operación:

    async for part in client.stream(system=..., contents=..., tools=...):
        part  # {"text": "..."}  o  {"functionCall": {"name": ..., "args": {...}}}

Las pruebas usan un cliente falso con la misma interfaz, sin red ni API key.
"""

from __future__ import annotations

import json
from typing import AsyncIterator, Optional, Protocol

import httpx

from app.config import settings

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


class LLMError(RuntimeError):
    """Error al hablar con el proveedor del modelo."""


class LLMClient(Protocol):
    model: str

    def stream(
        self, *, system: str, contents: list[dict], tools: Optional[list[dict]] = None
    ) -> AsyncIterator[dict]: ...


class GeminiClient:
    def __init__(self, api_key: str, model: str, timeout: float = 60.0):
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def _body(self, system: str, contents: list[dict], tools: Optional[list[dict]]) -> dict:
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": contents,
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": 1024},
        }
        if tools:
            body["tools"] = [{"functionDeclarations": tools}]
        return body

    async def stream(
        self, *, system: str, contents: list[dict], tools: Optional[list[dict]] = None
    ) -> AsyncIterator[dict]:
        url = f"{GEMINI_BASE_URL}/models/{self.model}:streamGenerateContent?alt=sse"
        headers = {"x-goog-api-key": self.api_key, "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream("POST", url, headers=headers, json=self._body(system, contents, tools)) as resp:
                if resp.status_code >= 300:
                    detail = (await resp.aread()).decode("utf-8", "replace")[:300]
                    raise LLMError(f"Gemini respondió {resp.status_code}: {detail}")
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if not payload:
                        continue
                    try:
                        chunk = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    for candidate in chunk.get("candidates", [])[:1]:
                        for part in (candidate.get("content") or {}).get("parts", []):
                            if "text" in part or "functionCall" in part:
                                yield part


def get_llm_client() -> Optional[LLMClient]:
    """Dependencia de FastAPI. None si no hay GEMINI_API_KEY (asistente apagado)."""
    if not settings.gemini_api_key:
        return None
    return GeminiClient(settings.gemini_api_key, settings.gemini_model or "gemini-2.5-flash")
