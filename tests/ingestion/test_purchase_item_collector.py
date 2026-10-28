import threading
import unittest

from src.ingestion.pncp_collector import PurchaseItemCollector
from src.schema import CollectionCheckpoint, CollectedRecord, SourceConfig


class FakePNCPClient:
    def __init__(self):
        self.requests = []

    def build_url(self, endpoint, params=None, base_url=None):
        return f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}"

    def get(self, endpoint, params=None, **kwargs):
        self.requests.append((endpoint, params, kwargs))
        return [
            {
                "numeroItem": 1,
                "descricao": "Serviço preservado",
                "quantidade": 2,
                "temResultado": True,
            }
        ]


class PurchaseItemCollectorTests(unittest.TestCase):
    def source(self):
        return SourceConfig(
            id="pncp_itens_contratacao_rj",
            base="https://pncp.example",
            endpoint=(
                "/api/pncp/v1/orgaos/{cnpj}/compras/{ano}/{sequencial}/itens"
            ),
            collector="pncp_purchase_items",
            resource_type="contratacao_item",
            targets=(
                {
                    "cnpj": "12345678000199",
                    "ano": 2026,
                    "sequencial": 7,
                    "numero_controle": "12345678000199-1-000007/2026",
                },
            ),
            key_fields=("numeroItem",),
        )

    def test_preserves_payload_and_builds_parent_scoped_external_id(self):
        collected = list(
            PurchaseItemCollector(FakePNCPClient()).collect(self.source())
        )
        records = [item for item in collected if isinstance(item, CollectedRecord)]

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].payload["descricao"], "Serviço preservado")
        self.assertEqual(
            records[0].external_id,
            "12345678000199-1-000007/2026|1",
        )
        self.assertEqual(records[0].cursor, "1")
        self.assertEqual(
            records[0].query_params["numeroControlePNCP"],
            "12345678000199-1-000007/2026",
        )
        self.assertEqual(collected[-1], CollectionCheckpoint("1"))

    def test_endpoint_is_called_once_without_fake_pagination(self):
        client = FakePNCPClient()

        list(PurchaseItemCollector(client).collect(self.source()))

        self.assertEqual(len(client.requests), 1)
        self.assertIsNone(client.requests[0][1])
        self.assertTrue(client.requests[0][2]["not_found_as_empty"])

    def test_parallel_requests_keep_target_checkpoint_order(self):
        class ConcurrentClient(FakePNCPClient):
            def __init__(self):
                super().__init__()
                self.barrier = threading.Barrier(2)

            def get(self, endpoint, params=None, **kwargs):
                self.requests.append((endpoint, params, kwargs))
                self.barrier.wait(timeout=1)
                return [{"numeroItem": 1, "temResultado": False}]

        first = self.source().targets[0]
        second = {
            **first,
            "sequencial": 8,
            "numero_controle": "12345678000199-1-000008/2026",
        }
        source = SourceConfig(
            **{
                **self.source().__dict__,
                "targets": (first, second),
                "max_concurrency": 2,
            }
        )

        collected = list(PurchaseItemCollector(ConcurrentClient()).collect(source))
        checkpoints = [
            item.cursor
            for item in collected
            if isinstance(item, CollectionCheckpoint)
        ]

        self.assertEqual(checkpoints, ["1", "2"])

    def test_empty_target_still_emits_checkpoint(self):
        client = FakePNCPClient()
        client.get = lambda *args, **kwargs: []

        collected = list(PurchaseItemCollector(client).collect(self.source()))

        self.assertEqual(collected, [CollectionCheckpoint("1")])

    def test_rejects_target_without_parent_control_number(self):
        source = self.source()
        incomplete = SourceConfig(
            **{
                **source.__dict__,
                "targets": (
                    {"cnpj": "12345678000199", "ano": 2026, "sequencial": 7},
                ),
            }
        )

        with self.assertRaisesRegex(ValueError, "numero_controle"):
            list(PurchaseItemCollector(FakePNCPClient()).collect(incomplete))


if __name__ == "__main__":
    unittest.main()
