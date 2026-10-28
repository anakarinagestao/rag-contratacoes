from datetime import date
import unittest

from pydantic import ValidationError

from src.query.schemas import (
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
)


class QuerySchemaTests(unittest.TestCase):
    def test_plano_estruturado_aceita_multiplas_operacoes(self):
        analysis = QueryAnalysis(
            route=QueryRoute.STRUCTURED,
            intent=QueryIntent.AGGREGATE,
            entity=QueryEntity.CONTRATO,
            bases=[KnowledgeBase.ESTRUTURADA],
            operations=[
                StructuredOperation.SUM,
                StructuredOperation.COUNT,
                StructuredOperation.GROUP_BY,
                StructuredOperation.ORDER_BY,
            ],
            aggregation_field=StructuredField.VALOR_GLOBAL,
            group_by=[StructuredField.MUNICIPIO],
            order_by=[
                OrderBy(
                    field=StructuredField.VALOR_GLOBAL,
                    direction=SortDirection.DESC,
                )
            ],
            filters=QueryFilters(
                period_start=date(2026, 8, 1),
                period_end=date(2026, 8, 31),
            ),
        )

        self.assertEqual(
            analysis.operations,
            [
                StructuredOperation.SUM,
                StructuredOperation.COUNT,
                StructuredOperation.GROUP_BY,
                StructuredOperation.ORDER_BY,
            ],
        )
        self.assertEqual(analysis.group_by, [StructuredField.MUNICIPIO])
        self.assertEqual(analysis.order_by[0].direction, SortDirection.DESC)

    def test_plano_rag_aceita_filtro_documental_sem_operacao_sql(self):
        analysis = QueryAnalysis(
            route=QueryRoute.RAG,
            intent=QueryIntent.DOCUMENTAL,
            entity=QueryEntity.DOCUMENTO,
            bases=[KnowledgeBase.ESTRUTURADA, KnowledgeBase.NORMATIVA],
            filters=QueryFilters(
                numero_controle_pncp="12345678000199-1-000001/2026",
                tipo_documento="Edital",
            ),
            semantic_query="documentos exigidos para habilitação",
            needs_document_retrieval=True,
        )

        self.assertEqual(analysis.operations, [])
        self.assertTrue(analysis.needs_document_retrieval)

    def test_rejeita_periodo_invertido(self):
        with self.assertRaises(ValidationError):
            QueryFilters(
                period_start=date(2026, 9, 1),
                period_end=date(2026, 8, 31),
            )

    def test_rejeita_intervalo_de_valor_invertido(self):
        with self.assertRaises(ValidationError):
            QueryFilters(valor_minimo=10, valor_maximo=5)

    def test_campo_data_nulo_da_llm_usa_default_seguro(self):
        filters = QueryFilters.model_validate({"ano": 2025, "campo_data": None})

        self.assertEqual(filters.campo_data.value, "data_publicacao")

    def test_agregacao_exige_campo(self):
        with self.assertRaisesRegex(ValidationError, "aggregation_field"):
            QueryAnalysis(
                route=QueryRoute.STRUCTURED,
                intent=QueryIntent.AGGREGATE,
                entity=QueryEntity.CONTRATO,
                bases=[KnowledgeBase.ESTRUTURADA],
                operations=[StructuredOperation.SUM],
            )

    def test_group_by_exige_operacao_correspondente(self):
        with self.assertRaisesRegex(ValidationError, "group_by exige"):
            QueryAnalysis(
                route=QueryRoute.STRUCTURED,
                intent=QueryIntent.AGGREGATE,
                entity=QueryEntity.CONTRATO,
                bases=[KnowledgeBase.ESTRUTURADA],
                group_by=[StructuredField.MUNICIPIO],
            )

    def test_rota_rag_rejeita_operacao_estruturada(self):
        with self.assertRaisesRegex(ValidationError, "rota rag"):
            QueryAnalysis(
                route=QueryRoute.RAG,
                intent=QueryIntent.DOCUMENTAL,
                entity=QueryEntity.DOCUMENTO,
                bases=[KnowledgeBase.NORMATIVA],
                operations=[StructuredOperation.COUNT],
                needs_document_retrieval=True,
            )

    def test_rota_hibrida_exige_base_estruturada_e_documental(self):
        with self.assertRaisesRegex(ValidationError, "base estruturada"):
            QueryAnalysis(
                route=QueryRoute.HYBRID,
                intent=QueryIntent.COMPARISON,
                entity=QueryEntity.CONTRATO,
                bases=[KnowledgeBase.NORMATIVA],
                needs_document_retrieval=True,
            )

    def test_rota_structured_aceita_bases_logicas_de_ata_e_pca(self):
        ata = QueryAnalysis(
            route=QueryRoute.STRUCTURED,
            intent=QueryIntent.LOOKUP,
            entity=QueryEntity.ATA_REGISTRO_PRECO,
            bases=[KnowledgeBase.CONVERSACIONAL],
            operations=[StructuredOperation.LIST],
        )
        pca = QueryAnalysis(
            route=QueryRoute.STRUCTURED,
            intent=QueryIntent.AGGREGATE,
            entity=QueryEntity.PCA,
            bases=[KnowledgeBase.AGREGADA],
            operations=[StructuredOperation.COUNT],
        )

        self.assertEqual(ata.entity, QueryEntity.ATA_REGISTRO_PRECO)
        self.assertEqual(pca.entity, QueryEntity.PCA)

    def test_rota_structured_rejeita_base_incorreta_para_pca(self):
        with self.assertRaisesRegex(ValidationError, "base correspondente"):
            QueryAnalysis(
                route=QueryRoute.STRUCTURED,
                intent=QueryIntent.LOOKUP,
                entity=QueryEntity.PCA,
                bases=[KnowledgeBase.ESTRUTURADA],
                operations=[StructuredOperation.LIST],
            )


if __name__ == "__main__":
    unittest.main()
