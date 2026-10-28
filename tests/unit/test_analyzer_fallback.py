"""Compatibilidade do Analyzer com o provedor unificado de LLM."""

from __future__ import annotations

import unittest
from unittest.mock import patch

import llm_ecoa
from app.core.llm_provider import LlmGeneration


class AnalyzerFallbackTests(unittest.TestCase):
    def test_analyzer_delega_ao_provedor_compartilhado(self):
        generation = LlmGeneration(
            content='{"route": "structured"}',
            provider="groq",
            model="openai/gpt-oss-120b",
            fallback_used=True,
            fallback_reason="timeout",
        )

        with patch("app.core.llm_provider.gerar_com_fallback", return_value=generation) as provider:
            resposta = llm_ecoa.gerar_resposta_analyzer("pergunta")

        self.assertEqual(resposta, generation.content)
        self.assertEqual(provider.call_args.kwargs["operation"], "analyzer")
        self.assertEqual(
            provider.call_args.kwargs["ecoa_model"], llm_ecoa.GENERATION_MODEL
        )
