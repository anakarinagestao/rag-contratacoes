from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.modules.auth import services
from app.modules.auth.model import User
from app.modules.auth.schemas import LoginRequest, TokenResponse, UserCreate, UserResponse


router = APIRouter(prefix="/auth", tags=["Auth"])


@router.post("/register", response_model=UserResponse, status_code=201,
             summary="Criar usuário comum",
             responses={409: {"description": "Username ou email já cadastrado"}})
def register(data: UserCreate, db: Annotated[Session, Depends(get_db)]):
    try:
        return services.create_user(db, data)
    except services.UserConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@router.post("/login", response_model=TokenResponse, summary="Obter token de acesso",
             responses={401: {"description": "Credenciais inválidas ou usuário inativo"}})
def login(data: LoginRequest, response: Response, db: Annotated[Session, Depends(get_db)]):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return services.login(db, data)


@router.get("/me", response_model=UserResponse, summary="Consultar usuário autenticado",
            responses={401: {"description": "Token inválido, expirado ou usuário inativo"}})
def me(user: Annotated[User, Depends(get_current_user)]):
    return user
