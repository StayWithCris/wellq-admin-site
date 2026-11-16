"""Pruebas de API de las Mejoras #4, #14 y #18 (sesión de base de datos falsa)."""
import json
import time
from datetime import datetime, timedelta

import pytest

from app.config import settings
from app.main import app
from app.models_db import (
    AdminSession, AdminUser, Clinic, ClinicDiscount, ClinicPlan, Discount, ImpersonateAuditLog, Plan,
)
from app.routers.auth import create_token, hash_password
from app.services import totp

PROTEGIDAS_GET = [
    "/api/discounts",
    "/api/impersonation/summary",
    "/api/impersonation/sessions",
    "/api/auth/2fa/status",
    "/api/auth/sessions",
]


def test_rutas_registradas():
    paths = {getattr(r, "path", "") for r in app.routes}
    for p in [
        "/api/discounts", "/api/discounts/{discount_id}", "/api/discounts/{discount_id}/apply",
        "/api/discounts/{discount_id}/preview", "/api/discounts/clinics/{clinic_id}",
        "/api/discounts/assignments/{assignment_id}/revoke",
        "/api/impersonation/summary", "/api/impersonation/sessions",
        "/api/impersonation/sessions/{audit_log_id}/revoke", "/api/impersonation/revoke-active",
        "/api/auth/2fa/status", "/api/auth/2fa/setup", "/api/auth/2fa/enable", "/api/auth/2fa/disable",
        "/api/auth/2fa/backup-codes", "/api/auth/2fa/verify", "/api/auth/2fa/reset/{user_id}",
        "/api/auth/sessions", "/api/auth/sessions/revoke-others", "/api/auth/sessions/{session_id}/revoke",
    ]:
        assert p in paths, f"Falta la ruta {p}"


@pytest.mark.parametrize("path", PROTEGIDAS_GET)
def test_requieren_token(client, path):
    assert client.get(path).status_code in (401, 403)


# ── Mejora #4 ────────────────────────────────────────────────────────────────
def test_crear_descuento(client, fake_db, auth_header):
    body = {"code": "bienvenida10", "name": "Bienvenida", "kind": "percent", "value": 10, "duration_months": 3}
    r = client.post("/api/discounts", json=body, headers=auth_header)
    assert r.status_code == 201, r.text
    d = r.json()["data"]
    assert d["code"] == "BIENVENIDA10" and d["status"] == "active"
    assert any(isinstance(o, Discount) for o in fake_db.added)


def test_descuento_porcentual_sobre_100_se_rechaza(client, fake_db, auth_header):
    body = {"code": "MUCHO", "name": "Demasiado", "kind": "percent", "value": 150}
    assert client.post("/api/discounts", json=body, headers=auth_header).status_code == 422


def test_codigo_repetido(client, fake_db, auth_header):
    fake_db.data[Discount] = [Discount(discount_id="disc-1", code="PROMO", name="Promo", kind="percent", value=5)]
    r = client.post("/api/discounts", json={"code": "promo", "name": "Otra", "kind": "percent", "value": 5}, headers=auth_header)
    assert r.status_code == 409


def _escenario_aplicar(fake_db, **disc):
    fake_db.data[Discount] = [Discount(discount_id="disc-1", code="PROMO", name="Promo", kind="percent", value=10, **disc)]
    fake_db.data[Clinic] = [Clinic(clinic_id="CL-1", name="Clínica Uno")]
    fake_db.data[ClinicPlan] = [ClinicPlan(assignment_id="a1", clinic_id="CL-1", plan_id="plan-pro",
                                           plan_snapshot="{}", effective_from=datetime.utcnow())]
    fake_db.data[Plan] = [Plan(plan_id="plan-pro", name="Pro", monthly_price=100.0)]


