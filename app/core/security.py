from datetime import datetime, timedelta, timezone
from functools import lru_cache
import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import jwt
from jwt.exceptions import InvalidTokenError
from pwdlib import PasswordHash
from pwdlib.exceptions import UnknownHashError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.modules.auth.model import User


password_hasher = PasswordHash.recommended()
bearer = HTTPBearer(
    scheme_name="BearerAuth", bearerFormat="JWT", auto_error=False,
    description="Cole apenas o access_token retornado por POST /auth/login.",
)


def validate_security_settings() -> None:
    if len(settings.jwt_secret_key.get_secret_value().encode("utf-8")) < 32:
        raise RuntimeError("Configure JWT_SECRET_KEY no .env com uma chave aleatória de pelo menos 32 bytes.")


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return password_hasher.verify(password, password_hash)
    except UnknownHashError:
        return False


@lru_cache(maxsize=1)
def dummy_password_hash() -> str:
    """Perform a real password check even when the username does not exist."""
    return hash_password(secrets.token_urlsafe(32))


def create_access_token(user_id: int) -> str:
    validate_security_settings()
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {"sub": str(user_id), "iat": now,
         "exp": now + timedelta(minutes=settings.jwt_access_token_expire_minutes)},
        settings.jwt_secret_key.get_secret_value(), algorithm=settings.jwt_algorithm,
    )


def unauthorized(detail: str = "Token inválido ou expirado.") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail, headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    if credentials is None:
        raise unauthorized("Autenticação necessária.")
    validate_security_settings()
    try:
        payload = jwt.decode(
            credentials.credentials, settings.jwt_secret_key.get_secret_value(),
            algorithms=[settings.jwt_algorithm], options={"require": ["sub", "exp", "iat"]},
        )
        subject = payload["sub"]
        # SQLite IDs are signed 64-bit integers.
        if not isinstance(subject, str) or not subject.isascii() or not subject.isdecimal():
            raise ValueError("Invalid subject")
        user_id = int(subject)
        if not 0 < user_id <= 2**63 - 1:
            raise ValueError("Invalid user ID")
    except (InvalidTokenError, ValueError, TypeError, OverflowError):
        raise unauthorized() from None
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise unauthorized()
    return user


def require_admin(user: Annotated[User, Depends(get_current_user)]) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Acesso restrito a administradores.")
    return user
