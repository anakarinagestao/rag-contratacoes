import unittest

from src.ingestion.incremental_http import (
    IncrementalAdapterError,
    IncrementalHTTPDocument,
    IncrementalHTTPPage,
)
from src.ingestion.pncp_incremental_adapters import (
    PNCPEndpointIncrementalAdapter,
    PNCPPCAIncrementalAdapter,
    PNCPRelationshipIncrementalAdapter,
)
from src.schema import CollectionCheckpoint, CollectedRecord


class FakeTransport:
    def __init__(self, pages, documents=()):
        self.pages = list(pages)
        self.documents = list(documents)
        self.calls = []
        self.document_calls = []

    def fetch_page(self, endpoint, params, **options):
        self.calls.append((endpoint, dict(params), options))
        return self.pages.pop(0)

    def fetch_document(self, endpoint, params=None, **options):
        self.document_calls.append((endpoint, dict(params or {}), options))
        return self.documents.pop(0)


def page(number, records=(), *, total=1, outcome=None, status=200):
    return IncrementalHTTPPage(
        status,
        outcome or ("FOUND" if records else "EMPTY"),
        tuple(records),
        number,
        total,
        f"https://pncp.example/api?pagina={number}",
        1,
    )


def document(payload=None, *, outcome="FOUND", status=200):
    return IncrementalHTTPDocument(
        status,
        outcome,
        payload,
        "https://pncp.example/api/pncp/detail",
        1,
    )


