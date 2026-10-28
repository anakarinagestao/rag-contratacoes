"""Endpoints locais da fila de processamento documental."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.modules.auth.model import User
from app.modules.documents.schemas import ProcessingTaskRequestCreate, ProcessingTaskResponse
from app.modules.documents.services import (
    consultar_tarefa_processamento,
    retentar_tarefa_processamento,
    solicitar_processamento_catalogo,
)


router = APIRouter(prefix="/documents", tags=["Documents"])


def _serializar_tarefa(
    tarefa: object,
    *,
    created: bool,
    retried: bool = False,
) -> ProcessingTaskResponse:
    return ProcessingTaskResponse(
        id=tarefa.id,
        task_id=tarefa.id,
        status_url=f"/documents/processing-tasks/{tarefa.id}",
        catalog_entry_id=tarefa.catalog_entry_id,
        status=tarefa.status,
        tentativas=tarefa.tentativas,
        erro=tarefa.erro,
        error_code=tarefa.error_code,
        lease_expires_at=tarefa.lease_expires_at,
        motivo=tarefa.motivo,
        criado_em=tarefa.criado_em,
        atualizado_em=tarefa.atualizado_em,
        iniciado_em=tarefa.iniciado_em,
        concluido_em=tarefa.concluido_em,
        created=created,
        retried=retried,
    )


@router.post(
    "/catalog/{catalog_entry_id}/processing-tasks",
    response_model=ProcessingTaskResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Solicitar processamento assíncrono de documento catalogado",
)
def solicitar_processamento(
    catalog_entry_id: int,
    request: ProcessingTaskRequestCreate,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(get_current_user)],
) -> ProcessingTaskResponse:
    """Registra a tarefa, sem baixar nem processar o arquivo nesta chamada."""

    try:
        tarefa, criada = solicitar_processamento_catalogo(
            db,
            catalog_entry_id,
            motivo=request.motivo,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    return _serializar_tarefa(tarefa, created=criada)


@router.post(
    "/processing-tasks/{tarefa_id}/retry",
    response_model=ProcessingTaskResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Retentar uma tarefa de processamento que falhou",
)
def retentar_processamento(
    tarefa_id: int,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(get_current_user)],
) -> ProcessingTaskResponse:
    """Devolve à fila uma tarefa em erro, sem duplicar documentos ou chunks."""

    try:
        tarefa = retentar_tarefa_processamento(db, tarefa_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    return _serializar_tarefa(tarefa, created=False, retried=True)


@router.get(
    "/processing-tasks/{tarefa_id}",
    response_model=ProcessingTaskResponse,
    summary="Consultar o estado de uma tarefa de processamento",
)
def consultar_processamento(
    tarefa_id: int,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(get_current_user)],
) -> ProcessingTaskResponse:
    try:
        tarefa = consultar_tarefa_processamento(db, tarefa_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    return _serializar_tarefa(tarefa, created=False)