def test_aplicar_descuento_a_clinica(client, fake_db, auth_header):
    _escenario_aplicar(fake_db, duration_months=3)
    r = client.post("/api/discounts/disc-1/apply", json={"clinic_id": "CL-1"}, headers=auth_header)
    assert r.status_code == 201, r.text
    a = r.json()["data"]
    assert a["basePrice"] == 100.0 and a["discountedPrice"] == 90.0 and a["monthlySaving"] == 10.0
    assert fake_db.data[Discount][0].redemptions == 1
    assert any(isinstance(o, ClinicDiscount) for o in fake_db.added)


def test_no_aplica_si_esta_agotado(client, fake_db, auth_header):
    _escenario_aplicar(fake_db, max_redemptions=1, redemptions=1)
    r = client.post("/api/discounts/disc-1/apply", json={"clinic_id": "CL-1"}, headers=auth_header)
    assert r.status_code == 422


def test_no_aplica_si_ya_hay_descuento_vigente(client, fake_db, auth_header):
    _escenario_aplicar(fake_db)
    fake_db.data[ClinicDiscount] = [ClinicDiscount(assignment_id="cd-1", clinic_id="CL-1", discount_id="disc-9",
                                                   discount_code="OTRO", base_price=100, discounted_price=80)]
    r = client.post("/api/discounts/disc-1/apply", json={"clinic_id": "CL-1"}, headers=auth_header)
    assert r.status_code == 409


def test_descuento_usado_no_se_elimina(client, fake_db, auth_header):
    fake_db.data[Discount] = [Discount(discount_id="disc-1", code="PROMO", name="Promo", kind="percent", value=5, redemptions=2)]
    assert client.delete("/api/discounts/disc-1", headers=auth_header).status_code == 409


# ── Mejora #14 ───────────────────────────────────────────────────────────────
def _sesion_soporte(**kw):
    base = dict(audit_log_id="audit_1", clinic_id="CL-1", clinic_name="Clínica Uno", admin_user_id="usr-1",
                admin_email="a@wellq.co", reason="Revisión de ticket 123", session_token_hash="imp_x",
                expires_at=datetime.utcnow() + timedelta(hours=1))
    base.update(kw)
    return ImpersonateAuditLog(**base)


def test_revocar_sesion_de_soporte(client, fake_db, auth_header):
    fake_db.data[ImpersonateAuditLog] = [_sesion_soporte()]
    r = client.post("/api/impersonation/sessions/audit_1/revoke", headers=auth_header)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["status"] == "revoked"
    assert fake_db.data[ImpersonateAuditLog][0].revoked_at is not None


def test_no_se_revoca_una_sesion_vencida(client, fake_db, auth_header):
    fake_db.data[ImpersonateAuditLog] = [_sesion_soporte(expires_at=datetime.utcnow() - timedelta(minutes=1))]
    assert client.post("/api/impersonation/sessions/audit_1/revoke", headers=auth_header).status_code == 409


def test_la_respuesta_no_incluye_el_token(client, fake_db, auth_header):
    fake_db.data[ImpersonateAuditLog] = [_sesion_soporte()]
    r = client.post("/api/impersonation/sessions/audit_1/revoke", headers=auth_header)
    assert "imp_x" not in r.text


def test_revocar_todas_las_activas(client, fake_db, auth_header):
    fake_db.data[ImpersonateAuditLog] = [_sesion_soporte(), _sesion_soporte(audit_log_id="audit_2")]
    r = client.post("/api/impersonation/revoke-active", json={}, headers=auth_header)
    assert r.status_code == 200 and r.json()["data"]["revoked"] == 2


# ── Mejora #18 ───────────────────────────────────────────────────────────────
def _usuario(**kw):
    base = dict(user_id="usr-test", full_name="QA", email="qa@wellq.test", role="super_admin",
                status="active", password_hash=hash_password("Clave-123"))
    base.update(kw)
    return AdminUser(**base)


