"""Diagnóstico observacional com HTTP local, SQLite em memória e LLMs simuladas."""

import asyncio
import logging
from pathlib import Path
import re
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import Base, create_sqlite_engine, get_db
from app.core.logging import RagPerfMiddleware, _perf_run_id, perf_stage
from app.core.security import get_current_user
from app.modules.corpus.model import CorpusVersion
from app.modules.documents.model import Document, DocumentChunk
import app.modules.feedback.model  # noqa: F401
import app.modules.rag.model  # noqa: F401
from app.modules.query.routes import router
from src.fts import inicializar_fts5
from src.generate import gerar_resposta_rag
from src.faiss_errors import FaissIndexMissingError
from src.index import indexar_chunks_incrementalmente
from src.query.analyzer import QueryAnalysisError, analisar_pergunta
from src.query.schemas import (
    AdaptiveQueryResult, KnowledgeBase, QueryAnalysis, QueryEntity, QueryIntent,
    QueryRoute, StructuredQueryResult,
)
from src.query.planner import planejar_execucao
from src.retrieve import retrieve_hibrido


class RagPerfHttpTests(unittest.TestCase):
    def setUp(self):
        engine = create_sqlite_engine("sqlite:///:memory:")
        self.addCleanup(engine.dispose)
        connection = self.enterContext(engine.connect())
        Base.metadata.create_all(connection)
        inicializar_fts5(engine)
        self.db = self.enterContext(Session(bind=connection))
        document = Document(
            corpus_version=CorpusVersion(version="perf-test", status="ready"),
            source_file="sensitive-source", title="sensitive-title",
            base_id="pncp_rj_estruturada", doc_type="contrato", sensitivity="publico",
        )
        self.db.add(DocumentChunk(document=document, chunk_id="sensitive-id",
                                  content="alpha sensitive-document", orgao="sensitive-person"))
        self.db.commit()
        temporary = self.enterContext(TemporaryDirectory())
        normalized_database = Path(temporary) / "normalized.db"
        sqlite3.connect(normalized_database).close()
        self.enterContext(patch.object(
            settings,
            "normalized_database_url",
            f"sqlite:///{normalized_database}",
        ))
        cache_dir = Path(temporary) / "index"
        indexar_chunks_incrementalmente(
            [("sensitive-id", "alpha sensitive-document")],
            lambda _: [1.0, 0.0],
            cache_dir,
            modelo_embedding="fake-v1",
        )
        analysis = QueryAnalysis(
            route=QueryRoute.RAG, intent=QueryIntent.DOCUMENTAL, entity=QueryEntity.DOCUMENTO,
            bases=[KnowledgeBase.AGREGADA], semantic_query="alpha",
            needs_document_retrieval=True,
        )
        self.enterContext(patch("src.query.analyzer.registrar"))
        self.enterContext(patch(
            "app.modules.query.services.analisar_pergunta",
            side_effect=lambda question: analisar_pergunta(question, llm_fn=lambda _: analysis.model_dump_json()),
        ))

        def isolated_generation(**kwargs):
            def retrieve(**retrieval_kwargs):
                retrieval_kwargs["cache_dir"] = cache_dir
                return retrieve_hibrido(**retrieval_kwargs, embedding_fn=lambda _: [1.0, 0.0])
            return gerar_resposta_rag(
                **kwargs, retrieval_fn=retrieve, llm_fn=lambda _: "sensitive-answer [1]",
                audit_dir=Path(temporary) / "audit",
            )

        self.enterContext(patch("src.generate.gerar_resposta_rag", side_effect=isolated_generation))
        app = FastAPI()  # Sem lifespan de produção, autenticação ou banco real.
        app.include_router(router)
        app.add_middleware(RagPerfMiddleware)
        app.dependency_overrides[get_db] = lambda: self.db
        app.dependency_overrides[get_current_user] = lambda: object()
        self.client = self.enterContext(TestClient(app))
        self.engine = engine

    def ask(self):
        return self.client.post("/query/ask", json={"pergunta": "alpha sensitive-question contratos", "top_k": 1},
                                headers={"Authorization": "Bearer sensitive-token"})

    def test_etapas_correlacao_privacidade_e_resultado_inalterado(self):
        queries = []
        def record(conn, cursor, statement, parameters, context, executemany):
            queries.append(statement)
        event.listen(self.engine, "before_cursor_execute", record)
        self.addCleanup(event.remove, self.engine, "before_cursor_execute", record)
        with patch.object(settings, "rag_perf_log_enabled", False):
            with self.assertNoLogs("uvicorn.error", level=logging.INFO):
                disabled = self.ask()
        disabled_query_count = len(queries)
        queries.clear()
        with patch.object(settings, "rag_perf_log_enabled", True):
            with self.assertLogs("uvicorn.error", level=logging.INFO) as captured:
                enabled = self.ask()
        self.assertEqual(disabled.status_code, 200)
        self.assertEqual(enabled.status_code, 200)
        disabled_body = disabled.json()
        enabled_body = enabled.json()
        self.assertIsInstance(disabled_body.pop("trace_id"), str)
        self.assertIsInstance(enabled_body.pop("trace_id"), str)
        self.assertEqual(disabled_body, enabled_body)
        self.assertEqual(disabled_query_count, len(queries))
        messages = [record.getMessage() for record in captured.records]
        self.assertEqual(len({re.search(r"run_id=(\w+)", message)[1] for message in messages}), 1)
        completed = {re.search(r"etapa=(\w+)", message)[1] for message in messages if "status=ok" in message}
        self.assertTrue({
            "recebimento", "analyzer", "analyzer_llm", "planner",
            "base_filters", "chunk_ids", "fts5", "faiss", "embedding", "faiss_load",
            "faiss_search", "faiss_filter", "rrf", "context", "llm_generation",
            "persistence", "audit", "total", "retrieval", "source_load",
        }.issubset(completed))
        for message in messages:
            self.assertNotIn("sensitive-", message)
            if "status=ok" in message:
                duration = re.search(r"duracao_ms=([\d.]+)", message)
                self.assertIsNotNone(duration)
                self.assertGreaterEqual(float(duration[1]), 0)
        self.assertTrue(any("etapa=chunk_ids" in m and "chunks_elegiveis=1" in m for m in messages))
        self.assertTrue(any("etapa=fts5" in m and "candidatos=1" in m for m in messages))
        self.assertTrue(any("etapa=context" in m and "fontes=1" in m for m in messages))
        self.assertIsNone(_perf_run_id.get())

    def test_excecao_propagada_sem_expor_mensagem(self):
        error = RuntimeError("sensitive-failure")
        with patch.object(settings, "rag_perf_log_enabled", True), patch(
            "app.modules.query.services.analisar_pergunta", side_effect=error
        ), self.assertLogs("uvicorn.error", level=logging.INFO) as captured:
            with self.assertRaises(RuntimeError) as raised:
                self.ask()
        self.assertIs(raised.exception, error)
        messages = "\n".join(record.getMessage() for record in captured.records)
        self.assertIn("excecao=RuntimeError", messages)
        self.assertIn("etapa=analyzer", messages)
        self.assertIn("etapa=total", messages)
        self.assertNotIn("sensitive-", messages)
        self.assertNotIn("etapa=fts5", messages)
        self.assertIsNone(_perf_run_id.get())

    def test_erro_422_preservado(self):
        responses = []
        for enabled in (False, True):
            with patch.object(settings, "rag_perf_log_enabled", enabled), patch(
                "app.modules.query.services.analisar_pergunta",
                side_effect=QueryAnalysisError("Plano inválido"),
            ):
                responses.append(self.ask())
        self.assertEqual([response.status_code for response in responses], [422, 422])
        self.assertEqual(responses[0].json(), responses[1].json())

    def test_falha_do_logger_nao_altera_resposta(self):
        with patch.object(settings, "rag_perf_log_enabled", True), patch(
            "app.core.logging._perf_logger.info", side_effect=RuntimeError("logger failure")
        ):
            response = self.ask()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["answer"], "sensitive-answer [1]")

    def test_falha_faiss_search_registra_etapa_e_auditoria(self):
        error = ValueError("sensitive-search")
        with patch.object(settings, "rag_perf_log_enabled", True), patch(
            "src.retrieve.carregar_indice_faiss"
        ) as index, self.assertLogs("uvicorn.error", level=logging.INFO) as captured:
            index.return_value.search.side_effect = error
            with self.assertRaises(ValueError) as raised:
                self.ask()
        self.assertIs(raised.exception, error)
        messages = "\n".join(record.getMessage() for record in captured.records)
        self.assertRegex(messages, r"etapa=faiss_search duracao_ms=[\d.]+ status=erro excecao=ValueError")
        self.assertIn("etapa=audit", messages)
        self.assertNotIn("etapa=context", messages)
        self.assertNotIn("sensitive-", messages)

    def test_faiss_ausente_no_fluxo_rag_retorna_503_e_log_seguro(self):
        with patch.object(settings, "rag_perf_log_enabled", True), patch(
            "src.retrieve.carregar_indice_faiss",
            side_effect=FaissIndexMissingError(),
        ), self.assertLogs("uvicorn.error", level=logging.INFO) as captured:
            response = self.ask()

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json()["detail"],
            {
                "code": "faiss_index_missing",
                "message": "Recuperação documental temporariamente indisponível.",
            },
        )
        messages = "\n".join(record.getMessage() for record in captured.records)
        self.assertIn("etapa=faiss_load", messages)
        self.assertIn("excecao=FaissIndexMissingError", messages)
        self.assertIn("etapa=total", messages)
        self.assertNotIn("sensitive-", messages)

    def test_rota_estruturada_nao_registra_etapas_documentais(self):
        analysis = QueryAnalysis(
            route=QueryRoute.STRUCTURED, intent=QueryIntent.LOOKUP, entity=QueryEntity.CONTRATO,
            bases=[KnowledgeBase.ESTRUTURADA],
        )
        result = AdaptiveQueryResult(
            analysis=analysis, plan=planejar_execucao(analysis),
            structured_result=StructuredQueryResult(
                entity=QueryEntity.CONTRATO, columns=["ano"], rows=[], row_count=0, limit_applied=20,
            ),
        )
        with patch.object(settings, "rag_perf_log_enabled", True), patch(
            "app.modules.query.services.analisar_pergunta", return_value=analysis
        ), patch("app.modules.query.services._executar_analise_normalizada", return_value=result), self.assertLogs(
            "uvicorn.error", level=logging.INFO
        ) as captured:
            response = self.ask()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["structured_result"]["rows"], [])
        messages = "\n".join(record.getMessage() for record in captured.records)
        self.assertIn("etapa=structured_query", messages)
        for stage in ("fts5", "faiss", "context", "llm_generation", "persistence", "audit"):
            self.assertNotIn(f"etapa={stage} ", messages)

    def test_recuperacao_vazia_nao_registra_etapas_nao_executadas(self):
        with patch.object(settings, "rag_perf_log_enabled", True), patch(
            "src.retrieve.construir_consulta_chunks_elegiveis",
            wraps=lambda *_args, **_kwargs: select(DocumentChunk.chunk_id).where(False),
        ), self.assertLogs("uvicorn.error", level=logging.INFO) as captured:
            response = self.ask()
        self.assertEqual(response.status_code, 200)
        messages = "\n".join(record.getMessage() for record in captured.records)
        for stage in ("fts5", "faiss", "rrf", "context", "llm_generation", "persistence"):
            self.assertNotIn(f"etapa={stage} ", messages)
        self.assertIn("chunks_elegiveis=0", messages)
        self.assertIn("etapa=audit", messages)


