import uuid
from datetime import datetime, timedelta

import bcrypt as _bcrypt
from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.config import settings
from app.db.neon import get_db
from app.models_db import AdminUser, AdminSession, Permission, RolePermission, Role   # ← Role añadido
from app.services.admin_sessions import describe_agent, session_active   # Mejora #18

# ── Configuración ──────────────────────────────────────────────────────────────
# JWT_SECRET se lee desde backend/.env. No usar valores de ejemplo en ambientes reales.
JWT_SECRET      = settings.jwt_secret
JWT_ALGORITHM   = settings.jwt_algorithm
ACCESS_EXPIRES  = timedelta(hours=1)
REFRESH_EXPIRES = timedelta(days=7)
TWO_FACTOR_CHALLENGE_EXPIRES = timedelta(minutes=5)   # Mejora #18: tiempo para ingresar el código

router = APIRouter(prefix="/api/auth", tags=["Autenticación y Seguridad"])

_bearer = HTTPBearer()


# ── Helpers de contraseña ─────────────────────────────────────────────────────
# Se usa bcrypt directo (sin passlib) para evitar el error:
#   ValueError: password cannot be longer than 72 bytes
# que ocurre porque passlib >= 1.7.4 con bcrypt >= 4.x tiene un bug interno.

def hash_password(password: str) -> str:
    """Genera hash bcrypt. Trunca a 72 bytes (límite real de bcrypt)."""
    return _bcrypt.hashpw(password[:72].encode(), _bcrypt.gensalt(12)).decode()

def verify_password(plain: str, hashed: str) -> bool:
    """Verifica contraseña contra hash bcrypt. Retorna False si hash es None/vacío."""
    if not hashed:
        return False
    return _bcrypt.checkpw(plain[:72].encode(), hashed.encode())


def create_token(data: dict, expires_delta: timedelta) -> str:
    payload = data.copy()
    payload["exp"] = datetime.utcnow() + expires_delta
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido o expirado"
        )


# ── RBAC: user_payload incluye permissions ────────────────────────────────────
def user_payload(user: AdminUser, permissions: list[str] = []) -> dict:
    """
    Construye el payload del JWT.
    'permissions' es la lista de permission keys del rol del usuario.
    Se incluye en el token para que el frontend filtre el sidebar
    y para que require_permission() funcione sin query a BD en cada request.
    """
    return {
        "user_id":     user.user_id,
        "email":       user.email,
        "role":        user.role,
        "permissions": permissions,
    }


# ── RBAC: carga los permission keys desde la BD ───────────────────────────────
async def _load_user_permissions(user: AdminUser, db: AsyncSession) -> list[str]:
    """
    Retorna la lista de permission keys del usuario.

    Backward compatibility:
      - Si role_id es NULL y role == 'super_admin'  → todos los permisos del catálogo.
      - Si role_id es NULL y role != 'super_admin'  → sin permisos (lista vacía).
      - Si role_id está seteado                      → permisos del rol RBAC via JOIN.
    """
    # Usuarios legacy super_admin que aún no tienen role_id asignado
    if user.role_id is None and user.role == "super_admin":
        result = await db.execute(select(Permission.key))
        return list(result.scalars().all())

    # Usuarios sin rol RBAC y sin legacy super_admin
    if user.role_id is None:
        return []

    # JOIN: permissions ← role_permissions WHERE role_id = user.role_id
    q = (
        select(Permission.key)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .where(RolePermission.role_id == user.role_id)
    )
    result = await db.execute(q)
    return list(result.scalars().all())


# ── NUEVO: carga el nombre del rol RBAC ───────────────────────────────────────
async def _load_role_name(user: AdminUser, db: AsyncSession) -> str:
    """
    Retorna el nombre legible del rol RBAC (ej: 'DevOps', 'Billing Manager').

    Fallback: si no tiene role_id, devuelve el campo legacy 'role' (string).
    Esto hace que el sidebar siempre muestre algo útil:
      - Con RBAC:   "DevOps" (nombre del rol de la tabla roles)
      - Sin RBAC:   "super_admin" (valor del campo texto legacy)
    """
    if user.role_id is None:
        return user.role or ""

    result = await db.execute(select(Role.name).where(Role.id == user.role_id))
    name = result.scalar_one_or_none()
    return name or user.role or ""


