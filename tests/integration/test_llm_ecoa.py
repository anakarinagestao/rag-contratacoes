from types import SimpleNamespace
import unittest
from unittest.mock import patch

from llm_ecoa import GENERATION_MAX_TOKENS, gerar_resposta


class FakeCompletions:
    def __init__(self, content, finish_reason="stop"):
        self.content = content
        self.finish_reason = finish_reason
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            id="chatcmpl-test",
            model="modelo-teste",
            choices=[
                SimpleNamespace(
                    finish_reason=self.finish_reason,
                    message=SimpleNamespace(content=self.content),
                )
            ],
        )


class FakeClient:
    def __init__(self, content, finish_reason="stop"):
        self.completions = FakeCompletions(content, finish_reason)
        self.chat = SimpleNamespace(
            completions=self.completions,
        )


class LlmEcoaTests(unittest.TestCase):
    def test_gerar_resposta_retorna_conteudo_valido(self):
        fake_client = FakeClient("Resposta fundamentada.")
        with patch("llm_ecoa.client", fake_client):
            self.assertEqual(gerar_resposta("prompt"), "Resposta fundamentada.")
        self.assertEqual(
            fake_client.completions.kwargs["max_tokens"],
            GENERATION_MAX_TOKENS,
        )

    def test_gerar_resposta_erro_quando_content_none(self):
        with patch("llm_ecoa.client", FakeClient(None)):
            with self.assertRaisesRegex(RuntimeError, "LLM retornou resposta vazia"):
                gerar_resposta("prompt")

    def test_gerar_resposta_erro_quando_content_vazio(self):
        with patch("llm_ecoa.client", FakeClient("   ")):
            with self.assertRaisesRegex(RuntimeError, "finish_reason='stop'"):
                gerar_resposta("prompt")

    def test_gerar_resposta_erro_quando_truncada(self):
        with patch("llm_ecoa.client", FakeClient("Resposta cortada", "length")):
            with self.assertRaisesRegex(RuntimeError, "truncou a resposta"):
                gerar_resposta("prompt")


if __name__ == "__main__":
    unittest.main()
