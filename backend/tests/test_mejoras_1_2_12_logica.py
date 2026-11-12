"""Pruebas unitarias de las Mejoras #1, #2 y #12 (sin base de datos ni FastAPI)."""
from datetime import date, datetime

import pytest

from app.services.mrr_close import (
    build_values, default_period, growth_pct, merge_closed, month_bounds, validate_period,
)
from app.services.ticket_routing import open_load, pick_responder, plan_bulk, suggest


# ── Mejora #1 — Cierre mensual de MRR ────────────────────────────────────────
def test_limites_del_mes():
    inicio, fin = month_bounds(2026, 2)
    assert inicio == datetime(2026, 2, 1)
    assert fin == datetime(2026, 2, 28, 23, 59, 59)
    assert month_bounds(2026, 12)[1] == datetime(2026, 12, 31, 23, 59, 59)


def test_periodo_por_defecto_es_el_mes_anterior():
    assert default_period(date(2026, 11, 9)) == (2026, 10)
    assert default_period(date(2027, 1, 3)) == (2026, 12)


def test_no_se_cierra_el_mes_en_curso_ni_futuro():
    hoy = date(2026, 11, 9)
    validate_period(2026, 10, hoy)  # no lanza
    for y, m in [(2026, 11), (2026, 12), (2027, 1)]:
        with pytest.raises(ValueError):
            validate_period(y, m, hoy)
    with pytest.raises(ValueError):
        validate_period(2026, 13, hoy)


def test_crecimiento():
    assert growth_pct(1100, 1000) == 10.0
    assert growth_pct(900, 1000) == -10.0
    assert growth_pct(500, 0) == 0.0


CALC = {"total_mrr": 1000, "new_business": 200, "expansion": 50, "contraction": 10, "churn": 30, "retained": 950}


def test_valores_automaticos_sin_cambios():
    v = build_values(CALC, None)
    assert v["total_mrr"] == 1000 and v["retained"] == 950


def test_valores_manuales_reemplazan_y_recalculan_retenido():
    v = build_values(CALC, {"total_mrr": 1200, "expansion": 100})
    assert v["total_mrr"] == 1200
    assert v["new_business"] == 200          # no ingresado → se conserva el cálculo
    assert v["retained"] == 1100             # total - expansión


def test_valores_manuales_invalidos():
    with pytest.raises(ValueError):
        build_values(CALC, {"churn": -5})
    with pytest.raises(ValueError):
        build_values(CALC, {"total_mrr": 100, "new_business": 300})


def test_meses_cerrados_reemplazan_al_calculo():
    serie = [
        {"period_month": "Sep", "period_year": 2026, "total_mrr": 900, "monthly_growth_percentage": 0},
        {"period_month": "Oct", "period_year": 2026, "total_mrr": 950, "monthly_growth_percentage": 5.6},
    ]
    cerrados = [
        {"period_month": "Oct", "period_year": 2026, "total_mrr": 1000, "monthly_growth_percentage": 11.1},
        {"period_month": "Ago", "period_year": 2026, "total_mrr": 800, "monthly_growth_percentage": 0},
    ]
    out = merge_closed(serie, cerrados)
    assert [(p["period_month"], p["source"]) for p in out] == [
        ("Ago", "closed"), ("Sep", "calculated"), ("Oct", "closed")]
    assert out[2]["total_mrr"] == 1000


# ── Mejora #12 — Asignación inteligente de tickets ───────────────────────────
RESPONDERS = [
    {"id": "r1", "name": "Ana", "team": "Financiero"},
    {"id": "r2", "name": "Beto", "team": "financiero "},   # mayúsculas/espacios no importan
    {"id": "r3", "name": "Caro", "team": "Técnico"},
]
TEAMS = {"Billing": "Financiero", "Bug": "Técnico", "Request": None, "Legal": "Legal"}


def test_carga_abierta_ignora_cerrados_y_sin_responsable():
    load = open_load([
        {"responder_id": "r1", "status": "Open"},
        {"responder_id": "r1", "status": "Closed"},
        {"responder_id": None, "status": "Sent"},
        {"responder_id": "r2", "status": "Sent"},
    ])
    assert load == {"r1": 1, "r2": 1}


def test_elige_al_de_menor_carga_y_desempata_por_nombre():
    assert pick_responder("Financiero", RESPONDERS, {"r1": 3, "r2": 1})["id"] == "r2"
    assert pick_responder("Financiero", RESPONDERS, {})["id"] == "r1"
    assert pick_responder("Legal", RESPONDERS, {}) is None


def test_sugerencia_por_categoria():
    assert suggest("billing", TEAMS, RESPONDERS, {})["responder"]["id"] == "r1"
    assert suggest("Request", TEAMS, RESPONDERS, {})["status"] == "no_team"
    assert suggest("Legal", TEAMS, RESPONDERS, {})["status"] == "no_responders"
    assert suggest("Otra", TEAMS, RESPONDERS, {})["status"] == "unknown"


def test_reparto_masivo_equilibra_la_carga():
    pendientes = [{"ticket_id": f"t{i}", "category": "Billing", "reported_at": f"2026-11-0{i}"} for i in range(1, 5)]
    pendientes.append({"ticket_id": "t9", "category": "Request", "reported_at": "2026-11-09"})
    asignados, omitidos = plan_bulk(pendientes, TEAMS, RESPONDERS, {"r1": 1})
    quien = [a["responder_id"] for a in asignados]
    assert quien == ["r2", "r1", "r2", "r1"]   # alterna según la carga
    assert omitidos == [{"ticket_id": "t9", "category": "Request", "reason": "no_team"}]