def _legacy_role_from_rbac(role: Role) -> str:
    role_name = (role.name or "").strip().lower()
    if role_name in {"super admin", "super administrator"}:
        return "super_admin"
    return "admin"


async def _load_register_role(role_id, db: AsyncSession) -> tuple[int | None, str, str | None]:
    if role_id in (None, ""):
        return None, "admin", None

    try:
        role_id_int = int(role_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="role_id inválido")

    result = await db.execute(select(Role).where(Role.id == role_id_int))
    role = result.scalars().first()
    if not role:
        raise HTTPException(status_code=400, detail=f"El role_id {role_id_int} no existe en la tabla de roles")

    return role_id_int, _legacy_role_from_rbac(role), role.name


# ==============================================================================
# ENDPOINT: #11 - POST /api/auth/register
# Descripción: Crear nuevo usuario administrador
# ==============================================================================
@router.post(
    "/register",
    summary="Crear nuevo usuario administrador",
    status_code=status.HTTP_201_CREATED,
)
async def register(body: dict = Body(...), db: AsyncSession = Depends(get_db)):
    email     = body.get("email", "").strip().lower()
    password  = body.get("password", "")
    full_name = body.get("full_name", "")
    role_id, role, role_name = await _load_register_role(body.get("role_id"), db)

    if not email or not password or not full_name:
        raise HTTPException(status_code=400, detail="email, password y full_name son requeridos")

    # Verificar que no exista
    result = await db.execute(select(AdminUser).where(AdminUser.email == email))
    if result.scalars().first():
        raise HTTPException(status_code=409, detail="Ya existe un usuario con ese email")

    new_user = AdminUser(
        user_id       = f"usr-{uuid.uuid4().hex[:8]}",
        full_name     = full_name,
        email         = email,
        role          = role,
        role_id       = role_id,
        status        = "active",
        password_hash = hash_password(password),
    )
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)

    return {
        "status":  "success",
        "message": "Usuario creado correctamente",
        "data": {
            "user_id":   new_user.user_id,
            "email":     new_user.email,
            "full_name": new_user.full_name,
            "role":      new_user.role,
            "role_id":   new_user.role_id,
            "role_name": role_name or new_user.role,
        }
    }


# ── Mejora #18: emisión de tokens con sesión registrada ───────────────────────
def client_ip(request: Request) -> str | None:
    """IP del cliente (Cloud Run entrega la original en X-Forwarded-For)."""
    forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    return forwarded or (request.client.host if request.client else None)


async def issue_session(db: AsyncSession, user: AdminUser, request: Request, two_factor: bool) -> dict:
    """
    Registra la sesión y devuelve la respuesta de login (tokens + usuario).
    Lo usan POST /login (sin 2FA) y POST /2fa/verify (con 2FA). Los tokens llevan "sid".
    """
    now = datetime.utcnow()
    user.last_login = now
    db.add(user)
    session = AdminSession(
        session_id=f"ses_{uuid.uuid4().hex[:16]}",
        user_id=user.user_id,
        email=user.email,
        created_at=now,
        last_seen_at=now,
        expires_at=now + REFRESH_EXPIRES,
        ip=client_ip(request),
        user_agent=(request.headers.get("user-agent") or "")[:300] or None,
        two_factor=two_factor,
    )
    db.add(session)
    await db.commit()
    # RBAC: refresh necesario para que user.role_id no esté expirado post-commit
    await db.refresh(user)

    permissions   = await _load_user_permissions(user, db)
    role_name     = await _load_role_name(user, db)
    payload       = {**user_payload(user, permissions), "sid": session.session_id}
    access_token  = create_token({**payload, "type": "access"},  ACCESS_EXPIRES)
    refresh_token = create_token({**payload, "type": "refresh"}, REFRESH_EXPIRES)

    return {
        "status":  "success",
        "message": "Autenticación exitosa",
        "data": {
            "access_token":  access_token,
            "refresh_token": refresh_token,
            "token_type":    "Bearer",
            "expires_in":    int(ACCESS_EXPIRES.total_seconds()),
            "user": {
                "user_id":     user.user_id,
                "email":       user.email,
                "full_name":   user.full_name,
                "role":        user.role,           # legacy string (backward compat)
                "role_name":   role_name,            # nombre real del rol RBAC para el sidebar
                "role_id":     user.role_id,         # ID para comparaciones en el frontend
                "permissions": permissions,
                "two_factor_enabled": bool(user.totp_enabled),
            }
        }
    }


