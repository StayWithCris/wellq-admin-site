"""Pruebas de lógica pura de las Mejoras #4, #14 y #18 (sin base de datos ni FastAPI)."""
from datetime import datetime, timedelta

import pytest

from app.services import admin_sessions as ses
from app.services import discounts as dsc
from app.services import impersonation as imp
from app.services import totp

NOW = datetime(2026, 11, 16, 12, 0, 0)


# ── #4 Descuentos ────────────────────────────────────────────────────────────
@pytest.mark.parametrize("kind,value,ok", [
    ("percent", 10, True), ("percent", 100, True), ("percent", 100.5, False), ("percent", 0, False),
    ("fixed", 25, True), ("fixed", -1, False), ("otro", 5, False),
])
def test_validar_valor(kind, value, ok):
    assert (dsc.validate_value(kind, value) is None) is ok


def test_precio_con_descuento():
    assert dsc.apply_discount(100, "percent", 10) == 90.0
    assert dsc.apply_discount(99.99, "percent", 33) == 66.99
    assert dsc.apply_discount(100, "fixed", 30) == 70.0
    assert dsc.apply_discount(20, "fixed", 50) == 0.0          # nunca negativo
    assert dsc.apply_discount(100, "percent", 100) == 0.0
    with pytest.raises(ValueError):
        dsc.apply_discount(100, "x", 1)


def test_sumar_meses():
    assert dsc.add_months(datetime(2026, 1, 31), 1) == datetime(2026, 2, 28)
    assert dsc.add_months(datetime(2028, 1, 31), 1) == datetime(2028, 2, 29)   # bisiesto
    assert dsc.add_months(datetime(2026, 11, 16), 3) == datetime(2027, 2, 16)
    assert dsc.add_months(datetime(2026, 12, 15), 1) == datetime(2027, 1, 15)
    assert dsc.add_months(datetime(2026, 5, 10), 24) == datetime(2028, 5, 10)


def _d(**kw):
    base = {"is_active": True, "starts_at": None, "ends_at": None, "max_redemptions": None, "redemptions": 0, "plan_id": None}
    base.update(kw)
    return base


def test_estados_del_descuento():
    assert dsc.status_of(_d(), NOW) == dsc.ACTIVE
    assert dsc.status_of(_d(is_active=False), NOW) == dsc.INACTIVE
    assert dsc.status_of(_d(starts_at=NOW + timedelta(days=1)), NOW) == dsc.SCHEDULED
    assert dsc.status_of(_d(ends_at=NOW), NOW) == dsc.EXPIRED                 # el término es exclusivo
    assert dsc.status_of(_d(ends_at=NOW + timedelta(seconds=1)), NOW) == dsc.ACTIVE
    assert dsc.status_of(_d(max_redemptions=3, redemptions=3), NOW) == dsc.EXHAUSTED
    assert dsc.status_of(_d(max_redemptions=3, redemptions=2), NOW) == dsc.ACTIVE
    # desactivado tiene prioridad sobre vencido
    assert dsc.status_of(_d(is_active=False, ends_at=NOW - timedelta(days=1)), NOW) == dsc.INACTIVE


def test_puede_aplicarse():
    assert dsc.can_apply(_d(), "plan-a", NOW) == (True, None)
    assert dsc.can_apply(_d(plan_id="plan-a"), "plan-a", NOW) == (True, None)
    assert dsc.can_apply(_d(plan_id="plan-a"), "plan-b", NOW) == (False, "plan_mismatch")
    assert dsc.can_apply(_d(is_active=False), "plan-a", NOW) == (False, "inactive")
    assert dsc.can_apply(_d(max_redemptions=1, redemptions=1), "plan-a", NOW) == (False, "exhausted")


def test_vigencia_de_la_asignacion_e_impacto():
    assert dsc.assignment_end(NOW, None) is None
    assert dsc.assignment_end(NOW, 3) == datetime(2027, 2, 16, 12, 0, 0)
    assert dsc.assignment_running({"revoked_at": None, "ends_at": None}, NOW) is True
    assert dsc.assignment_running({"revoked_at": NOW, "ends_at": None}, NOW) is False
    assert dsc.assignment_running({"revoked_at": None, "ends_at": NOW}, NOW) is False
    asignaciones = [
        {"base_price": 100, "discounted_price": 80, "revoked_at": None, "ends_at": None},                # 20
        {"base_price": 50, "discounted_price": 50, "revoked_at": None, "ends_at": None},                 # 0
        {"base_price": 200, "discounted_price": 100, "revoked_at": NOW, "ends_at": None},                # revocada
        {"base_price": 90, "discounted_price": 60, "revoked_at": None, "ends_at": NOW - timedelta(1)},   # vencida
    ]
    assert dsc.monthly_impact(asignaciones, NOW) == 20.0


# ── #14 Consola de impersonación ─────────────────────────────────────────────
def test_estado_de_sesion_de_soporte():
    assert imp.session_status(NOW + timedelta(hours=1), None, NOW) == imp.ACTIVE
    assert imp.session_status(NOW, None, NOW) == imp.EXPIRED
    assert imp.session_status(NOW - timedelta(hours=1), None, NOW) == imp.EXPIRED
    assert imp.session_status(NOW + timedelta(hours=1), NOW, NOW) == imp.REVOKED   # revocada gana
    assert imp.session_status(NOW - timedelta(hours=1), NOW, NOW) == imp.REVOKED


