"""Testes do roteamento técnico entre Structured Tool e RAG."""

from __future__ import annotations

import json
import sqlite3
import unittest

from app.core.database.init_db import init_db
from src.query.adaptive_executor import executar_plano_adaptativo
from src.query.executor import QueryExecutionError
from src.query.schemas import (
    KnowledgeBase,
    QueryAnalysis,
    QueryEntity,
    QueryIntent,
    QueryRoute,
)


class AdaptiveExecutorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.connection.execute("PRAGMA foreign_keys = ON")
        init_db(self.connection)
        self.connection.execute(
            """
            INSERT INTO ingestion_run (
                id, source, resource_type, started_at, status, created_at
            ) VALUES ('run-1', 'teste', 'contrato', '2026-01-01', 'SUCCESS', '2026-01-01')
            """
        )
        self.connection.execute(
            """
            INSERT INTO raw_asset (
                id, source, base_id, natureza, resource_type, endpoint,
                external_id, query_params, payload, payload_hash, fetched_at,
                corpus_version, sensitivity, ingestion_run_id, created_at
            ) VALUES ('contract-1', 'teste', 'pncp', 'estruturada', 'contrato',
                      'pncp://teste', '001-2-1/2026', '{}', ?, 'hash',
                      '2026-01-01', 'v1', 'publico', 'run-1', '2026-01-01')
            """,
            (json.dumps({"anoContrato": 2026, "valorGlobal": 100.0}),),
        )
        self.connection.commit()

    def tearDown(self) -> None:
        self.connection.close()

    def test_rota_estruturada_nao_chama_executor_rag(self) -> None:
        analysis = QueryAnalysis(
            route=QueryRoute.STRUCTURED,
            intent=QueryIntent.LOOKUP,
            entity=QueryEntity.CONTRATO,
            bases=[KnowledgeBase.ESTRUTURADA],
        )

        def rag_nao_deve_rodar() -> object:
            raise AssertionError("RAG não deveria ser chamado")

        result = executar_plano_adaptativo(
            analysis,
            structured_connection=self.connection,
            rag_executor=rag_nao_deve_rodar,
        )

        self.assertIsNotNone(result.structured_result)
        self.assertIsNone(result.rag_result)
        self.assertEqual(result.structured_result.structured_result.row_count, 1)

    def test_rota_rag_nao_consulta_sql(self) -> None:
        analysis = QueryAnalysis(
            route=QueryRoute.RAG,
            intent=QueryIntent.DOCUMENTAL,
            entity=QueryEntity.DOCUMENTO,
            bases=[KnowledgeBase.NORMATIVA],
            semantic_query="Quais documentos são exigidos?",
            needs_document_retrieval=True,
        )
        resposta_rag = object()

        result = executar_plano_adaptativo(
            analysis,
            rag_executor=lambda: resposta_rag,
        )

        self.assertIsNone(result.structured_result)
        self.assertIs(result.rag_result, resposta_rag)

    def test_rota_hibrida_entrega_sql_ao_executor_documental(self) -> None:
        analysis = QueryAnalysis(
            route=QueryRoute.HYBRID,
            intent=QueryIntent.COMPARISON,
            entity=QueryEntity.CONTRATO,
            bases=[KnowledgeBase.ESTRUTURADA, KnowledgeBase.NORMATIVA],
            semantic_query="Compare contratos e regras.",
            needs_document_retrieval=True,
        )

        recebidos = []
        resposta_hibrida = object()

        def executor_hibrido(structured):
            recebidos.append(structured)
            return resposta_hibrida

        result = executar_plano_adaptativo(
            analysis,
            structured_connection=self.connection,
            hybrid_executor=executor_hibrido,
        )

        self.assertEqual(len(recebidos), 1)
        self.assertEqual(recebidos[0].structured_result.row_count, 1)
        self.assertIs(result.structured_result, recebidos[0])
        self.assertIs(result.rag_result, resposta_hibrida)


if __name__ == "__main__":
    unittest.main()
