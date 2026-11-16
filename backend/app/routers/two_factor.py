"""Recomendación #18 — Doble factor (TOTP) y gestión de sesiones.

Doble factor
  GET  /api/auth/2fa/status                 Estado del usuario actual
  POST /api/auth/2fa/setup                  Genera el secreto y la URI para la app de autenticación
  POST /api/auth/2fa/enable                 Confirma con un código y entrega los códigos de respaldo
  POST /api/auth/2fa/disable                Desactiva (pide contraseña y código)
  POST /api/auth/2fa/backup-codes           Regenera los códigos de respaldo (pide contraseña y código)
  POST /api/auth/2fa/verify                 Segundo paso del login (público: usa el challenge_token)
  POST /api/auth/2fa/reset/{user_id}        Un administrador quita el 2FA de otro usuario [users.manage]

Sesiones
  GET  /api/auth/sessions                   Sesiones activas del usuario actual
  POST /api/auth/sessions/{session_id}/revoke   Cerrar una sesión propia
  POST /api/auth/sessions/revoke-others     Cerrar todas menos la actual

Cómo se aplica la revocación: una sesión revocada ya no puede renovar el token de acceso
(POST /api/auth/refresh). El token de acceso ya emitido sigue valiendo hasta que expire
(máximo 1 hora). La lógica de códigos vive en app/services/totp.py.
"""
import json
import time
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.neon import get_db
from app.models_db import ActionLog, AdminSession, AdminUser
from app.services import totp
from app.services.admin_sessions import describe_agent, session_active
from .auth import decode_token, issue_session, require_permission, verify_password

router = APIRouter(prefix="/api/auth", tags=["Doble factor y Sesiones"])
_bearer = HTTPBearer()
ISSUER = "WellQ Admin"


# ── Utilidades ────────────────────────────────────────────────────────────────

async def _me(credentials: HTTPAuthorizationCredentials = Depends(_bearer),
              db: AsyncSession = Depends(get_db)) -> tuple[AdminUser, dict]:
    payload = decode_token(credentials.credentials)
    if payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Token inválido")
    user = (await db.execute(select(AdminUser).where(AdminUser.email == payload.get("email")))).scalars().first()
    if not user:
        raise HTTPException(status_code=401, detail="Usuario no encontrado")
    if user.status != "active":
        raise HTTPException(status_code=403, detail="Usuario inactivo")
    return user, payload


def _log(db: AsyncSession, actor_id: str, actor_email: str, user: AdminUser, change: str) -> None:
    db.add(ActionLog(
        log_id=f"log-{uuid.uuid4().hex[:10]}",
        admin_user_id=actor_id, admin_email=actor_email,
        action="UPDATE", entity_type="admin_user", entity_id=user.user_id, entity_name=user.email,
        changes=json.dumps({"after": {"two_factor": change}}, ensure_ascii=False),
    ))


def _secret_of(user: AdminUser) -> str:
    if not user.totp_secret:
        raise HTTPException(status_code=409, detail="Primero genera la configuración del doble factor.")
    try:
        return totp.decrypt_secret(user.totp_secret, settings.jwt_secret)
    except ValueError:
        raise HTTPException(status_code=409,
                            detail="No se pudo leer el secreto del doble factor. Pide a un administrador que lo restablezca.")


def _check_not_locked(user: AdminUser) -> None:
    now = datetime.utcnow()
    if totp.is_locked(user.totp_locked_until, now):
        raise HTTPException(status_code=429,
                            detail=f"Demasiados intentos fallidos. Intenta de nuevo en {totp.minutes_left(user.totp_locked_until, now)} min.")


def _try_second_factor(user: AdminUser, code: str) -> Optional[str]:
    """
    Prueba el código como TOTP y, si no sirve, como código de respaldo.
    Devuelve 'totp' o 'backup' (y deja el usuario actualizado, sin confirmar) o None.
    """
    counter = totp.verify_totp(_secret_of(user), code, time.time(), user.totp_last_counter)
    if counter is not None:
        user.totp_last_counter = counter
        return "totp"
    hashes = json.loads(user.totp_backup_hashes or "[]")
    remaining = totp.consume_backup_code(hashes, code)
    if remaining is not None:
        user.totp_backup_hashes = json.dumps(remaining)
        return "backup"
    return None


async def _require_second_factor(db: AsyncSession, user: AdminUser, code: str) -> str:
    """Valida el código (con bloqueo por intentos). Lanza 401/429 si no corresponde."""
    _check_not_locked(user)
    kind = _try_second_factor(user, code)
    if kind is None:
        user.totp_fail_count, user.totp_locked_until = totp.after_failure(user.totp_fail_count, datetime.utcnow())
        db.add(user)
        await db.commit()
        _check_not_locked(user)
        raise HTTPException(status_code=401, detail="Código incorrecto.")
    user.totp_fail_count, user.totp_locked_until = 0, None
    return kind