def _con_2fa(secret="JBSWY3DPEHPK3PXP", **kw):
    plain, hashes = totp.generate_backup_codes()
    user = _usuario(totp_enabled=True, totp_secret=totp.encrypt_secret(secret, settings.jwt_secret),
                    totp_backup_hashes=json.dumps(hashes), **kw)
    return user, secret, plain


def test_login_sin_2fa_registra_la_sesion(client, fake_db):
    fake_db.data[AdminUser] = [_usuario()]
    r = client.post("/api/auth/login", json={"email": "qa@wellq.test", "password": "Clave-123"})
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["access_token"] and d["user"]["two_factor_enabled"] is False
    assert any(isinstance(o, AdminSession) for o in fake_db.added)


def test_login_con_2fa_no_entrega_tokens(client, fake_db):
    user, _, _ = _con_2fa()
    fake_db.data[AdminUser] = [user]
    r = client.post("/api/auth/login", json={"email": "qa@wellq.test", "password": "Clave-123"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "2fa_required"
    assert body["data"]["challenge_token"] and "access_token" not in body["data"]
    assert not any(isinstance(o, AdminSession) for o in fake_db.added)


def test_verificar_codigo_correcto_entrega_tokens(client, fake_db):
    user, secret, _ = _con_2fa()
    fake_db.data[AdminUser] = [user]
    challenge = client.post("/api/auth/login", json={"email": "qa@wellq.test", "password": "Clave-123"}).json()["data"]["challenge_token"]
    code = totp.totp_at(secret, time.time())
    r = client.post("/api/auth/2fa/verify", json={"challenge_token": challenge, "code": code})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["access_token"] and r.json()["data"]["user"]["two_factor_enabled"] is True
    assert any(isinstance(o, AdminSession) and o.two_factor for o in fake_db.added)


def test_codigo_incorrecto_suma_intento(client, fake_db):
    user, _, _ = _con_2fa()
    fake_db.data[AdminUser] = [user]
    challenge = client.post("/api/auth/login", json={"email": "qa@wellq.test", "password": "Clave-123"}).json()["data"]["challenge_token"]
    r = client.post("/api/auth/2fa/verify", json={"challenge_token": challenge, "code": "000000"})
    assert r.status_code == 401
    assert user.totp_fail_count == 1


def test_bloqueo_tras_cinco_fallos(client, fake_db):
    user, _, _ = _con_2fa()
    fake_db.data[AdminUser] = [user]
    challenge = client.post("/api/auth/login", json={"email": "qa@wellq.test", "password": "Clave-123"}).json()["data"]["challenge_token"]
    codigos = [r.status_code for r in (client.post("/api/auth/2fa/verify", json={"challenge_token": challenge, "code": "000000"}) for _ in range(6))]
    assert codigos[:4] == [401] * 4 and codigos[4] == 429 and codigos[5] == 429


def test_codigo_de_respaldo_sirve_una_vez(client, fake_db):
    user, _, plain = _con_2fa()
    fake_db.data[AdminUser] = [user]
    challenge = client.post("/api/auth/login", json={"email": "qa@wellq.test", "password": "Clave-123"}).json()["data"]["challenge_token"]
    r = client.post("/api/auth/2fa/verify", json={"challenge_token": challenge, "code": plain[0]})
    assert r.status_code == 200, r.text
    assert len(json.loads(user.totp_backup_hashes)) == 9
    r2 = client.post("/api/auth/2fa/verify", json={"challenge_token": challenge, "code": plain[0]})
    assert r2.status_code == 401


def test_un_token_de_desafio_no_sirve_como_acceso(client, fake_db):
    user, _, _ = _con_2fa()
    fake_db.data[AdminUser] = [user]
    challenge = client.post("/api/auth/login", json={"email": "qa@wellq.test", "password": "Clave-123"}).json()["data"]["challenge_token"]
    r = client.get("/api/auth/2fa/status", headers={"Authorization": f"Bearer {challenge}"})
    assert r.status_code == 401


def test_activar_2fa_entrega_codigos_de_respaldo(client, fake_db, auth_header):
    fake_db.data[AdminUser] = [_usuario()]
    setup = client.post("/api/auth/2fa/setup", headers=auth_header)
    assert setup.status_code == 200, setup.text
    secret = setup.json()["data"]["secret"].replace(" ", "")
    assert setup.json()["data"]["otpauthUri"].startswith("otpauth://totp/")
    r = client.post("/api/auth/2fa/enable", json={"code": totp.totp_at(secret, time.time())}, headers=auth_header)
    assert r.status_code == 200, r.text
    assert len(r.json()["data"]["backupCodes"]) == 10
    assert fake_db.data[AdminUser][0].totp_enabled is True


def test_activar_con_codigo_incorrecto(client, fake_db, auth_header):
    fake_db.data[AdminUser] = [_usuario()]
    client.post("/api/auth/2fa/setup", headers=auth_header)
    r = client.post("/api/auth/2fa/enable", json={"code": "000000"}, headers=auth_header)
    assert r.status_code == 422
    assert fake_db.data[AdminUser][0].totp_enabled is False


def test_listar_sesiones_marca_la_actual(client, fake_db):
    ahora = datetime.utcnow()
    fake_db.data[AdminUser] = [_usuario()]
    fake_db.data[AdminSession] = [
        AdminSession(session_id="ses_a", user_id="usr-test", email="qa@wellq.test", expires_at=ahora + timedelta(days=1),
                     user_agent="Mozilla/5.0 (Windows NT 10.0) Chrome/120 Safari/537"),
        AdminSession(session_id="ses_b", user_id="usr-test", email="qa@wellq.test", expires_at=ahora + timedelta(days=1)),
        AdminSession(session_id="ses_c", user_id="usr-test", email="qa@wellq.test", expires_at=ahora + timedelta(days=1), revoked_at=ahora),
    ]
    token = create_token({"user_id": "usr-test", "email": "qa@wellq.test", "role": "super_admin", "type": "access", "sid": "ses_a"},
                         timedelta(minutes=5))
    r = client.get("/api/auth/sessions", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    filas = r.json()["data"]
    assert {f["id"] for f in filas} == {"ses_a", "ses_b"}
    assert next(f for f in filas if f["id"] == "ses_a")["current"] is True
    assert next(f for f in filas if f["id"] == "ses_a")["device"] == "Chrome · Windows"


def test_cerrar_otras_sesiones(client, fake_db):
    ahora = datetime.utcnow()
    fake_db.data[AdminUser] = [_usuario()]
    fake_db.data[AdminSession] = [
        AdminSession(session_id="ses_a", user_id="usr-test", email="qa@wellq.test", expires_at=ahora + timedelta(days=1)),
        AdminSession(session_id="ses_b", user_id="usr-test", email="qa@wellq.test", expires_at=ahora + timedelta(days=1)),
    ]
    token = create_token({"user_id": "usr-test", "email": "qa@wellq.test", "role": "super_admin", "type": "access", "sid": "ses_a"},
                         timedelta(minutes=5))
    r = client.post("/api/auth/sessions/revoke-others", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200 and r.json()["data"]["revoked"] == 1
    assert fake_db.data[AdminSession][0].revoked_at is None and fake_db.data[AdminSession][1].revoked_at is not None


def test_refresh_con_sesion_revocada_falla(client, fake_db):
    ahora = datetime.utcnow()
    fake_db.data[AdminUser] = [_usuario()]
    fake_db.data[AdminSession] = [
        AdminSession(session_id="ses_a", user_id="usr-test", email="qa@wellq.test", expires_at=ahora + timedelta(days=1), revoked_at=ahora)]
    refresh = create_token({"user_id": "usr-test", "email": "qa@wellq.test", "role": "super_admin", "type": "refresh", "sid": "ses_a"},
                           timedelta(days=1))
    r = client.post("/api/auth/refresh", json={"refresh_token": refresh})
    assert r.status_code == 401