class PNCPEndpointIncrementalAdapterTests(unittest.TestCase):
    def test_contracting_feed_builds_closed_window_and_identity(self):
        transport = FakeTransport(
            [
                page(
                    1,
                    (
                        {
                            "numeroControlePNCP": "compra-1",
                            "unidadeOrgao": {"ufSigla": "RJ"},
                        },
                    ),
                )
            ]
        )
        events = list(
            PNCPEndpointIncrementalAdapter(transport).collect(
                "contratacao",
                "incremental-contratacao",
                "2023-04-01",
                "2023-04-02",
                filters={"codigoModalidadeContratacao": 6, "uf": "RJ"},
            )
        )

        record = events[0]
        self.assertIsInstance(record, CollectedRecord)
        self.assertEqual(record.external_id, "compra-1")
        self.assertEqual(events[-1], CollectionCheckpoint("1"))
        endpoint, params, options = transport.calls[0]
        self.assertEqual(
            endpoint, "/api/consulta/v1/contratacoes/atualizacao"
        )
        self.assertEqual(params["dataInicial"], "20230401")
        self.assertEqual(params["dataFinal"], "20230402")
        self.assertEqual(params["tamanhoPagina"], 50)
        self.assertEqual(options["page"], 1)

    def test_empty_204_finishes_but_global_404_fails(self):
        empty = FakeTransport([page(1, total=None, outcome="EMPTY", status=204)])
        events = list(
            PNCPEndpointIncrementalAdapter(empty).collect(
                "contrato",
                "incremental-contrato",
                "2023-04-01",
                "2023-04-01",
                filters={"cnpjOrgao": "12345678000199"},
            )
        )
        self.assertEqual(events, [CollectionCheckpoint("1")])

        missing = FakeTransport(
            [page(1, total=None, outcome="NOT_FOUND", status=404)]
        )
        with self.assertRaisesRegex(IncrementalAdapterError, "404"):
            list(
                PNCPEndpointIncrementalAdapter(missing).collect(
                    "ata_registro_preco",
                    "incremental-ata",
                    "2023-04-01",
                    "2023-04-01",
                    filters={"cnpj": "12345678000199"},
                )
            )

    def test_rejects_empty_intermediate_and_mutable_total(self):
        empty_middle = FakeTransport([page(1, (), total=2)])
        with self.assertRaisesRegex(IncrementalAdapterError, "intermediária"):
            list(
                PNCPEndpointIncrementalAdapter(empty_middle).collect(
                    "contratacao",
                    "source",
                    "2023-04-01",
                    "2023-04-01",
                    filters={"codigoModalidadeContratacao": 6, "uf": "RJ"},
                )
            )

        mutable = FakeTransport(
            [
                page(
                    1,
                    (
                        {
                            "numeroControlePNCP": "A",
                            "unidadeOrgao": {"ufSigla": "RJ"},
                        },
                    ),
                    total=2,
                ),
                page(
                    2,
                    (
                        {
                            "numeroControlePNCP": "B",
                            "unidadeOrgao": {"ufSigla": "RJ"},
                        },
                    ),
                    total=3,
                ),
            ]
        )
        with self.assertRaisesRegex(IncrementalAdapterError, "mudou"):
            list(
                PNCPEndpointIncrementalAdapter(mutable).collect(
                    "contratacao",
                    "source",
                    "2023-04-01",
                    "2023-04-01",
                    filters={"codigoModalidadeContratacao": 6, "uf": "RJ"},
                )
            )

    def test_requires_scope_filters_and_identity(self):
        adapter = PNCPEndpointIncrementalAdapter(
            FakeTransport([page(1, (), total=1)])
        )
        with self.assertRaisesRegex(ValueError, "codigoModalidadeContratacao"):
            list(
                adapter.collect(
                    "contratacao",
                    "source",
                    "2023-04-01",
                    "2023-04-01",
                    filters={"uf": "RJ"},
                )
            )

        missing_id = FakeTransport(
            [
                page(
                    1,
                    (
                        {
                            "objeto": "sem id",
                            "orgaoEntidade": {"cnpj": "12345678000199"},
                        },
                    ),
                )
            ]
        )
        with self.assertRaisesRegex(IncrementalAdapterError, "identidade"):
            list(
                PNCPEndpointIncrementalAdapter(missing_id).collect(
                    "contrato",
                    "source",
                    "2023-04-01",
                    "2023-04-01",
                    filters={"cnpjOrgao": "12345678000199"},
                )
            )

    def test_rejects_payload_outside_requested_scope(self):
        transport = FakeTransport(
            [
                page(
                    1,
                    (
                        {
                            "numeroControlePNCP": "contrato-1",
                            "orgaoEntidade": {"cnpj": "99999999000199"},
                            "unidadeOrgao": {"codigoUnidade": "U1"},
                        },
                    ),
                )
            ]
        )

        with self.assertRaisesRegex(IncrementalAdapterError, "CNPJ"):
            list(
                PNCPEndpointIncrementalAdapter(transport).collect(
                    "contrato",
                    "source",
                    "2023-04-01",
                    "2023-04-01",
                    filters={
                        "cnpjOrgao": "12345678000199",
                        "codigoUnidadeAdministrativa": "U1",
                    },
                )
            )

    def test_validates_direct_ata_scope_and_equivalent_numeric_unit(self):
        payload = {
            "numeroControlePNCPAta": "12345678000199-1-000007/2023-000001",
            "cnpjOrgao": "12345678000199",
            "codigoUnidadeOrgao": "00042",
        }
        transport = FakeTransport([page(1, (payload,))])

        events = list(
            PNCPEndpointIncrementalAdapter(transport).collect(
                "ata_registro_preco",
                "source",
                "2023-04-01",
                "2023-04-01",
                filters={
                    "cnpj": "12345678000199",
                    "codigoUnidadeAdministrativa": "42",
                },
            )
        )

        self.assertEqual(events[0].external_id, payload["numeroControlePNCPAta"])


