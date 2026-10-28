import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from src.inspection.freeze_scope_inventory import build_inventory_document


class FreezeScopeInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "raw.db"
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "CREATE TABLE raw_asset (resource_type TEXT, payload TEXT)"
            )

    def tearDown(self):
        self.temporary.cleanup()

    def insert(self, resource, payload):
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "INSERT INTO raw_asset VALUES (?, ?)",
                (resource, json.dumps(payload)),
            )

    @staticmethod
    def nested(cnpj, unit, *, uf="RJ", modality=None):
        payload = {
            "orgaoEntidade": {"cnpj": cnpj},
            "unidadeOrgao": {
                "codigoUnidade": unit,
                "ufSigla": uf,
            },
        }
        if modality is not None:
            payload["modalidadeId"] = modality
        return payload

    def test_freezes_members_and_reports_historical_scope_contamination(self):
        self.insert(
            "contratacao",
            self.nested("12345678000199", "00042", modality=1),
        )
        self.insert(
            "contrato", self.nested("12345678000199", "00042")
        )
        self.insert(
            "ata_registro_preco",
            {
                "cnpjOrgao": "12345678000199",
                "codigoUnidadeOrgao": "00042",
            },
        )
        self.insert(
            "pca",
            {
                "itens": [
                    {
                        "cnpj": "12345678000199",
                        "codigoUnidade": "99999",
                    }
                ]
            },
        )

        document = build_inventory_document(self.database)

        self.assertEqual(document["scope"]["member_count"], 1)
        self.assertEqual(
            document["scope"]["members"],
            [
                {
                    "cnpj": "12345678000199",
                    "codigo_unidade": "00042",
                    "uf": "RJ",
                }
            ],
        )
        self.assertEqual(
            document["validation"]["observed_modalities"], [1]
        )
        self.assertIn(
            2,
            document["validation"][
                "expected_modalities_without_observation"
            ],
        )
        self.assertEqual(
            document["validation"]["resource_pair_validation"]["pca"][
                "outside_inventory"
            ],
            1,
        )
        self.assertEqual(
            document["validation"]["pca_plans_outside_inventory"], 1
        )
        self.assertEqual(
            document["coverage_plans"]["contratacao"]["members"],
            [f"modalidade:{number}" for number in range(1, 15)],
        )


if __name__ == "__main__":
    unittest.main()
