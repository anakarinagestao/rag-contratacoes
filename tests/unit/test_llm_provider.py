"""Testes sem rede para seleção e fallback de provedores de LLM."""

from __future__ import annotations

import unittest
from unittest.mock import patch

import app.core.llm_provider as llm_provider_module
from app.core.config import settings
from app.core.llm_provider import LlmProviderError, gerar_com_fallback


class LlmProviderTests(unittest.TestCase):
    def setUp(self):
        # Testes não podem depender do provedor escolhido no .env local.
        primary = patch.object(settings, "llm_primary_provider", "ecoa")
        primary.start()
        self.addCleanup(primary.stop)

    def test_ecoa_responde_sem_chamar_fallback(self):
        fallback = unittest.mock.Mock(return_value="não deveria executar")
        with patch.object(settings, "llm_fallback_provider", "groq"):
            result = gerar_com_fallback(
                "pergunta",
                operation="generation",
                ecoa_fn=lambda _prompt: "resposta Ecoa",
                ecoa_model="modelo-ecoa",
                fallback_fn=fallback,
            )

        self.assertEqual(result.provider, "ecoa")
        self.assertFalse(result.fallback_used)
        fallback.assert_not_called()

    def test_openai_principal_nao_chama_ecoa(self):
        ecoa = unittest.mock.Mock(side_effect=AssertionError("Ecoa foi chamado"))
        executor = unittest.mock.Mock(return_value="resposta OpenAI")
        with patch.object(settings, "llm_primary_provider", "openai"), \
                patch.object(settings, "llm_primary_openai_model", "modelo-openai"):
            result = gerar_com_fallback(
                "pergunta",
                operation="generation",
                ecoa_fn=ecoa,
                ecoa_model="modelo-ecoa",
                fallback_fn=executor,
            )

        ecoa.assert_not_called()
        executor.assert_called_once_with("pergunta", "openai", "modelo-openai")
        self.assertEqual(result.provider, "openai")
        self.assertFalse(result.fallback_used)

    def test_falha_do_provedor_principal_e_segura(self):
        with patch.object(settings, "llm_primary_provider", "openai"):
            with self.assertRaisesRegex(
                LlmProviderError, "openai/RuntimeError"
            ):
                gerar_com_fallback(
                    "conteúdo que não deve aparecer no erro",
                    operation="generation",
                    ecoa_fn=lambda _prompt: "não usado",
                    ecoa_model="modelo-ecoa",
                    fallback_fn=lambda *_args: (_ for _ in ()).throw(
                        RuntimeError("segredo no erro original")
                    ),
                )

    def test_timeout_da_ecoa_usa_groq_configurada(self):
        with patch.object(settings, "llm_fallback_provider", "groq"), \
                patch.object(settings, "llm_fallback_groq_model", "modelo-groq"):
            result = gerar_com_fallback(
                "pergunta",
                operation="generation",
                ecoa_fn=lambda _prompt: (_ for _ in ()).throw(TimeoutError()),
                ecoa_model="modelo-ecoa",
                fallback_fn=lambda _prompt, provider, model: (
                    self.assertEqual((provider, model),
                                     ("groq", "modelo-groq"))
                    or "resposta Groq"
                ),
            )

        self.assertEqual(result.provider, "groq")
        self.assertTrue(result.fallback_used)
        self.assertEqual(result.fallback_reason, "timeout_or_connection")

    def test_timeout_da_ecoa_usa_openai_configurada(self):
        with patch.object(settings, "llm_fallback_provider", "openai"), \
                patch.object(settings, "llm_fallback_openai_model", "modelo-openai"):
            result = gerar_com_fallback(
                "pergunta",
                operation="analyzer",
                ecoa_fn=lambda _prompt: (
                    _ for _ in ()).throw(ConnectionError()),
                ecoa_model="modelo-ecoa",
                fallback_fn=lambda _prompt, provider, model: (
                    self.assertEqual((provider, model),
                                     ("openai", "modelo-openai"))
                    or "resposta OpenAI"
                ),
            )

        self.assertEqual(result.provider, "openai")
        self.assertTrue(result.fallback_used)

    def test_erro_nao_transitorio_nao_aciona_fallback(self):
        fallback = unittest.mock.Mock(return_value="resposta")
        with patch.object(settings, "llm_fallback_provider", "groq"):
            with self.assertRaisesRegex(RuntimeError, "plano inválido"):
                gerar_com_fallback(
                    "pergunta",
                    operation="analyzer",
                    ecoa_fn=lambda _prompt: (_ for _ in ()).throw(
                        RuntimeError("plano inválido")),
                    ecoa_model="modelo-ecoa",
                    fallback_fn=fallback,
                )
        fallback.assert_not_called()

    def test_dupla_falha_retorna_erro_unificado(self):
        with patch.object(settings, "llm_fallback_provider", "groq"):
            with self.assertRaises(LlmProviderError) as captured:
                gerar_com_fallback(
                    "pergunta",
                    operation="generation",
                    ecoa_fn=lambda _prompt: (
                        _ for _ in ()).throw(TimeoutError()),
                    ecoa_model="modelo-ecoa",
                    fallback_fn=lambda *_args: (_ for _ in ()
                                                ).throw(RuntimeError("indisponível")),
                )
        self.assertIn("Ecoa falhou", str(captured.exception))
        self.assertIn("groq", str(captured.exception))

    def test_timeout_sem_fallback_retorna_erro_unificado(self):
        with patch.object(settings, "llm_fallback_provider", "none"):
            with self.assertRaises(LlmProviderError) as captured:
                gerar_com_fallback(
                    "pergunta",
                    operation="generation",
                    ecoa_fn=lambda _prompt: (
                        _ for _ in ()).throw(TimeoutError()),
                    ecoa_model="modelo-ecoa",
                )

        self.assertIn("não há fallback configurado", str(captured.exception))

    def test_timeout_usa_executor_padrao_com_operacao(self):
        with patch.object(settings, "llm_fallback_provider", "groq"), \
                patch.object(settings, "llm_fallback_groq_model", "modelo-groq"), \
                patch.object(llm_provider_module, "_executar_fallback", return_value="resposta") as executor:
            result = gerar_com_fallback(
                "pergunta",
                operation="analyzer",
                ecoa_fn=lambda _prompt: (_ for _ in ()).throw(TimeoutError()),
                ecoa_model="modelo-ecoa",
            )

        self.assertEqual(result.provider, "groq")
        executor.assert_called_once_with(
            "pergunta", "groq", "modelo-groq", "analyzer")
