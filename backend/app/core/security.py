import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from functools import lru_cache

import jwt
from pwdlib import PasswordHash

from app.core.config import Settings
from app.models import AuthTokenFamily, User

hasher = PasswordHash.recommended()


def now() -> datetime:
    return datetime.now(UTC)


def token_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


@lru_cache(maxsize=1)
def dummy_hash() -> str:
    return hasher.hash(secrets.token_urlsafe(32))


def password_valid(value: str, password_hash: str) -> bool:
    try:
        return hasher.verify(value, password_hash)
    except (ValueError, TypeError):
        return False


def csrf_token(family_id: uuid.UUID, settings: Settings) -> str:
    return hmac.new(
        settings.jwt_secret_key.get_secret_value().encode(),
        f"csrf:{family_id}".encode(),
        hashlib.sha256,
    ).hexdigest()


def access_token(user: User, family: AuthTokenFamily, settings: Settings) -> str:
    current = now()
    expiry = min(current + timedelta(minutes=settings.access_token_minutes), family.expires_at)
    return jwt.encode(
        {
            "sub": str(user.id),
            "sid": str(family.id),
            "jti": str(uuid.uuid4()),
            "ver": user.auth_version,
            "iat": current,
            "exp": expiry,
            "iss": settings.jwt_issuer,
            "aud": settings.jwt_audience,
        },
        settings.jwt_secret_key.get_secret_value(),
        algorithm="HS256",
    )


def decode_access(value: str, settings: Settings) -> dict:
    return jwt.decode(
        value,
        settings.jwt_secret_key.get_secret_value(),
        algorithms=["HS256"],
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
        options={"require": ["sub", "sid", "jti", "ver", "iat", "exp", "iss", "aud"]},
    )