class PNCPPCAIncrementalAdapterTests(unittest.TestCase):
    CNPJ = "12345678000199"
    PCA_ID = f"{CNPJ}-0-000007/2023"

    def complete_plan(self):
        return {
            "idPcaPncp": self.PCA_ID,
            "itens": [
                {
                    "cnpj": self.CNPJ,
                    "codigoUnidade": "U1",
                    "anoPca": 2023,
                    "sequencialPca": 7,
                    "numeroItem": number,
                }
                for number in (1, 2, 3)
            ],
        }

    def test_discovers_fragments_once_and_emits_only_complete_plan(self):
        transport = FakeTransport(
            [
                page(
                    1,
                    ({"idPcaPncp": self.PCA_ID, "itens": [{"numeroItem": 1}]},),
                    total=2,
                ),
                page(
                    2,
                    ({"idPcaPncp": self.PCA_ID, "itens": [{"numeroItem": 2}]},),
                    total=2,
                ),
            ],
            [document(self.complete_plan())],
        )

        events = list(
            PNCPPCAIncrementalAdapter(transport).collect(
                "incremental-pca",
                "2023-04-01",
                "2023-04-02",
                filters={"cnpj": self.CNPJ, "codigoUnidade": "U1"},
            )
        )

        record = events[0]
        self.assertIsInstance(record, CollectedRecord)
        self.assertEqual(record.external_id, self.PCA_ID)
        self.assertEqual(record.payload, self.complete_plan())
        self.assertEqual(record.query_params["discovery_pages"], [1, 2])
        self.assertEqual(record.query_params["discovery_fragment_count"], 2)
        self.assertEqual(record.query_params["representation"], "complete_pca_plan")
        self.assertEqual(events[-1], CollectionCheckpoint("2"))
        self.assertEqual(len(transport.document_calls), 1)
        self.assertIn("/12345678000199/pca/2023/7/", transport.document_calls[0][0])
        self.assertEqual(transport.calls[0][1]["dataInicio"], "20230401")
        self.assertEqual(transport.calls[0][1]["dataFim"], "20230402")

    def test_fetches_all_details_before_emitting_and_rejects_missing_plan(self):
        second_id = f"{self.CNPJ}-0-000008/2023"
        transport = FakeTransport(
            [page(1, ({"idPcaPncp": self.PCA_ID}, {"idPcaPncp": second_id}))],
            [document(self.complete_plan()), document(None, outcome="NOT_FOUND", status=404)],
        )

        with self.assertRaisesRegex(IncrementalAdapterError, "sem plano integral"):
            list(
                PNCPPCAIncrementalAdapter(transport).collect(
                    "incremental-pca",
                    "2023-04-01",
                    "2023-04-01",
                    filters={
                        "cnpj": self.CNPJ,
                        "codigoUnidade": "U1",
                    },
                )
            )
        self.assertEqual(len(transport.document_calls), 2)

    def test_rejects_inventory_mismatch_and_invalid_complete_plan(self):
        mismatch = FakeTransport(
            [page(1, ({"idPcaPncp": self.PCA_ID},))]
        )
        with self.assertRaisesRegex(IncrementalAdapterError, "inconsistente"):
            list(
                PNCPPCAIncrementalAdapter(mismatch).collect(
                    "incremental-pca",
                    "2023-04-01",
                    "2023-04-01",
                    filters={
                        "cnpj": "99999999000199",
                        "codigoUnidade": "U1",
                    },
                )
            )

        invalid_plan = self.complete_plan()
        invalid_plan["itens"][0]["sequencialPca"] = 99
        transport = FakeTransport(
            [page(1, ({"idPcaPncp": self.PCA_ID},))],
            [document(invalid_plan)],
        )
        with self.assertRaisesRegex(IncrementalAdapterError, "fora do PCA"):
            list(
                PNCPPCAIncrementalAdapter(transport).collect(
                    "incremental-pca",
                    "2023-04-01",
                    "2023-04-01",
                    filters={
                        "cnpj": self.CNPJ,
                        "codigoUnidade": "U1",
                    },
                )
            )

    def test_requires_and_validates_inventory_unit(self):
        with self.assertRaisesRegex(ValueError, "unidade"):
            list(
                PNCPPCAIncrementalAdapter(FakeTransport([])).collect(
                    "incremental-pca",
                    "2023-04-01",
                    "2023-04-01",
                    filters={"cnpj": self.CNPJ},
                )
            )

        transport = FakeTransport(
            [page(1, ({"idPcaPncp": self.PCA_ID},))],
            [document(self.complete_plan())],
        )
        with self.assertRaisesRegex(IncrementalAdapterError, "unidade"):
            list(
                PNCPPCAIncrementalAdapter(transport).collect(
                    "incremental-pca",
                    "2023-04-01",
                    "2023-04-01",
                    filters={
                        "cnpj": self.CNPJ,
                        "codigoUnidade": "OUTRA",
                    },
                )
            )


