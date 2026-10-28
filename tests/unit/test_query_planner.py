"""Testes do Planner determinístico, sem banco, LLM ou retrieval."""

import unittest

from pydantic import ValidationError

from src.query.planner import planejar_execucao
from src.query.schemas import (
    ExecutionPlan,
    ExecutionTool,
    KnowledgeBase,
    QueryAnalysis,
    QueryEntity,
    QueryIntent,
    QueryRoute,
)


def criar_analise(route: QueryRoute) -> QueryAnalysis:
    """Monta análises mínimas válidas para cada rota."""

    dados_por_rota = {
        QueryRoute.STRUCTURED: {
            "intent": QueryIntent.LOOKUP,
            "entity": QueryEntity.CONTRATO,
            "bases": [KnowledgeBase.ESTRUTURADA],
            "needs_document_retrieval": False,
        },
        QueryRoute.RAG: {
            "intent": QueryIntent.DOCUMENTAL,
            "entity": QueryEntity.DOCUMENTO,
            "bases": [KnowledgeBase.NORMATIVA],
            "needs_document_retrieval": True,
            "semantic_query": "Quais documentos são exigidos?",
        },
        QueryRoute.HYBRID: {
            "intent": QueryIntent.COMPARISON,
            "entity": QueryEntity.CONTRATACAO,
            "bases": [KnowledgeBase.ESTRUTURADA, KnowledgeBase.NORMATIVA],
            "needs_document_retrieval": True,
            "semantic_query": "Compare exigências e valores.",
        },
    }
    return QueryAnalysis(route=route, **dados_por_rota[route])


class QueryPlannerTests(unittest.TestCase):
    def test_rota_estruturada_usa_apenas_banco_estruturado(self) -> None:
        analise = criar_analise(QueryRoute.STRUCTURED)

        plano = planejar_execucao(analise)

        self.assertEqual(plano.route, QueryRoute.STRUCTURED)
        self.assertEqual(plano.analysis, analise)
        self.assertEqual(plano.tools, [ExecutionTool.STRUCTURED_DATABASE])

    def test_rota_rag_usa_apenas_retrieval(self) -> None:
        plano = planejar_execucao(criar_analise(QueryRoute.RAG))

        self.assertEqual(plano.tools, [ExecutionTool.RAG_RETRIEVAL])

    def test_rota_hibrida_mantem_ordem_das_duas_ferramentas(self) -> None:
        plano = planejar_execucao(criar_analise(QueryRoute.HYBRID))

        self.assertEqual(
            plano.tools,
            [ExecutionTool.STRUCTURED_DATABASE, ExecutionTool.RAG_RETRIEVAL],
        )

    def test_plano_rejeita_ferramenta_incompativel_com_rota(self) -> None:
        analise = criar_analise(QueryRoute.STRUCTURED)

        with self.assertRaises(ValidationError):
            ExecutionPlan(
                route=QueryRoute.STRUCTURED,
                tools=[ExecutionTool.RAG_RETRIEVAL],
                analysis=analise,
            )


if __name__ == "__main__":
    unittest.main()
