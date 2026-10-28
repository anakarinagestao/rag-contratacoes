"""Instrumentação HTTP da ECOA com transporte local simulado."""

import logging
import re
import unittest
from unittest.mock import patch

import httpx2
from openai import OpenAI

from app.core.config import settings
from app.core.llm_http_observability import (
    http_request_hook,
    http_response_hook,
    llm_http_operation,
    log_current_usage,
)
from app.core.logging import _perf_run_id
from app.core.logging import RagPerfMiddleware


def _chat_response(prompt_tokens=12, completion_tokens=4):
    return {
        "id": "response-id",
        "object": "chat.completion",
        "created": 1,
        "model": "test-model",
        "choices": [{
            "index": 0,
            "finish_reason": "stop",
            "message": {"role": "assistant", "content": "private-answer"},
        }],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


class LlmHttpObservabilityTests(unittest.TestCase):
    def _client(self, handler, max_retries=2):
        transport = httpx2.MockTransport(handler)
        http_client = httpx2.Client(
            transport=transport,
            event_hooks={"request": [http_request_hook], "response": [http_response_hook]},
        )
        self.addCleanup(http_client.close)
        return OpenAI(
            base_url="http://local.test/v1",
            api_key="private-api-key",
            max_retries=max_retries,
            http_client=http_client,
        )

    def _execute_chat(self, client, kind="analyzer"):
        with llm_http_operation(kind):
            response = client.chat.completions.create(
                model="test-model",
                messages=[{"role": "user", "content": "private-prompt"}],
                max_tokens=10,
                temperature=0.2,
            )
            log_current_usage(response.usage)
        return response

    def _instrumented(self):
        token = _perf_run_id.set("run-observe")
        self.addCleanup(_perf_run_id.reset, token)
        return patch.object(settings, "rag_llm_http_log_enabled", True)

    def test_sucesso_registra_tentativa_tokens_correlacao_e_privacidade(self):
        client = self._client(lambda request: httpx2.Response(200, json=_chat_response()))
        with self._instrumented(), self.assertLogs("uvicorn.error", logging.INFO) as captured:
            response = self._execute_chat(client)
        self.assertEqual(response.choices[0].message.content, "private-answer")
        messages = [record.getMessage() for record in captured.records]
        self.assertTrue(any(
            "run_id=run-observe" in message
            and "etapa=analyzer_http_attempt" in message
            and "tentativa=1" in message
            and "status_http=200" in message
            for message in messages
        ))
        self.assertTrue(any(
            "etapa=analyzer_http_usage" in message
            and "tokens_entrada=12" in message
            and "tokens_saida=4" in message
            and "tokens_total=16" in message
            for message in messages
        ))
        joined = "\n".join(messages)
        for secret in ("private-api-key", "private-prompt", "private-answer", "local.test"):
            self.assertNotIn(secret, joined)

    def test_retry_registra_cada_resposta_e_espera(self):
        calls = 0
        def handler(request):
            nonlocal calls
            calls += 1
            if calls == 1:
                return httpx2.Response(500, json={"error": {"message": "temporary"}})
            return httpx2.Response(200, json=_chat_response())
        client = self._client(handler, max_retries=1)
        with self._instrumented(), patch("openai._base_client.time.sleep"), self.assertLogs(
            "uvicorn.error", logging.INFO
        ) as captured:
            self._execute_chat(client, kind="generation")
        messages = [record.getMessage() for record in captured.records]
        self.assertEqual(calls, 2)
        self.assertTrue(any(
            "etapa=generation_http_attempt" in message
            and "tentativa=1" in message and "status_http=500" in message
            and "status=erro_http" in message
            for message in messages
        ))
        self.assertTrue(any(
            "etapa=generation_http_retry" in message
            and "tentativa=2" in message and "retry=1" in message
            and re.search(r"espera_ms=[\d.]+", message)
            for message in messages
        ))
        self.assertTrue(any(
            "etapa=generation_http_attempt" in message
            and "tentativa=2" in message and "status_http=200" in message
            for message in messages
        ))

    def test_erro_http_final_preserva_excecao(self):
        client = self._client(
            lambda request: httpx2.Response(400, json={"error": {"message": "invalid"}}),
            max_retries=0,
        )
        with self._instrumented(), self.assertLogs("uvicorn.error", logging.INFO) as captured:
            with self.assertRaises(Exception) as raised:
                self._execute_chat(client, kind="generation")
        self.assertEqual(getattr(raised.exception, "status_code", None), 400)
        messages = "\n".join(record.getMessage() for record in captured.records)
        self.assertIn("etapa=generation_http_attempt", messages)
        self.assertIn("status_http=400", messages)
        self.assertNotIn("invalid", messages)

    def test_erro_sem_resposta_e_retries_sao_observados(self):
        calls = 0
        def handler(request):
            nonlocal calls
            calls += 1
            raise httpx2.ConnectError("private-network-error", request=request)
        client = self._client(handler, max_retries=1)
        with self._instrumented(), patch("openai._base_client.time.sleep"), self.assertLogs(
            "uvicorn.error", logging.INFO
        ) as captured:
            with self.assertRaises(Exception):
                self._execute_chat(client, kind="embedding")
        messages = "\n".join(record.getMessage() for record in captured.records)
        self.assertEqual(calls, 2)
        self.assertIn("etapa=embedding_http_retry", messages)
        self.assertIn("status=erro_sem_resposta", messages)
        self.assertIn("excecao=APIConnectionError", messages)
        self.assertNotIn("private-network-error", messages)

    def test_desabilitado_nao_emite_logs_nem_altera_resultado(self):
        client = self._client(lambda request: httpx2.Response(200, json=_chat_response()))
        token = _perf_run_id.set("run-disabled")
        self.addCleanup(_perf_run_id.reset, token)
        with patch.object(settings, "rag_llm_http_log_enabled", False), self.assertNoLogs(
            "uvicorn.error", logging.INFO
        ):
            response = self._execute_chat(client)
        self.assertEqual(response.choices[0].message.content, "private-answer")

    def test_tipos_de_operacao_ficam_separados(self):
        client = self._client(lambda request: httpx2.Response(200, json=_chat_response()))
        with self._instrumented(), self.assertLogs("uvicorn.error", logging.INFO) as captured:
            for kind in ("analyzer", "generation", "embedding"):
                self._execute_chat(client, kind=kind)
        messages = "\n".join(record.getMessage() for record in captured.records)
        for kind in ("analyzer", "generation", "embedding"):
            self.assertIn(f"etapa={kind}_http_attempt", messages)

    def test_flag_http_cria_correlacao_sem_habilitar_logs_gerais(self):
        observed_run_ids = []
        async def app(scope, receive, send):
            observed_run_ids.append(_perf_run_id.get())
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})
        middleware = RagPerfMiddleware(app)
        async def receive():
            return {"type": "http.request", "body": b""}
        async def send(_message):
            pass
        import asyncio
        with patch.object(settings, "rag_perf_log_enabled", False), patch.object(
            settings, "rag_llm_http_log_enabled", True
        ), self.assertNoLogs("uvicorn.error", logging.INFO):
            asyncio.run(middleware(
                {"type": "http", "method": "POST", "path": "/query/ask"}, receive, send
            ))
        self.assertEqual(len(observed_run_ids), 1)
        self.assertIsNotNone(observed_run_ids[0])