def _backup_remaining(user: AdminUser) -> int:
    try:
        return len(json.loads(user.totp_backup_hashes or "[]"))
    except ValueError:
        return 0


# ── Doble factor ──────────────────────────────────────────────────────────────

@router.get("/2fa/status", summary="Estado del doble factor del usuario actual")
async def two_factor_status(ctx: tuple = Depends(_me)):
    user, _ = ctx
    return {"status": "success", "data": {
        "enabled": bool(user.totp_enabled),
        "pendingSetup": bool(user.totp_secret) and not user.totp_enabled,
        "backupCodesRemaining": _backup_remaining(user) if user.totp_enabled else 0,
    }}


@router.post("/2fa/setup", summary="Generar el secreto del doble factor")
async def two_factor_setup(db: AsyncSession = Depends(get_db), ctx: tuple = Depends(_me)):
    user, _ = ctx
    if user.totp_enabled:
        raise HTTPException(status_code=409, detail="El doble factor ya está activo. Desactívalo primero para reconfigurarlo.")
    secret = totp.generate_secret()
    user.totp_secret = totp.encrypt_secret(secret, settings.jwt_secret)
    user.totp_last_counter = None
    db.add(user)
    await db.commit()
    return {"status": "success", "data": {
        "secret": totp.format_secret(secret),
        "otpauthUri": totp.provisioning_uri(secret, user.email, ISSUER),
        "issuer": ISSUER,
        "account": user.email,
    }}


class CodeBody(BaseModel):
    code: str = Field(..., min_length=4, max_length=20)


class PasswordCodeBody(CodeBody):
    password: str = Field(..., min_length=1)


@router.post("/2fa/enable", summary="Confirmar y activar el doble factor")
async def two_factor_enable(body: CodeBody, db: AsyncSession = Depends(get_db), ctx: tuple = Depends(_me)):
    user, _ = ctx
    if user.totp_enabled:
        raise HTTPException(status_code=409, detail="El doble factor ya está activo.")
    _check_not_locked(user)
    counter = totp.verify_totp(_secret_of(user), body.code, time.time(), None)
    if counter is None:
        user.totp_fail_count, user.totp_locked_until = totp.after_failure(user.totp_fail_count, datetime.utcnow())
        db.add(user)
        await db.commit()
        _check_not_locked(user)
        raise HTTPException(status_code=422, detail="El código no coincide. Revisa la hora de tu teléfono e inténtalo de nuevo.")
    plain, hashes = totp.generate_backup_codes()
    user.totp_enabled = True
    user.totp_last_counter = counter
    user.totp_backup_hashes = json.dumps(hashes)
    user.totp_fail_count, user.totp_locked_until = 0, None
    db.add(user)
    _log(db, user.user_id, user.email, user, "enabled")
    await db.commit()
    return {"status": "success", "data": {"backupCodes": plain}}


@router.post("/2fa/disable", summary="Desactivar el doble factor")
async def two_factor_disable(body: PasswordCodeBody, db: AsyncSession = Depends(get_db), ctx: tuple = Depends(_me)):
    user, _ = ctx
    if not user.totp_enabled:
        raise HTTPException(status_code=409, detail="El doble factor no está activo.")
    if not verify_password(body.password, user.password_hash or ""):
        raise HTTPException(status_code=401, detail="Contraseña incorrecta.")
    await _require_second_factor(db, user, body.code)
    user.totp_enabled, user.totp_secret, user.totp_backup_hashes = False, None, None
    user.totp_last_counter, user.totp_fail_count, user.totp_locked_until = None, 0, None
    db.add(user)
    _log(db, user.user_id, user.email, user, "disabled")
    await db.commit()
    return {"status": "success", "data": {"enabled": False}}


@router.post("/2fa/backup-codes", summary="Regenerar los códigos de respaldo")
async def regenerate_backup_codes(body: PasswordCodeBody, db: AsyncSession = Depends(get_db), ctx: tuple = Depends(_me)):
    user, _ = ctx
    if not user.totp_enabled:
        raise HTTPException(status_code=409, detail="El doble factor no está activo.")
    if not verify_password(body.password, user.password_hash or ""):
        raise HTTPException(status_code=401, detail="Contraseña incorrecta.")
    await _require_second_factor(db, user, body.code)
    plain, hashes = totp.generate_backup_codes()
    user.totp_backup_hashes = json.dumps(hashes)
    db.add(user)
    _log(db, user.user_id, user.email, user, "backup_codes_regenerated")
    await db.commit()
    return {"status": "success", "data": {"backupCodes": plain}}


