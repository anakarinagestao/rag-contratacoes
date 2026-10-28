import unittest

from src.ingestion.pncp_collector import (
    PriceRegistryCollector,
    _annual_date_windows,
)
from src.schema import CollectionCheckpoint, CollectedRecord, SourceConfig


class FakePNCPClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def build_url(self, endpoint, params=None, base_url=None):
        return f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}"

    def get(self, endpoint, params=None, **kwargs):
        self.requests.append((endpoint, dict(params or {})))
        return self.responses.pop(0)


class PriceRegistryCollectorTests(unittest.TestCase):
    CNPJ = "12345678000199"
    UNIT = "000001"

    def source(self, start="20231230", end="20240102"):
        return SourceConfig(
            id="pncp_atas_rj_desde_2023",
            base="https://pncp.example",
            endpoint="/api/consulta/v1/atas",
            collector="pncp_price_registry",
            natureza="conversacional",
            resource_type="ata_registro_preco",
            params={"dataInicial": start, "dataFinal": end},
            audit_context={"uf": "RJ", "criterio_temporal": "vigencia"},
            targets=({
                "cnpj": self.CNPJ,
                "codigo_unidade": self.UNIT,
                "uf": "RJ",
            },),
            item_path="data",
            page_size=500,
            max_concurrency=1,
            key_fields=("numeroControlePNCPAta",),
        )

    def ata(self, suffix="000001", unit=None):
        return {
            "numeroControlePNCPAta": (
                f"{self.CNPJ}-1-000007/2023-{suffix}"
            ),
            "numeroControlePNCPCompra": f"{self.CNPJ}-1-000007/2023",
            "cnpjOrgao": self.CNPJ,
            "codigoUnidadeOrgao": unit or self.UNIT,
            "objetoContratacao": "Texto integral preservado no JSON",
        }

    def test_splits_years_and_preserves_payloads(self):
        first = self.ata("000001")
        second = self.ata("000002")
        client = FakePNCPClient([
            {"data": [first], "totalPaginas": 1},
            {"data": [second], "totalPaginas": 1},
        ])

        collected = list(PriceRegistryCollector(client).collect(self.source()))
        records = [x for x in collected if isinstance(x, CollectedRecord)]

        self.assertEqual([x.payload for x in records], [first, second])
        self.assertEqual(
            [(x[1]["dataInicial"], x[1]["dataFinal"]) for x in client.requests],
            [("20231230", "20231231"), ("20240101", "20240102")],
        )
        self.assertEqual(records[0].query_params["uf"], "RJ")
        self.assertEqual(records[0].query_params["window_index"], 1)
        self.assertEqual(
            [x.cursor for x in collected if isinstance(x, CollectionCheckpoint)],
            ["1", "2"],
        )

    def test_empty_windows_advance_checkpoint(self):
        client = FakePNCPClient([[], []])

        collected = list(PriceRegistryCollector(client).collect(self.source()))

        self.assertEqual(
            collected, [CollectionCheckpoint("1"), CollectionCheckpoint("2")]
        )

    def test_skips_record_from_another_unit(self):
        source = self.source("20230101", "20230131")
        client = FakePNCPClient([
            {"data": [self.ata(unit="999999")], "totalPaginas": 1}
        ])

        collected = list(PriceRegistryCollector(client).collect(source))

        self.assertEqual(collected, [CollectionCheckpoint("1")])

    def test_rejects_record_from_another_organization(self):
        payload = self.ata()
        payload["cnpjOrgao"] = "99999999000199"
        source = self.source("20230101", "20230131")
        client = FakePNCPClient([{"data": [payload], "totalPaginas": 1}])

        with self.assertRaisesRegex(ValueError, "fora do órgão alvo"):
            list(PriceRegistryCollector(client).collect(source))

    def test_validates_date_range(self):
        self.assertEqual(
            _annual_date_windows("20240101", "20241231"),
            (("20240101", "20241231"),),
        )
        with self.assertRaisesRegex(ValueError, "intervalo de datas invertido"):
            _annual_date_windows("20240102", "20240101")
        with self.assertRaisesRegex(ValueError, "yyyyMMdd"):
            _annual_date_windows("2024-01-01", "20241231")


if __name__ == "__main__":
    unittest.main()
