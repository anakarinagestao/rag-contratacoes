import unittest

from src.ingestion.pncp_collector import PCACollector
from src.schema import CollectedRecord, SourceConfig


class FakePNCPClient:
    CNPJ = "12345678000199"
    PCA_ID = f"{CNPJ}-0-000007/2023"

    def __init__(self):
        self.detail_requests = []
        self.pages = {
            1: {
                "data": [
                    {
                        "idPcaPncp": self.PCA_ID,
                        "itens": [{"numeroItem": 1}, {"numeroItem": 2}],
                    }
                ]
            },
            2: {
                "data": [
                    {
                        "idPcaPncp": self.PCA_ID,
                        "itens": [{"numeroItem": 2}, {"numeroItem": 3}],
                    }
                ]
            },
            3: {"data": []},
        }
        self.plan_payload = {
            "uasg": "123456",
            "unidadeResponsavel": "Unidade",
            "itens": [
                {
                    "cnpj": self.CNPJ,
                    "codigoUnidade": "123456",
                    "anoPca": 2023,
                    "sequencialPca": 7,
                    "numeroItem": number,
                }
                for number in (1, 2, 3)
            ],
        }

    def fetch_page(self, source, page):
        return self.pages[page], f"{source.url}?pagina={page}"

    def build_url(self, endpoint, params=None, base_url=None):
        return f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}"

    def get(self, endpoint, params=None, **kwargs):
        self.detail_requests.append(endpoint)
        return self.plan_payload


class PCACollectorTests(unittest.TestCase):
    def source(self):
        return SourceConfig(
            id="pncp_pca_rj",
            base="https://pncp.example",
            endpoint="/api/consulta/v1/pca/atualizacao",
            resource_type="pca",
            item_path="data",
            key_fields=("idPcaPncp",),
            detail_endpoint_template=(
                "/api/pncp/v1/orgaos/{cnpj}/pca/{ano}/{sequencial}"
                "/itens/plano"
            ),
        )

    def test_fragments_discover_one_complete_plan_without_merging_pages(self):
        client = FakePNCPClient()

        records = list(PCACollector(client).collect(self.source()))

        self.assertEqual(len(records), 1)
        self.assertIsInstance(records[0], CollectedRecord)
        self.assertIs(records[0].payload, client.plan_payload)
        self.assertEqual(records[0].external_id, client.PCA_ID)
        self.assertEqual(records[0].cursor, "2")
        self.assertEqual(
            records[0].query_params["discovery_pages"], ["1", "2"]
        )
        self.assertEqual(
            records[0].query_params["discovery_fragment_count"], 2
        )
        self.assertEqual(
            records[0].query_params["representation"],
            "complete_pca_plan",
        )
        self.assertEqual(len(client.detail_requests), 1)
        self.assertTrue(client.detail_requests[0].endswith("/7/itens/plano"))

    def test_rejects_pca_record_without_primary_identifier(self):
        client = FakePNCPClient()
        client.pages[1] = {"data": [{"valor": 10}]}

        with self.assertRaises(ValueError):
            list(PCACollector(client).collect(self.source()))

    def test_rejects_source_without_complete_plan_endpoint(self):
        source = SourceConfig(
            **{**self.source().__dict__, "detail_endpoint_template": None}
        )

        with self.assertRaisesRegex(ValueError, "detail_endpoint_template"):
            list(PCACollector(FakePNCPClient()).collect(source))

    def test_rejects_inconsistent_complete_plan_before_emitting_records(self):
        client = FakePNCPClient()
        client.plan_payload["itens"][0]["sequencialPca"] = 8

        with self.assertRaisesRegex(ValueError, "fora do PCA esperado"):
            list(PCACollector(client).collect(self.source()))


if __name__ == "__main__":
    unittest.main()
