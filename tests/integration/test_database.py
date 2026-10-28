"""Offline relational persistence tests, isolated from the project's app.db."""

from contextlib import closing
from datetime import date
from decimal import Decimal
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from sqlalchemy import delete, inspect, insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

# Allow tests to run even before a local .env has been created.
with patch.dict(os.environ, {"DATABASE_URL": "sqlite:///:memory:"}):
    from app.core.database import Base, create_sqlite_engine, get_db, init_db

from app.modules.corpus.model import CorpusVersion
from app.modules.documents.model import Document, DocumentChunk
from app.modules.feedback.model import Feedback
from app.modules.query.model import Query, Response, ResponseSource
from app.modules.rag.model import PIPELINE_STAGES, PipelineEvent


TABLES = {
    "corpus_versions", "documents", "document_chunks", "queries",
    "responses", "response_sources", "feedback", "pipeline_events", "users",
}
ROOT = Path(__file__).resolve().parents[2]


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_sqlite_engine("sqlite:///:memory:")
        init_db(self.engine)
        self.session = Session(self.engine)
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.session.close)

    def make_chunk(self):
        corpus = CorpusVersion(version="v1", status="ready")
        document = Document(
            corpus_version=corpus, source_file="contratos.json",
            base_id="pncp", doc_type="estruturada", sensitivity="publico",
        )
        return DocumentChunk(
            document=document, chunk_id="v1:contrato:1", content="Software",
            valor=Decimal("123456.78"), data_publicacao=date(2025, 1, 2),
            vigencia=date(2026, 1, 2), ano=2025,
        )

    def test_schema_and_idempotent_initialization(self):
        self.session.add(Query(run_id="kept", text="Preservar consulta"))
        self.session.commit()
        init_db(self.engine)
        tabelas = set(inspect(self.engine).get_table_names())
        self.assertTrue(TABLES.issubset(tabelas))
        self.assertIn("document_chunks_fts", tabelas)
        self.assertEqual(self.session.scalar(select(Query)).run_id, "kept")
        columns = {c["name"]: c for c in inspect(self.engine).get_columns("document_chunks")}
        self.assertNotIn("embedding", columns)
        self.assertEqual(columns["valor"]["type"].precision, 15)
        self.assertEqual(columns["valor"]["type"].scale, 2)

    def test_traceability_and_value_round_trip(self):
        chunk = self.make_chunk()
        query = Query(run_id="run-1", text="Qual contrato?")
        response = Response(query=query, answer="Contrato de software")
        source = ResponseSource(
            response=response, chunk=chunk, rank=1, relevance_score=Decimal("0.85")
        )
        self.session.add(source)
        self.session.commit()
        self.session.expunge_all()

        saved = self.session.scalar(select(Query))
        evidence = saved.response.sources[0]
        self.assertEqual(evidence.chunk_id, evidence.chunk.id)
        self.assertEqual(evidence.chunk.chunk_id, "v1:contrato:1")
        self.assertEqual(evidence.chunk.document.corpus_version.version, "v1")
        self.assertEqual(evidence.chunk.document.chunks, [evidence.chunk])
        self.assertEqual(evidence.chunk.response_sources, [evidence])
        self.assertEqual(evidence.chunk.valor, Decimal("123456.78"))
        self.assertEqual(evidence.chunk.data_publicacao, date(2025, 1, 2))
        self.assertEqual(evidence.chunk.vigencia, date(2026, 1, 2))
        self.assertIsNone(evidence.chunk.orgao)
        self.assertIsNone(evidence.chunk.document.source_url)
        self.assertFalse(saved.response.is_refusal)
        self.assertIsNotNone(saved.created_at)
        self.assertIsNotNone(saved.response.created_at)

    def test_query_without_response_and_multiple_feedback_events(self):
        query = Query(run_id="failed-run", text="Consulta sem resposta")
        query.feedback = [Feedback(rating=1), Feedback(rating=-1, comment="Revisar")]
        query.pipeline_events = [
            PipelineEvent(run_id=query.run_id, stage=stage, status="ok")
            for stage in PIPELINE_STAGES
        ]
        self.session.add(query)
        self.session.commit()
        self.session.expire_all()
        self.assertIsNone(query.response)
        self.assertEqual(len(query.feedback), 2)
        self.assertEqual({e.stage for e in query.pipeline_events}, set(PIPELINE_STAGES))
        self.assertTrue(all(e.query is query for e in query.pipeline_events))
        self.assertTrue(all(f.query is query for f in query.feedback))

    def test_unique_identifiers_and_single_response_enforced_by_database(self):
        chunk = self.make_chunk()
        response = Response(query=Query(run_id="unique-run", text="Pergunta"), answer="Resposta")
        self.session.add_all([chunk, response])
        self.session.commit()
        duplicates = [
            (CorpusVersion, {"version": "v1", "status": "ready"}),
            (DocumentChunk, {"document_id": chunk.document_id, "chunk_id": chunk.chunk_id, "content": "Outro"}),
            (Query, {"run_id": "unique-run", "text": "Outra"}),
            (Response, {"query_id": response.query_id, "answer": "Segunda"}),
        ]
        for model, values in duplicates:
            with self.subTest(table=model.__tablename__):
                with self.assertRaises(IntegrityError):
                    self.session.execute(insert(model).values(**values))
                    self.session.commit()
                self.session.rollback()

    def test_all_foreign_keys_reject_orphans(self):
        # Supply valid values for every other FK to isolate each constraint.
        chunk = self.make_chunk()
        response = Response(query=Query(run_id="valid", text="Pergunta"), answer="Resposta")
        self.session.add_all([chunk, response])
        self.session.commit()
        invalid_rows = [
            (Document, {"corpus_version_id": -1, "source_file": "x", "base_id": "x", "doc_type": "x", "sensitivity": "publico"}),
            (DocumentChunk, {"document_id": -1, "chunk_id": "orphan", "content": "x"}),
            (Response, {"query_id": -1, "answer": "x"}),
            (ResponseSource, {"response_id": -1, "chunk_id": chunk.id}),
            (ResponseSource, {"response_id": response.id, "chunk_id": -1}),
            (Feedback, {"query_id": -1, "rating": 1}),
            (PipelineEvent, {"query_id": -1, "run_id": "x", "stage": "query_analysis", "status": "error"}),
        ]
        for model, values in invalid_rows:
            with self.subTest(table=model.__tablename__, values=values):
                with self.assertRaises(IntegrityError):
                    self.session.execute(insert(model).values(**values))
                    self.session.commit()
                self.session.rollback()

    def test_referenced_evidence_cannot_be_deleted(self):
        chunk = self.make_chunk()
        self.session.add(ResponseSource(
            chunk=chunk,
            response=Response(query=Query(run_id="audit", text="Pergunta"), answer="Resposta"),
        ))
        self.session.commit()
        for model in (CorpusVersion, Document, DocumentChunk, Query, Response):
            with self.subTest(table=model.__tablename__):
                with self.assertRaises(IntegrityError):
                    self.session.execute(delete(model))
                    self.session.commit()
                self.session.rollback()

    def test_dependency_closes_session_on_success_and_error(self):
        for fail in (False, True):
            with self.subTest(fail=fail), patch("app.core.database.SessionLocal") as factory:
                dependency = get_db()
                self.assertIs(next(dependency), factory.return_value)
                if fail:
                    with self.assertRaises(RuntimeError):
                        dependency.throw(RuntimeError("processing failed"))
                else:
                    with self.assertRaises(StopIteration):
                        next(dependency)
                factory.return_value.close.assert_called_once()

    def test_example_environment_is_accepted(self):
        from app.core.config import Settings

        with patch.dict(os.environ, {"DATABASE_URL": "sqlite:///./app.db"}):
            settings = Settings(_env_file=ROOT / ".env.example")
        self.assertEqual(settings.database_url, "sqlite:///./app.db")

    def test_cli_initializes_file_without_uvicorn_and_preserves_data(self):
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "app.db"
            environment = {**os.environ, "DATABASE_URL": f"sqlite:///{db_path.as_posix()}"}
            command = [sys.executable, "-m", "app.init_db"]
            subprocess.run(command, cwd=ROOT, env=environment, check=True, capture_output=True)
            with closing(sqlite3.connect(db_path)) as connection, connection:
                tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                self.assertTrue(TABLES.issubset(tables))
                self.assertIn("document_chunks_fts", tables)
                connection.execute("INSERT INTO queries (run_id, text) VALUES ('saved', 'Pergunta')")
            subprocess.run(command, cwd=ROOT, env=environment, check=True, capture_output=True)
            with closing(sqlite3.connect(db_path)) as connection:
                self.assertEqual(connection.execute("SELECT run_id FROM queries").fetchall(), [("saved",)])
                self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])


if __name__ == "__main__":
    unittest.main()
