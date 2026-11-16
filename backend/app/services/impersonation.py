"""Mejora #14 — Consola de impersonación: reglas puras (sin base de datos).

Una sesión de soporte (tabla impersonate_audit_log) está:
  - revoked  si tiene revoked_at (tiene prioridad sobre lo demás),
  - expired  si ya pasó expires_at,
  - active   en cualquier otro caso.
El portal de clínica ya rechaza las sesiones revocadas, así que revocar corta el acceso
de inmediato.
"""
from collections import Counter
from datetime import datetime
from typing import Iterable, Optional

ACTIVE, EXPIRED, REVOKED = "active", "expired", "revoked"
STATUSES = (ACTIVE, EXPIRED, REVOKED)


def session_status(expires_at: Optional[datetime], revoked_at: Optional[datetime], now: datetime) -> str:
    if revoked_at:
        return REVOKED
    if expires_at is not None and expires_at <= now:
        return EXPIRED
    return ACTIVE


def minutes_remaining(expires_at: Optional[datetime], now: datetime) -> int:
    """Minutos que le quedan a una sesión (0 si ya terminó)."""
    if expires_at is None:
        return 0
    return max(int((expires_at - now).total_seconds() // 60), 0)


def top_counts(values: Iterable[str], n: int = 5) -> list[dict]:
    """Los n valores más frecuentes; en empate, orden alfabético."""
    counts = Counter(v for v in values if v)
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [{"name": k, "count": c} for k, c in ranked[:n]]
