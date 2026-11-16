"""Mejora #18 — Gestión de sesiones: reglas puras."""
from datetime import datetime
from typing import Optional


def session_active(expires_at: Optional[datetime], revoked_at: Optional[datetime], now: datetime) -> bool:
    if revoked_at:
        return False
    return expires_at is None or now < expires_at


def describe_agent(user_agent: Optional[str]) -> str:
    """Etiqueta corta («Chrome · Windows») a partir del User-Agent; el orden de las pruebas importa."""
    ua = (user_agent or "").lower()
    if not ua:
        return "Dispositivo desconocido"
    if "edg/" in ua or "edge/" in ua:
        browser = "Edge"
    elif "opr/" in ua or "opera" in ua:
        browser = "Opera"
    elif "firefox" in ua:
        browser = "Firefox"
    elif "chrome" in ua or "crios" in ua:
        browser = "Chrome"
    elif "safari" in ua:
        browser = "Safari"
    else:
        browser = "Navegador"
    if "android" in ua:
        system = "Android"
    elif "iphone" in ua or "ipad" in ua or "ios" in ua:
        system = "iOS"
    elif "windows" in ua:
        system = "Windows"
    elif "mac os" in ua or "macintosh" in ua:
        system = "macOS"
    elif "linux" in ua:
        system = "Linux"
    else:
        system = "Sistema desconocido"
    return f"{browser} · {system}"
