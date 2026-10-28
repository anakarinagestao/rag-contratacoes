"""Contrato HTTP do endpoint adaptativo sem depender de LLM, FAISS ou FTS5."""

from __future__ import annotations

import unittest
from contextlib import nullcontext
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.security import get_current_user
from app.main import app
from app.modules.query.services import NormalizedDatabaseUnavailable
from src.faiss_errors import FaissIndexMissingError
from src.query.adaptive_executor import AdaptiveExecutionResult
from src.query.analyzer import QueryAnalysisError
from src.query.planner import planejar_execucao
from src.query.schemas import (
    AdaptiveQueryResult,
    KnowledgeBase,
    QueryAnalysis,
    QueryEntity,
    QueryIntent,
    QueryRoute,
    StructuredQueryResult,
)
from src.retrieve import RankedChunk


class AdaptiveQueryRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        def test_db():
            yield object()

        app.dependency_overrides[get_db] = test_db
        app.dependency_overrides[get_current_user] = lambda: object()
        self.addCleanup(app.dependency_overrides.clear)
        self.client = self.enterContext(TestClient(app))

    def test_retorna_saida_estruturada_quando_plano_e_structured(self) -> None:
        analysis = QueryAnalysis(
            route=QueryRoute.STRUCTURED,
            intent=QueryIntent.LOOKUP,
            entity=QueryEntity.CONTRATO,
            bases=[KnowledgeBase.ESTRUTURADA],
        )
        execution = AdaptiveExecutionResult(
            analysis=analysis,
            plan=planejar_execucao(analysis),
            structured_result=AdaptiveQueryResult(
                analysis=analysis,
                plan=planejar_execucao(analysis),
                structured_result=StructuredQueryResult(
                    entity=QueryEntity.CONTRATO,
                    columns=["ano"],
                    rows=[{"ano": 2026}],
                    row_count=1,
                    limit_applied=20,
                ),
            ),
        )
        with patch(
            "app.modules.query.routes.executar_consulta_adaptativa_api",
            return_value=execution,
        ):
            response = self.client.post(
                "/query/ask",
                json={"pergunta": "Quais contratos existem em 2026?"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["route"], "structured")
        self.assertEqual(response.json()["structured_result"]["rows"], [{"ano": 2026}])
        self.assertIn("A consulta retornou 1 contrato", response.json()["answer"])
        self.assertEqual(response.json()["external_documents"], [])

    def test_saudacao_retorna_resposta_social_sem_executar_analyzer(self) -> None:
        with patch(
            "app.modules.query.routes.executar_consulta_adaptativa_api",
        ) as execute:
            response = self.client.post(
                "/query/ask",
                json={"pergunta": "Olá, bom dia!"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("assistente PNCP/RJ", response.json()["answer"])
        self.assertIsNone(response.json()["route"])
        self.assertIsNone(response.json()["analysis"])
        execute.assert_not_called()

    def test_encaminha_contexto_validado_sem_historico_textual(self) -> None:
        analysis = QueryAnalysis(
            route=QueryRoute.STRUCTURED,
            intent=QueryIntent.AGGREGATE,
            entity=QueryEntity.CONTRATO,
            bases=[KnowledgeBase.ESTRUTURADA],
            operations=["count"],
        )
        execution = AdaptiveExecutionResult(
            analysis=analysis,
            plan=planejar_execucao(analysis),
            structured_result=AdaptiveQueryResult(
                analysis=analysis,
                plan=planejar_execucao(analysis),
                structured_result=StructuredQueryResult(
                    entity=QueryEntity.CONTRATO,
                    columns=["count"], rows=[{"count": 1}], row_count=1,
                    limit_applied=20,
                ),
            ),
        )
        with patch(
            "app.modules.query.routes.executar_consulta_adaptativa_api",
            return_value=execution,
        ) as execute:
            response = self.client.post(
                "/query/ask",
                json={
                    "pergunta": "E somente os estaduais?",
                    "contexto": analysis.model_dump(mode="json"),
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(execute.call_args.kwargs["previous_analysis"], analysis)

    def test_retorna_saida_rag_quando_plano_e_rag(self) -> None:
        analysis = QueryAnalysis(
            route=QueryRoute.RAG,
            intent=QueryIntent.DOCUMENTAL,
            entity=QueryEntity.DOCUMENTO,
            bases=[KnowledgeBase.NORMATIVA],
            semantic_query="Quais documentos são exigidos?",
            needs_document_retrieval=True,
        )
        rag_result = type(
            "RagResult",
            (),
            {
                "answer": "Os documentos exigidos constam na evidência [1].",
                "sources": [
                    RankedChunk(
                        chunk_id="chunk-1",
                        content="Conteúdo",
                        rrf_score=0.03,
                        bm25_rank=1,
                        bm25_score=1.0,
                        dense_rank=1,
                        dense_score=0.9,
                        metadata={},
                    )
                ],
            },
        )()
        execution = AdaptiveExecutionResult(
            analysis=analysis,
            plan=planejar_execucao(analysis),
            rag_result=rag_result,
        )
        with patch(
            "app.modules.query.routes.executar_consulta_adaptativa_api",
            return_value=execution,
        ):
            response = self.client.post(
                "/query/ask",
                json={"pergunta": "Quais documentos são exigidos?", "top_k": 3},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["route"], "rag")
        self.assertEqual(response.json()["answer"], rag_result.answer)
        self.assertEqual(response.json()["sources"][0]["chunk_id"], "chunk-1")

    def test_retorna_dados_sql_e_fontes_documentais_na_rota_hybrid(self) -> None:
        analysis = QueryAnalysis(
            route=QueryRoute.HYBRID,
            intent=QueryIntent.COMPARISON,
            entity=QueryEntity.CONTRATO,
            bases=[KnowledgeBase.ESTRUTURADA, KnowledgeBase.NORMATIVA],
            semantic_query="Compare valores e cláusulas.",
            needs_document_retrieval=True,
        )
        rag_result = type(
            "RagResult",
            (),
            {"answer": "O valor está nos dados SQL e a cláusula consta em [1].", "sources": []},
        )()
        structured = AdaptiveQueryResult(
            analysis=analysis,
            plan=planejar_execucao(analysis),
            structured_result=StructuredQueryResult(
                entity=QueryEntity.CONTRATO,
                columns=["valor_global"],
                rows=[{"valor_global": 100.0}],
                row_count=1,
                limit_applied=20,
            ),
        )
        execution = AdaptiveExecutionResult(
            analysis=analysis,
            plan=planejar_execucao(analysis),
            structured_result=structured,
            rag_result=rag_result,
        )
        with patch(
            "app.modules.query.routes.executar_consulta_adaptativa_api",
            return_value=execution,
        ):
            response = self.client.post(
                "/query/ask",
                json={"pergunta": "Compare valores e cláusulas."},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["route"], "hybrid")
        self.assertEqual(response.json()["structured_result"]["rows"], [{"valor_global": 100.0}])
        self.assertTrue(response.json()["answer"].startswith(rag_result.answer))
        self.assertIn("Cobertura não comprovada", response.json()["answer"])

    def test_catalogo_com_processamento_pendente_retorna_202(self) -> None:
        analysis = QueryAnalysis(
            route=QueryRoute.CATALOG,
            intent=QueryIntent.LOOKUP,
            entity=QueryEntity.DOCUMENTO,
            bases=[KnowledgeBase.AGREGADA],
        )
        document = {
            "id": 7,
            "tipoEntidade": "contratacao",
            "numeroControlePNCP": "12345678000199-1-000001/2026",
            "sequencialDocumento": 1,
            "periodoColeta": "fixture",
            "statusProcessamento": "PENDENTE",
            "documentoPersistido": False,
            "chunksPersistidos": 0,
        }
        task = {
            "task_id": 11,
            "catalog_entry_id": 7,
            "status": "PENDENTE",
            "status_url": "/documents/processing-tasks/11",
            "created": True,
        }
        execution = AdaptiveExecutionResult(
            analysis=analysis,
            plan=planejar_execucao(analysis),
            external_documents=[document],
            processing_tasks=[task],
        )

        with patch(
            "app.modules.query.routes.executar_consulta_adaptativa_api",
            return_value=execution,
        ):
            response = self.client.post(
                "/query/ask",
                json={"pergunta": "Quais documentos estão disponíveis?"},
            )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["processing_tasks"], [task])
        returned_document = response.json()["external_documents"][0]
        for key, value in document.items():
            self.assertEqual(returned_document[key], value)

    def test_retorna_campos_invalidos_quando_analyzer_rejeita_plano(self) -> None:
        erro = QueryAnalysisError(
            "A LLM retornou um plano inválido.",
            erros_validacao=[
                {
                    "campo": "needs_document_retrieval",
                    "mensagem": "A rota hybrid exige needs_document_retrieval igual a true.",
                    "tipo": "value_error",
                }
            ],
        )
        with patch(
            "app.modules.query.routes.executar_consulta_adaptativa_api",
            side_effect=erro,
        ):
            response = self.client.post(
                "/query/ask",
                json={"pergunta": "Compare contratos e documento."},
            )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            response.json()["detail"]["errosValidacao"][0]["campo"],
            "needs_document_retrieval",
        )

    def test_base_normalizada_indisponivel_retorna_503(self) -> None:
        with patch(
            "app.modules.query.routes.executar_consulta_adaptativa_api",
            side_effect=NormalizedDatabaseUnavailable(
                "Base normalizada indisponível."
            ),
        ):
            response = self.client.post(
                "/query/ask",
                json={"pergunta": "Quais contratos existem?"},
            )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json()["detail"],
            "Base normalizada indisponível.",
        )

    def test_rag_sem_faiss_retorna_503_controlado(self) -> None:
        with patch(
            "app.modules.query.routes.executar_consulta_adaptativa_api",
            side_effect=FaissIndexMissingError(),
        ):
            response = self.client.post(
                "/query/ask",
                json={"pergunta": "Quais documentos são exigidos?"},
            )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json()["detail"],
            {
                "code": "faiss_index_missing",
                "message": "Recuperação documental temporariamente indisponível.",
            },
        )

    def test_hybrid_sem_faiss_nao_retorna_resposta_parcial(self) -> None:
        analysis = QueryAnalysis(
            route=QueryRoute.HYBRID,
            intent=QueryIntent.COMPARISON,
            entity=QueryEntity.CONTRATO,
            bases=[KnowledgeBase.ESTRUTURADA, KnowledgeBase.NORMATIVA],
            semantic_query="Compare valores e cláusulas.",
            needs_document_retrieval=True,
        )
        structured = AdaptiveQueryResult(
            analysis=analysis,
            plan=planejar_execucao(analysis),
            structured_result=StructuredQueryResult(
                entity=QueryEntity.CONTRATO,
                columns=["valor_global"],
                rows=[{"valor_global": 100.0}],
                row_count=1,
                limit_applied=20,
            ),
        )
        structured_executed = []

        def execute_structured(*_args):
            structured_executed.append(True)
            return structured

        def fail_documental(**kwargs):
            self.assertIs(kwargs["structured_result"], structured.structured_result)
            raise FaissIndexMissingError()

        with patch(
            "app.modules.query.services.analisar_pergunta",
            return_value=analysis,
        ), patch(
            "app.modules.query.services._normalized_connection",
            return_value=nullcontext(object()),
        ), patch(
            "app.modules.query.services._executar_analise_normalizada",
            side_effect=execute_structured,
        ), patch(
            "src.generate.gerar_resposta_rag",
            side_effect=fail_documental,
        ):
            response = self.client.post(
                "/query/ask",
                json={"pergunta": "Compare os valores com as cláusulas."},
            )

        self.assertEqual(structured_executed, [True])
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("answer", response.json())
        self.assertNotIn("structured_result", response.json())


if __name__ == "__main__":
    unittest.main()
