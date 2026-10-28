from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path


MIGRATION = (
    Path(__file__).parents[2] / "migrations" / "100_normalized_schema.sql"
)


class NormalizedSchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.executescript(MIGRATION.read_text(encoding="utf-8"))

    def tearDown(self) -> None:
        self.connection.close()

    def test_creates_expected_tables(self) -> None:
        tables = {
            row[0]
            for row in self.connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        self.assertTrue(
            {
                "normalization_run",
                "normalization_coverage",
                "orgao",
                "unidade",
                "fornecedor",
                "contratacao",
                "contratacao_item",
                "resultado_item",
                "contrato",
                "empenho",
                "ata_registro_preco",
                "pca",
                "pca_item",
                "pca_contratacao_link",
                "normalization_lineage",
                "normalization_issue",
            }.issubset(tables)
        )

    def test_migration_is_idempotent(self) -> None:
        self.connection.executescript(MIGRATION.read_text(encoding="utf-8"))
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM sqlite_master "
                "WHERE type = 'table' AND name = 'contratacao'"
            ).fetchone()[0],
            1,
        )

    def test_item_requires_a_procurement_parent(self) -> None:
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(
                "INSERT INTO contratacao_item "
                "(numero_controle_pncp, numero_item, tem_resultado, "
                "current_source_raw_asset_id, current_source_payload_hash, "
                "normalized_at) VALUES (?, ?, ?, ?, ?, ?)",
                ("compra-ausente", 1, 0, "raw-1", "hash-1", "2026-10-19"),
            )

    def test_unresolved_contract_preserves_external_parent(self) -> None:
        self._insert_organization_and_unit()
        self._insert_supplier()
        self.connection.execute(
            "INSERT INTO contrato "
            "(numero_controle_pncp, numero_controle_compra_externo, "
            "contratacao_id, parent_resolution_status, orgao_cnpj, "
            "codigo_unidade, tipo_pessoa_fornecedor, ni_fornecedor, "
            "current_source_raw_asset_id, current_source_payload_hash, "
            "normalized_at) VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "contrato-1",
                "compra-fora-do-recorte",
                "OUT_OF_SCOPE",
                "12345678000199",
                "U1",
                "PJ",
                "99887766000155",
                "raw-contrato-1",
                "hash-contrato-1",
                "2026-10-19",
            ),
        )
        row = self.connection.execute(
            "SELECT numero_controle_compra_externo, contratacao_id, "
            "parent_resolution_status FROM contrato"
        ).fetchone()
        self.assertEqual(row, ("compra-fora-do-recorte", None, "OUT_OF_SCOPE"))

    def test_resolved_contract_requires_a_real_procurement(self) -> None:
        self._insert_organization_and_unit()
        self._insert_supplier()
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(
                "INSERT INTO contrato "
                "(numero_controle_pncp, numero_controle_compra_externo, "
                "contratacao_id, parent_resolution_status, orgao_cnpj, "
                "codigo_unidade, tipo_pessoa_fornecedor, ni_fornecedor, "
                "current_source_raw_asset_id, current_source_payload_hash, "
                "normalized_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "contrato-1",
                    "compra-ausente",
                    "compra-ausente",
                    "RESOLVED",
                    "12345678000199",
                    "U1",
                    "PJ",
                    "99887766000155",
                    "raw-contrato-1",
                    "hash-contrato-1",
                    "2026-10-19",
                ),
            )

    def test_raw_lineage_is_opaque_and_does_not_require_raw_table(self) -> None:
        self.connection.execute(
            "INSERT INTO normalization_run "
            "(id, source_snapshot_sha256, transform_version, started_at, "
            "status) VALUES (?, ?, ?, ?, ?)",
            ("run-1", "snapshot-hash", "v1", "2026-10-19", "RUNNING"),
        )
        self.connection.execute(
            "INSERT INTO normalization_lineage "
            "(normalized_table, entity_key, raw_asset_id, payload_hash, "
            "normalization_run_id, is_current, observed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "contratacao",
                "compra-1",
                "raw-id-from-another-database",
                "payload-hash",
                "run-1",
                1,
                "2026-10-19",
            ),
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM normalization_lineage"
            ).fetchone()[0],
            1,
        )

    def test_commitment_defaults_to_partial_coverage(self) -> None:
        self._insert_organization_and_unit()
        self._insert_supplier()
        self.connection.execute(
            "INSERT INTO contrato "
            "(numero_controle_pncp, numero_controle_compra_externo, "
            "contratacao_id, parent_resolution_status, orgao_cnpj, "
            "codigo_unidade, tipo_pessoa_fornecedor, ni_fornecedor, "
            "current_source_raw_asset_id, current_source_payload_hash, "
            "normalized_at) VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "contrato-1",
                "compra-fora-do-recorte",
                "OUT_OF_SCOPE",
                "12345678000199",
                "U1",
                "PJ",
                "99887766000155",
                "raw-contrato-1",
                "hash-contrato-1",
                "2026-10-19",
            ),
        )
        self.connection.execute(
            "INSERT INTO empenho "
            "(numero_controle_contrato, sequencial_empenho, "
            "tipo_pessoa_credor, ni_credor_fornecedor, "
            "current_source_raw_asset_id, current_source_payload_hash, "
            "normalized_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "contrato-1",
                1,
                "PJ",
                "99887766000155",
                "raw-empenho-1",
                "hash-empenho-1",
                "2026-10-19",
            ),
        )
        coverage = self.connection.execute(
            "SELECT coverage_status FROM empenho"
        ).fetchone()[0]
        self.assertEqual(coverage, "PARTIAL")

    def test_contract_can_preserve_missing_supplier_identifier(self) -> None:
        self._insert_organization_and_unit()
        self.connection.execute(
            "INSERT INTO contrato "
            "(numero_controle_pncp, numero_controle_compra_externo, "
            "contratacao_id, parent_resolution_status, orgao_cnpj, "
            "codigo_unidade, tipo_pessoa_fornecedor, ni_fornecedor, "
            "nome_fornecedor_informado, supplier_resolution_status, "
            "current_source_raw_asset_id, current_source_payload_hash, "
            "normalized_at) VALUES (?, ?, NULL, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?)",
            (
                "contrato-sem-ni",
                "compra-fora-recorte",
                "OUT_OF_SCOPE",
                "12345678000199",
                "U1",
                "PE",
                "Fornecedor informado em texto",
                "MISSING_IDENTIFIER",
                "raw-contract",
                "hash-contract",
                "2026-10-19",
            ),
        )
        row = self.connection.execute(
            "SELECT ni_fornecedor, supplier_resolution_status FROM contrato"
        ).fetchone()
        self.assertEqual(tuple(row), (None, "MISSING_IDENTIFIER"))

    def _insert_organization_and_unit(self) -> None:
        self.connection.execute(
            "INSERT INTO orgao "
            "(cnpj, razao_social, current_source_raw_asset_id, "
            "current_source_payload_hash, normalized_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                "12345678000199",
                "Orgao de teste",
                "raw-orgao-1",
                "hash-orgao-1",
                "2026-10-19",
            ),
        )
        self.connection.execute(
            "INSERT INTO unidade "
            "(orgao_cnpj, codigo_unidade, uf_sigla, "
            "current_source_raw_asset_id, current_source_payload_hash, "
            "normalized_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                "12345678000199",
                "U1",
                "RJ",
                "raw-unidade-1",
                "hash-unidade-1",
                "2026-10-19",
            ),
        )

    def _insert_supplier(self) -> None:
        self.connection.execute(
            "INSERT INTO fornecedor "
            "(tipo_pessoa, ni_fornecedor, current_source_raw_asset_id, "
            "current_source_payload_hash, normalized_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                "PJ",
                "99887766000155",
                "raw-fornecedor-1",
                "hash-fornecedor-1",
                "2026-10-19",
            ),
        )


if __name__ == "__main__":
    unittest.main()
