"""Pruebas de humo de la API: arranque, rutas registradas y protección por token."""
from datetime import datetime, timedelta

import pytest

from app.main import app
from app.models_db import SupportTicket

PROTECTED = [
    "/api/simulator/revenue",
    "/api/capacity/alerts",
    "/api/sla/tickets",
    "/api/audit/logs",
]


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_rutas_de_mejoras_registradas():
    paths = {getattr(r, "path", "") for r in app.routes}
    for p in [
        "/api/analytics/retention/heatmap",   # #8
        "/api/simulator/revenue",             # #3
        "/api/capacity/alerts",               # #21
        "/api/sla/tickets",                   # #13
        "/api/audit/logs",                    # #16
        "/api/roles",                         # #17 (usa roles y permisos)
    ]:
        assert p in paths, f"Falta la ruta {p}"


@pytest.mark.parametrize("path", PROTECTED)
def test_endpoints_protegidos_sin_token(client, path):
    r = client.get(path)
    assert r.status_code in (401, 403)


@pytest.mark.parametrize("path", PROTECTED)
def test_endpoints_protegidos_token_invalido(client, path):
    r = client.get(path, headers={"Authorization": "Bearer token-falso"})
    assert r.status_code == 401


def test_simulador_sin_clinicas(client, auth_header):
    r = client.get("/api/simulator/revenue?months=6", headers=auth_header)
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["base"]["mrr"] == 0
    assert len(data["series"]) == 7  # mes 0 + 6 meses


def test_alertas_capacidad_sin_clinicas(client, auth_header):
    r = client.get("/api/capacity/alerts", headers=auth_header)
    assert r.status_code == 200
    assert r.json()["data"]["total"] == 0


def test_sla_con_tickets(client, fake_db, auth_header):
    ahora = datetime.utcnow()

    def ticket(tid, status, cat, horas, cerrado_en=None):
        return SupportTicket(ticket_id=tid, clinic_id="CL-1", title=f"Ticket {tid}", status=status,
                             category=cat, reported_at=ahora - timedelta(hours=horas), closed_at=cerrado_en)

    fake_db.data[SupportTicket] = [
        ticket("a", "Open", "Bug", 2),        # en plazo (24 h)
        ticket("b", "Open", "Bug", 20),       # en riesgo
        ticket("c", "Sent", "Billing", 60),   # vencido (48 h)
        ticket("d", "Closed", "Bug", 30, cerrado_en=ahora - timedelta(hours=10)),  # cerrado en 20 h: cumple
    ]
    r = client.get("/api/sla/tickets", headers=auth_header)
    assert r.status_code == 200
    s = r.json()["data"]["summary"]
    assert (s["open"], s["on_track"], s["at_risk"], s["breached"]) == (3, 1, 1, 1)
    assert s["compliance_30d_pct"] == 100.0
    # ordenado por tiempo restante: el vencido primero
    assert r.json()["data"]["items"][0]["ticket_id"] == "c"
