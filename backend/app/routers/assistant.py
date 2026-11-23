"""Mejora #20 — Asistente conversacional del panel (LLM, solo lectura).

GET  /api/v1/assistant/status   ¿Está activo? Modelo y herramientas del usuario  [autenticado]
POST /api/v1/assistant/chat     Pregunta en lenguaje natural; respuesta por SSE  [autenticado]

Eventos SSE de /chat (``event: <tipo>`` + ``data: <json>``):
  meta   {model, tools}           al iniciar
  tool   {name, status}           cada consulta de solo lectura ejecutada
  delta  {text}                   fragmento de la respuesta
  done   {tools_used}             fin
  error  {code, message}          error controlado (sin detalles internos)

Cada herramienta respeta el permiso RBAC de su vista; ver
``app/services/assistant_tools.py``.
"""

import contextlib
import json
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import neon
from app.db.neon import get_db
from app.services import assistant_service as svc
from app.services import assistant_tools as tools_mod
from app.services.assistant_llm import LLMClient, get_llm_client
from .dependencies import get_current_user

router = APIRouter(prefix="/api/v1/assistant", tags=["Asistente"])


class HistoryTurn(BaseModel):
    role: Literal["user", "assistant"]
    text: str = Field(max_length=4000)


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=svc.MAX_MESSAGE_CHARS)
    history: list[HistoryTurn] = Field(default_factory=list, max_length=20)


def _sse(event: dict) -> str:
    return f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"


def _session_scope(fallback: AsyncSession):
    """Sesión propia para todo el streaming.

    La sesión de ``get_db`` se cierra cuando termina el endpoint, antes de que
    el cuerpo SSE termine de enviarse; por eso el generador abre la suya. En
    pruebas (sin Neon) se usa la sesión inyectada.
    """
    if neon._session_factory is not None:
        return neon._session_factory()
    return contextlib.nullcontext(fallback)


@router.get("/status", summary="Estado del asistente")
async def assistant_status(
    user: dict = Depends(get_current_user),
    llm: Optional[LLMClient] = Depends(get_llm_client),
):
    return {
        "status": "success",
        "data": {
            "enabled": llm is not None,
            "model": getattr(llm, "model", None),
            "tools": [t.name for t in tools_mod.allowed_tools(user)],
        },
    }


@router.post("/chat", summary="Preguntar al asistente (respuesta en streaming SSE)")
async def chat(
    body: ChatIn,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
    llm: Optional[LLMClient] = Depends(get_llm_client),
):
    if llm is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="El asistente no está configurado (falta GEMINI_API_KEY).",
        )
    if not body.message.strip():
        raise HTTPException(status_code=422, detail="La pregunta no puede estar vacía.")
    try:
        svc.check_rate_limit(user)
    except svc.RateLimitExceeded:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Demasiadas consultas al asistente. Espera un minuto e intenta de nuevo.",
        )

    history = [h.model_dump() for h in body.history]

    async def event_stream():
        async with _session_scope(db) as session:
            async for event in svc.run_chat(session, llm, user, body.message, history):
                yield _sse(event)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