def test_minutos_restantes_y_top():
    assert imp.minutes_remaining(NOW + timedelta(minutes=90, seconds=30), NOW) == 90
    assert imp.minutes_remaining(NOW - timedelta(minutes=5), NOW) == 0
    assert imp.minutes_remaining(None, NOW) == 0
    top = imp.top_counts(["b", "a", "b", "c", "a", "", None, "b"], 2)
    assert top == [{"name": "b", "count": 3}, {"name": "a", "count": 2}]
    assert imp.top_counts(["z", "y"], 5) == [{"name": "y", "count": 1}, {"name": "z", "count": 1}]   # empate: alfabético


# ── #18 Doble factor ─────────────────────────────────────────────────────────
RFC_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"   # "12345678901234567890" (vectores de la RFC 6238)


@pytest.mark.parametrize("ts,code", [
    (59, "287082"), (1111111109, "081804"), (1111111111, "050471"),
    (1234567890, "005924"), (2000000000, "279037"), (20000000000, "353130"),
])
def test_vectores_rfc_6238(ts, code):
    assert totp.totp_at(RFC_SECRET, ts) == code


def test_verificar_codigo_con_ventana_y_sin_repeticion():
    ts = 1234567890
    code = totp.totp_at(RFC_SECRET, ts)
    c = totp.verify_totp(RFC_SECRET, code, ts)
    assert c == ts // 30
    assert totp.verify_totp(RFC_SECRET, code, ts + 30) == c           # un intervalo después, aún dentro de la ventana
    assert totp.verify_totp(RFC_SECRET, code, ts + 90) is None        # dos intervalos: fuera de la ventana
    assert totp.verify_totp(RFC_SECRET, code, ts, last_counter=c) is None        # ya usado
    assert totp.verify_totp(RFC_SECRET, code, ts, last_counter=c - 1) == c       # uno anterior sí deja pasar
    assert totp.verify_totp(RFC_SECRET, "12345", ts) is None
    assert totp.verify_totp(RFC_SECRET, "abcdef", ts) is None
    assert totp.verify_totp(RFC_SECRET, None, ts) is None
    assert totp.verify_totp(RFC_SECRET, f"{code[:3]} {code[3:]}", ts) == c     # acepta espacios


def test_secreto_generado_es_valido():
    s = totp.generate_secret()
    assert len(s) == 32 and totp.generate_secret() != s
    assert totp.verify_totp(s, totp.totp_at(s, 1_800_000_000), 1_800_000_000) is not None
    assert " " in totp.format_secret(s) and totp.format_secret(s).replace(" ", "") == s


def test_uri_de_aprovisionamiento():
    uri = totp.provisioning_uri("ABCDEFGH", "ana@wellq.co", "WellQ Admin")
    assert uri.startswith("otpauth://totp/WellQ%20Admin%3Aana%40wellq.co?")
    assert "secret=ABCDEFGH" in uri and "issuer=WellQ%20Admin" in uri and "digits=6" in uri and "period=30" in uri


def test_codigos_de_respaldo_un_solo_uso():
    plain, hashes = totp.generate_backup_codes()
    assert len(plain) == 10 and len(set(plain)) == 10 and len(hashes) == 10
    assert all(len(c) == 9 and c[4] == "-" for c in plain)
    assert plain[0] not in "".join(hashes)                      # solo se guardan hashes
    resto = totp.consume_backup_code(hashes, plain[0].lower().replace("-", " "))   # tolera minúsculas y separadores
    assert resto is not None and len(resto) == 9
    assert totp.consume_backup_code(resto, plain[0]) is None    # ya usado
    assert totp.consume_backup_code(hashes, "ZZZZ-ZZZZ") is None
    assert totp.consume_backup_code(hashes, "") is None


def test_bloqueo_por_intentos():
    count, locked = 0, None
    for i in range(4):
        count, locked = totp.after_failure(count, NOW)
        assert locked is None and count == i + 1
    count, locked = totp.after_failure(count, NOW)
    assert count == 0 and locked == NOW + timedelta(minutes=totp.LOCK_MINUTES)
    assert totp.is_locked(locked, NOW + timedelta(minutes=14)) is True
    assert totp.is_locked(locked, NOW + timedelta(minutes=15)) is False
    assert totp.minutes_left(locked, NOW) == 16 or totp.minutes_left(locked, NOW) == 15
    assert totp.minutes_left(None, NOW) == 0


def test_cifrado_del_secreto():
    token = totp.encrypt_secret("ABCDEFGH23456723", "clave-del-servidor")
    assert "ABCDEFGH" not in token
    assert totp.decrypt_secret(token, "clave-del-servidor") == "ABCDEFGH23456723"
    with pytest.raises(ValueError):
        totp.decrypt_secret(token, "otra-clave")


# ── #18 Sesiones ─────────────────────────────────────────────────────────────
def test_sesion_activa():
    assert ses.session_active(NOW + timedelta(days=1), None, NOW) is True
    assert ses.session_active(NOW, None, NOW) is False
    assert ses.session_active(NOW + timedelta(days=1), NOW, NOW) is False


@pytest.mark.parametrize("ua,label", [
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36", "Chrome · Windows"),
    ("Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 Chrome/120.0 Safari/537.36 Edg/120.0", "Edge · Windows"),
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605 Version/17.0 Safari/605", "Safari · macOS"),
    ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605 Version/17.0 Mobile Safari/604", "Safari · iOS"),
    ("Mozilla/5.0 (X11; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0", "Firefox · Linux"),
    ("", "Dispositivo desconocido"), (None, "Dispositivo desconocido"),
])
def test_describir_navegador(ua, label):
    assert ses.describe_agent(ua) == label
