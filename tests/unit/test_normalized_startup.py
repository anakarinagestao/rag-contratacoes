"""Prontidão observacional da base normalizada no startup da API."""

from __future__ import annotations

import logging
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi import FastAPI

from app.core.config import settings
from app.core.normalized_startup import validar_normalizada_no_startup


ROOT = Path(__file__).resolve().parents[2]


def _normalized_database(path: Path, *, status: str = "SUCCESS") -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        (ROOT / "migrations" / "100_normalized_schema.sql").read_text(
            encoding="utf-8"
        )
    )
    connection.execute(
        "INSERT INTO normalization_run "
        "(id, source_snapshot_sha256, transform_version, started_at, status) "
        "VALUES ('run-test', 'hash-test', 'test-v1', '2026-10-21', ?)",
        (status,),
    )
    connection.commit()
    connection.close()


class NormalizedStartupTests(unittest.TestCase):
    def test_configuracao_ausente_nao_bloqueia_aplicacao(self) -> None:
        app = FastAPI()
        with patch.object(settings, "normalized_database_url", None), \
                self.assertLogs("uvicorn.error", logging.WARNING) as captured:
            ready = validar_normalizada_no_startup(app)

        self.assertFalse(ready)
        self.assertFalse(app.state.normalized_database_ready)
        self.assertEqual(app.state.normalized_database_error, "NotConfigured")
        self.assertIn("structured e hybrid", "\n".join(captured.output))

    def test_base_valida_fica_pronta_sem_escrita(self) -> None:
        app = FastAPI()
        with TemporaryDirectory() as directory:
            database = Path(directory) / "normalized.db"
            _normalized_database(database)
            before = database.read_bytes()
            with patch.object(
                settings,
                "normalized_database_url",
                f"sqlite:///{database}",
            ), self.assertLogs("uvicorn.error", logging.INFO) as captured:
                ready = validar_normalizada_no_startup(app)
            after = database.read_bytes()

        self.assertTrue(ready)
        self.assertTrue(app.state.normalized_database_ready)
        self.assertIsNone(app.state.normalized_database_error)
        self.assertEqual(after, before)
        self.assertIn("somente leitura", "\n".join(captured.output))

    def test_esquema_incompleto_fica_indisponivel_sem_expor_caminho(self) -> None:
        app = FastAPI()
        with TemporaryDirectory() as directory:
            database = Path(directory) / "private-normalized.db"
            sqlite3.connect(database).close()
            with patch.object(
                settings,
                "normalized_database_url",
                f"sqlite:///{database}",
            ), self.assertLogs("uvicorn.error", logging.ERROR) as captured:
                ready = validar_normalizada_no_startup(app)

        messages = "\n".join(captured.output)
        self.assertFalse(ready)
        self.assertEqual(app.state.normalized_database_error, "RuntimeError")
        self.assertNotIn(str(database), messages)

    def test_execucao_mais_recente_incompleta_fica_indisponivel(self) -> None:
        app = FastAPI()
        with TemporaryDirectory() as directory:
            database = Path(directory) / "normalized.db"
            _normalized_database(database, status="RUNNING")
            with patch.object(
                settings,
                "normalized_database_url",
                f"sqlite:///{database}",
            ), self.assertLogs("uvicorn.error", logging.ERROR):
                ready = validar_normalizada_no_startup(app)

        self.assertFalse(ready)
        self.assertEqual(app.state.normalized_database_error, "RuntimeError")


if __name__ == "__main__":
    unittest.main()
