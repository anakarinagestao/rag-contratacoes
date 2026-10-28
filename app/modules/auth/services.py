from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import (
    create_access_token, dummy_password_hash, hash_password, unauthorized, verify_password,
)
from app.modules.auth.model import User
from app.modules.auth.schemas import LoginRequest, TokenResponse, UserCreate


class UserConflictError(ValueError):
    pass


def create_user(db: Session, data: UserCreate) -> User:
    """Provision an ordinary user; this service cannot grant administrator role."""
    user = User(
        username=data.username, email=str(data.email),
        password_hash=hash_password(data.password.get_secret_value()), role="user",
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise UserConflictError("Username ou email já cadastrado.") from None
    db.refresh(user)
    return user


def login(db: Session, data: LoginRequest) -> TokenResponse:
    user = db.scalar(select(User).where(User.username == data.username))
    stored_hash = user.password_hash if user is not None else dummy_password_hash()
    valid_password = verify_password(data.password.get_secret_value(), stored_hash)
    if not valid_password or user is None or not user.is_active:
        raise unauthorized("Usuário ou senha inválidos.")
    return TokenResponse(
        access_token=create_access_token(user.id),
        expires_in=settings.jwt_access_token_expire_minutes * 60,
    )
