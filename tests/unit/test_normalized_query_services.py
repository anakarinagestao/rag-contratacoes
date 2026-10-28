"""Integração dos serviços HTTP com a base normalizada isolada da RAW."""

from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app.core.config import settings
from app.modules.query.services import (
    NormalizedDatabaseUnavailable,
    executar_consulta_adaptativa_api,
    executar_consulta_estruturada,
)
from src.query.schemas import (
    KnowledgeBase,
    QueryAnalysis,
    QueryEntity,
    QueryIntent,
    QueryRoute,
)


ROOT = Path(__file__).resolve().parents[2]


def _analysis(route: QueryRoute) -> QueryAnalysis:
    return QueryAnalysis(
        route=route,
        intent=(
            QueryIntent.LOOKUP
            if route is QueryRoute.STRUCTURED
            else QueryIntent.DOCUMENTAL
        ),
        entity=(
            QueryEntity.CONTRATACAO
            if route is QueryRoute.STRUCTURED
            else QueryEntity.DOCUMENTO
        ),
        bases=(
            [KnowledgeBase.ESTRUTURADA]
            if route is QueryRoute.STRUCTURED
            else [KnowledgeBase.NORMATIVA]
        ),
        semantic_query="documentos" if route is QueryRoute.RAG else None,
        needs_document_retrieval=route is QueryRoute.RAG,
    )


def _hybrid_analysis() -> QueryAnalysis:
    return QueryAnalysis(
        route=QueryRoute.HYBRID,
        intent=QueryIntent.COMPARISON,
        entity=QueryEntity.CONTRATACAO,
        bases=[KnowledgeBase.ESTRUTURADA, KnowledgeBase.NORMATIVA],
        semantic_query="Compare valores e regras.",
        needs_document_retrieval=True,
    )


def _create_normalized_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        (ROOT / "migrations" / "100_normalized_schema.sql").read_text(
            encoding="utf-8"
        )
    )
    lineage = ("raw-normalized", "hash-normalized", "2026-10-21T00:00:00Z")
    connection.execute(
        "INSERT INTO orgao VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("12345678000199", "Órgão Normalizado", "E", "E", *lineage),
    )
    connection.execute(
        "INSERT INTO unidade VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "12345678000199",
            "001",
            "Unidade Normalizada",
            "3304557",
            "Rio de Janeiro",
            "RJ",
            "Rio de Janeiro",
            *lineage,
        ),
    )
    connection.execute(
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
            "Aquisição normalizada",
            0,
            100_00,
            90_00,
            "2025-01-10T00:00:00Z",
            *lineage,
        ),
    )
    connection.commit()
    connection.close()


class NormalizedQueryServicesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.database = Path(self.directory.name) / "normalized.db"
        _create_normalized_database(self.database)
        self.database_url = f"sqlite:///{self.database}"

    def test_rota_estruturada_consulta_exclusivamente_base_normalizada(self) -> None:
        analysis = _analysis(QueryRoute.STRUCTURED)
        result = executar_consulta_estruturada(
            object(),  # type: ignore[arg-type]
            "Liste as contratações",
            analyzer_fn=lambda _: analysis,
            normalized_database_url=self.database_url,
        )

        self.assertEqual(result.structured_result.row_count, 1)
        self.assertEqual(
            result.structured_result.rows[0]["orgao"],
            "Órgão Normalizado",
        )

    def test_configuracao_ausente_falha_sem_fallback_para_raw(self) -> None:
        with patch.object(settings, "normalized_database_url", None):
            with self.assertRaisesRegex(
                NormalizedDatabaseUnavailable,
                "não configurada",
            ):
                executar_consulta_estruturada(
                    object(),  # type: ignore[arg-type]
                    "Liste as contratações",
                    analyzer_fn=lambda _: _analysis(QueryRoute.STRUCTURED),
                )

    def test_caminho_invalido_retorna_erro_publico_sem_expor_caminho(self) -> None:
        secret_path = Path(self.directory.name) / "segredo" / "ausente.db"
        with self.assertRaises(NormalizedDatabaseUnavailable) as raised:
            executar_consulta_estruturada(
                object(),  # type: ignore[arg-type]
                "Liste as contratações",
                analyzer_fn=lambda _: _analysis(QueryRoute.STRUCTURED),
                normalized_database_url=f"sqlite:///{secret_path}",
            )
        self.assertEqual(str(raised.exception), "Base normalizada indisponível.")
        self.assertNotIn(str(secret_path), str(raised.exception))

    def test_rota_rag_nao_exige_base_normalizada(self) -> None:
        expected = object()
        with patch.object(settings, "normalized_database_url", None):
            result = executar_consulta_adaptativa_api(
                object(),  # type: ignore[arg-type]
                "Quais documentos?",
                3,
                analyzer_fn=lambda _: _analysis(QueryRoute.RAG),
                rag_executor=lambda: expected,
            )

        self.assertIs(result.rag_result, expected)
        self.assertIsNone(result.structured_result)

    def test_rota_hibrida_entrega_dados_da_base_normalizada_ao_rag(self) -> None:
        received = []
        expected = object()

        def hybrid_executor(structured):
            received.append(structured)
            return expected

        result = executar_consulta_adaptativa_api(
            object(),  # type: ignore[arg-type]
            "Compare valores e regras.",
            3,
            analyzer_fn=lambda _: _hybrid_analysis(),
            hybrid_executor=hybrid_executor,
            normalized_database_url=self.database_url,
        )

        self.assertIs(result.rag_result, expected)
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].structured_result.row_count, 1)
        self.assertEqual(
            received[0].structured_result.rows[0]["orgao"],
            "Órgão Normalizado",
        )


if __name__ == "__main__":
    unittest.main()
