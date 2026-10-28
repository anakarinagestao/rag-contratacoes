"""Testes unitários da ferramenta SQL controlada para a camada RAW."""

from __future__ import annotations

import json
import sqlite3
import unittest

from app.core.database.init_db import init_db
from src.query.schemas import (
    AggregateSortField,
    KnowledgeBase,
    OrderBy,
    QueryAnalysis,
    QueryEntity,
    QueryFilters,
    QueryIntent,
    QueryRoute,
    SortDirection,
    StructuredField,
    StructuredOperation,
    ValueFilterField,
)
from src.query.structured_tool import (
    StructuredQueryError,
    executar_consulta_estruturada,
)


class StructuredToolTests(unittest.TestCase):
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
        self._inserir_asset(
            "contrato-1",
            "contrato",
            "001-2-000001/2026",
            {
                "anoContrato": 2026,
                "dataPublicacaoPncp": "2026-08-10T12:00:00",
                "valorGlobal": 500000.0,
                "valorInicial": 450000.0,
                "objetoContrato": "Licenciamento de software",
                "nomeRazaoSocialFornecedor": "Empresa Alfa",
                "orgaoEntidade": {"razaoSocial": "Órgão A"},
                "unidadeOrgao": {"municipioNome": "Rio de Janeiro"},
            },
        )
        self._inserir_asset(
            "contratacao-1",
            "contratacao",
            "002-1-000001/2026",
            {
                "anoCompra": 2026,
                "dataPublicacaoPncp": "2026-08-11T12:00:00",
                "valorTotalHomologado": 100.0,
                "valorTotalEstimado": 120.0,
                "modalidadeNome": "Pregão Eletrônico",
                "objetoCompra": "Material de escritório",
                "orgaoEntidade": {"razaoSocial": "Órgão A"},
                "unidadeOrgao": {"municipioNome": "Rio de Janeiro"},
            },
        )
        self._inserir_asset(
            "contratacao-2",
            "contratacao",
            "002-1-000002/2026",
            {
                "anoCompra": 2026,
                "dataPublicacaoPncp": "2026-08-12T12:00:00",
                "valorTotalHomologado": 200.0,
                "valorTotalEstimado": 220.0,
                "modalidadeNome": "Dispensa",
                "objetoCompra": "Material hospitalar",
                "orgaoEntidade": {"razaoSocial": "Órgão B"},
                "unidadeOrgao": {"municipioNome": "Niterói"},
            },
        )
        self._inserir_asset(
            "contrato-fiocruz",
            "contrato",
            "003-2-000001/2026",
            {
                "anoContrato": 2026,
                "dataPublicacaoPncp": "2026-08-13T12:00:00",
                "valorGlobal": 10.0,
                "valorInicial": 10.0,
                "objetoContrato": "Serviço de teste",
                "nomeRazaoSocialFornecedor": "Empresa Teste",
                "orgaoEntidade": {"razaoSocial": "FUNDACAO OSWALDO CRUZ"},
                "unidadeOrgao": {"municipioNome": "Rio de Janeiro"},
            },
        )

    def tearDown(self) -> None:
        self.connection.close()

    def _inserir_asset(self, asset_id: str, resource_type: str, external_id: str, payload: dict) -> None:
        self.connection.execute(
            """
            INSERT INTO raw_asset (
                id, source, base_id, natureza, resource_type, endpoint,
                external_id, query_params, payload, payload_hash, fetched_at,
                corpus_version, sensitivity, ingestion_run_id, created_at
            ) VALUES (?, 'teste', 'pncp', 'estruturada', ?, 'pncp://teste', ?,
                      '{}', ?, 'hash', '2026-01-01', 'v1', 'publico', 'run-1', '2026-01-01')
            """,
            (asset_id, resource_type, external_id, json.dumps(payload)),
        )
        self.connection.commit()

    def test_lista_contratos_com_filtros_estruturados(self) -> None:
        analysis = QueryAnalysis(
            route=QueryRoute.STRUCTURED,
            intent=QueryIntent.LOOKUP,
            entity=QueryEntity.CONTRATO,
            bases=[KnowledgeBase.ESTRUTURADA],
            filters=QueryFilters(ano=2026, valor_minimo=400000),
        )

        result = executar_consulta_estruturada(self.connection, analysis)

        self.assertEqual(result.row_count, 1)
        self.assertEqual(result.rows[0]["numero_controle_pncp"], "001-2-000001/2026")
        self.assertEqual(result.rows[0]["fornecedor"], "Empresa Alfa")

    def test_agrega_contratacoes_por_modalidade(self) -> None:
        analysis = QueryAnalysis(
            route=QueryRoute.STRUCTURED,
            intent=QueryIntent.AGGREGATE,
            entity=QueryEntity.CONTRATACAO,
            bases=[KnowledgeBase.ESTRUTURADA],
            operations=[
                StructuredOperation.COUNT,
                StructuredOperation.SUM,
                StructuredOperation.GROUP_BY,
                StructuredOperation.ORDER_BY,
            ],
            aggregation_field=StructuredField.VALOR_GLOBAL,
            group_by=[StructuredField.MODALIDADE],
            order_by=[OrderBy(field=StructuredField.MODALIDADE, direction=SortDirection.ASC)],
        )

        result = executar_consulta_estruturada(self.connection, analysis)

        self.assertEqual(result.row_count, 2)
        self.assertEqual(result.rows[0], {"modalidade": "Dispensa", "count": 1, "sum_valor_global": 200.0})
        self.assertEqual(result.rows[1], {"modalidade": "Pregão Eletrônico", "count": 1, "sum_valor_global": 100.0})

    def test_ordena_agrupamento_pela_quantidade(self) -> None:
        self._inserir_asset(
            "contratacao-3",
            "contratacao",
            "002-1-000003/2026",
            {
                "anoCompra": 2026,
                "dataPublicacaoPncp": "2026-08-13T12:00:00",
                "valorTotalHomologado": 300.0,
                "valorTotalEstimado": 350.0,
                "modalidadeNome": "Dispensa",
                "objetoCompra": "Material hospitalar complementar",
                "orgaoEntidade": {"razaoSocial": "Órgão B"},
                "unidadeOrgao": {"municipioNome": "Niterói"},
            },
        )
        analysis = QueryAnalysis(
            route=QueryRoute.STRUCTURED,
            intent=QueryIntent.AGGREGATE,
            entity=QueryEntity.CONTRATACAO,
            bases=[KnowledgeBase.ESTRUTURADA],
            operations=[
                StructuredOperation.COUNT,
                StructuredOperation.GROUP_BY,
                StructuredOperation.ORDER_BY,
            ],
            group_by=[StructuredField.MODALIDADE],
            order_by=[OrderBy(field=AggregateSortField.COUNT)],
        )

        result = executar_consulta_estruturada(self.connection, analysis)

        self.assertEqual(result.rows[0], {"modalidade": "Dispensa", "count": 2})

    def test_faixa_de_valor_estimado_nao_usa_valor_global(self) -> None:
        analysis = QueryAnalysis(
            route=QueryRoute.STRUCTURED,
            intent=QueryIntent.LOOKUP,
            entity=QueryEntity.CONTRATACAO,
            bases=[KnowledgeBase.ESTRUTURADA],
            filters=QueryFilters(
                campo_valor=ValueFilterField.VALOR_ESTIMADO,
                valor_minimo=210,
            ),
        )

        result = executar_consulta_estruturada(self.connection, analysis)

        self.assertEqual(result.row_count, 1)
        self.assertEqual(result.rows[0]["numero_controle_pncp"], "002-1-000002/2026")

    def test_parametros_nao_permitem_injecao_em_filtro_textual(self) -> None:
        analysis = QueryAnalysis(
            route=QueryRoute.STRUCTURED,
            intent=QueryIntent.LOOKUP,
            entity=QueryEntity.CONTRATO,
            bases=[KnowledgeBase.ESTRUTURADA],
            filters=QueryFilters(orgao="' OR 1=1 --"),
        )

        result = executar_consulta_estruturada(self.connection, analysis)

        self.assertEqual(result.row_count, 0)

    def test_lista_contratacoes_filtradas_por_objeto(self) -> None:
        analysis = QueryAnalysis(
            route=QueryRoute.STRUCTURED,
            intent=QueryIntent.LOOKUP,
            entity=QueryEntity.CONTRATACAO,
            bases=[KnowledgeBase.ESTRUTURADA],
            filters=QueryFilters(objeto="hospitalar"),
        )

        result = executar_consulta_estruturada(self.connection, analysis)

        self.assertEqual(result.row_count, 1)
        self.assertEqual(result.rows[0]["numero_controle_pncp"], "002-1-000002/2026")

    def test_filtro_textual_nao_diferencia_acentos(self) -> None:
        analysis = QueryAnalysis(
            route=QueryRoute.STRUCTURED,
            intent=QueryIntent.LOOKUP,
            entity=QueryEntity.CONTRATO,
            bases=[KnowledgeBase.ESTRUTURADA],
            filters=QueryFilters(orgao="Fundação Oswaldo Cruz", ano=2026),
        )

        result = executar_consulta_estruturada(self.connection, analysis)

        self.assertEqual(result.row_count, 1)
        self.assertEqual(
            result.rows[0]["numero_controle_pncp"],
            "003-2-000001/2026",
        )

    def test_rejeita_filtro_de_documento_na_base_estruturada(self) -> None:
        analysis = QueryAnalysis(
            route=QueryRoute.STRUCTURED,
            intent=QueryIntent.LOOKUP,
            entity=QueryEntity.CONTRATO,
            bases=[KnowledgeBase.ESTRUTURADA],
            filters=QueryFilters(tipo_documento="Edital"),
        )

        with self.assertRaisesRegex(StructuredQueryError, "Filtros de documento"):
            executar_consulta_estruturada(self.connection, analysis)



    def test_restaurar_query_only_apos_consulta(self) -> None:
        analysis = QueryAnalysis(
            route=QueryRoute.STRUCTURED,
            intent=QueryIntent.LOOKUP,
            entity=QueryEntity.CONTRATO,
            bases=[KnowledgeBase.ESTRUTURADA],
        )

        for initial_state in (0, 1):
            with self.subTest(query_only=initial_state):
                self.connection.execute(
                    f"PRAGMA query_only = {initial_state}"
                )
                executar_consulta_estruturada(self.connection, analysis)
                actual_state = self.connection.execute(
                    "PRAGMA query_only"
                ).fetchone()[0]
                self.assertEqual(actual_state, initial_state)

    def test_limite_aplicado_nao_ultrapassa_100(self) -> None:
        analysis = QueryAnalysis(
            route=QueryRoute.STRUCTURED,
            intent=QueryIntent.LOOKUP,
            entity=QueryEntity.CONTRATO,
            bases=[KnowledgeBase.ESTRUTURADA],
            limit=100,
        )

        result = executar_consulta_estruturada(self.connection, analysis)

        self.assertLessEqual(result.row_count, 100)
        self.assertEqual(result.limit_applied, 100)

if __name__ == "__main__":
    unittest.main()
