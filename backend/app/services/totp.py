"""Mejora #18 — Doble factor (TOTP, RFC 6238): reglas puras, solo librería estándar + cryptography.

- Códigos de 6 dígitos que cambian cada 30 s (compatibles con Google Authenticator,
  Microsoft Authenticator, Authy, 1Password, etc.).
- Se acepta el código del intervalo actual y el anterior/siguiente (ventana ±1) para
  tolerar diferencias de reloj. Un código ya usado no se acepta de nuevo (anti-repetición).
- Códigos de respaldo de un solo uso, guardados solo como hash.
- Bloqueo temporal tras varios intentos fallidos.
- El secreto TOTP se guarda cifrado (Fernet) con una llave derivada de un secreto del servidor.
"""
import base64
import hashlib
import hmac
import secrets
import struct
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import quote

from cryptography.fernet import Fernet, InvalidToken

STEP_SECONDS = 30
DIGITS = 6
WINDOW = 1
MAX_ATTEMPTS = 5
LOCK_MINUTES = 15
BACKUP_COUNT = 10
_BACKUP_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # sin 0/O ni 1/I para evitar confusiones


def generate_secret() -> str:
    """Secreto aleatorio de 160 bits en base32 (32 caracteres)."""
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii")


def format_secret(secret: str) -> str:
    """Agrupa en bloques de 4 para que sea fácil de escribir a mano."""
    return " ".join(secret[i:i + 4] for i in range(0, len(secret), 4))


def _counter(ts: float) -> int:
    return int(ts // STEP_SECONDS)


def _hotp(secret_b32: str, counter: int) -> str:
    key = base64.b32decode(secret_b32.replace(" ", "").upper())
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % (10 ** DIGITS)
    return str(code).zfill(DIGITS)


def totp_at(secret_b32: str, ts: float) -> str:
    return _hotp(secret_b32, _counter(ts))


def normalize_code(code: Optional[str]) -> str:
    return "".join(ch for ch in (code or "") if ch.isalnum()).upper()


def verify_totp(secret_b32: str, code: Optional[str], ts: float,
                last_counter: Optional[int] = None) -> Optional[int]:
    """Devuelve el contador que coincide, o None si el código no sirve (incorrecto o ya usado)."""
    digits = "".join(ch for ch in (code or "") if ch.isdigit())
    if len(digits) != DIGITS:
        return None
    current = _counter(ts)
    match = None
    for c in range(current - WINDOW, current + WINDOW + 1):
        if hmac.compare_digest(_hotp(secret_b32, c), digits):
            match = c
    if match is None:
        return None
    if last_counter is not None and match <= last_counter:
        return None
    return match


def provisioning_uri(secret_b32: str, account: str, issuer: str = "WellQ Admin") -> str:
    label = quote(f"{issuer}:{account}")
    return (f"otpauth://totp/{label}?secret={secret_b32}&issuer={quote(issuer)}"
            f"&algorithm=SHA1&digits={DIGITS}&period={STEP_SECONDS}")


# ── Códigos de respaldo ───────────────────────────────────────────────────────

def hash_backup_code(code: str) -> str:
    return hashlib.sha256(("wellq-backup:" + normalize_code(code)).encode()).hexdigest()


def generate_backup_codes(n: int = BACKUP_COUNT) -> tuple[list[str], list[str]]:
    """(códigos en claro para mostrar una vez, hashes para guardar)."""
    plain = []
    for _ in range(n):
        raw = "".join(secrets.choice(_BACKUP_ALPHABET) for _ in range(8))
        plain.append(f"{raw[:4]}-{raw[4:]}")
    return plain, [hash_backup_code(c) for c in plain]


def consume_backup_code(hashes: list[str], code: Optional[str]) -> Optional[list[str]]:
    """Si el código es válido devuelve la lista sin ese hash (un solo uso); si no, None."""
    if not normalize_code(code):
        return None
    target = hash_backup_code(code or "")
    for h in hashes:
        if hmac.compare_digest(h, target):
            return [x for x in hashes if x != h]
    return None


# ── Bloqueo por intentos fallidos ─────────────────────────────────────────────

def is_locked(locked_until: Optional[datetime], now: datetime) -> bool:
    return locked_until is not None and now < locked_until


def minutes_left(locked_until: Optional[datetime], now: datetime) -> int:
    if not is_locked(locked_until, now):
        return 0
    return max(int((locked_until - now).total_seconds() // 60) + 1, 1)


def after_failure(fail_count: int, now: datetime) -> tuple[int, Optional[datetime]]:
    """Nuevo contador y, si se llegó al máximo, hasta cuándo queda bloqueado (el contador vuelve a 0)."""
    count = (fail_count or 0) + 1
    if count >= MAX_ATTEMPTS:
        return 0, now + timedelta(minutes=LOCK_MINUTES)
    return count, None


# ── Cifrado del secreto en reposo ─────────────────────────────────────────────

def _fernet(key_material: str) -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(("wellq-totp:" + key_material).encode()).digest())
    return Fernet(key)


def encrypt_secret(secret_b32: str, key_material: str) -> str:
    return _fernet(key_material).encrypt(secret_b32.encode()).decode()


def decrypt_secret(token: str, key_material: str) -> str:
    try:
        return _fernet(key_material).decrypt(token.encode()).decode()
    except InvalidToken as exc:   # la llave del servidor cambió o el dato está dañado
        raise ValueError("No se pudo descifrar el secreto de doble factor.") from exc
