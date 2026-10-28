from collections.abc import Generator

from sqlalchemy.orm import Session

from .connection import connect_database, database_path, get_connection
from .init_db import initialize_database
from .orm import (
    Base,
    SessionLocal,
    create_sqlite_engine,
    engine,
    init_db,
)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


__all__ = [
    "Base",
    "SessionLocal",
    "connect_database",
    "create_sqlite_engine",
    "database_path",
    "engine",
    "get_connection",
    "get_db",
    "init_db",
    "initialize_database",
]