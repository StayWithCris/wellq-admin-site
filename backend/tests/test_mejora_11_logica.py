"""Pruebas de lógica de la Recomendación #11 — Reportes programados.

Sin base de datos ni red: FakeSession con datos de ejemplo y un transporte
falso que registra los envíos.
"""
import asyncio
import json
from datetime import datetime, timedelta

import pytest

from app.config import settings
from app.models_db import (
    AdherenceSnapshot, AppUsageStat, Clinic, ClinicUsageMetric, FeatureAdoption, Invoice,
    MrrSnapshot, ScheduledReport, ScheduledReportRun, SoapQualityMetric, SupportTicket,
)
from app.routers.cohort_heatmap import compute_retention_cohorts
from app.services import report_builder as builder
from app.services import scheduled_reports_service as svc
from tests.conftest import FakeSession


class FakeTransport:
    def __init__(self, fail_email=False, fail_slack=False):
        self.emails, self.slacks = [], []
        self.fail_email, self.fail_slack = fail_email, fail_slack

    async def send_email(self, recipients, subject, text, html_body, csv_bytes=None, csv_filename=None):
        if self.fail_email:
            raise RuntimeError("smtp caído")
        self.emails.append({"to": recipients, "subject": subject, "html": html_body,
                            "csv": csv_bytes, "filename": csv_filename})

    async def send_slack(self, webhook_url, payload):
        if self.fail_slack:
            raise RuntimeError("slack 500")
        self.slacks.append(payload)


@pytest.fixture
def canales(monkeypatch):
    monkeypatch.setattr(settings, "smtp_user", "reportes@wellq.cl")
    monkeypatch.setattr(settings, "smtp_app_password", "clave-prueba")
    monkeypatch.setattr(settings, "slack_webhook_url", "https://hooks.slack.com/services/T/B/X")
    fake = FakeTransport()
    monkeypatch.setattr(svc, "transport", fake)
    return fake


NOW = datetime(2026, 11, 26, 10, 30)   # jueves


def _datos():
    return FakeSession({
        Clinic: [
            Clinic(clinic_id="c1", name="Kine Sur", tier="smb", status="active", mrr=100.0,
                   patients_used=460, patients_limit=500, created_at=datetime(2026, 6, 1)),
            Clinic(clinic_id="c2", name="Trauma Norte", tier="enterprise", status="critical", mrr=400.0,
                   patients_used=10, patients_limit=500, created_at=datetime(2026, 7, 1)),
            Clinic(clinic_id="c3", name="Cerrada", tier="smb", status="churned", mrr=50.0,
                   created_at=datetime(2026, 7, 1), last_login=datetime(2026, 8, 15)),
        ],
        ClinicUsageMetric: [ClinicUsageMetric(clinic_id="c1", notes_generated=40),
                            ClinicUsageMetric(clinic_id="c2", notes_generated=2)],
        SoapQualityMetric: [SoapQualityMetric(acceptance_rate_percentage=91.0, edits_required_percentage=22.0,
                                              average_time_saved_minutes_per_note=6.5, recorded_at=NOW)],
        SupportTicket: [SupportTicket(ticket_id="t1", clinic_id="c1", title="x", status="Open", reported_at=NOW)],
        Invoice: [Invoice(invoice_id="i1", clinic_id="c2", amount=400, status="overdue", issued_at=NOW)],
        AppUsageStat: [AppUsageStat(app_type="patients", monthly_active_users=320, recorded_at=NOW),
                       AppUsageStat(app_type="tablet", monthly_active_users=45, recorded_at=NOW)],
        FeatureAdoption: [FeatureAdoption(feature_name="Notas SOAP", adoption_rate_percentage=80, total_uses=500)],
        AdherenceSnapshot: [AdherenceSnapshot(overall_adherence_percentage=72.5, top_dropping_point="Semana 3", recorded_at=NOW)],
        MrrSnapshot: [MrrSnapshot(period_month="Oct", period_year=2026, total_mrr=480.0, source="auto",
                                  monthly_growth_percentage=4.0, created_at=NOW)],
    })


# ── next_run_at ─────────────────────────────────────────────────────────────

