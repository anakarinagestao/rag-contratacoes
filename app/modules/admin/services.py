from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.modules.admin.schemas import AdminUserCreate
from app.modules.auth.model import User
from app.modules.auth.schemas import UserCreate
from app.modules.auth.services import UserConflictError


class AdminAlreadyExistsError(ValueError):
    pass


def create_user(db: Session, data: AdminUserCreate) -> User:
    """Create an account with the role selected through the protected admin router."""
    user = User(
        username=data.username, email=str(data.email), role=data.role,
        password_hash=hash_password(data.password.get_secret_value()),
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise UserConflictError("Username ou email já cadastrado.") from None
    db.refresh(user)
    return user


def create_first_admin(db: Session, data: UserCreate) -> User:
    """Local bootstrap only; serialize the check/write using SQLite's write lock."""
    password_hash = hash_password(data.password.get_secret_value())
    try:
        with db.begin():
            db.execute(text("BEGIN IMMEDIATE"))
            if db.scalar(select(User.id).where(User.role == "admin").limit(1)) is not None:
                raise AdminAlreadyExistsError("Já existe um administrador. Nenhuma conta foi alterada.")
            user = User(username=data.username, email=str(data.email),
                        password_hash=password_hash, role="admin", is_active=True)
            db.add(user)
            db.flush()
    except IntegrityError:
        raise UserConflictError("Username ou email já cadastrado. Nenhuma conta foi promovida.") from None
    db.refresh(user)
    return user


def list_users(db: Session, offset: int, limit: int) -> list[User]:
    return list(db.scalars(select(User).order_by(User.id).offset(offset).limit(limit)))


def get_user(db: Session, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Usuário não encontrado.")
    return user


def update_user_status(db: Session, user_id: int, is_active: bool, actor: User) -> User:
    user = get_user(db, user_id)
    if user.id == actor.id and not is_active:
        raise HTTPException(status_code=409, detail="Não é permitido desativar a própria conta.")
    user.is_active = is_active
    db.commit()
    db.refresh(user)
    return user
