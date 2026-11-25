import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, timedelta

import jwt
from fastapi import Depends, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.db import get_db
from app.core.errors import ApiError
from app.core.security import (
    access_token,
    csrf_token,
    decode_access,
    dummy_hash,
    now,
    password_valid,
    token_hash,
)
from app.models import AuthTokenFamily, EmployeeProfile, KBGrant, KnowledgeBase, RefreshToken, User

bearer = HTTPBearer(auto_error=False)
COOKIE_PATH = "/api/v1/auth"


@dataclass
class AuthContext:
    user: User
    family: AuthTokenFamily
    version: int


def unauthorized(*, clear: bool = False):
    return ApiError(401, "AUTH_REQUIRED", "登录状态已失效，请重新登录", clear_refresh=clear)


def check_origin(request: Request):
    origin = request.headers.get("origin", "").rstrip("/")
    if origin not in request.app.state.settings.origins:
        raise ApiError(403, "ORIGIN_REJECTED", "请求来源不受信任")


async def current_context(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> AuthContext:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise unauthorized()
    try:
        claims = decode_access(credentials.credentials, request.app.state.settings)
        user_id, family_id = uuid.UUID(claims["sub"]), uuid.UUID(claims["sid"])
    except (jwt.InvalidTokenError, ValueError, KeyError, TypeError):
        raise unauthorized() from None
    row = (
        await db.execute(
            select(User, AuthTokenFamily)
            .join(AuthTokenFamily, AuthTokenFamily.user_id == User.id)
            .where(User.id == user_id, AuthTokenFamily.id == family_id)
        )
    ).one_or_none()
    if row is None:
        raise unauthorized()
    user, family = row
    if (
        not user.is_active
        or family.revoked_at is not None
        or family.expires_at <= now()
        or claims["ver"] != user.auth_version
        or family.auth_version != user.auth_version
    ):
        raise unauthorized()
    return AuthContext(user, family, claims["ver"])


async def ready_context(context: AuthContext = Depends(current_context)) -> AuthContext:
    if context.user.must_change_password:
        raise ApiError(403, "PASSWORD_CHANGE_REQUIRED", "请先修改临时密码")
    return context


async def admin_context(context: AuthContext = Depends(ready_context)) -> AuthContext:
    if context.user.role != "admin":
        raise ApiError(403, "ADMIN_REQUIRED", "此操作需要管理员权限")
    return context


async def allowed_kbs(db: AsyncSession, user: User) -> list[KnowledgeBase]:
    query = select(KnowledgeBase).where(KnowledgeBase.is_active.is_(True))
    if user.role != "admin":
        query = query.join(KBGrant, KBGrant.kb_id == KnowledgeBase.id).where(
            KBGrant.user_id == user.id, KBGrant.revoked_at.is_(None)
        )
    return list(
        (await db.scalars(query.order_by(KnowledgeBase.created_at, KnowledgeBase.id))).all()
    )


def kb_out(kb: KnowledgeBase) -> dict:
    return {
        "id": str(kb.id),
        "name": kb.name,
        "description": kb.description,
        "is_active": kb.is_active,
        "revision": kb.revision,
    }


def profile_out(profile: EmployeeProfile | None) -> dict | None:
    if profile is None:
        return None
    return {
        "display_name": profile.display_name,
        "department": profile.department,
        "grade": profile.grade,
        "revision": profile.revision,
    }


def user_out(user: User, profile: EmployeeProfile | None) -> dict:
    return {
        "id": str(user.id),
        "username": user.username,
        "role": user.role,
        "is_active": user.is_active,
        "must_change_password": user.must_change_password,
        "profile": profile_out(profile),
    }


async def me(db: AsyncSession, user: User) -> dict:
    profile = await db.get(EmployeeProfile, user.id)
    return {
        **user_out(user, profile),
        "knowledge_bases": [kb_out(k) for k in await allowed_kbs(db, user)],
    }


def put_cookie(response: Response, raw: str, family: AuthTokenFamily, settings: Settings):
    response.set_cookie(
        settings.refresh_cookie,
        raw,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path=COOKIE_PATH,
        max_age=max(0, int((family.expires_at - now()).total_seconds())),
        expires=family.expires_at.astimezone(UTC),
    )


def clear_cookie(response: Response, settings: Settings):
    response.delete_cookie(
        settings.refresh_cookie,
        path=COOKIE_PATH,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
    )


def token_result(user: User, family: AuthTokenFamily, settings: Settings) -> dict:
    return {
        "access_token": access_token(user, family, settings),
        "token_type": "bearer",
        "expires_in": min(
            settings.access_token_minutes * 60,
            max(0, int((family.expires_at - now()).total_seconds())),
        ),
    }


async def login(db: AsyncSession, username: str, password: str, settings: Settings):
    user = await db.scalar(select(User).where(User.username == username).with_for_update())
    valid = password_valid(password, user.password_hash if user else dummy_hash())
    if user is None or not valid or not user.is_active:
        raise ApiError(401, "INVALID_CREDENTIALS", "账号或密码不正确")
    family = AuthTokenFamily(
        id=uuid.uuid4(),
        user_id=user.id,
        auth_version=user.auth_version,
        expires_at=now() + timedelta(days=settings.refresh_token_days),
    )
    raw = secrets.token_urlsafe(48)
    db.add(family)
    await db.flush()
    db.add(
        RefreshToken(family_id=family.id, token_hash=token_hash(raw), expires_at=family.expires_at)
    )
    await db.commit()
    return user, family, raw


async def refresh_record(db: AsyncSession, raw: str | None, *, lock: bool = False):
    if not raw or len(raw) > 256:
        return None
    row = (
        await db.execute(
            select(RefreshToken, AuthTokenFamily, User)
            .join(AuthTokenFamily, RefreshToken.family_id == AuthTokenFamily.id)
            .join(User, AuthTokenFamily.user_id == User.id)
            .where(RefreshToken.token_hash == token_hash(raw))
        )
    ).one_or_none()
    if row is None or not lock:
        return row
    token, family, user = row
    # All security writes lock in user -> family -> token order.
    user = await db.scalar(
        select(User)
        .where(User.id == user.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    family = await db.scalar(
        select(AuthTokenFamily)
        .where(AuthTokenFamily.id == family.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    token = await db.scalar(
        select(RefreshToken)
        .where(RefreshToken.id == token.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return token, family, user


def usable(row) -> bool:
    if row is None:
        return False
    token, family, user = row
    return bool(
        user.is_active
        and family.revoked_at is None
        and family.expires_at > now()
        and token.expires_at > now()
        and family.auth_version == user.auth_version
    )


def check_csrf(request: Request, family: AuthTokenFamily):
    check_origin(request)
    given = request.headers.get("x-csrf-token", "")
    if not hmac.compare_digest(
        given.encode(), csrf_token(family.id, request.app.state.settings).encode()
    ):
        raise ApiError(403, "CSRF_INVALID", "请求校验失败，请刷新页面后重试")


async def rotate(request: Request, db: AsyncSession):
    settings = request.app.state.settings
    check_origin(request)
    row = await refresh_record(db, request.cookies.get(settings.refresh_cookie), lock=True)
    if row is None:
        raise unauthorized(clear=True)
    token, family, user = row
    check_csrf(request, family)
    if token.consumed_at is not None:
        family.revoked_at, family.revoke_reason = now(), "refresh_replay"
        await db.commit()
        raise ApiError(401, "REFRESH_REPLAY", "登录凭据已失效，请重新登录", clear_refresh=True)
    if not usable(row):
        raise unauthorized(clear=True)
    raw = secrets.token_urlsafe(48)
    replacement = RefreshToken(
        id=uuid.uuid4(),
        family_id=family.id,
        token_hash=token_hash(raw),
        expires_at=family.expires_at,
    )
    db.add(replacement)
    await db.flush()
    token.consumed_at, token.replaced_by = now(), replacement.id
    await db.commit()
    return user, family, raw


async def revoke_all(db: AsyncSession, user: User, reason: str):
    user.auth_version += 1
    await db.execute(
        update(AuthTokenFamily)
        .where(AuthTokenFamily.user_id == user.id, AuthTokenFamily.revoked_at.is_(None))
        .values(revoked_at=now(), revoke_reason=reason)
    )