class PNCPRelationshipIncrementalAdapterTests(unittest.TestCase):
    PURCHASE = "12345678000199-1-000007/2023"
    CONTRACT = "12345678000199-2-000009/2023"

    def test_collects_items_and_confirms_found_target(self):
        transport = FakeTransport(
            [page(1, ({"numeroItem": 1}, {"numeroItem": 2}))]
        )
        target = {"numero_controle": self.PURCHASE}

        events = list(
            PNCPRelationshipIncrementalAdapter(transport).collect_target(
                "contratacao_item", "items-source", target
            )
        )

        self.assertEqual(
            [event.external_id for event in events[:-1]],
            [f"{self.PURCHASE}|1", f"{self.PURCHASE}|2"],
        )
        checkpoint = events[-1]
        self.assertIsInstance(checkpoint, CollectionCheckpoint)
        self.assertEqual(checkpoint.reconciliation_outcome, "FOUND")
        self.assertIsNotNone(checkpoint.target_key)
        self.assertIn("/compras/2023/7/itens", transport.calls[0][0])

    def test_validates_result_scope_and_canonical_identity(self):
        payload = {
            "numeroControlePNCPCompra": self.PURCHASE,
            "numeroItem": 3,
            "sequencialResultado": 4,
        }
        target = {"numero_controle": self.PURCHASE, "numero_item": 3}
        transport = FakeTransport([page(1, (payload,))])
        events = list(
            PNCPRelationshipIncrementalAdapter(transport).collect_target(
                "resultado_item", "result-source", target
            )
        )
        self.assertEqual(events[0].external_id, f"{self.PURCHASE}|3|4")
        self.assertEqual(transport.calls[0][1], {})
        self.assertIsNone(transport.calls[0][2]["page_param"])
        self.assertNotIn("pagina", events[0].query_params)
        self.assertNotIn("tamanhoPagina", events[0].query_params)

        wrong = dict(payload, numeroItem=99)
        with self.assertRaisesRegex(IncrementalAdapterError, "fora do item"):
            list(
                PNCPRelationshipIncrementalAdapter(
                    FakeTransport([page(1, (wrong,))])
                ).collect_target(
                    "resultado_item", "result-source", target
                )
            )

    def test_contextual_commitment_404_is_not_found_but_item_404_fails(self):
        commitment_events = list(
            PNCPRelationshipIncrementalAdapter(
                FakeTransport(
                    [page(1, total=None, outcome="NOT_FOUND", status=404)]
                )
            ).collect_target(
                "empenho",
                "commitment-source",
                {"numero_controle": self.CONTRACT},
            )
        )
        self.assertEqual(
            commitment_events[-1].reconciliation_outcome, "NOT_FOUND"
        )

        with self.assertRaisesRegex(IncrementalAdapterError, "404"):
            list(
                PNCPRelationshipIncrementalAdapter(
                    FakeTransport(
                        [
                            page(
                                1,
                                total=None,
                                outcome="NOT_FOUND",
                                status=404,
                            )
                        ]
                    )
                ).collect_target(
                    "contratacao_item",
                    "items-source",
                    {"numero_controle": self.PURCHASE},
                )
            )

    def test_empty_target_is_reconciled_and_duplicate_child_fails(self):
        events = list(
            PNCPRelationshipIncrementalAdapter(
                FakeTransport([page(1, (), total=None, outcome="EMPTY")])
            ).collect_target(
                "resultado_item",
                "result-source",
                {"numero_controle": self.PURCHASE, "numero_item": 1},
            )
        )
        self.assertEqual(events[-1].reconciliation_outcome, "EMPTY")

        duplicate = FakeTransport(
            [
                page(1, ({"numeroItem": 1},), total=2),
                page(2, ({"numeroItem": 1},), total=2),
            ]
        )
        with self.assertRaisesRegex(IncrementalAdapterError, "repetiu"):
            list(
                PNCPRelationshipIncrementalAdapter(
                    duplicate
                ).collect_target(
                    "contratacao_item",
                    "items-source",
                    {"numero_controle": self.PURCHASE},
                )
            )


if __name__ == "__main__":
    unittest.main()
