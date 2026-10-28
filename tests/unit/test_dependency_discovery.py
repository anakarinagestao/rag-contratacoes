from pathlib import Path
import tempfile
import unittest

from src.ingest import RawRepository, ingest_records
from src.ingestion.dependency_discovery import (
    DependencyDiscoveryError,
    discover_dependency_targets,
)
from src.schema import CollectedRecord


class SilentLogger:
    def event(self, event, **fields):
        pass


class DependencyDiscoveryTests(unittest.TestCase):
    PURCHASE = "12345678000199-1-000007/2023"
    CONTRACT = "12345678000199-2-000009/2023"

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        path = Path(self.temporary.name) / "raw.db"
        self.repository = RawRepository(f"sqlite:///{path}")

    def tearDown(self):
        self.repository.close()
        self.temporary.cleanup()

    def ingest(self, resource, external_id, payload):
        result = ingest_records(
            self.repository,
            f"incremental-{resource}",
            ("id",),
            [
                CollectedRecord(
                    f"incremental-{resource}",
                    payload,
                    "https://pncp.example/api",
                    "1",
                    1,
                    external_id=external_id,
                )
            ],
            resource_type=resource,
            logger=SilentLogger(),
        )
        return result.run_id

    def test_discovers_items_results_and_commitments_from_changed_raw(self):
        purchase_run = self.ingest(
            "contratacao", self.PURCHASE, {"estado": "PUBLICADA"}
        )
        item_run = self.ingest(
            "contratacao_item",
            f"{self.PURCHASE}|3",
            {
                "numeroControlePNCPCompra": self.PURCHASE,
                "numeroItem": 3,
            },
        )
        contract_run = self.ingest(
            "contrato", self.CONTRACT, {"estado": "ATIVO"}
        )

        self.assertEqual(
            discover_dependency_targets(
                self.repository.connection,
                purchase_run,
                "contratacao_item",
            ),
            ({"numero_controle": self.PURCHASE},),
        )
        self.assertEqual(
            discover_dependency_targets(
                self.repository.connection,
                item_run,
                "resultado_item",
            ),
            (
                {
                    "numero_controle": self.PURCHASE,
                    "numero_item": 3,
                },
            ),
        )
        self.assertEqual(
            discover_dependency_targets(
                self.repository.connection,
                contract_run,
                "empenho",
            ),
            ({"numero_controle": self.CONTRACT},),
        )

    def test_unchanged_replay_does_not_create_another_discovery(self):
        payload = {"estado": "PUBLICADA"}
        self.ingest("contratacao", self.PURCHASE, payload)
        replay_run = self.ingest("contratacao", self.PURCHASE, payload)

        self.assertEqual(
            discover_dependency_targets(
                self.repository.connection,
                replay_run,
                "contratacao_item",
            ),
            (),
        )

    def test_rejects_failed_or_incompatible_parent_run(self):
        run_id = self.ingest(
            "contratacao", self.PURCHASE, {"estado": "PUBLICADA"}
        )
        with self.assertRaisesRegex(
            DependencyDiscoveryError, "incompatível"
        ):
            discover_dependency_targets(
                self.repository.connection, run_id, "empenho"
            )
        with self.assertRaisesRegex(
            DependencyDiscoveryError, "não suportado"
        ):
            discover_dependency_targets(
                self.repository.connection, run_id, "desconhecido"
            )


if __name__ == "__main__":
    unittest.main()