# ==============================================================================
# ENDPOINT: #12 - POST /api/auth/login
# Descripción: Valida usuario y contraseña; entrega token JWT real
# ==============================================================================
@router.post(
    "/login",
    summary="Valida usuario y contraseña; entrega token JWT real",
    status_code=status.HTTP_200_OK,
)
async def login(request: Request, body: dict = Body(...), db: AsyncSession = Depends(get_db)):
    email    = body.get("email", "").strip().lower()
    password = body.get("password", "")

    if not email or not password:
        raise HTTPException(status_code=400, detail="email y password son requeridos")

    result = await db.execute(select(AdminUser).where(AdminUser.email == email))
    user: AdminUser | None = result.scalars().first()

    if not user or not user.password_hash:
        raise HTTPException(status_code=401, detail="Credenciales inválidas")

    if not verify_password(password, user.password_hash):
        raise HTTPException(status_code=401, detail="Credenciales inválidas")

    if user.status != "active":
        raise HTTPException(status_code=403, detail="Usuario inactivo")

    # Mejora #18: con doble factor activo no se entregan tokens todavía; se pide el código
    if user.totp_enabled:
        challenge = create_token(
            {"user_id": user.user_id, "email": user.email, "type": "2fa"},
            TWO_FACTOR_CHALLENGE_EXPIRES,
        )
        return {
            "status":  "2fa_required",
            "message": "Ingresa el código de tu aplicación de autenticación",
            "data": {
                "two_factor_required": True,
                "challenge_token": challenge,
                "expires_in": int(TWO_FACTOR_CHALLENGE_EXPIRES.total_seconds()),
            },
        }

    return await issue_session(db, user, request, two_factor=False)


# ==============================================================================
# ENDPOINT: #13 - POST /api/auth/logout
# Descripción: Registrar cierre de sesión
# ==============================================================================
@router.post(
    "/logout",
    summary="Registrar cierre de sesión",
    status_code=status.HTTP_200_OK,
)
async def logout(body: dict = Body(default={}), db: AsyncSession = Depends(get_db)):
    # JWT es stateless: el frontend elimina el token del lado cliente.
    # Mejora #18: si llega el refresh token, además se revoca su sesión registrada,
    # así ese refresh token ya no sirve para renovar el acceso.
    try:
        payload = decode_token((body or {}).get("refresh_token", ""))
        sid = payload.get("sid")
        if sid:
            sess = (await db.execute(select(AdminSession).where(AdminSession.session_id == sid))).scalars().first()
            if sess and not sess.revoked_at:
                sess.revoked_at = datetime.utcnow()
                db.add(sess)
                await db.commit()
    except HTTPException:
        pass   # token ausente o vencido: no hay sesión que cerrar
    return {
        "status":  "success",
        "message": "Sesión cerrada correctamente en el servidor.",
        "action":  "clear_local_storage"
    }


