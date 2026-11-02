"""Pruebas unitarias de los cálculos de las recomendaciones (sin base de datos)."""
from datetime import datetime, timedelta

from app.routers.capacity_alerts import classify
from app.routers.revenue_simulator import project
from app.routers.sla import met_sla, sla_hours_for, sla_state


def test_simulador_crecimiento_basico():
    s = project(base_mrr=1000, new_clinics=1, mrr_per_new=500, churn_pct=2, expansion_pct=0, months=12)
    assert s[0]["mrr"] == 1000
    assert s[1]["mrr"] == 1480.0          # 1000 * 0,98 + 500
    # el ARR se calcula con el MRR sin redondear, por eso se compara con tolerancia de centavos
    assert abs(s[12]["arr"] - s[12]["mrr"] * 12) < 0.1


def test_simulador_sin_crecimiento_ni_churn():
    s = project(1000, 0, 0, 0, 0, 6)
    assert all(p["mrr"] == 1000 for p in s)


def test_alertas_clasificacion():
    assert classify(450, 500, 90) == (90.0, "warning")
    assert classify(500, 500, 90) == (100.0, "critical")
    assert classify(100, 500, 90) == (20.0, None)
    assert classify(10, 0, 90) == (0.0, None)  # sin límite definido


def test_sla_plazos_por_categoria():
    assert sla_hours_for("Bug") == 24
    assert sla_hours_for("Billing") == 48
    assert sla_hours_for(None) == 72
    assert sla_hours_for("Otra") == 72


def test_sla_estados():
    ahora = datetime(2026, 11, 2, 12, 0)
    assert sla_state(ahora - timedelta(hours=10), "Bug", ahora)["state"] == "on_track"
    assert sla_state(ahora - timedelta(hours=20), "Bug", ahora)["state"] == "at_risk"
    vencido = sla_state(ahora - timedelta(hours=30), "Bug", ahora)
    assert vencido["state"] == "breached"
    assert vencido["remaining_hours"] == -6.0


def test_sla_cumplimiento():
    ahora = datetime(2026, 11, 2, 12, 0)
    assert met_sla(ahora - timedelta(hours=20), ahora, "Bug") is True
    assert met_sla(ahora - timedelta(hours=30), ahora, "Bug") is False
