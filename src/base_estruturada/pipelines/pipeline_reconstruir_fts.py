"""Reconstrói explicitamente o índice lexical FTS5 a partir de DocumentChunk."""

from __future__ import annotations

from app.core.database import engine, init_db
from src.fts import reconstruir_fts5


def main() -> None:
    init_db()
    total = reconstruir_fts5(engine)
    print(f"FTS5 reconstruído com sucesso | chunks_indexados={total}")


if __name__ == "__main__":
    main()