def test_diario_hoy_si_no_paso_la_hora():
    assert svc.compute_next_run("daily", 12, now=NOW) == datetime(2026, 11, 26, 12, 0)


def test_diario_manana_si_ya_paso():
    assert svc.compute_next_run("daily", 9, now=NOW) == datetime(2026, 11, 27, 9, 0)


def test_semanal_proximo_lunes():
    assert svc.compute_next_run("weekly", 8, day_of_week=0, now=NOW) == datetime(2026, 11, 30, 8, 0)


def test_semanal_mismo_dia_pasada_la_hora_va_a_la_semana_siguiente():
    assert svc.compute_next_run("weekly", 9, day_of_week=3, now=NOW) == datetime(2026, 12, 3, 9, 0)


def test_mensual_cambio_de_anio():
    now = datetime(2026, 12, 20, 15, 0)
    assert svc.compute_next_run("monthly", 12, day_of_month=5, now=now) == datetime(2027, 1, 5, 12, 0)


def test_mensual_mismo_mes():
    assert svc.compute_next_run("monthly", 12, day_of_month=28, now=NOW) == datetime(2026, 11, 28, 12, 0)


def _base(**kw):
    data = {"name": "Semanal", "frequency": "daily", "send_hour_utc": 12, "sections": ["retention"],
            "channels": ["email"], "recipients": ["ops@wellq.cl"]}
    data.update(kw)
    return data


@pytest.mark.parametrize("kw", [
    {"frequency": "weekly"}, {"frequency": "weekly", "day_of_week": 7},
    {"frequency": "monthly", "day_of_month": 29}, {"frequency": "monthly", "day_of_month": 0},
    {"frequency": "hourly"}, {"send_hour_utc": 24}, {"sections": []}, {"sections": ["secretos"]},
    {"channels": ["sms"]}, {"channels": []}, {"recipients": []}, {"name": ""},
])
def test_programacion_invalida(kw):
    with pytest.raises(svc.ReportValidationError):
        svc.validate_schedule(_base(**kw))


def test_programacion_valida_normaliza():
    values = svc.validate_schedule(_base(frequency="weekly", day_of_week=0, day_of_month=5,
                                         sections=["executive_mrr", "retention"], recipients=["OPS@WellQ.cl"]))
    assert values["day_of_month"] is None
    assert values["sections"] == ["retention", "executive_mrr"]
    assert values["recipients"] == ["ops@wellq.cl"]


def test_slack_sin_destinatarios_es_valido():
    assert svc.validate_schedule(_base(channels=["slack"], recipients=[]))["recipients"] == []


@pytest.mark.parametrize("email", ["sin-arroba", "a@b", "a@b.c\r\nBcc: x@y.cl", "a b@c.cl", "a@b.cl\n", "a@b.cl,c@d.cl"])
def test_email_invalido(email):
    with pytest.raises(svc.ReportValidationError):
        svc.validate_email(email)


# ── CSV ─────────────────────────────────────────────────────────────────────

def test_csv_con_bom_y_columnas():
    payload = asyncio.run(builder.build_payload(_datos(), ["clinic_health"], now=NOW))
    data = builder.render_csv(payload)
    assert data.startswith("﻿".encode("utf-8"))
    text = data.decode("utf-8-sig")
    assert text.splitlines()[0] == "Seccion,Metrica,Valor,Detalle"
    assert "Clínicas activas" in text


@pytest.mark.parametrize("valor", ["=SUM(A1:A2)", "+1", "-1", "@cmd", "\tx", "\rx"])
def test_csv_sin_inyeccion_de_formulas(valor):
    payload = {"sections": {"x": {"title": valor, "metrics": [{"label": valor, "value": valor, "detail": valor}]}}}
    row = builder.render_csv(payload).decode("utf-8-sig").splitlines()[1]
    assert not row.startswith(valor[0])
    assert row.startswith("'") or row.startswith("\"'")


# ── Compilación ─────────────────────────────────────────────────────────────

