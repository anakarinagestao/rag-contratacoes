from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from src.normalization.incremental import normalize_incremental
from src.normalization.runner import normalize_core


class IncrementalNormalizationTests(unittest.TestCase):
    def test_updates_once_is_idempotent_and_ignores_out_of_order_version(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw_path = root / "raw.db"
            normalized_path = root / "normalized.db"
            inventory_path = root / "inventory.json"
            purchase_id = "12345678000199-1-000001/2023"
            self._create_raw(raw_path)
            self._write_inventory(inventory_path)
            self._insert_purchase(
                raw_path,
                raw_id="raw-a",
                purchase_id=purchase_id,
                object_text="Versao A",
                digest="hash-a",
                source_updated_at="2026-01-01T10:00:00",
                fetched_at="2026-01-01T11:00:00",
            )
            normalize_core(
                raw_path,
                normalized_path,
                scope_inventory_path=inventory_path,
            )
            self._insert_purchase(
                raw_path,
                raw_id="raw-b",
                purchase_id=purchase_id,
                object_text="Versao B",
                digest="hash-b",
                source_updated_at="2026-01-02T10:00:00",
                fetched_at="2026-01-02T11:00:00",
            )
            self._insert_purchase(
                raw_path,
                raw_id="raw-old",
                purchase_id=purchase_id,
                object_text="Versao antiga fora de ordem",
                digest="hash-old",
                source_updated_at="2025-12-31T10:00:00",
                fetched_at="2026-01-03T11:00:00",
            )

            first = normalize_incremental(
                raw_path,
                normalized_path,
                scope_inventory_path=inventory_path,
            )
            second = normalize_incremental(
                raw_path,
                normalized_path,
                scope_inventory_path=inventory_path,
            )

            self.assertEqual(first.inserted, 0)
            self.assertEqual(first.updated, 3)
            self.assertEqual(first.unchanged, 0)
            self.assertEqual(second.inserted, 0)
            self.assertEqual(second.updated, 0)
            self.assertEqual(second.unchanged, 3)
            connection = sqlite3.connect(normalized_path)
            try:
                self.assertEqual(
                    connection.execute(
                        "SELECT objeto_compra FROM contratacao"
                    ).fetchone()[0],
                    "Versao B",
                )
                for table, key in (
                    ("orgao", "12345678000199"),
                    ("unidade", "12345678000199|U1"),
                    ("contratacao", purchase_id),
                ):
                    rows = connection.execute(
                        "SELECT raw_asset_id, is_current "
                        "FROM normalization_lineage "
                        "WHERE normalized_table = ? AND entity_key = ? "
                        "ORDER BY raw_asset_id",
                        (table, key),
                    ).fetchall()
                    self.assertEqual(rows, [("raw-a", 0), ("raw-b", 1)])
                self.assertEqual(
                    connection.execute(
                        "SELECT updated_count FROM normalization_reconciliation "
                        "WHERE normalization_run_id = ? AND table_name = 'contratacao'",
                        (first.run_id,),
                    ).fetchone()[0],
                    1,
                )
                self.assertEqual(
                    connection.execute("PRAGMA foreign_key_check").fetchall(),
                    [],
                )
            finally:
                connection.close()

    @staticmethod
    def _create_raw(path: Path) -> None:
        connection = sqlite3.connect(path)
        connection.execute(
            "CREATE TABLE raw_asset ("
            "id TEXT PRIMARY KEY, base_id TEXT NOT NULL, "
            "resource_type TEXT NOT NULL, external_id TEXT NOT NULL, "
            "payload_hash TEXT NOT NULL, payload TEXT NOT NULL, "
            "query_params TEXT NOT NULL, source_updated_at TEXT, "
            "fetched_at TEXT NOT NULL)"
        )
        connection.commit()
        connection.close()

    @staticmethod
    def _write_inventory(path: Path) -> None:
        path.write_text(
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

    @staticmethod
    def _insert_purchase(
        path: Path,
        *,
        raw_id: str,
        purchase_id: str,
        object_text: str,
        digest: str,
        source_updated_at: str,
        fetched_at: str,
    ) -> None:
        payload = {
            "numeroControlePNCP": purchase_id,
            "anoCompra": 2023,
            "sequencialCompra": 1,
            "modalidadeId": 6,
            "srp": False,
            "objetoCompra": object_text,
            "dataAtualizacaoGlobal": source_updated_at,
            "orgaoEntidade": {
                "cnpj": "12345678000199",
                "razaoSocial": "Orgao",
            },
            "unidadeOrgao": {
                "codigoUnidade": "U1",
                "nomeUnidade": "Unidade",
                "ufSigla": "RJ",
            },
        }
        connection = sqlite3.connect(path)
        connection.execute(
            "INSERT INTO raw_asset VALUES (?, 'pncp', 'contratacao', ?, ?, ?, "
            "'{}', ?, ?)",
            (
                raw_id,
                purchase_id,
                digest,
                json.dumps(payload),
                source_updated_at,
                fetched_at,
            ),
        )
        connection.commit()
        connection.close()


if __name__ == "__main__":
    unittest.main()
