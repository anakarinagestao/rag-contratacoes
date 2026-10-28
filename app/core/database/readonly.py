"""Conexões SQLite imutáveis para tarefas de diagnóstico e indexação."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from .orm import register_models


def _readonly_sqlite_uri(database_url: str) -> str:
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite" or not url.database:
        raise ValueError("A conexão somente leitura exige um arquivo SQLite existente.")
    if url.database == ":memory:":
        raise ValueError("SQLite em memória não pode ser aberto em modo somente leitura.")
    caminho = Path(url.database).resolve()
    if not caminho.is_file():
        raise FileNotFoundError(f"Banco SQLite não encontrado: {caminho}")
    return caminho.as_uri() + "?mode=ro"


@contextmanager
def readonly_sqlite_connection(database_url: str) -> Iterator[sqlite3.Connection]:
    """Entrega uma conexão SQLite direta, existente e estritamente somente leitura."""

    connection = sqlite3.connect(
        _readonly_sqlite_uri(database_url),
        uri=True,
        timeout=30,
    )
    try:
        connection.execute("PRAGMA query_only = ON")
        connection.execute("PRAGMA foreign_keys = ON")
        yield connection
    finally:
        connection.close()


def create_readonly_sqlite_engine(database_url: str) -> Engine:
    """Abre banco SQLite com URI ``mode=ro`` e ``query_only`` ativado."""

    uri = _readonly_sqlite_uri(database_url)

    def conectar() -> sqlite3.Connection:
        return sqlite3.connect(uri, uri=True, timeout=30)

    engine = create_engine(
        "sqlite://",
        creator=conectar,
        poolclass=NullPool,
    )

    @event.listens_for(engine, "connect")
    def habilitar_somente_leitura(
        connection: sqlite3.Connection,
        _record: object,
    ) -> None:
        connection.execute("PRAGMA query_only = ON")
        connection.execute("PRAGMA foreign_keys = ON")

    return engine


@contextmanager
def readonly_session(database_url: str) -> Iterator[Session]:
    register_models()
    engine = create_readonly_sqlite_engine(database_url)
    try:
        with sessionmaker(bind=engine, autoflush=False, autocommit=False)() as db:
            yield db
    finally:
        engine.dispose()