# ==============================================================================
# ENDPOINT: #14 - POST /api/auth/refresh
# Descripción: Renueva el access token usando el refresh token
# ==============================================================================
@router.post(
    "/refresh",
    summary="Renueva el access token usando el refresh token",
    status_code=status.HTTP_200_OK,
)
async def refresh_token(body: dict = Body(...), db: AsyncSession = Depends(get_db)):
    token = body.get("refresh_token", "")
    if not token:
        raise HTTPException(status_code=400, detail="refresh_token es requerido")

    payload = decode_token(token)   # lanza 401 si es inválido

    if payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Token inválido")

    # Mejora #18: si el token pertenece a una sesión registrada, debe seguir vigente
    sid = payload.get("sid")
    sess = None
    if sid:
        sess = (await db.execute(select(AdminSession).where(AdminSession.session_id == sid))).scalars().first()
        if not sess or not session_active(sess.expires_at, sess.revoked_at, datetime.utcnow()):
            raise HTTPException(status_code=401, detail="La sesión fue cerrada. Inicia sesión nuevamente.")

    # Verificar que el usuario sigue activo en BD
    result = await db.execute(select(AdminUser).where(AdminUser.email == payload["email"]))
    user: AdminUser | None = result.scalars().first()

    if not user or user.status != "active":
        raise HTTPException(status_code=401, detail="Usuario no encontrado o inactivo")

    # RBAC: recargar permissions actuales — pueden haber cambiado desde el último login
    permissions = await _load_user_permissions(user, db)
    new_payload = {**user_payload(user, permissions), **({"sid": sid} if sid else {})}
    new_access  = create_token({**new_payload, "type": "access"}, ACCESS_EXPIRES)
    if sess:
        sess.last_seen_at = datetime.utcnow()
        db.add(sess)
        await db.commit()

    return {
        "status":  "success",
        "message": "Token de sesión renovado",
        "data": {
            "access_token": new_access,
            "token_type":   "Bearer",
            "expires_in":   int(ACCESS_EXPIRES.total_seconds()),
        }
    }


# ==============================================================================
# ENDPOINT: #15 - GET /api/auth/me
# Descripción: Obtener perfil del usuario autenticado (requiere Bearer token)
# ==============================================================================
@router.get(
    "/me",
    summary="Obtener perfil del usuario autenticado (requiere Bearer token)",
)
async def get_me(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
):
    payload = decode_token(credentials.credentials)

    if payload.get("type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido")

    result = await db.execute(select(AdminUser).where(AdminUser.email == payload["email"]))
    user: AdminUser | None = result.scalars().first()

    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    # Cargar permissions y nombre del rol frescos desde BD
    permissions = await _load_user_permissions(user, db)
    role_name   = await _load_role_name(user, db)           # ← NUEVO

    return {
        "user_id":     user.user_id,
        "email":       user.email,
        "full_name":   user.full_name,
        "role":        user.role,           # legacy string (backward compat)
        "role_name":   role_name,            # ← NUEVO: nombre real del rol RBAC para el sidebar
        "role_id":     user.role_id,         # ← NUEVO: ID del rol
        "status":      user.status,
        "last_login":  user.last_login.isoformat() if user.last_login else None,
        "permissions": permissions,
        "two_factor_enabled": bool(user.totp_enabled),
    }


# ── Dependencies exportables (usados por otros routers) ───────────────────────

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
) -> AdminUser:
    """
    Dependency reutilizable: valida el access token y retorna el AdminUser activo.

    Uso en cualquier router:
        from app.auth import get_current_user

        @router.get("/algo")
        async def algo(current_user: AdminUser = Depends(get_current_user)):
            ...
    """
    payload = decode_token(credentials.credentials)

    if payload.get("type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido")

    result = await db.execute(select(AdminUser).where(AdminUser.email == payload["email"]))
    user: AdminUser | None = result.scalars().first()

    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuario no encontrado")

    if user.status != "active":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Usuario inactivo")

    return user


def require_permission(permission_key: str):
    """
    Dependency factory: verifica que el usuario tenga un permiso específico.
    Lee los permissions desde el JWT (cargados en login/refresh) — sin query a BD.

    Uso:
        @router.get("/billing", dependencies=[Depends(require_permission("billing.view"))])
        async def billing_view(...):
            ...

    Lanza HTTP 403 si el usuario no tiene el permiso requerido.
    Backward compat: super_admin legacy sin permissions en JWT → acceso total.
    """
    async def _check(
        credentials: HTTPAuthorizationCredentials = Depends(_bearer),
    ) -> None:
        payload = decode_token(credentials.credentials)

        if payload.get("type") != "access":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token inválido",
            )

        perms = payload.get("permissions", [])
        role  = payload.get("role", "")

        # Backward compat: super_admin que aún no pasó por login con RBAC activo
        if role == "super_admin" and not perms:
            return

        if permission_key not in perms:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Acceso denegado: se requiere el permiso '{permission_key}'",
            )

    return _check
