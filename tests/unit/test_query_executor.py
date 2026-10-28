"""Testes do fluxo Analyzer -> Planner -> Structured Tool."""

from __future__ import annotations

import json
import sqlite3
import unittest

from app.core.database.init_db import init_db
from src.query.executor import QueryExecutionError, executar_consulta_adaptativa
from src.query.schemas import (
    KnowledgeBase,
    QueryAnalysis,
    QueryEntity,
    QueryFilters,
    QueryIntent,
    QueryRoute,
)


class QueryExecutorTests(unittest.TestCase):
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
                      'pncp://teste', '001-2-000001/2026', '{}', ?, 'hash',
                      '2026-01-01', 'v1', 'publico', 'run-1', '2026-01-01')
            """,
            (
                json.dumps(
                    {
                        "anoContrato": 2026,
                        "dataPublicacaoPncp": "2026-08-10T12:00:00",
                        "valorGlobal": 500000.0,
                        "objetoContrato": "Licenciamento de software",
                        "nomeRazaoSocialFornecedor": "Empresa Alfa",
                        "orgaoEntidade": {"razaoSocial": "Órgão A"},
                        "unidadeOrgao": {"municipioNome": "Rio de Janeiro"},
                    }
                ),
            ),
        )
        self.connection.commit()

    def tearDown(self) -> None:
        self.connection.close()

    def test_executa_pergunta_estruturada_de_ponta_a_ponta(self) -> None:
        perguntas: list[str] = []

        def analyzer_simulado(pergunta: str) -> QueryAnalysis:
            perguntas.append(pergunta)
            return QueryAnalysis(
                route=QueryRoute.STRUCTURED,
                intent=QueryIntent.LOOKUP,
                entity=QueryEntity.CONTRATO,
                bases=[KnowledgeBase.ESTRUTURADA],
                filters=QueryFilters(ano=2026),
            )

        result = executar_consulta_adaptativa(
            self.connection,
            "Quais contratos existem em 2026?",
            analyzer_fn=analyzer_simulado,
        )

        self.assertEqual(perguntas, ["Quais contratos existem em 2026?"])
        self.assertEqual(result.plan.route, QueryRoute.STRUCTURED)
        self.assertEqual(result.structured_result.row_count, 1)
        self.assertEqual(
            result.structured_result.rows[0]["numero_controle_pncp"],
            "001-2-000001/2026",
        )

    def test_rejeita_rota_rag_sem_fallback_para_sql(self) -> None:
        def analyzer_simulado(_pergunta: str) -> QueryAnalysis:
            return QueryAnalysis(
                route=QueryRoute.RAG,
                intent=QueryIntent.DOCUMENTAL,
                entity=QueryEntity.DOCUMENTO,
                bases=[KnowledgeBase.NORMATIVA],
                semantic_query="documentos para habilitação",
                needs_document_retrieval=True,
            )

        with self.assertRaisesRegex(QueryExecutionError, "rag"):
            executar_consulta_adaptativa(
                self.connection,
                "Quais documentos são exigidos?",
                analyzer_fn=analyzer_simulado,
            )

    def test_rejeita_rota_hibrida_sem_fallback_para_sql(self) -> None:
        def analyzer_simulado(_pergunta: str) -> QueryAnalysis:
            return QueryAnalysis(
                route=QueryRoute.HYBRID,
                intent=QueryIntent.COMPARISON,
                entity=QueryEntity.CONTRATO,
                bases=[KnowledgeBase.ESTRUTURADA, KnowledgeBase.NORMATIVA],
                semantic_query="Compare contratos e regras.",
                needs_document_retrieval=True,
            )

        with self.assertRaisesRegex(QueryExecutionError, "hybrid"):
            executar_consulta_adaptativa(
                self.connection,
                "Compare contratos e regras.",
                analyzer_fn=analyzer_simulado,
            )


if __name__ == "__main__":
    unittest.main()
