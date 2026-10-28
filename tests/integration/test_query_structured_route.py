"""Contrato HTTP da primeira rota de consulta estruturada."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.security import get_current_user
from app.main import app
from app.modules.query.services import NormalizedDatabaseUnavailable
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


def _resultado_estruturado() -> AdaptiveQueryResult:
    analysis = QueryAnalysis(
        route=QueryRoute.STRUCTURED,
        intent=QueryIntent.LOOKUP,
        entity=QueryEntity.CONTRATO,
        bases=[KnowledgeBase.ESTRUTURADA],
    )
    return AdaptiveQueryResult(
        analysis=analysis,
        plan=planejar_execucao(analysis),
        structured_result=StructuredQueryResult(
            entity=QueryEntity.CONTRATO,
            columns=["numero_controle_pncp", "ano"],
            rows=[{"numero_controle_pncp": "001-2-000001/2026", "ano": 2026}],
            row_count=1,
            limit_applied=20,
        ),
    )


class StructuredQueryRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        def test_db():
            yield object()

        app.dependency_overrides[get_db] = test_db
        app.dependency_overrides[get_current_user] = lambda: object()
        self.addCleanup(app.dependency_overrides.clear)
        self.client = self.enterContext(TestClient(app))

    def test_retorna_analise_plano_e_registros(self) -> None:
        with patch(
            "app.modules.query.routes.executar_consulta_estruturada",
            return_value=_resultado_estruturado(),
        ) as executar:
            response = self.client.post(
                "/query/structured",
                json={"pergunta": "Quais contratos existem em 2026?"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["analysis"]["route"], "structured")
        self.assertEqual(
            response.json()["plan"]["tools"],
            ["structured_database"],
        )
        self.assertEqual(
            response.json()["structured_result"]["rows"],
            [{"numero_controle_pncp": "001-2-000001/2026", "ano": 2026}],
        )
        executar.assert_called_once()

    def test_rejeita_pergunta_vazia_antes_da_execucao(self) -> None:
        with patch("app.modules.query.routes.executar_consulta_estruturada") as executar:
            response = self.client.post("/query/structured", json={"pergunta": ""})

        self.assertEqual(response.status_code, 422)
        executar.assert_not_called()

    def test_base_normalizada_indisponivel_retorna_503(self) -> None:
        with patch(
            "app.modules.query.routes.executar_consulta_estruturada",
            side_effect=NormalizedDatabaseUnavailable(
                "Base normalizada indisponível."
            ),
        ):
            response = self.client.post(
                "/query/structured",
                json={"pergunta": "Quais contratos existem?"},
            )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json()["detail"],
            "Base normalizada indisponível.",
        )


if __name__ == "__main__":
    unittest.main()
