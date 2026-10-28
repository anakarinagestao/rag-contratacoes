import unittest

from src.ingestion.incremental_http import (
    IncrementalAdapterError,
    PNCPIncrementalHTTPAdapter,
)
from src.ingestion.pncp_client import PNCPHTTPResponse


class FakeClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get_response(self, endpoint, params=None, **options):
        self.calls.append((endpoint, dict(params or {}), options))
        return self.response


class IncrementalHTTPAdapterTests(unittest.TestCase):
    def response(self, payload, *, status=200, outcome="RESPONSE"):
        return PNCPHTTPResponse(
            status,
            payload,
            "https://pncp.example/api?pagina=1",
            "application/json",
            1,
            outcome,
        )

    def test_preserves_page_metadata_and_records(self):
        client = FakeClient(
            self.response(
                {
                    "data": [{"id": "1"}],
                    "numeroPagina": 2,
                    "totalPaginas": 4,
                }
            )
        )

        page = PNCPIncrementalHTTPAdapter(client).fetch_page(
            "/api", {"uf": "RJ"}, page=2
        )

        self.assertEqual(page.outcome, "FOUND")
        self.assertEqual(page.records, ({"id": "1"},))
        self.assertEqual((page.page, page.total_pages), (2, 4))
        self.assertEqual(client.calls[0][1]["pagina"], 2)

    def test_can_call_non_paginated_list_without_inventing_page_param(self):
        client = FakeClient(self.response([{"id": "1"}]))

        page = PNCPIncrementalHTTPAdapter(client).fetch_page(
            "/api/results", {"scope": "item"}, page=1, page_param=None
        )

        self.assertEqual(page.records, ({"id": "1"},))
        self.assertEqual(client.calls[0][1], {"scope": "item"})

    def test_distinguishes_204_404_and_200_empty(self):
        cases = (
            (204, "EMPTY", "EMPTY"),
            (404, "NOT_FOUND", "NOT_FOUND"),
            (200, "RESPONSE", "EMPTY"),
        )
        for status, transport_outcome, expected in cases:
            with self.subTest(status=status):
                response = self.response(
                    [], status=status, outcome=transport_outcome
                )
                page = PNCPIncrementalHTTPAdapter(
                    FakeClient(response)
                ).fetch_page("/api", {}, page=1)
                self.assertEqual(page.outcome, expected)
                self.assertEqual(page.records, ())

    def test_rejects_wrong_page_and_invalid_record_shape(self):
        wrong_page = self.response(
            {"data": [], "numeroPagina": 3, "totalPaginas": 3}
        )
        with self.assertRaisesRegex(IncrementalAdapterError, "esperada 2"):
            PNCPIncrementalHTTPAdapter(FakeClient(wrong_page)).fetch_page(
                "/api", {}, page=2
            )

        invalid = self.response({"data": [{"id": 1}, "inválido"]})
        with self.assertRaisesRegex(IncrementalAdapterError, "não é objeto"):
            PNCPIncrementalHTTPAdapter(FakeClient(invalid)).fetch_page(
                "/api", {}, page=1
            )

    def test_fetch_document_preserves_empty_and_requires_object(self):
        found = PNCPIncrementalHTTPAdapter(
            FakeClient(self.response({"id": "pca-1"}))
        ).fetch_document("/detail")
        self.assertEqual(found.outcome, "FOUND")
        self.assertEqual(found.payload, {"id": "pca-1"})

        empty = PNCPIncrementalHTTPAdapter(
            FakeClient(self.response([], status=204, outcome="EMPTY"))
        ).fetch_document("/detail")
        self.assertEqual(empty.outcome, "EMPTY")
        self.assertIsNone(empty.payload)

        with self.assertRaisesRegex(IncrementalAdapterError, "objeto"):
            PNCPIncrementalHTTPAdapter(
                FakeClient(self.response([{"id": "inválido"}]))
            ).fetch_document("/detail")


if __name__ == "__main__":
    unittest.main()
