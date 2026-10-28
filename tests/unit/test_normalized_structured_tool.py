"""Consultas seguras sobre a camada normalizada em SQLite temporário."""

from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path

from src.query.normalized_structured_tool import executar_consulta_normalizada
from src.query.schemas import (
    KnowledgeBase,
    QueryAnalysis,
    QueryEntity,
    QueryFilters,
    QueryIntent,
    QueryRoute,
    StructuredField,
    StructuredOperation,
)
from src.query.structured_tool import StructuredQueryError


ROOT = Path(__file__).resolve().parents[2]


class NormalizedStructuredToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.connection.executescript(
            (ROOT / "migrations" / "100_normalized_schema.sql").read_text(
                encoding="utf-8"
            )
        )
        lineage = ("raw-1", "hash-1", "2026-10-21T00:00:00Z")
        self.connection.execute(
            "INSERT INTO orgao VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("12345678000199", "Secretaria Estadual", "E", "E", *lineage),
        )
        self.connection.execute(
            "INSERT INTO unidade VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "12345678000199",
                "001",
                "Unidade Central",
                "3304557",
                "Rio de Janeiro",
                "RJ",
                "Rio de Janeiro",
                *lineage,
            ),
        )
        self.connection.execute(
            "INSERT INTO fornecedor VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "PJ",
                "99887766000155",
                "Fornecedor Tecnologia",
                None,
                None,
                None,
                None,
                "BRA",
                0,
                *lineage,
            ),
        )
        self.connection.execute(
            """INSERT INTO contratacao (
                numero_controle_pncp, orgao_cnpj, codigo_unidade, ano_compra,
                sequencial_compra, modalidade_id, modalidade_nome, objeto_compra,
                srp, valor_total_estimado_centavos,
                valor_total_homologado_centavos, data_publicacao_pncp,
                current_source_raw_asset_id, current_source_payload_hash,
                normalized_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "12345678000199-1-000001/2025",
                "12345678000199",
                "001",
                2025,
                1,
                6,
                "Pregão Eletrônico",
                "Aquisição de licenças de software",
                0,
                800_000_00,
                750_000_00,
                "2025-03-10T10:00:00Z",
                *lineage,
            ),
        )
        self.connection.execute(
            """INSERT INTO contrato (
                numero_controle_pncp, numero_controle_compra_externo,
                contratacao_id, parent_resolution_status, orgao_cnpj,
                codigo_unidade, tipo_pessoa_fornecedor, ni_fornecedor,
                nome_fornecedor_informado, supplier_resolution_status,
                ano_contrato, objeto_contrato, valor_inicial_centavos,
                valor_global_centavos, data_publicacao_pncp,
                current_source_raw_asset_id, current_source_payload_hash,
                normalized_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "12345678000199-2-000010/2025",
                "12345678000199-1-000001/2025",
                "12345678000199-1-000001/2025",
                "RESOLVED",
                "12345678000199",
                "001",
                "PJ",
                "99887766000155",
                "Fornecedor Tecnologia",
                "RESOLVED",
                2025,
                "Fornecimento de licenças de uso de software corporativo",
                700_000_00,
                750_000_00,
                "2025-04-01T12:00:00Z",
                *lineage,
            ),
        )
        self.connection.execute(
            """INSERT INTO contratacao_item (
                numero_controle_pncp, numero_item, descricao, quantidade,
                unidade_medida, valor_unitario_estimado_centavos,
                valor_total_estimado_centavos, categoria_item_nome,
                situacao_item_nome, tem_resultado,
                current_source_raw_asset_id, current_source_payload_hash,
                normalized_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "12345678000199-1-000001/2025",
                1,
                "Arroz tipo 1",
                100,
                "Quilograma",
                500,
                50_000,
                "Material",
                "Homologado",
                1,
                *lineage,
            ),
        )
        self.connection.execute(
            """INSERT INTO resultado_item (
                numero_controle_pncp, numero_item, sequencial_resultado,
                tipo_pessoa_fornecedor, ni_fornecedor,
                quantidade_homologada, valor_unitario_homologado_centavos,
                valor_total_homologado_centavos, situacao_resultado_nome,
                aplicacao_beneficio_me_epp, data_resultado, data_quality_status,
                current_source_raw_asset_id, current_source_payload_hash,
                normalized_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "12345678000199-1-000001/2025",
                1,
                1,
                "PJ",
                "99887766000155",
                90,
                480,
                43_200,
                "Informado",
                1,
                "2025-05-01",
                "VALID",
                *lineage,
            ),
        )
        self.connection.execute(
            """INSERT INTO ata_registro_preco (
                numero_controle_pncp_ata, numero_controle_compra_externo,
                contratacao_id, parent_resolution_status, orgao_cnpj,
                codigo_unidade, ano_ata, numero_ata, objeto_contratacao,
                data_publicacao_pncp, data_assinatura, vigencia_inicio,
                vigencia_fim, cancelado, possibilidade_adesao, coverage_status,
                current_source_raw_asset_id, current_source_payload_hash,
                normalized_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "12345678000199-1-000001/2025-000001",
                "12345678000199-1-000001/2025",
                "12345678000199-1-000001/2025",
                "RESOLVED",
                "12345678000199",
                "001",
                2025,
                "ARP 1/2025",
                "Registro de preços de material de escritório",
                "2025-06-01",
                "2025-06-02",
                "2025-06-02",
                "2099-06-01",
                0,
                1,
                "PARTIAL",
                *lineage,
            ),
        )
        self.connection.execute(
            """INSERT INTO pca (
                id_pca_pncp, orgao_cnpj, codigo_unidade, ano_pca,
                nome_unidade, data_publicacao_pncp, source_format,
                scope_status, current_source_raw_asset_id,
                current_source_payload_hash, normalized_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "PCA-2025-001",
                "12345678000199",
                "001",
                2025,
                "Unidade Central",
                "2025-01-10",
                "TOP_LEVEL",
                "IN_SCOPE",
                *lineage,
            ),
        )
        self.connection.execute(
            """INSERT INTO pca_item (
                id_pca_pncp, numero_item, descricao, quantidade_estimada,
                unidade_fornecimento, valor_unitario_planejado_centavos,
                valor_total_planejado_centavos, data_desejada,
                grupo_contratacao_codigo, grupo_contratacao_nome,
                categoria_item_nome, current_source_raw_asset_id,
                current_source_payload_hash, normalized_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "PCA-2025-001",
                1,
                "Computadores para laboratório",
                10,
                "Unidade",
                500_000,
                5_000_000,
                "2025-08-01",
                "100",
                "Tecnologia",
                "Material",
                *lineage,
            ),
        )
        self.connection.commit()
        self.addCleanup(self.connection.close)

    @staticmethod
    def analysis(
        entity: QueryEntity,
        *,
        filters: QueryFilters | None = None,
        operations: list[StructuredOperation] | None = None,
        aggregation_field: StructuredField | None = None,
        group_by: list[StructuredField] | None = None,
    ) -> QueryAnalysis:
        operations = operations or [StructuredOperation.LIST]
        if group_by:
            operations = [*operations, StructuredOperation.GROUP_BY]
        base = {
            QueryEntity.ATA_REGISTRO_PRECO: KnowledgeBase.CONVERSACIONAL,
            QueryEntity.PCA: KnowledgeBase.AGREGADA,
            QueryEntity.PCA_ITEM: KnowledgeBase.AGREGADA,
        }.get(entity, KnowledgeBase.ESTRUTURADA)
        return QueryAnalysis(
            route=QueryRoute.STRUCTURED,
            intent=(
                QueryIntent.AGGREGATE
                if StructuredOperation.LIST not in operations
                else QueryIntent.LOOKUP
            ),
            entity=entity,
            bases=[base],
            filters=filters or QueryFilters(),
            operations=operations,
            aggregation_field=aggregation_field,
            group_by=group_by or [],
            limit=20,
        )

    def test_lista_contrato_com_joins_e_valores_em_reais(self) -> None:
        result = executar_consulta_normalizada(
            self.connection,
            self.analysis(
                QueryEntity.CONTRATO,
                filters=QueryFilters(
                    ano=2025,
                    objeto="software",
                    valor_minimo=500_000,
                    fornecedor="tecnologia",
                ),
            ),
        )

        self.assertEqual(result.row_count, 1)
        row = result.rows[0]
        self.assertEqual(row["numero_controle_pncp"], "12345678000199-2-000010/2025")
        self.assertEqual(row["orgao"], "Secretaria Estadual")
        self.assertEqual(row["municipio"], "Rio de Janeiro")
        self.assertEqual(row["fornecedor"], "Fornecedor Tecnologia")
        self.assertEqual(row["modalidade"], "Pregão Eletrônico")
        self.assertEqual(row["valor_inicial"], 700_000.0)
        self.assertEqual(row["valor_global"], 750_000.0)

    def test_agrega_contratacoes_por_modalidade(self) -> None:
        result = executar_consulta_normalizada(
            self.connection,
            self.analysis(
                QueryEntity.CONTRATACAO,
                operations=[StructuredOperation.COUNT, StructuredOperation.SUM],
                aggregation_field=StructuredField.VALOR_GLOBAL,
                group_by=[StructuredField.MODALIDADE],
            ),
        )

        self.assertEqual(
            result.rows,
            [
                {
                    "modalidade": "Pregão Eletrônico",
                    "count": 1,
                    "sum_valor_global": 750_000.0,
                }
            ],
        )

    def test_objeto_aceita_variacao_licenca_sem_falso_filtro_de_frase(self) -> None:
        result = executar_consulta_normalizada(
            self.connection,
            self.analysis(
                QueryEntity.CONTRATO,
                filters=QueryFilters(objeto="licenciamento de software"),
            ),
        )

        self.assertEqual(result.row_count, 1)
        self.assertIn("licenças", result.rows[0]["objeto"].casefold())

    def test_rejeita_campo_inexistente_para_entidade(self) -> None:
        with self.assertRaisesRegex(StructuredQueryError, "fornecedor"):
            executar_consulta_normalizada(
                self.connection,
                self.analysis(
                    QueryEntity.CONTRATACAO,
                    filters=QueryFilters(fornecedor="empresa"),
                ),
            )

    def test_valores_sao_parametrizados_e_nao_executam_sql(self) -> None:
        result = executar_consulta_normalizada(
            self.connection,
            self.analysis(
                QueryEntity.CONTRATO,
                filters=QueryFilters(objeto="software%' OR 1=1 --"),
            ),
        )

        self.assertEqual(result.rows, [])
        count = self.connection.execute("SELECT COUNT(*) FROM contrato").fetchone()[0]
        self.assertEqual(count, 1)

    def test_exige_esquema_normalizado(self) -> None:
        empty = sqlite3.connect(":memory:")
        self.addCleanup(empty.close)
        with self.assertRaisesRegex(StructuredQueryError, "tabelas ausentes"):
            executar_consulta_normalizada(
                empty,
                self.analysis(QueryEntity.CONTRATO),
            )

    def test_lista_item_com_filtros_de_produto_unidade_quantidade_e_esfera(self) -> None:
        result = executar_consulta_normalizada(
            self.connection,
            self.analysis(
                QueryEntity.CONTRATACAO_ITEM,
                filters=QueryFilters(
                    ano=2025,
                    objeto="arroz",
                    unidade_medida="quilograma",
                    quantidade_minima=50,
                    esfera="E",
                ),
            ),
        )

        self.assertEqual(result.row_count, 1)
        self.assertEqual(result.rows[0]["numero_item"], 1)
        self.assertEqual(result.rows[0]["quantidade"], 100)
        self.assertEqual(result.rows[0]["valor_unitario"], 5.0)
        self.assertEqual(result.rows[0]["valor_total"], 500.0)
        self.assertEqual(result.rows[0]["esfera"], "E")

    def test_lista_resultado_com_fornecedor_e_periodo_do_resultado(self) -> None:
        result = executar_consulta_normalizada(
            self.connection,
            self.analysis(
                QueryEntity.RESULTADO_ITEM,
                filters=QueryFilters(
                    fornecedor="tecnologia",
                    campo_data="data_resultado",
                    period_start="2025-05-01",
                    period_end="2025-05-31",
                    valor_minimo=400,
                ),
            ),
        )

        self.assertEqual(result.row_count, 1)
        self.assertEqual(result.rows[0]["sequencial_resultado"], 1)
        self.assertEqual(result.rows[0]["quantidade"], 90)
        self.assertEqual(result.rows[0]["valor_total"], 432.0)
        self.assertEqual(result.rows[0]["fornecedor"], "Fornecedor Tecnologia")
        self.assertEqual(result.rows[0]["qualidade_dado"], "VALID")

    def test_rejeita_data_de_assinatura_em_item_sem_esse_campo(self) -> None:
        with self.assertRaisesRegex(StructuredQueryError, "data_assinatura"):
            executar_consulta_normalizada(
                self.connection,
                self.analysis(
                    QueryEntity.CONTRATACAO_ITEM,
                    filters=QueryFilters(
                        campo_data="data_assinatura",
                        period_start="2025-01-01",
                    ),
                ),
            )

    def test_media_ponderada_por_kg_exclui_unidade_sem_conversao(self) -> None:
        lineage = ("raw-2", "hash-2", "2026-10-21T00:00:00Z")
        self.connection.execute(
            """INSERT INTO contratacao_item (
                numero_controle_pncp, numero_item, descricao, quantidade,
                unidade_medida, valor_unitario_estimado_centavos,
                valor_total_estimado_centavos, tem_resultado,
                current_source_raw_asset_id, current_source_payload_hash,
                normalized_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "12345678000199-1-000001/2025", 2, "Arroz em pacote",
                10, "PACOTE", 1000, 10_000, 1, *lineage,
            ),
        )
        self.connection.execute(
            """INSERT INTO resultado_item (
                numero_controle_pncp, numero_item, sequencial_resultado,
                tipo_pessoa_fornecedor, ni_fornecedor,
                quantidade_homologada, valor_unitario_homologado_centavos,
                valor_total_homologado_centavos, data_quality_status,
                current_source_raw_asset_id, current_source_payload_hash,
                normalized_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "12345678000199-1-000001/2025", 2, 1, "PJ",
                "99887766000155", 10, 1000, 10_000, "VALID", *lineage,
            ),
        )
        self.connection.commit()
        result = executar_consulta_normalizada(
            self.connection,
            self.analysis(
                QueryEntity.RESULTADO_ITEM,
                filters=QueryFilters(ano=2025, objeto="arroz", esfera="E"),
                operations=[StructuredOperation.WEIGHTED_AVG_KG],
                aggregation_field=StructuredField.VALOR_HOMOLOGADO,
            ),
        )

        self.assertEqual(result.rows[0]["weighted_avg_valor_homologado_kg"], 4.8)
        self.assertEqual(result.rows[0]["included_records"], 1)
        self.assertEqual(result.rows[0]["excluded_records"], 1)
        self.assertEqual(result.rows[0]["total_quantity_kg"], 90)
        self.assertEqual(result.warnings[0].code, "unit_conversion_exclusions")

    def test_media_ponderada_agrupada_omite_grupo_sem_massa_elegivel(self) -> None:
        result = executar_consulta_normalizada(
            self.connection,
            self.analysis(
                QueryEntity.RESULTADO_ITEM,
                filters=QueryFilters(ano=2025, objeto="arroz"),
                operations=[StructuredOperation.WEIGHTED_AVG_KG],
                aggregation_field=StructuredField.VALOR_HOMOLOGADO,
                group_by=[StructuredField.UNIDADE_MEDIDA],
            ),
        )

        self.assertEqual(result.row_count, 1)
        self.assertEqual(result.rows[0]["unidade_medida"], "Quilograma")
        self.assertIsNotNone(result.rows[0]["weighted_avg_valor_homologado_kg"])

    def test_lista_ata_vigente_com_adesao_e_proveniencia_de_cobertura(self) -> None:
        result = executar_consulta_normalizada(
            self.connection,
            self.analysis(
                QueryEntity.ATA_REGISTRO_PRECO,
                filters=QueryFilters(
                    ano=2025,
                    situacao="vigente",
                    possibilidade_adesao=True,
                    objeto="material de escritório",
                ),
            ),
        )

        self.assertEqual(result.row_count, 1)
        self.assertEqual(
            result.rows[0]["numero_controle_pncp_ata"],
            "12345678000199-1-000001/2025-000001",
        )
        self.assertEqual(result.rows[0]["situacao"], "vigente")
        self.assertEqual(result.rows[0]["possibilidade_adesao"], 1)
        self.assertEqual(result.rows[0]["cobertura"], "PARTIAL")

    def test_conta_pcas_por_ano(self) -> None:
        result = executar_consulta_normalizada(
            self.connection,
            self.analysis(
                QueryEntity.PCA,
                filters=QueryFilters(ano=2025),
                operations=[StructuredOperation.COUNT],
            ),
        )

        self.assertEqual(result.rows, [{"count": 1}])

    def test_soma_valor_planejado_dos_itens_do_pca_sem_duplicar(self) -> None:
        result = executar_consulta_normalizada(
            self.connection,
            self.analysis(
                QueryEntity.PCA_ITEM,
                filters=QueryFilters(ano=2025),
                operations=[StructuredOperation.COUNT, StructuredOperation.SUM],
                aggregation_field=StructuredField.VALOR_PLANEJADO,
                group_by=[StructuredField.CATEGORIA],
            ),
        )

        self.assertEqual(
            result.rows,
            [{"categoria": "Material", "count": 1, "sum_valor_planejado": 50_000.0}],
        )

    def test_filtra_item_pca_por_data_desejada(self) -> None:
        result = executar_consulta_normalizada(
            self.connection,
            self.analysis(
                QueryEntity.PCA_ITEM,
                filters=QueryFilters(
                    campo_data="data_desejada",
                    period_start="2025-08-01",
                    period_end="2025-08-31",
                ),
            ),
        )

        self.assertEqual(result.row_count, 1)
        self.assertEqual(result.rows[0]["id_pca_pncp"], "PCA-2025-001")
        self.assertEqual(result.rows[0]["valor_planejado"], 50_000.0)
