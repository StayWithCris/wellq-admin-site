"""Pruebas de lógica de la Mejora #20 — Asistente conversacional de solo lectura.

El modelo de lenguaje se reemplaza por `FakeLLM`, que devuelve un guion fijo:
no hay red, ni API key, ni costo.
"""
import asyncio
from datetime import datetime

import pytest

from app.models_db import Alert, Clinic, Invoice, SupportTicket
from app.services import assistant_service as svc
from app.services import assistant_tools as tools
from app.services.assistant_llm import LLMError
from tests.conftest import FakeSession

SUPER = {"user_id": "u-root", "email": "root@wellq.test", "role": "super_admin", "permissions": []}
FINANZAS = {"user_id": "u-fin", "email": "fin@wellq.test", "role": "admin",
            "permissions": ["overview.view", "billing.view"]}


class FakeLLM:
    """Cada elemento de `script` es la lista de partes de una ronda."""

    model = "gemini-falso"

    def __init__(self, script, fail=False):
        self.script = list(script)
        self.calls = []
        self.fail = fail

    async def stream(self, *, system, contents, tools=None):
        self.calls.append({"system": system, "contents": [dict(c) for c in contents], "tools": tools})
        if self.fail:
            raise LLMError("cuota agotada")
        for part in (self.script.pop(0) if self.script else [{"text": "sin guion"}]):
            yield part


def _collect(gen):
    async def run():
        return [e async for e in gen]
    return asyncio.run(run())


def _db():
    return FakeSession({
        Clinic: [
            Clinic(clinic_id="c1", name="Kine Sur", tier="smb", status="active", health_score=90, mrr=100.0),
            Clinic(clinic_id="c2", name="Trauma Norte", tier="enterprise", status="critical", health_score=30, mrr=500.0),
            Clinic(clinic_id="c3", name="Cerrada", tier="trial", status="churned", health_score=10, mrr=50.0),
            Clinic(clinic_id="c4", name="Borrada", status="active", mrr=999.0, is_deleted=True),
        ],
        Invoice: [
            Invoice(invoice_id="i1", clinic_id="c1", amount=100, status="paid", issued_at=datetime(2026, 10, 1)),
            Invoice(invoice_id="i2", clinic_id="c2", amount=500, status="overdue", issued_at=datetime(2026, 9, 1)),
            Invoice(invoice_id="i3", clinic_id="c2", amount=200, status="overdue", issued_at=datetime(2026, 8, 1)),
        ],
        Alert: [
            Alert(alert_id="a1", type="sync_failure", title="Sync", message="m", severity="critical",
                  created_at=datetime(2026, 11, 18)),
            Alert(alert_id="a2", type="capacity", title="Capacidad", message="m", severity="low",
                  created_at=datetime(2026, 11, 17)),
            Alert(alert_id="a3", type="x", title="Gestionada", message="m", severity="high",
                  created_at=datetime(2026, 11, 16), acknowledged_at=datetime(2026, 11, 16)),
        ],
        SupportTicket: [
            SupportTicket(ticket_id="t1", clinic_id="c1", title="No carga", status="Open", reported_at=datetime(2026, 11, 18)),
            SupportTicket(ticket_id="t2", clinic_id="c2", title="Factura", status="Closed", reported_at=datetime(2026, 11, 1)),
        ],
    })


# ── Permisos de herramientas ────────────────────────────────────────────────

def test_seis_herramientas_con_permiso():
    assert len(tools.TOOLS) == 6
    assert all(t.permission.endswith(".view") for t in tools.TOOLS)


def test_super_admin_legacy_ve_todo():
    assert len(tools.allowed_tools(SUPER)) == 6


def test_usuario_solo_ve_herramientas_de_sus_permisos():
    names = {t.name for t in tools.allowed_tools(FINANZAS)}
    assert names == {"resumen_panel", "resumen_facturas"}


def test_herramienta_sin_permiso_es_denegada():
    status, result = asyncio.run(tools.run_tool(_db(), "tickets_soporte", {}, FINANZAS))
    assert status == "denied" and "tickets.view" in result["error"]


def test_herramienta_inexistente():
    status, _ = asyncio.run(tools.run_tool(_db(), "borrar_clinicas", {}, SUPER))
    assert status == "unknown"


def test_argumento_fuera_de_lista_cerrada():
    status, result = asyncio.run(tools.run_tool(_db(), "listar_clinicas", {"estado": "'; DROP TABLE"}, SUPER))
    assert status == "error" and "no permitido" in result["error"]


def test_declaraciones_validas_para_gemini():
    for t in tools.TOOLS:
        decl = t.declaration()
        assert decl["name"] == t.name and decl["description"]
        if "parameters" in decl:
            assert decl["parameters"]["type"] == "object"


# ── Resultados de las consultas fijas ───────────────────────────────────────

def test_resumen_panel():
    _, r = asyncio.run(tools.run_tool(_db(), "resumen_panel", {}, SUPER))
    assert r["clinicas_total"] == 3                      # excluye la borrada
    assert r["mrr_total"] == 600.0                       # excluye churned
    assert r["tickets_abiertos"] == 1
    assert r["alertas_sin_gestionar"] == 2 and r["alertas_criticas_o_altas"] == 1


def test_listar_clinicas_filtra_y_ordena_por_salud():
    _, r = asyncio.run(tools.run_tool(_db(), "listar_clinicas", {"estado": "CRITICAL"}, SUPER))
    assert r["filtro_estado"] == "critical"
    assert [c["nombre"] for c in r["clinicas"]] == ["Trauma Norte"]
    _, todas = asyncio.run(tools.run_tool(_db(), "listar_clinicas", {"limite": 2}, SUPER))
    assert [c["nombre"] for c in todas["clinicas"]] == ["Cerrada", "Trauma Norte"]


