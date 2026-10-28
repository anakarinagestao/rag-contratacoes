"""Initialize the configured SQLite database: python -m app.init_db."""

from app.core.database import Base, init_db


if __name__ == "__main__":
    init_db()
    print(f"Banco SQLite inicializado: {len(Base.metadata.tables)} tabelas disponíveis.")
