"""Pruebas de API de las Mejoras #1, #2 y #12 (sesión de base de datos falsa)."""
from datetime import datetime

import pytest

from app.main import app
from app.models_db import MrrSnapshot, PlanCatalog, Responder, SupportTicket, TicketCategory

NUEVAS = [
    "/api/financials/mrr/closed",
    "/api/plan-catalogs",
    "/api/ticket-routing/rules",
]


def test_rutas_registradas():
    paths = {getattr(r, "path", "") for r in app.routes}
    for p in [
        "/api/financials/mrr/close/preview", "/api/financials/mrr/close", "/api/financials/mrr/closed",
        "/api/plan-catalogs", "/api/plan-catalogs/{catalog_id}", "/api/plan-catalogs/{catalog_id}/plans",
        "/api/ticket-routing/rules", "/api/ticket-routing/suggest",
        "/api/ticket-routing/assign/{ticket_id}", "/api/ticket-routing/assign-pending",
    ]:
        assert p in paths, f"Falta la ruta {p}"


@pytest.mark.parametrize("path", NUEVAS)
def test_requieren_token(client, path):
    assert client.get(path).status_code in (401, 403)


# ── Mejora #1 ────────────────────────────────────────────────────────────────
def test_preview_mes_anterior(client, auth_header):
    r = client.get("/api/financials/mrr/close/preview?year=2026&month=1", headers=auth_header)
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["period_month"] == "Ene" and d["already_closed"] is False
    assert d["calculated"]["total_mrr"] == 0


def test_preview_mes_en_curso_rechazado(client, auth_header):
    hoy = datetime.utcnow()
    r = client.get(f"/api/financials/mrr/close/preview?year={hoy.year}&month={hoy.month}", headers=auth_header)
    assert r.status_code == 422


def test_cerrar_mes_manual(client, fake_db, auth_header):
    body = {"year": 2026, "month": 1, "mode": "manual",
            "values": {"total_mrr": 1500, "new_business": 300, "expansion": 100, "churn": 50}}
    r = client.post("/api/financials/mrr/close", json=body, headers=auth_header)
    assert r.status_code == 201, r.text
    d = r.json()["data"]
    assert d["source"] == "manual" and d["total_mrr"] == 1500 and d["retained"] == 1400
    tipos = {type(o).__name__ for o in fake_db.added}
    assert {"MrrSnapshot", "ActionLog"} <= tipos


def test_cerrar_mes_ya_cerrado_pide_reemplazar(client, fake_db, auth_header):
    fake_db.data[MrrSnapshot] = [MrrSnapshot(period_month="Ene", period_year=2026, source="auto", total_mrr=10)]
    r = client.post("/api/financials/mrr/close", json={"year": 2026, "month": 1}, headers=auth_header)
    assert r.status_code == 409
    r = client.post("/api/financials/mrr/close", json={"year": 2026, "month": 1, "replace": True}, headers=auth_header)
    assert r.status_code == 201


def test_historial_usa_meses_cerrados(client, fake_db):
    fake_db.data[MrrSnapshot] = [MrrSnapshot(period_month="Ene", period_year=2026, source="auto", total_mrr=10)]
    r = client.get("/api/financials/mrr/snapshots")
    assert r.status_code == 200
    assert r.json()["data"][0]["source"] == "closed"


# ── Mejora #2 ────────────────────────────────────────────────────────────────
def test_crear_catalogo(client, fake_db, auth_header):
    r = client.post("/api/plan-catalogs", json={"name": "Pilotos", "color": "amber"}, headers=auth_header)
    assert r.status_code == 201, r.text
    assert r.json()["data"]["isActive"] is True
    assert any(isinstance(o, PlanCatalog) for o in fake_db.added)


def test_catalogo_con_nombre_repetido(client, fake_db, auth_header):
    fake_db.data[PlanCatalog] = [PlanCatalog(catalog_id="pcat-1", name="Pilotos")]
    r = client.post("/api/plan-catalogs", json={"name": "pilotos"}, headers=auth_header)
    assert r.status_code == 409


def test_desactivar_catalogo(client, fake_db, auth_header):
    fake_db.data[PlanCatalog] = [PlanCatalog(catalog_id="pcat-1", name="Pilotos", is_active=True)]
    r = client.patch("/api/plan-catalogs/pcat-1", json={"isActive": False}, headers=auth_header)
    assert r.status_code == 200
    assert r.json()["data"]["isActive"] is False


# ── Mejora #12 ───────────────────────────────────────────────────────────────
def _soporte(fake_db):
    fake_db.data[TicketCategory] = [
        TicketCategory(category_id="cat-1", name="Billing", team="Financiero", is_active=True, auto_assign=True),
        TicketCategory(category_id="cat-2", name="Request", team=None, is_active=True),
    ]
    fake_db.data[Responder] = [
        Responder(responder_id="r1", name="Ana", team="Financiero", username="ana", password="x"),
        Responder(responder_id="r2", name="Beto", team="Financiero", username="beto", password="x"),
    ]
    ahora = datetime.utcnow()
    fake_db.data[SupportTicket] = [
        SupportTicket(ticket_id="t1", clinic_id="CL-1", title="Cobro", status="Open", category="Billing",
                      reported_at=ahora, responder_id="r1", responder_name="Ana"),
        SupportTicket(ticket_id="t2", clinic_id="CL-1", title="Factura", status="Sent", category="Billing",
                      reported_at=ahora),
        SupportTicket(ticket_id="t3", clinic_id="CL-1", title="Consulta", status="Sent", category="Request",
                      reported_at=ahora),
    ]


def test_reglas_de_asignacion(client, fake_db, auth_header):
    _soporte(fake_db)
    r = client.get("/api/ticket-routing/rules", headers=auth_header)
    assert r.status_code == 200
    d = r.json()["data"]
    billing = next(x for x in d["rules"] if x["category"] == "Billing")
    assert billing["next_responder"] == "Beto" and billing["auto_assign"] is True
    assert d["unassigned_total"] == 2


def test_sugerencia(client, fake_db, auth_header):
    _soporte(fake_db)
    r = client.get("/api/ticket-routing/suggest?category=Billing", headers=auth_header)
    assert r.json()["data"]["responder"]["name"] == "Beto"


def test_repartir_pendientes(client, fake_db, auth_header):
    _soporte(fake_db)
    r = client.post("/api/ticket-routing/assign-pending", headers=auth_header)
    assert r.status_code == 200
    d = r.json()["data"]
    assert [a["ticket_id"] for a in d["assigned"]] == ["t2"]
    assert d["skipped"][0]["reason"] == "no_team"
    t2 = next(t for t in fake_db.data[SupportTicket] if t.ticket_id == "t2")
    assert t2.responder_name == "Beto" and t2.status == "Open"