def test_limite_se_acota():
    assert tools._limit_arg({"limite": 999}) == tools.MAX_ROWS
    assert tools._limit_arg({"limite": "abc"}) == 10


def test_resumen_facturas():
    _, r = asyncio.run(tools.run_tool(_db(), "resumen_facturas", {}, SUPER))
    assert r["por_estado"]["overdue"] == {"cantidad": 2, "monto": 700.0}
    assert [f["factura"] for f in r["mayores_vencidas"]] == ["i2", "i3"]


def test_alertas_activas_ordenadas_por_severidad():
    _, r = asyncio.run(tools.run_tool(_db(), "alertas_activas", {}, SUPER))
    assert r["total"] == 2
    assert [a["severidad"] for a in r["alertas"]] == ["critical", "low"]


def test_tickets_soporte():
    _, r = asyncio.run(tools.run_tool(_db(), "tickets_soporte", {"estado": "open"}, SUPER))
    assert r["por_estado"] == {"Open": 1, "Closed": 1}
    assert [t["titulo"] for t in r["tickets"]] == ["No carga"]


def test_estado_sincronizacion():
    status, r = asyncio.run(tools.run_tool(_db(), "estado_sincronizacion", {}, SUPER))
    assert status == "ok"
    assert r["frecuencia_horas"] == 24 and r["fallos_consecutivos"] == 0


# ── Orquestación ────────────────────────────────────────────────────────────

def test_conversacion_con_herramienta():
    llm = FakeLLM([
        [{"functionCall": {"name": "resumen_facturas", "args": {"estado": "overdue"}}}],
        [{"text": "Hay 2 facturas "}, {"text": "vencidas por 700 USD."}],
    ])
    events = _collect(svc.run_chat(_db(), llm, FINANZAS, "¿Cuánto está vencido?"))
    tipos = [e["type"] for e in events]
    assert tipos == ["meta", "tool", "delta", "delta", "done"]
    assert events[1] == {"type": "tool", "name": "resumen_facturas", "status": "ok"}
    assert "".join(e["text"] for e in events if e["type"] == "delta") == "Hay 2 facturas vencidas por 700 USD."
    # La segunda ronda recibe el resultado de la herramienta.
    ultima = llm.calls[1]["contents"][-1]["parts"][0]["functionResponse"]
    assert ultima["name"] == "resumen_facturas"
    assert ultima["response"]["resultado"]["por_estado"]["overdue"]["cantidad"] == 2


def test_solo_ofrece_herramientas_permitidas():
    llm = FakeLLM([[{"text": "Hola"}]])
    _collect(svc.run_chat(_db(), llm, FINANZAS, "hola"))
    ofrecidas = {d["name"] for d in llm.calls[0]["tools"]}
    assert ofrecidas == {"resumen_panel", "resumen_facturas"}
    assert "resumen_facturas" in llm.calls[0]["system"]


def test_herramienta_denegada_se_informa_al_modelo():
    llm = FakeLLM([
        [{"functionCall": {"name": "tickets_soporte", "args": {}}}],
        [{"text": "No tienes permiso para ver tickets."}],
    ])
    events = _collect(svc.run_chat(_db(), llm, FINANZAS, "tickets"))
    assert {"type": "tool", "name": "tickets_soporte", "status": "denied"} in events


def test_maximo_de_rondas_fuerza_respuesta():
    llamada = [{"functionCall": {"name": "resumen_panel", "args": {}}}]
    llm = FakeLLM([llamada, llamada, llamada, [{"text": "Listo"}]])
    events = _collect(svc.run_chat(_db(), llm, SUPER, "resumen"))
    assert sum(e["type"] == "tool" for e in events) == svc.MAX_TOOL_ROUNDS
    assert llm.calls[-1]["tools"] is None
    assert events[-1]["type"] == "done"


def test_error_del_modelo_no_expone_detalles():
    events = _collect(svc.run_chat(_db(), FakeLLM([], fail=True), SUPER, "hola"))
    assert events[-1]["type"] == "error" and events[-1]["code"] == "llm_error"
    assert "cuota" not in events[-1]["message"]


def test_historial_se_traduce_y_recorta():
    history = [{"role": "user", "text": f"p{i}"} if i % 2 == 0 else {"role": "assistant", "text": f"r{i}"}
               for i in range(15)] + [{"role": "user", "text": "   "}]
    contents = svc.build_contents("¿y ahora?", history)
    # Se toman los últimos 10 turnos (uno viene vacío y se descarta) + la pregunta.
    assert len(contents) == 10
    assert contents[-1] == {"role": "user", "parts": [{"text": "¿y ahora?"}]}
    assert {c["role"] for c in contents} <= {"user", "model"}


def test_pregunta_se_trunca():
    contents = svc.build_contents("x" * 5000, [])
    assert len(contents[0]["parts"][0]["text"]) == svc.MAX_MESSAGE_CHARS


def test_limite_de_consultas_por_minuto(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "assistant_rate_limit_per_minute", 2)
    svc.rate_limiter.reset()
    svc.check_rate_limit(SUPER)
    svc.check_rate_limit(SUPER)
    with pytest.raises(svc.RateLimitExceeded):
        svc.check_rate_limit(SUPER)
    svc.check_rate_limit(FINANZAS)  # el límite es por usuario
    svc.rate_limiter.reset()