class RagPerfMiddlewareTests(unittest.IsolatedAsyncioTestCase):
    async def test_desabilitado_e_outras_rotas_nao_mede(self):
        async def app(scope, receive, send):
            with perf_stage("fts5"):
                pass
        middleware = RagPerfMiddleware(app)
        for enabled, path in ((False, "/query/ask"), (True, "/query/structured")):
            with patch.object(settings, "rag_perf_log_enabled", enabled), patch(
                "app.core.logging.time.perf_counter"
            ) as clock, self.assertNoLogs("uvicorn.error", level=logging.INFO):
                await middleware({"type": "http", "method": "POST", "path": path}, None, None)
                clock.assert_not_called()

    async def test_tempos_precisos_e_concorrencia(self):
        sent = []
        async def app(scope, receive, send):
            with perf_stage("fts5"):
                await asyncio.sleep(0)
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"unchanged"})
        async def send(message):
            sent.append(message)
        async def receive():
            return {"type": "http.request", "body": b""}
        middleware = RagPerfMiddleware(app)
        scope = {"type": "http", "method": "POST", "path": "/query/ask"}
        with patch.object(settings, "rag_perf_log_enabled", True), self.assertLogs(
            "uvicorn.error", level=logging.INFO
        ) as captured:
            await asyncio.gather(*(middleware(scope, receive, send) for _ in range(2)))
        runs = {}
        for record in captured.records:
            message = record.getMessage()
            run_id = re.search(r"run_id=(\w+)", message)[1]
            runs.setdefault(run_id, []).append(message)
        self.assertEqual(len(runs), 2)
        for messages in runs.values():
            self.assertEqual(sum("etapa=total" in m and "status=ok" in m for m in messages), 1)
            self.assertEqual(sum("etapa=fts5" in m and "status=ok" in m for m in messages), 1)
        self.assertEqual([m["body"] for m in sent if m["type"] == "http.response.body"], [b"unchanged"] * 2)
        self.assertIsNone(_perf_run_id.get())

    async def test_medicao_perf_counter(self):
        token = _perf_run_id.set("test-run")
        try:
            with patch.object(settings, "rag_perf_log_enabled", True), patch(
                "app.core.logging.time.perf_counter", side_effect=[10.0, 10.025]
            ), self.assertLogs(
                "uvicorn.error", level=logging.INFO
            ) as captured:
                with perf_stage("fts5"):
                    pass
            self.assertIn("duracao_ms=25.000", captured.records[-1].getMessage())
        finally:
            _perf_run_id.reset(token)
