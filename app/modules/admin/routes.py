from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import require_admin
from app.modules.admin import services
from app.modules.admin.schemas import AdminUserCreate, UserStatusUpdate
from app.modules.auth.model import User
from app.modules.auth.schemas import UserResponse
from app.modules.auth.services import UserConflictError


router = APIRouter(
    prefix="/admin", tags=["Admin"], dependencies=[Depends(require_admin)],
    responses={401: {"description": "Autenticação necessária"},
               403: {"description": "Acesso restrito a administradores"}},
)
UserId = Annotated[int, Path(gt=0, le=2**63 - 1)]


@router.post("/users", response_model=UserResponse, status_code=201,
             summary="Criar usuário ou administrador",
             responses={409: {"description": "Username ou email já cadastrado"}})
def create_user(data: AdminUserCreate, db: Annotated[Session, Depends(get_db)]):
    try:
        return services.create_user(db, data)
    except UserConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@router.get("/users", response_model=list[UserResponse], summary="Listar usuários")
def list_users(
    db: Annotated[Session, Depends(get_db)],
    offset: Annotated[int, Query(ge=0, le=2**63 - 1)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
):
    return services.list_users(db, offset, limit)


@router.get("/users/{user_id}", response_model=UserResponse, summary="Consultar usuário",
            responses={404: {"description": "Usuário não encontrado"}})
def get_user(user_id: UserId, db: Annotated[Session, Depends(get_db)]):
    return services.get_user(db, user_id)


@router.patch("/users/{user_id}/status", response_model=UserResponse,
              summary="Ativar ou desativar usuário",
              responses={404: {"description": "Usuário não encontrado"},
                         409: {"description": "Tentativa de desativar a própria conta"}})
def update_status(
    user_id: UserId, data: UserStatusUpdate,
    db: Annotated[Session, Depends(get_db)],
    actor: Annotated[User, Depends(require_admin)],
):
    return services.update_user_status(db, user_id, data.is_active, actor)
