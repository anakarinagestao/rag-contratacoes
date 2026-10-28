from http.client import IncompleteRead, RemoteDisconnected
import io
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from src.ingestion.pncp_client import PNCPClient, PNCPClientError


class FakeResponse:
    def __init__(self, payload=None, status=200):
        self.payload = (
            b"" if payload is None else json.dumps(payload).encode("utf-8")
        )
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return self.payload


class PNCPClientTests(unittest.TestCase):
    @patch("src.ingestion.pncp_client.urlopen")
    def test_get_uses_configured_base_and_returns_json(self, urlopen):
        urlopen.return_value = FakeResponse({"items": [{"id": "1"}]})
        client = PNCPClient(base_url="https://pncp.example")

        payload = client.get("/api/contratos", {"uf": "RJ", "pagina": 1})

        self.assertEqual(payload, {"items": [{"id": "1"}]})
        request = urlopen.call_args.args[0]
        self.assertEqual(
            request.full_url,
            "https://pncp.example/api/contratos?uf=RJ&pagina=1",
        )
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 30.0)

    @patch("src.ingestion.pncp_client.urlopen")
    def test_get_translates_http_and_transport_errors(self, urlopen):
        client = PNCPClient(base_url="https://pncp.example", max_retries=0)
        urlopen.side_effect = HTTPError(
            "https://pncp.example/api", 503, "unavailable", {}, io.BytesIO()
        )
        with self.assertRaises(PNCPClientError):
            client.get("/api")

        urlopen.side_effect = URLError("offline")
        with self.assertRaises(PNCPClientError):
            client.get("/api")

    @patch("src.ingestion.pncp_client.urlopen")
    def test_http_error_exposes_status_code(self, urlopen):
        client = PNCPClient(base_url="https://pncp.example", max_retries=0)
        urlopen.side_effect = HTTPError(
            "https://pncp.example/api",
            422,
            "unprocessable entity",
            {},
            io.BytesIO(),
        )

        with self.assertRaises(PNCPClientError) as raised:
            client.get("/api")

        self.assertEqual(raised.exception.status_code, 422)

    @patch("src.ingestion.pncp_client.urlopen")
    def test_get_retries_transient_http_error(self, urlopen):
        urlopen.side_effect = [
            HTTPError(
                "https://pncp.example/api",
                503,
                "unavailable",
                {},
                io.BytesIO(),
            ),
            FakeResponse({"items": [{"id": "1"}]}),
        ]
        client = PNCPClient(
            base_url="https://pncp.example",
            max_retries=1,
            retry_backoff_seconds=0,
        )

        payload = client.get("/api")

        self.assertEqual(payload, {"items": [{"id": "1"}]})
        self.assertEqual(urlopen.call_count, 2)

    @patch("src.ingestion.pncp_client.urlopen")
    def test_get_retries_remote_disconnect(self, urlopen):
        urlopen.side_effect = [
            RemoteDisconnected("server closed connection"),
            FakeResponse({"items": [{"id": "1"}]}),
        ]
        client = PNCPClient(
            base_url="https://pncp.example",
            max_retries=1,
            retry_backoff_seconds=0,
        )

        payload = client.get("/api")

        self.assertEqual(payload, {"items": [{"id": "1"}]})
        self.assertEqual(urlopen.call_count, 2)

    @patch("src.ingestion.pncp_client.urlopen")
    def test_get_retries_incomplete_response(self, urlopen):
        urlopen.side_effect = [
            IncompleteRead(b"partial"),
            FakeResponse({"items": [{"id": "1"}]}),
        ]
        client = PNCPClient(
            base_url="https://pncp.example",
            max_retries=1,
            retry_backoff_seconds=0,
        )

        self.assertEqual(client.get("/api"), {"items": [{"id": "1"}]})
        self.assertEqual(urlopen.call_count, 2)

    @patch("src.ingestion.pncp_client.time.sleep")
    @patch("src.ingestion.pncp_client.urlopen")
    def test_get_respects_retry_after_header(self, urlopen, sleep):
        urlopen.side_effect = [
            HTTPError(
                "https://pncp.example/api",
                429,
                "too many requests",
                {"Retry-After": "3"},
                io.BytesIO(),
            ),
            FakeResponse({"data": []}),
        ]
        client = PNCPClient(
            base_url="https://pncp.example",
            max_retries=1,
            retry_backoff_seconds=0.5,
        )

        self.assertEqual(client.get("/api"), {"data": []})
        sleep.assert_called_once_with(3.0)

    @patch("src.ingestion.pncp_client.urlopen")
    def test_get_can_treat_http_404_as_empty_without_retry(self, urlopen):
        urlopen.side_effect = HTTPError(
            "https://pncp.example/api",
            404,
            "not found",
            {},
            io.BytesIO(),
        )
        client = PNCPClient(
            base_url="https://pncp.example",
            max_retries=3,
            retry_backoff_seconds=0,
        )

        payload = client.get("/api", not_found_as_empty=True)

        self.assertEqual(payload, [])
        self.assertEqual(urlopen.call_count, 1)

    @patch("src.ingestion.pncp_client.urlopen")
    def test_get_returns_empty_list_for_http_204(self, urlopen):
        urlopen.return_value = FakeResponse(status=204)
        client = PNCPClient(base_url="https://pncp.example")

        payload = client.get("/api")

        self.assertEqual(payload, [])

    @patch("src.ingestion.pncp_client.urlopen")
    def test_get_response_preserves_http_outcome_and_attempts(self, urlopen):
        urlopen.side_effect = HTTPError(
            "https://pncp.example/api",
            404,
            "not found",
            {"Content-Type": "application/json"},
            io.BytesIO(),
        )
        client = PNCPClient(base_url="https://pncp.example")

        response = client.get_response("/api", not_found_as_empty=True)

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.outcome, "NOT_FOUND")
        self.assertEqual(response.payload, [])
        self.assertEqual(response.attempts, 1)
        self.assertEqual(response.content_type, "application/json")

    @patch("src.ingestion.pncp_client.urlopen")
    def test_get_response_reports_retry_count(self, urlopen):
        urlopen.side_effect = [
            HTTPError(
                "https://pncp.example/api",
                503,
                "unavailable",
                {},
                io.BytesIO(),
            ),
            FakeResponse({"data": []}),
        ]
        client = PNCPClient(
            base_url="https://pncp.example",
            max_retries=1,
            retry_backoff_seconds=0,
        )

        response = client.get_response("/api")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.outcome, "RESPONSE")
        self.assertEqual(response.attempts, 2)


if __name__ == "__main__":
    unittest.main()
