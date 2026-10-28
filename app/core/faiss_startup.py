"""Validação observacional do índice FAISS selecionado para a API."""

from __future__ import annotations

import logging

from fastapi import FastAPI

from app.core.config import resolver_caminho_indice_faiss
from llm_ecoa import EMBEDDING_MODEL
from src.faiss_errors import FAISS_UNAVAILABLE_ERRORS
from src.index import validar_indice_faiss


logger = logging.getLogger("uvicorn.error")


def validar_faiss_no_startup(app: FastAPI) -> bool:
    """Valida o FAISS sem impedir que rotas independentes iniciem.

    Uma falha fica registrada no estado da aplicação e no log. Rotas RAG e
    hybrid continuam falhando no carregamento normal do índice; autenticação e
    consultas structured permanecem disponíveis.
    """

    caminho = resolver_caminho_indice_faiss()
    logger.info("FAISS selecionado para validação")
    try:
        auditoria = validar_indice_faiss(
            caminho,
            modelo_esperado=EMBEDDING_MODEL,
        )
    except FAISS_UNAVAILABLE_ERRORS as error:
        app.state.faiss_index_ready = False
        app.state.faiss_index_error = error.error_code
        logger.error(
            "FAISS indisponível no startup codigo=%s erro=%s; "
            "rotas structured permanecem disponíveis",
            error.error_code,
            type(error).__name__,
        )
        return False

    app.state.faiss_index_ready = True
    app.state.faiss_index_error = None
    logger.info(
        "FAISS validado vetores=%d dimensao=%d modelo=%s",
        auditoria.total_vetores,
        auditoria.dimensao,
        auditoria.modelo_embedding,
    )
    return True
