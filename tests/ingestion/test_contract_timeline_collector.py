import unittest

from src.ingestion.pncp_collector import (
    ContractTimelineCollector,
    _quarter_date_windows,
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


class ContractTimelineCollectorTests(unittest.TestCase):
    def source(self, start="20230630", end="20230701", modalities=(1,)):
        return SourceConfig(
            id="pncp_contratacoes_rj_2023_t2_ate_hoje",
            base="https://pncp.example",
            endpoint="/api/consulta/v1/contratacoes/publicacao",
            collector="pncp_contract_timeline",
            resource_type="contratacao",
            params={
                "dataInicial": start,
                "dataFinal": end,
                "uf": "RJ",
                "modalidades": list(modalities),
            },
            audit_context={"criterio_temporal": "publicacao", "uf": "RJ"},
            item_path="data",
            page_size=50,
            max_concurrency=1,
            key_fields=("numeroControlePNCP",),
        )

    @staticmethod
    def contract(identifier="compra-1", uf="RJ"):
        return {
            "numeroControlePNCP": identifier,
            "objetoCompra": "JSON integral",
            "unidadeOrgao": {"ufSigla": uf},
        }

    def test_splits_quarters_and_preserves_query_context(self):
        first = self.contract("compra-1")
        second = self.contract("compra-2")
        client = FakePNCPClient([
            {"data": [first], "totalPaginas": 1},
            {"data": [second], "totalPaginas": 1},
        ])

        collected = list(ContractTimelineCollector(client).collect(self.source()))
        records = [x for x in collected if isinstance(x, CollectedRecord)]

        self.assertEqual([x.payload for x in records], [first, second])
        self.assertEqual(
            [(x[1]["dataInicial"], x[1]["dataFinal"]) for x in client.requests],
            [("20230630", "20230630"), ("20230701", "20230701")],
        )
        self.assertEqual(records[0].query_params["trimestre"], 2)
        self.assertEqual(records[1].query_params["trimestre"], 3)
        self.assertEqual(
            [x.cursor for x in collected if isinstance(x, CollectionCheckpoint)],
            ["1", "2"],
        )

    def test_paginates_each_work_item_before_checkpoint(self):
        client = FakePNCPClient([
            {"data": [self.contract("compra-1")], "totalPaginas": 2},
            {"data": [self.contract("compra-2")], "totalPaginas": 2},
        ])
        source = self.source("20230401", "20230430")

        collected = list(ContractTimelineCollector(client).collect(source))

        self.assertEqual([x[1]["pagina"] for x in client.requests], [1, 2])
        self.assertEqual(collected[-1], CollectionCheckpoint("1"))

    def test_empty_modalities_advance_checkpoint(self):
        client = FakePNCPClient([[], []])
        source = self.source("20230401", "20230430", modalities=(1, 2))

        collected = list(ContractTimelineCollector(client).collect(source))

        self.assertEqual(
            collected, [CollectionCheckpoint("1"), CollectionCheckpoint("2")]
        )

    def test_rejects_unexpected_state(self):
        client = FakePNCPClient([
            {"data": [self.contract(uf="SP")], "totalPaginas": 1}
        ])
        source = self.source("20230401", "20230430")

        with self.assertRaisesRegex(ValueError, "fora do recorte estadual"):
            list(ContractTimelineCollector(client).collect(source))

    def test_resume_uses_composite_checkpoint(self):
        client = FakePNCPClient([[ ]])
        source = self.source("20230401", "20230430", modalities=(1, 2))

        collected = list(
            ContractTimelineCollector(client).collect(source, start_page=2)
        )

        self.assertEqual(collected, [CollectionCheckpoint("2")])
        self.assertEqual(client.requests[0][1]["codigoModalidadeContratacao"], 2)

    def test_generates_all_remaining_quarters(self):
        windows = _quarter_date_windows("20230401", "20260926")

        self.assertEqual(len(windows), 14)
        self.assertEqual(windows[0], ("20230401", "20230630"))
        self.assertEqual(windows[-1], ("20260701", "20260926"))


if __name__ == "__main__":
    unittest.main()
