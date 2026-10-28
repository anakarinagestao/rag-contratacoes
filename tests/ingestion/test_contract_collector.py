from dataclasses import replace
import unittest

from src.ingestion.pncp_collector import (
    ContractCollector,
    PNCPPaginationError,
)
from src.schema import CollectionCheckpoint, SourceConfig


class MemoryLogger:
    def __init__(self):
        self.events = []

    def event(self, event, **fields):
        self.events.append({"event": event, **fields})


class FakePNCPClient:
    def __init__(self, pages):
        self.pages = pages
        self.requested_pages = []

    def fetch_page(self, source, page):
        self.requested_pages.append(page)
        return self.pages[page], f"{source.url}?pagina={page}"


class ScriptedPNCPClient:
    def __init__(self, pages):
        self.pages = {page: list(responses) for page, responses in pages.items()}
        self.requested_pages = []

    def fetch_page(self, source, page):
        self.requested_pages.append(page)
        return self.pages[page].pop(0), f"{source.url}?pagina={page}"


class ContractCollectorTests(unittest.TestCase):
    def source(self):
        return SourceConfig(
            id="pncp_contratacoes_rj_2023_t1_m06_smoke",
            base="https://pncp.example",
            endpoint="/api/consulta/v1/contratacoes/publicacao",
            collector="pncp_contracts",
            resource_type="contratacao",
            params={
                "dataInicial": "20230101",
                "dataFinal": "20230331",
                "codigoModalidadeContratacao": 6,
                "uf": "RJ",
            },
            audit_context={
                "source_id": "pncp_contratacoes_rj",
                "ano": 2023,
                "trimestre": 1,
            },
            item_path="data",
            key_fields=("numeroControlePNCP",),
            page_size=10,
        )

    @staticmethod
    def contract(external_id="compra-1", uf="RJ"):
        return {
            "numeroControlePNCP": external_id,
            "objetoCompra": "Payload integral",
            "unidadeOrgao": {"ufSigla": uf, "ufNome": "Rio de Janeiro"},
            "camposExtras": {"preservados": [1, 2, 3]},
        }

    def test_collects_all_envelope_pages_and_preserves_complete_payload(self):
        first = self.contract()
        second = self.contract("compra-2")
        client = FakePNCPClient(
            {
                1: {
                    "data": [first],
                    "totalRegistros": 2,
                    "totalPaginas": 2,
                    "numeroPagina": 1,
                },
                2: {
                    "data": [second],
                    "totalRegistros": 2,
                    "totalPaginas": 2,
                    "numeroPagina": 2,
                },
            }
        )

        records = list(ContractCollector(client).collect(self.source()))

        self.assertEqual(client.requested_pages, [1, 2])
        self.assertEqual([record.external_id for record in records], ["compra-1", "compra-2"])
        self.assertEqual(records[0].payload, first)
        self.assertEqual(records[0].query_params["pagina"], 1)
        self.assertEqual(records[1].query_params["pagina"], 2)
        self.assertEqual(records[0].query_params["ano"], 2023)
        self.assertEqual(records[0].query_params["tamanhoPagina"], 10)

    def test_rejects_missing_external_id_and_unexpected_state(self):
        logger = MemoryLogger()
        missing_id = self.contract()
        del missing_id["numeroControlePNCP"]
        client = FakePNCPClient({1: {"data": [missing_id], "totalPaginas": 1}})
        with self.assertRaisesRegex(ValueError, "numeroControlePNCP"):
            list(ContractCollector(client, logger).collect(self.source()))
        self.assertEqual(logger.events[-1]["reason"], "missing_external_id")

        logger = MemoryLogger()
        client = FakePNCPClient(
            {1: {"data": [self.contract(uf="SP")], "totalPaginas": 1}}
        )
        with self.assertRaisesRegex(ValueError, "recorte estadual"):
            list(ContractCollector(client, logger).collect(self.source()))
        self.assertEqual(logger.events[-1]["reason"], "unexpected_uf")
        self.assertNotIn("payload", logger.events[-1])

    def test_empty_page_is_recorded_as_successful_checkpoint(self):
        client = FakePNCPClient({1: []})

        records = list(ContractCollector(client).collect(self.source()))

        self.assertEqual(len(records), 1)
        self.assertIsInstance(records[0], CollectionCheckpoint)
        self.assertEqual(records[0].cursor, "1")

    def test_transient_empty_intermediate_page_is_retried(self):
        first = self.contract("compra-1")
        second = self.contract("compra-2")
        third = self.contract("compra-3")
        client = ScriptedPNCPClient(
            {
                1: [
                    {
                        "data": [first],
                        "totalPaginas": 3,
                        "numeroPagina": 1,
                    }
                ],
                2: [
                    {
                        "data": [],
                        "totalPaginas": 3,
                        "numeroPagina": 2,
                    },
                    {
                        "data": [second],
                        "totalPaginas": 3,
                        "numeroPagina": 2,
                    },
                ],
                3: [
                    {
                        "data": [third],
                        "totalPaginas": 3,
                        "numeroPagina": 3,
                    }
                ],
            }
        )
        source = replace(
            self.source(), max_retries=1, retry_backoff_seconds=0
        )

        records = list(ContractCollector(client).collect(source))

        self.assertEqual(client.requested_pages, [1, 2, 2, 3])
        self.assertEqual(
            [record.external_id for record in records],
            ["compra-1", "compra-2", "compra-3"],
        )

    def test_persistent_empty_intermediate_page_fails_collection(self):
        empty = {
            "data": [],
            "totalPaginas": 3,
            "numeroPagina": 2,
        }
        client = ScriptedPNCPClient(
            {
                1: [
                    {
                        "data": [self.contract("compra-1")],
                        "totalPaginas": 3,
                        "numeroPagina": 1,
                    }
                ],
                2: [empty, empty, empty],
            }
        )
        source = replace(self.source(), retry_backoff_seconds=0)

        with self.assertRaisesRegex(
            PNCPPaginationError, "intermediária vazia"
        ):
            list(ContractCollector(client).collect(source))

        self.assertEqual(client.requested_pages, [1, 2, 2, 2])

    def test_total_pages_change_fails_collection(self):
        client = FakePNCPClient(
            {
                1: {
                    "data": [self.contract("compra-1")],
                    "totalPaginas": 2,
                    "numeroPagina": 1,
                },
                2: {
                    "data": [self.contract("compra-2")],
                    "totalPaginas": 3,
                    "numeroPagina": 2,
                },
            }
        )

        with self.assertRaisesRegex(
            PNCPPaginationError, "total_pages_changed"
        ):
            list(ContractCollector(client).collect(self.source()))

    def test_unexpected_response_page_fails_collection(self):
        client = FakePNCPClient(
            {
                1: {
                    "data": [self.contract()],
                    "totalPaginas": 2,
                    "numeroPagina": 2,
                }
            }
        )

        with self.assertRaisesRegex(
            PNCPPaginationError, "unexpected_page_number"
        ):
            list(ContractCollector(client).collect(self.source()))

    def test_empty_declared_final_page_finishes_normally(self):
        client = FakePNCPClient(
            {
                1: {
                    "data": [self.contract()],
                    "totalPaginas": 2,
                    "numeroPagina": 1,
                },
                2: {
                    "data": [],
                    "totalPaginas": 2,
                    "numeroPagina": 2,
                },
            }
        )

        records = list(ContractCollector(client).collect(self.source()))

        self.assertEqual(client.requested_pages, [1, 2])
        self.assertEqual(records[0].external_id, "compra-1")
        self.assertEqual(records[-1], CollectionCheckpoint("2"))


if __name__ == "__main__":
    unittest.main()
