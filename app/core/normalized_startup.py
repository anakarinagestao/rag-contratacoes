"""Validação observacional da base normalizada selecionada para a API."""

from __future__ import annotations

import logging
import sqlite3

from fastapi import FastAPI

from app.core.config import settings
from app.core.database.readonly import readonly_sqlite_connection


logger = logging.getLogger("uvicorn.error")

_REQUIRED_TABLES = frozenset(
    {
        "normalization_run",
        "orgao",
        "unidade",
        "fornecedor",
        "contratacao",
        "contrato",
    }
)


def validar_normalizada_no_startup(app: FastAPI) -> bool:
    """Registra a prontidão da base sem bloquear rotas independentes.

    O teste é propositalmente leve: valida configuração, abertura em modo
    somente leitura, tabelas exigidas pelo SQL Tool e a última normalização
    concluída. A integridade completa continua sendo responsabilidade do fluxo
    de construção/publicação da base.
    """

    database_url = settings.normalized_database_url
    if not database_url:
        app.state.normalized_database_ready = False
        app.state.normalized_database_error = "NotConfigured"
        logger.warning(
            "Base normalizada não configurada; rotas structured e hybrid "
            "permanecem indisponíveis"
        )
        return False

    try:
        with readonly_sqlite_connection(database_url) as connection:
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            missing = _REQUIRED_TABLES - tables
            if missing:
                raise RuntimeError("Esquema normalizado incompleto")
            latest_run = connection.execute(
                "SELECT status FROM normalization_run "
                "ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
            if latest_run is None or latest_run[0] != "SUCCESS":
                raise RuntimeError("Normalização concluída não encontrada")
            if connection.execute("PRAGMA query_only").fetchone()[0] != 1:
                raise RuntimeError("Conexão normalizada não está somente leitura")
    except (ValueError, FileNotFoundError, sqlite3.Error, RuntimeError) as error:
        app.state.normalized_database_ready = False
        app.state.normalized_database_error = type(error).__name__
        logger.error(
            "Base normalizada indisponível no startup erro=%s; "
            "rotas RAG e catalog permanecem disponíveis",
            type(error).__name__,
        )
        return False

    app.state.normalized_database_ready = True
    app.state.normalized_database_error = None
    logger.info("Base normalizada validada em modo somente leitura")
    return True