def test_compila_las_cinco_secciones_con_datos_reales():
    payload = asyncio.run(builder.build_payload(_datos(), builder.ALLOWED_SECTIONS, now=NOW))
    assert list(payload["sections"]) == builder.ALLOWED_SECTIONS and payload["omitted"] == []
    health = {m["label"]: m for m in payload["sections"]["clinic_health"]["metrics"]}
    assert health["Clínicas sobre el 90 % de su límite de pacientes"]["value"] == 1
    assert health["Facturas vencidas"]["value"] == 1
    soap = {m["label"]: m["value"] for m in payload["sections"]["soap_quality"]["metrics"]}
    assert soap["Notas generadas en el período"] == 42
    mrr = {m["label"]: m["value"] for m in payload["sections"]["executive_mrr"]["metrics"]}
    assert mrr["MRR activo total"].startswith("500,00")       # sin la clínica dada de baja


def test_sin_datos_omite_secciones_sin_inventar():
    payload = asyncio.run(builder.build_payload(FakeSession(), builder.ALLOWED_SECTIONS, now=NOW))
    assert payload["sections"] == {}
    assert payload["omitted"] == builder.ALLOWED_SECTIONS
    assert "No hay datos" in builder.render_html("Vacío", payload)


def test_retencion_reutiliza_logica_del_mapa_de_calor():
    clinics = _datos().data[Clinic]
    cohorts, _ = compute_retention_cohorts(clinics, NOW)
    payload = asyncio.run(builder.build_payload(_datos(), ["retention"], now=NOW))
    assert len(cohorts) == 2
    assert payload["sections"]["retention"]["metrics"][1]["value"] == 2


def test_html_escapa_texto_dinamico():
    payload = {"period_label": "<b>", "omitted": [],
               "sections": {"x": {"title": "<script>", "metrics": [{"label": "<img>", "value": "1", "detail": ""}]}}}
    out = builder.render_html("<script>alert(1)</script>", payload)
    assert "<script>" not in out and "&lt;script&gt;" in out


# ── Envío y despacho ────────────────────────────────────────────────────────

def _reporte(**kw):
    values = svc.validate_schedule(_base(channels=["email", "slack"], sections=["clinic_health"], **kw))
    report = svc.new_report(values, "qa@wellq.cl", now=NOW - timedelta(days=1))
    report.next_run_at = NOW - timedelta(minutes=5)
    return report


def test_envio_por_ambos_canales_con_csv(canales):
    run = asyncio.run(svc.execute_report(_datos(), _reporte(), trigger="manual", triggered_by="qa@wellq.cl", now=NOW))
    assert run.status == "sent" and json.loads(run.channels_summary) == {"email": "sent", "slack": "sent"}
    assert canales.emails[0]["csv"].startswith("﻿".encode("utf-8"))
    assert canales.emails[0]["filename"].endswith(".csv")
    assert run.recipients_count == 1


def test_fallo_parcial_email_falla_slack_ok(monkeypatch, canales):
    monkeypatch.setattr(svc, "transport", FakeTransport(fail_email=True))
    run = asyncio.run(svc.execute_report(_datos(), _reporte(), trigger="manual", triggered_by="x", now=NOW))
    assert run.status == "partial"
    assert "smtp caído" in run.error_detail


def test_canales_sin_configurar_quedan_omitidos(monkeypatch, canales):
    monkeypatch.setattr(settings, "slack_webhook_url", "")
    monkeypatch.setattr(settings, "smtp_app_password", "")
    run = asyncio.run(svc.execute_report(_datos(), _reporte(), trigger="manual", triggered_by="x", now=NOW))
    assert run.status == "skipped" and canales.emails == [] and canales.slacks == []


def test_despacho_no_envia_dos_veces(canales):
    report = _reporte()
    db = _datos()
    db.data[ScheduledReport] = [report]
    primero = asyncio.run(svc.dispatch_due_reports(db, now=NOW))
    assert len(primero["processed"]) == 1
    assert report.last_run_at == NOW and report.next_run_at > NOW
    segundo = asyncio.run(svc.dispatch_due_reports(db, now=NOW + timedelta(minutes=1)))
    assert segundo["processed"] == []
    assert len(canales.emails) == 1
    assert sum(isinstance(o, ScheduledReportRun) for o in db.added) == 1


def test_despacho_ignora_pausados(canales):
    report = _reporte()
    report.is_active = False
    db = _datos()
    db.data[ScheduledReport] = [report]
    assert asyncio.run(svc.dispatch_due_reports(db, now=NOW))["processed"] == []
