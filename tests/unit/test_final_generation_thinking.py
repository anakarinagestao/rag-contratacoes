import json
import unittest
from unittest.mock import patch

import httpx2 as httpx
from openai import APIStatusError, DefaultHttpxClient, OpenAI

import llm_ecoa
from app.core.config import settings


def _response(content="resultado", finish_reason="stop"):
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 1,
        "model": "test-model",
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": finish_reason,
        }],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
    }


def _client(handler):
    return OpenAI(
        api_key="test-key",
        base_url="https://provider.invalid/v1",
        http_client=DefaultHttpxClient(transport=httpx.MockTransport(handler)),
        max_retries=0,
    )


class FinalGenerationThinkingTests(unittest.TestCase):
    def _capture_body(self, *, enabled):
        bodies = []

        def handler(request):
            bodies.append(json.loads(request.content))
            return httpx.Response(200, json=_response(), request=request)

        with patch.object(settings, "generation_disable_thinking", enabled), \
                patch.object(llm_ecoa, "client", _client(handler)):
            self.assertEqual(llm_ecoa.gerar_resposta("prompt privado"), "resultado")
        self.assertEqual(len(bodies), 1)
        return bodies[0]

    def test_disabled_preserves_current_request_body(self):
        body = self._capture_body(enabled=False)
        self.assertNotIn("reasoning_effort", body)
        self.assertEqual(body["temperature"], 0.2)
        self.assertEqual(body["max_tokens"], llm_ecoa.GENERATION_MAX_TOKENS)

    def test_enabled_disables_thinking_only_for_final_generation(self):
        final_body = self._capture_body(enabled=True)
        self.assertEqual(final_body["reasoning_effort"], "none")

        analyzer_bodies = []

        def handler(request):
            analyzer_bodies.append(json.loads(request.content))
            return httpx.Response(200, json=_response(), request=request)

        with patch.object(settings, "generation_disable_thinking", True), \
                patch.object(settings, "llm_primary_provider", "ecoa"), \
                patch.object(settings, "analyzer_openai_fallback_enabled", False), \
                patch.object(llm_ecoa, "client", _client(handler)):
            self.assertEqual(llm_ecoa.gerar_resposta_analyzer("analisar"), "resultado")
        self.assertNotIn("reasoning_effort", analyzer_bodies[0])

    def test_incompatible_server_error_is_propagated_without_retry(self):
        requests = []

        def handler(request):
            requests.append(json.loads(request.content))
            return httpx.Response(
                400,
                json={"error": {"message": "unsupported reasoning_effort"}},
                request=request,
            )

        with patch.object(settings, "generation_disable_thinking", True), \
                patch.object(llm_ecoa, "client", _client(handler)):
            with self.assertRaises(APIStatusError) as caught:
                llm_ecoa.gerar_resposta("prompt privado")
        self.assertEqual(caught.exception.status_code, 400)
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["reasoning_effort"], "none")

    def test_length_response_remains_rejected(self):
        def handler(request):
            return httpx.Response(
                200,
                json=_response("resposta parcial", finish_reason="length"),
                request=request,
            )

        with patch.object(settings, "generation_disable_thinking", True), \
                patch.object(llm_ecoa, "client", _client(handler)):
            with self.assertRaisesRegex(RuntimeError, "truncou a resposta"):
                llm_ecoa.gerar_resposta("prompt privado")


if __name__ == "__main__":
    unittest.main()