@router.post("/2fa/verify", summary="Segundo paso del inicio de sesión")
async def two_factor_verify(request: Request, body: dict = Body(...), db: AsyncSession = Depends(get_db)):
    challenge = decode_token(body.get("challenge_token", ""))
    if challenge.get("type") != "2fa":
        raise HTTPException(status_code=401, detail="Desafío inválido o vencido. Inicia sesión nuevamente.")
    user = (await db.execute(select(AdminUser).where(AdminUser.email == challenge.get("email")))).scalars().first()
    if not user or user.status != "active" or not user.totp_enabled:
        raise HTTPException(status_code=401, detail="Desafío inválido o vencido. Inicia sesión nuevamente.")
    code = str(body.get("code", ""))
    kind = await _require_second_factor(db, user, code)
    if kind == "backup":
        _log(db, user.user_id, user.email, user, "backup_code_used")
    return await issue_session(db, user, request, two_factor=True)


@router.post("/2fa/reset/{user_id}", summary="Quitar el doble factor a otro usuario",
             dependencies=[Depends(require_permission("users.manage"))])
async def two_factor_reset(user_id: str = Path(...), db: AsyncSession = Depends(get_db), ctx: tuple = Depends(_me)):
    actor, _ = ctx
    target = (await db.execute(select(AdminUser).where(AdminUser.user_id == user_id))).scalars().first()
    if not target:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    target.totp_enabled, target.totp_secret, target.totp_backup_hashes = False, None, None
    target.totp_last_counter, target.totp_fail_count, target.totp_locked_until = None, 0, None
    db.add(target)
    now = datetime.utcnow()
    sessions = (await db.execute(select(AdminSession).where(AdminSession.email == target.email))).scalars().all()
    revoked = 0
    for s in sessions:
        if session_active(s.expires_at, s.revoked_at, now):
            s.revoked_at = now
            db.add(s)
            revoked += 1
    _log(db, actor.user_id, actor.email, target, "reset_by_admin")
    await db.commit()
    return {"status": "success", "data": {"userId": target.user_id, "sessionsRevoked": revoked}}


# ── Sesiones ──────────────────────────────────────────────────────────────────

def _serialize_session(s: AdminSession, current_sid: Optional[str]) -> dict:
    iso = lambda v: v.isoformat() + "Z" if v else None   # noqa: E731
    return {
        "id": s.session_id,
        "device": describe_agent(s.user_agent),
        "ip": s.ip,
        "createdAt": iso(s.created_at),
        "lastSeenAt": iso(s.last_seen_at),
        "expiresAt": iso(s.expires_at),
        "twoFactor": bool(s.two_factor),
        "current": bool(current_sid) and s.session_id == current_sid,
    }


@router.get("/sessions", summary="Sesiones activas del usuario actual")
async def list_sessions(db: AsyncSession = Depends(get_db), ctx: tuple = Depends(_me)):
    user, payload = ctx
    now = datetime.utcnow()
    rows = (await db.execute(select(AdminSession).where(AdminSession.email == user.email))).scalars().all()
    active = [s for s in rows if session_active(s.expires_at, s.revoked_at, now)]
    active.sort(key=lambda s: s.last_seen_at or s.created_at, reverse=True)
    return {"status": "success", "data": [_serialize_session(s, payload.get("sid")) for s in active]}


@router.post("/sessions/revoke-others", summary="Cerrar todas las sesiones menos la actual")
async def revoke_other_sessions(db: AsyncSession = Depends(get_db), ctx: tuple = Depends(_me)):
    user, payload = ctx
    sid = payload.get("sid")
    if not sid:
        raise HTTPException(status_code=409, detail="Esta sesión es anterior a la gestión de sesiones. Cierra sesión y vuelve a entrar para usar esta función.")
    now = datetime.utcnow()
    rows = (await db.execute(select(AdminSession).where(AdminSession.email == user.email))).scalars().all()
    count = 0
    for s in rows:
        if s.session_id != sid and session_active(s.expires_at, s.revoked_at, now):
            s.revoked_at = now
            db.add(s)
            count += 1
    await db.commit()
    return {"status": "success", "data": {"revoked": count}}


@router.post("/sessions/{session_id}/revoke", summary="Cerrar una sesión propia")
async def revoke_session(session_id: str = Path(...), db: AsyncSession = Depends(get_db), ctx: tuple = Depends(_me)):
    user, _ = ctx
    s = (await db.execute(select(AdminSession).where(
        AdminSession.session_id == session_id, AdminSession.email == user.email))).scalars().first()
    if not s:
        raise HTTPException(status_code=404, detail="Sesión no encontrada")
    if s.revoked_at:
        raise HTTPException(status_code=409, detail="La sesión ya estaba cerrada.")
    s.revoked_at = datetime.utcnow()
    db.add(s)
    await db.commit()
    return {"status": "success", "data": {"id": session_id}}
