from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from src.normalization.runner import normalize_core


class NormalizationRunnerTests(unittest.TestCase):
    def test_normalizes_core_resources_in_an_independent_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw_path = root / "raw.db"
            normalized_path = root / "normalized.db"
            self._create_raw_fixture(raw_path)

            result = normalize_core(raw_path, normalized_path)

            self.assertEqual(result.raw_records_seen, 3)
            self.assertEqual(result.rejected, 0)
            self.assertEqual(result.issues, 1)
            self.assertEqual(result.counts["contratacao"], 1)
            self.assertEqual(result.counts["contratacao_item"], 1)
            self.assertEqual(result.counts["resultado_item"], 1)
            self.assertEqual(len(result.source_snapshot_sha256), 64)

            connection = sqlite3.connect(normalized_path)
            try:
                self.assertEqual(
                    connection.execute(
                        "SELECT valor_total_homologado_centavos "
                        "FROM resultado_item"
                    ).fetchone()[0],
                    6_600_000,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT status FROM normalization_run"
                    ).fetchone()[0],
                    "SUCCESS",
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT issue_code FROM normalization_issue"
                    ).fetchone()[0],
                    "SUSPECT_DATE",
                )
                self.assertEqual(
                    connection.execute("PRAGMA integrity_check").fetchone()[0],
                    "ok",
                )
                self.assertEqual(
                    connection.execute("PRAGMA foreign_key_check").fetchall(),
                    [],
                )
            finally:
                connection.close()

    def test_refuses_to_overwrite_destination(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw_path = root / "raw.db"
            normalized_path = root / "normalized.db"
            self._create_raw_fixture(raw_path)
            normalized_path.touch()
            with self.assertRaises(FileExistsError):
                normalize_core(raw_path, normalized_path)

    def test_normalizes_dependent_resources_and_filters_pca_scope(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw_path = root / "raw.db"
            normalized_path = root / "normalized.db"
            inventory_path = root / "inventory.json"
            self._create_extended_raw_fixture(raw_path)
            inventory_path.write_text(
                json.dumps(
                    {
                        "scope": {
                            "inventory_id": "inventory-test",
                            "members": [
                                {
                                    "cnpj": "12345678000199",
                                    "codigo_unidade": "U1",
                                }
                            ],
                        }
                    }
                ),
                encoding="utf-8",
            )

            result = normalize_core(
                raw_path,
                normalized_path,
                scope_inventory_path=inventory_path,
                known_commitment_gap_count=4,
            )

            self.assertEqual(result.raw_records_seen, 6)
            self.assertEqual(result.skipped, 1)
            self.assertEqual(result.issues, 1)
            self.assertEqual(result.counts["contrato"], 1)
            self.assertEqual(result.counts["empenho"], 1)
            self.assertEqual(result.counts["ata_registro_preco"], 1)
            self.assertEqual(result.counts["pca"], 1)
            self.assertEqual(result.counts["pca_item"], 1)

            connection = sqlite3.connect(normalized_path)
            try:
                self.assertEqual(
                    connection.execute(
                        "SELECT parent_resolution_status FROM contrato"
                    ).fetchone()[0],
                    "RESOLVED",
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT parent_resolution_status FROM ata_registro_preco"
                    ).fetchone()[0],
                    "RESOLVED",
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT issue_code FROM normalization_issue"
                    ).fetchone()[0],
                    "OUT_OF_SCOPE",
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT coverage_status, known_gap_count "
                        "FROM normalization_coverage "
                        "WHERE resource_type = 'empenho'"
                    ).fetchone(),
                    ("PARTIAL", 4),
                )
                self.assertEqual(
                    connection.execute("PRAGMA foreign_key_check").fetchall(),
                    [],
                )
            finally:
                connection.close()

    @staticmethod
    def _create_raw_fixture(path: Path) -> None:
        connection = sqlite3.connect(path)
        connection.execute(
            "CREATE TABLE raw_asset ("
            "id TEXT PRIMARY KEY, resource_type TEXT NOT NULL, "
            "external_id TEXT NOT NULL, payload_hash TEXT NOT NULL, "
            "payload TEXT NOT NULL)"
        )
        purchase_id = "12345678000199-1-000001/2023"
        payloads = (
            (
                "raw-purchase",
                "contratacao",
                purchase_id,
                {
                    "numeroControlePNCP": purchase_id,
                    "anoCompra": 2023,
                    "sequencialCompra": 1,
                    "modalidadeId": 6,
                    "srp": False,
                    "valorTotalEstimado": 66000.0,
                    "valorTotalHomologado": 66000.0,
                    "orgaoEntidade": {
                        "cnpj": "12345678000199",
                        "razaoSocial": "Orgao",
                    },
                    "unidadeOrgao": {
                        "codigoUnidade": "U1",
                        "nomeUnidade": "Unidade",
                        "ufSigla": "RJ",
                    },
                },
            ),
            (
                "raw-item",
                "contratacao_item",
                f"{purchase_id}|1",
                {
                    "numeroItem": 1,
                    "temResultado": True,
                    "valorTotal": 66000.0,
                    "valorUnitarioEstimado": 66000.0,
                },
            ),
            (
                "raw-result",
                "resultado_item",
                f"{purchase_id}|1|1",
                {
                    "numeroControlePNCPCompra": purchase_id,
                    "numeroItem": 1,
                    "sequencialResultado": 1,
                    "tipoPessoa": "PJ",
                    "niFornecedor": "99887766000155",
                    "valorTotalHomologado": 66000.0,
                    "valorUnitarioHomologado": 66000.0,
                    "dataResultado": "2013-09-13",
                },
            ),
        )
        for raw_id, resource, external_id, payload in payloads:
            connection.execute(
                "INSERT INTO raw_asset VALUES (?, ?, ?, ?, ?)",
                (
                    raw_id,
                    resource,
                    external_id,
                    f"hash-{raw_id}",
                    json.dumps(payload),
                ),
            )
        connection.commit()
        connection.close()

    @staticmethod
    def _create_extended_raw_fixture(path: Path) -> None:
        connection = sqlite3.connect(path)
        connection.execute(
            "CREATE TABLE raw_asset ("
            "id TEXT PRIMARY KEY, resource_type TEXT NOT NULL, "
            "external_id TEXT NOT NULL, payload_hash TEXT NOT NULL, "
            "payload TEXT NOT NULL, query_params TEXT NOT NULL)"
        )
        purchase_id = "12345678000199-1-000001/2023"
        contract_id = "12345678000199-2-000001/2023"
        organization = {"cnpj": "12345678000199", "razaoSocial": "Orgao"}
        unit = {
            "codigoUnidade": "U1",
            "nomeUnidade": "Unidade",
            "ufSigla": "RJ",
        }
        records = (
            (
                "raw-purchase",
                "contratacao",
                purchase_id,
                {
                    "numeroControlePNCP": purchase_id,
                    "anoCompra": 2023,
                    "sequencialCompra": 1,
                    "modalidadeId": 6,
                    "srp": True,
                    "orgaoEntidade": organization,
                    "unidadeOrgao": unit,
                },
                {},
            ),
            (
                "raw-contract",
                "contrato",
                contract_id,
                {
                    "numeroControlePNCP": contract_id,
                    "numeroControlePncpCompra": purchase_id,
                    "tipoPessoa": "PJ",
                    "niFornecedor": "99887766000155",
                    "orgaoEntidade": organization,
                    "unidadeOrgao": unit,
                },
                {},
            ),
            (
                "raw-commitment",
                "empenho",
                f"{contract_id}|1",
                {
                    "sequencialEmpenho": 1,
                    "tipoPessoa": "PJ",
                    "niCredorFornecedor": "99887766000155",
                    "valorTotal": "10.00",
                },
                {},
            ),
            (
                "raw-ata",
                "ata_registro_preco",
                f"{purchase_id}-000001",
                {
                    "numeroControlePNCPAta": f"{purchase_id}-000001",
                    "numeroControlePNCPCompra": purchase_id,
                    "cnpjOrgao": organization["cnpj"],
                    "nomeOrgao": organization["razaoSocial"],
                    "codigoUnidadeOrgao": unit["codigoUnidade"],
                    "nomeUnidadeOrgao": unit["nomeUnidade"],
                    "cancelado": False,
                },
                {"uf": "RJ"},
            ),
            (
                "raw-pca-in",
                "pca",
                "12345678000199-0-000001/2023",
                {
                    "unidadeResponsavel": "Unidade",
                    "itens": [
                        {
                            "cnpj": "12345678000199",
                            "codigoUnidade": "U1",
                            "anoPca": 2023,
                            "numeroItem": 1,
                        }
                    ],
                },
                {},
            ),
            (
                "raw-pca-out",
                "pca",
                "00000000000000-0-000001/2023",
                {
                    "itens": [
                        {
                            "cnpj": "00000000000000",
                            "codigoUnidade": "U9",
                            "anoPca": 2023,
                            "numeroItem": 1,
                        }
                    ],
                },
                {},
            ),
        )
        for raw_id, resource, external_id, payload, query_params in records:
            connection.execute(
                "INSERT INTO raw_asset VALUES (?, ?, ?, ?, ?, ?)",
                (
                    raw_id,
                    resource,
                    external_id,
                    f"hash-{raw_id}",
                    json.dumps(payload),
                    json.dumps(query_params),
                ),
            )
        connection.commit()
        connection.close()


if __name__ == "__main__":
    unittest.main()
