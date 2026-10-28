from __future__ import annotations

import os
import sqlite3
from pathlib import Path


def database_path(database_url: str | None = None) -> str:
    url = database_url or os.getenv("DATABASE_URL", "sqlite:///./app.db")
    if url.startswith("sqlite:///"):
        return str(Path(url[10:]).resolve())
    if url.startswith("sqlite://"):
        return url.removeprefix("sqlite://")
    raise ValueError("A camada RAW requer uma DATABASE_URL SQLite")


def get_connection(database_url: str | None = None) -> sqlite3.Connection:
    path = database_path(database_url)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 30000")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection


def connect_database(database_url: str | None = None) -> sqlite3.Connection:
    """Alias de compatibilidade para a abertura centralizada da conexão."""
    return get_connection(database_url)
