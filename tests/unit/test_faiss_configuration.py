from __future__ import annotations

import logging
import os
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import Settings, resolver_caminho_indice_faiss, settings
from app.core.faiss_startup import validar_faiss_no_startup
from app.modules.query import services as query_services
from src.faiss_errors import FaissIndexIncompatibleError
from src.index import indexar_chunks_incrementalmente, validar_indice_faiss
from src.query.adaptive_executor import AdaptiveExecutionResult
from src.query.planner import planejar_execucao
from src.query.schemas import (
    ExecutionPlan,
    KnowledgeBase,
    QueryAnalysis,
    QueryEntity,
    QueryIntent,
    QueryRoute,
)


def _analysis(route: QueryRoute) -> QueryAnalysis:
    return QueryAnalysis(
        route=route,
        intent=(
            QueryIntent.LOOKUP
            if route is QueryRoute.STRUCTURED
            else QueryIntent.DOCUMENTAL
        ),
        entity=(
            QueryEntity.CONTRATO
            if route is QueryRoute.STRUCTURED
            else QueryEntity.DOCUMENTO
        ),
        bases=[KnowledgeBase.ESTRUTURADA],
        needs_document_retrieval=route is not QueryRoute.STRUCTURED,
    )


class FaissConfigurationTests(unittest.TestCase):
    def test_default_permanece_v1_e_resolucao_e_absoluta(self):
        # Plugins de teste podem carregar o .env no processo do pytest. O
        # contrato aqui é o default da classe, sem overrides operacionais.
        with patch.dict(os.environ, {}, clear=True):
            isolated = Settings(database_url="sqlite:///:memory:", _env_file=None)
        self.assertEqual(isolated.rag_faiss_index_dir, Path("data/index/faiss/v1"))
        with patch.object(settings, "rag_faiss_index_dir", Path("indice-relativo")):
            self.assertEqual(
                resolver_caminho_indice_faiss(),
                Path("indice-relativo").resolve(),
            )

    def test_valida_indice_temporario_modelo_dimensao_e_integridade(self):
        with TemporaryDirectory() as directory:
            path = Path(directory)
            indexar_chunks_incrementalmente(
                [("chunk-1", "conteudo")],
                lambda _: [1.0, 0.0],
                path,
                modelo_embedding="modelo-teste",
            )
            audit = validar_indice_faiss(path, modelo_esperado="modelo-teste")
            self.assertEqual(audit.total_vetores, 1)
            self.assertEqual(audit.dimensao, 2)
            with self.assertRaises(FaissIndexIncompatibleError):
                validar_indice_faiss(path, modelo_esperado="outro-modelo")

    def test_startup_invalido_nao_bloqueia_aplicacao_independente(self):
        app = FastAPI()
        with TemporaryDirectory() as directory, \
                patch.object(settings, "rag_faiss_index_dir", Path(directory) / "ausente"), \
                self.assertLogs("uvicorn.error", level=logging.INFO) as captured:
            ready = validar_faiss_no_startup(app)
        self.assertFalse(ready)
        self.assertFalse(app.state.faiss_index_ready)
        self.assertEqual(app.state.faiss_index_error, "faiss_index_missing")
        self.assertNotIn(str((Path(directory) / "ausente").resolve()), "\n".join(captured.output))
        self.assertIn("codigo=faiss_index_missing", "\n".join(captured.output))
        self.assertIn("structured permanecem disponíveis", "\n".join(captured.output))

    def test_startup_valido_registra_caminho_absoluto(self):
        app = FastAPI()
        with TemporaryDirectory() as directory:
            path = Path(directory) / "index"
            indexar_chunks_incrementalmente(
                [("chunk-1", "conteudo")],
                lambda _: [1.0, 0.0],
                path,
                modelo_embedding="modelo-teste",
            )
            with patch.object(settings, "rag_faiss_index_dir", path), \
                    patch("app.core.faiss_startup.EMBEDDING_MODEL", "modelo-teste"), \
                    self.assertLogs("uvicorn.error", level=logging.INFO) as captured:
                ready = validar_faiss_no_startup(app)
        self.assertTrue(ready)
        self.assertTrue(app.state.faiss_index_ready)
        self.assertNotIn(str(path.resolve()), "\n".join(captured.output))
        self.assertIn("FAISS validado", "\n".join(captured.output))

    def test_rollback_reseleciona_v1_sem_alterar_indices(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            v1 = root / "v1"
            v2 = root / "v2"
            v1.mkdir()
            v2.mkdir()
            before = {v1: list(v1.iterdir()), v2: list(v2.iterdir())}
            with patch.object(settings, "rag_faiss_index_dir", v2):
                self.assertEqual(resolver_caminho_indice_faiss(), v2.resolve())
            with patch.object(settings, "rag_faiss_index_dir", v1):
                self.assertEqual(resolver_caminho_indice_faiss(), v1.resolve())
            self.assertEqual(before, {v1: list(v1.iterdir()), v2: list(v2.iterdir())})

    def test_rag_recebe_caminho_configurado(self):
        analysis = _analysis(QueryRoute.RAG)
        engine = create_engine("sqlite:///:memory:")
        with Session(engine) as db, TemporaryDirectory() as directory, \
                patch.object(settings, "rag_faiss_index_dir", Path(directory)), \
                patch("src.generate.gerar_resposta_rag", return_value=object()) as generate:
            query_services.executar_consulta_adaptativa_api(
                db, "pergunta", 5, analyzer_fn=lambda _: analysis
            )
        self.assertEqual(generate.call_args.kwargs["cache_dir"], Path(directory).resolve())

    def test_hybrid_recebe_caminho_configurado(self):
        analysis = _analysis(QueryRoute.HYBRID)
        expected = object()

        def execute(_analysis_value, **kwargs):
            structured = type("Structured", (), {"structured_result": expected})()
            return AdaptiveExecutionResult(
                analysis=analysis,
                plan=ExecutionPlan(
                    route=analysis.route,
                    tools=["structured_database", "rag_retrieval"],
                    analysis=analysis,
                ),
                rag_result=kwargs["hybrid_executor"](structured),
            )

        engine = create_engine("sqlite:///:memory:")
        with Session(engine) as db, TemporaryDirectory() as directory:
            normalized = Path(directory) / "normalized.db"
            sqlite3.connect(normalized).close()
            with \
                patch.object(settings, "rag_faiss_index_dir", Path(directory)), \
                patch.object(query_services, "executar_plano_adaptativo", side_effect=execute), \
                patch("src.generate.gerar_resposta_rag", return_value=object()) as generate:
                query_services.executar_consulta_adaptativa_api(
                    db,
                    "pergunta",
                    5,
                    analyzer_fn=lambda _: analysis,
                    normalized_database_url=f"sqlite:///{normalized}",
                )
        self.assertEqual(generate.call_args.kwargs["cache_dir"], Path(directory).resolve())
        self.assertIs(generate.call_args.kwargs["structured_result"], expected)

    def test_structured_nao_resolve_nem_carrega_faiss(self):
        analysis = _analysis(QueryRoute.STRUCTURED)
        result = AdaptiveExecutionResult(
            analysis=analysis,
            plan=planejar_execucao(analysis),
        )
        engine = create_engine("sqlite:///:memory:")
        with Session(engine) as db, TemporaryDirectory() as directory:
            normalized = Path(directory) / "normalized.db"
            sqlite3.connect(normalized).close()
            with \
                patch.object(query_services, "resolver_caminho_indice_faiss") as resolver, \
                patch.object(query_services, "executar_plano_adaptativo", return_value=result):
                actual = query_services.executar_consulta_adaptativa_api(
                    db,
                    "pergunta",
                    5,
                    analyzer_fn=lambda _: analysis,
                    normalized_database_url=f"sqlite:///{normalized}",
                )
        self.assertIs(actual, result)
        resolver.assert_not_called()


if __name__ == "__main__":
    unittest.main()
