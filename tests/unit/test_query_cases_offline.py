"""Regressões determinísticas do analisador de perguntas.

Os planos da fixture simulam saídas já observadas da LLM. Assim, estes testes
validam a normalização sem chamar Ecoa, PNCP, FAISS ou SQLite.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from src.query.analyzer import analisar_pergunta


ARQUIVO_CASOS = Path(__file__).parents[1] / "fixtures" / "query_cases.json"


class QueryCasesOfflineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.casos = json.loads(ARQUIVO_CASOS.read_text(encoding="utf-8"))["cases"]

    def test_casos_fixos_do_analisador(self) -> None:
        for caso in self.casos:
            with self.subTest(caso=caso["id"]):
                resposta_simulada = json.dumps(
                    caso["simulated_llm_output"], ensure_ascii=False
                )
                analise = analisar_pergunta(
                    caso["question"],
                    llm_fn=lambda _prompt, resposta=resposta_simulada: resposta,
                )
                esperado = caso["expected"]

                self.assertEqual(esperado["route"], analise.route)
                self.assertEqual(esperado["entity"], analise.entity)

                if "bases" in esperado:
                    self.assertEqual(esperado["bases"], analise.bases)
                if "bases_contains" in esperado:
                    self.assertTrue(
                        set(esperado["bases_contains"]).issubset(analise.bases)
                    )
                if "operations_equals" in esperado:
                    self.assertEqual(
                        esperado["operations_equals"], analise.operations
                    )
                for operacao in esperado.get("operations_contains", []):
                    self.assertIn(operacao, analise.operations)
                for campo, valor in esperado.get("filters", {}).items():
                    self.assertEqual(valor, getattr(analise.filters, campo))
