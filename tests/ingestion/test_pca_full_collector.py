import unittest

from src.ingestion.pncp_collector import PCAFullCollector
from src.schema import CollectionCheckpoint, CollectedRecord, SourceConfig


class FakePNCPClient:
    def __init__(self):
        self.requests = []
        self.plan_payload = {
            "uasg": "123456",
            "unidadeResponsavel": "Unidade preservada",
            "itens": [
                {
                    "cnpj": "12345678000199",
                    "anoPca": 2023,
                    "sequencialPca": 7,
                    "numeroItem": 1,
                    "descricao": "Conteúdo RAW preservado",
                }
            ],
        }

    def build_url(self, endpoint, params=None, base_url=None):
        return f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}"

    def get(self, endpoint, params=None, **kwargs):
        self.requests.append((endpoint, params))
        if endpoint.endswith("/consolidado/unidades"):
            return [
                {
                    "sequencialPca": 7,
                    "numeroControlePNCP": "12345678000199-0-000007/2023",
                }
            ]
        return self.plan_payload


class PCAFullCollectorTests(unittest.TestCase):
    def source(self):
        return SourceConfig(
            id="pncp_pca_rj_2023",
            base="https://pncp.example",
            endpoint=(
                "/api/pncp/v1/orgaos/{cnpj}/pca/{ano}/{sequencial}"
                "/itens/plano"
            ),
            discovery_endpoint_template=(
                "/api/pncp/v1/orgaos/{cnpj}/pca/{ano}"
                "/consolidado/unidades"
            ),
            collector="pncp_pca_plans",
            natureza="agregada",
            resource_type="pca",
            params={"anoPca": 2023},
            targets=({"cnpj": "12345678000199"},),
            key_fields=("idPcaPncp",),
        )

    def test_discovers_and_preserves_complete_plan_payload(self):
        client = FakePNCPClient()

        collected = list(PCAFullCollector(client).collect(self.source()))
        records = [x for x in collected if isinstance(x, CollectedRecord)]

        self.assertEqual(len(records), 1)
        self.assertIs(records[0].payload, client.plan_payload)
        self.assertEqual(
            records[0].external_id,
            "12345678000199-0-000007/2023",
        )
        self.assertEqual(records[0].query_params["anoPca"], 2023)
        self.assertEqual(records[0].query_params["sequencialPca"], 7)
        self.assertEqual(collected[-1], CollectionCheckpoint("1"))
        self.assertEqual(len(client.requests), 2)
        self.assertTrue(client.requests[1][0].endswith("/7/itens/plano"))
        self.assertIsNone(client.requests[0][1])
        self.assertIsNone(client.requests[1][1])

    def test_organ_without_pca_still_advances_checkpoint(self):
        client = FakePNCPClient()
        client.get = lambda endpoint, params=None, **kwargs: []

        collected = list(PCAFullCollector(client).collect(self.source()))

        self.assertEqual(collected, [CollectionCheckpoint("1")])

    def test_rejects_item_from_another_plan(self):
        client = FakePNCPClient()
        client.plan_payload["itens"][0]["sequencialPca"] = 8

        with self.assertRaisesRegex(ValueError, "fora do PCA esperado"):
            list(PCAFullCollector(client).collect(self.source()))

    def test_rejects_invalid_target_cnpj(self):
        source = SourceConfig(
            **{**self.source().__dict__, "targets": ({"cnpj": "123"},)}
        )

        with self.assertRaisesRegex(ValueError, "CNPJ inválido"):
            list(PCAFullCollector(FakePNCPClient()).collect(source))


if __name__ == "__main__":
    unittest.main()
