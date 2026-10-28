"""Garantias da conexão SQLite direta usada para consulta normalizada."""

from __future__ import annotations

import hashlib
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.core.database.readonly import readonly_sqlite_connection


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ReadonlySqliteConnectionTests(unittest.TestCase):
    def test_permite_select_mas_bloqueia_escrita_sem_alterar_arquivo(self) -> None:
        with TemporaryDirectory() as directory:
            database = Path(directory) / "normalized.db"
            writable = sqlite3.connect(database)
            writable.execute("CREATE TABLE example (id INTEGER PRIMARY KEY)")
            writable.execute("INSERT INTO example VALUES (1)")
            writable.commit()
            writable.close()
            before = _sha256(database)

            with readonly_sqlite_connection(f"sqlite:///{database}") as connection:
                self.assertEqual(
                    connection.execute("PRAGMA query_only").fetchone()[0],
                    1,
                )
                self.assertEqual(
                    connection.execute("SELECT id FROM example").fetchall(),
                    [(1,)],
                )
                with self.assertRaises(sqlite3.OperationalError):
                    connection.execute("INSERT INTO example VALUES (2)")
                with self.assertRaises(sqlite3.OperationalError):
                    connection.execute("CREATE TABLE forbidden (id INTEGER)")

            self.assertEqual(_sha256(database), before)

    def test_rejeita_memoria_banco_ausente_e_outro_backend(self) -> None:
        invalid_urls = (
            "sqlite:///:memory:",
            "sqlite:////tmp/pncp-normalized-does-not-exist.db",
            "postgresql://localhost/pncp",
        )
        for database_url in invalid_urls:
            with self.subTest(database_url=database_url):
                with self.assertRaises((ValueError, FileNotFoundError)):
                    with readonly_sqlite_connection(database_url):
                        pass


if __name__ == "__main__":
    unittest.main()
