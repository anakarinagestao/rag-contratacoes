"""Erros de plano explicáveis, data de assinatura e recusas por escopo."""

from __future__ import annotations

import os
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

with patch.dict(os.environ, {"DATABASE_URL": "sqlite:///:memory:"}):
    from fastapi.testclient import TestClient

    from app.core.database import get_db
    from app.core.security import get_current_user
    from app.main import app
    from app.modules.query.services import (
        NormalizedDatabaseUnavailable,
        _normalized_connection,
    )

from src.guardrails import (
    MENSAGEM_FORA_DO_ESCOPO,
    MENSAGEM_RECUSA_PADRAO,
    pergunta_fora_do_dominio,
    verificar_guardrails_pergunta,
)
from src.query.analyzer import QueryAnalysisError, _normalizar_plano_conhecido
from src.query.structured_tool import StructuredQueryError


def _plano(**campos: object) -> dict[str, object]:
    plano: dict[str, object] = {
        "route": "structured",
        "intent": "aggregate",
        "entity": "contratacao",
        "bases": ["estruturada"],
        "filters": {"ano": 2026, "campo_data": "data_assinatura"},
        "operations": ["count", "group_by"],
        "group_by": ["data_assinatura"],
        "order_by": [],
    }
    plano.update(campos)
    return plano


class DataDeAssinaturaTests(unittest.TestCase):
    def test_pergunta_sobre_contratos_troca_a_entidade(self) -> None:
        plano, correcoes = _normalizar_plano_conhecido(
            _plano(), "Quantos contratos foram assinados em 2026 por mês?"
        )

        self.assertEqual(plano["entity"], "contrato")
        self.assertEqual(plano["filters"]["campo_data"], "data_assinatura")
        self.assertIn("entity.contrato_por_data_assinatura", correcoes)

    def test_pergunta_sobre_contratacoes_usa_data_de_publicacao(self) -> None:
        plano, correcoes = _normalizar_plano_conhecido(
            _plano(order_by=[{"field": "data_assinatura", "direction": "desc"}]),
            "Quais são as 5 maiores contratações de 2026 por valor?",
        )

        self.assertEqual(plano["entity"], "contratacao")
        self.assertEqual(plano["filters"]["campo_data"], "data_publicacao")
        self.assertEqual(plano["group_by"], ["data_publicacao"])
        self.assertEqual(plano["order_by"][0]["field"], "data_publicacao")
        self.assertIn("campo_data.assinatura_para_publicacao", correcoes)

    def test_nao_duplica_campo_ja_agrupado(self) -> None:
        plano, _ = _normalizar_plano_conhecido(
            _plano(group_by=["data_assinatura", "data_publicacao"]),
            "Contratações de 2026 por data",
        )

        self.assertEqual(plano["group_by"], ["data_publicacao"])

    def test_plano_valido_nao_e_alterado(self) -> None:
        original = _plano(
            filters={"ano": 2026, "campo_data": "data_publicacao"},
            group_by=["modalidade"],
        )

        plano, correcoes = _normalizar_plano_conhecido(
            original, "Contratações por modalidade em 2026"
        )

        self.assertEqual(plano["entity"], "contratacao")
        self.assertEqual(plano["group_by"], ["modalidade"])
        self.assertEqual(correcoes, [])

    def test_contrato_com_data_de_assinatura_nao_e_alterado(self) -> None:
        plano, correcoes = _normalizar_plano_conhecido(
            _plano(entity="contrato"), "Contratos assinados em 2026"
        )

        self.assertEqual(plano["entity"], "contrato")
        self.assertEqual(plano["filters"]["campo_data"], "data_assinatura")
        self.assertEqual(correcoes, [])


class ConexaoNormalizadaTests(unittest.TestCase):
    def test_erro_de_plano_no_bloco_nao_vira_indisponibilidade(self) -> None:
        with TemporaryDirectory() as tmp:
            caminho = Path(tmp) / "normalizada.db"
            sqlite3.connect(caminho).close()

            with self.assertRaises(StructuredQueryError):
                with _normalized_connection(caminho.as_uri().replace("file:", "sqlite:")):
                    raise StructuredQueryError("campo indisponível")

    def test_banco_ausente_continua_indisponivel(self) -> None:
        with TemporaryDirectory() as tmp:
            ausente = Path(tmp) / "nao_existe.db"

            with self.assertRaises(NormalizedDatabaseUnavailable):
                with _normalized_connection(ausente.as_uri().replace("file:", "sqlite:")):
                    pass

    def test_erro_sqlite_no_bloco_vira_indisponibilidade(self) -> None:
        with TemporaryDirectory() as tmp:
            caminho = Path(tmp) / "normalizada.db"
            sqlite3.connect(caminho).close()

            with self.assertRaises(NormalizedDatabaseUnavailable):
                with _normalized_connection(caminho.as_uri().replace("file:", "sqlite:")) as con:
                    con.execute("SELECT * FROM tabela_inexistente")


class GuardrailTests(unittest.TestCase):
    def test_recomendacao_comercial_com_outras_palavras(self) -> None:
        for pergunta in (
            "Qual a melhor empresa para eu contratar para fornecer merenda?",
            "Você recomenda algum fornecedor de merenda escolar?",
            "Qual fornecedor devo escolher para obras?",
        ):
            with self.subTest(pergunta=pergunta):
                bloqueado, mensagem = verificar_guardrails_pergunta(pergunta)
                self.assertTrue(bloqueado)
                self.assertEqual(mensagem, MENSAGEM_RECUSA_PADRAO)

    def test_perguntas_de_dados_nao_sao_bloqueadas(self) -> None:
        for pergunta in (
            "Qual fornecedor mais recebeu em 2025?",
            "Quais empresas foram contratadas pela Prefeitura de Niterói?",
            "Qual o valor total gasto com merenda em 2026?",
        ):
            with self.subTest(pergunta=pergunta):
                self.assertEqual(
                    verificar_guardrails_pergunta(pergunta), (False, None)
                )

    def test_fora_do_dominio(self) -> None:
        self.assertTrue(pergunta_fora_do_dominio("Qual é a capital da França?"))
        self.assertFalse(
            pergunta_fora_do_dominio("Quantos contratos foram assinados em 2026?")
        )
        self.assertFalse(
            pergunta_fora_do_dominio("O que diz o art. 75 da Lei 14.133?")
        )


class RotaAskTests(unittest.TestCase):
    def setUp(self) -> None:
        def test_db():
            yield object()

        app.dependency_overrides[get_db] = test_db
        app.dependency_overrides[get_current_user] = lambda: object()
        self.addCleanup(app.dependency_overrides.clear)
        self.client = self.enterContext(TestClient(app))

    def test_recomendacao_comercial_e_recusada_sem_chamar_o_analisador(self) -> None:
        with patch(
            "app.modules.query.routes.executar_consulta_adaptativa_api"
        ) as executar:
            resposta = self.client.post(
                "/query/ask",
                json={"pergunta": "Qual a melhor empresa para eu contratar?"},
            )

        self.assertEqual(resposta.status_code, 200)
        self.assertTrue(resposta.json()["refused"])
        self.assertEqual(resposta.json()["answer"], MENSAGEM_RECUSA_PADRAO)
        executar.assert_not_called()

    def test_pergunta_fora_do_dominio_com_plano_invalido_e_recusada(self) -> None:
        with patch(
            "app.modules.query.routes.executar_consulta_adaptativa_api",
            side_effect=QueryAnalysisError("plano inválido"),
        ):
            resposta = self.client.post(
                "/query/ask", json={"pergunta": "Qual é a capital da França?"}
            )

        self.assertEqual(resposta.status_code, 200)
        self.assertTrue(resposta.json()["refused"])
        self.assertEqual(resposta.json()["answer"], MENSAGEM_FORA_DO_ESCOPO)

    def test_pergunta_do_dominio_com_plano_invalido_continua_422(self) -> None:
        with patch(
            "app.modules.query.routes.executar_consulta_adaptativa_api",
            side_effect=QueryAnalysisError("plano inválido"),
        ):
            resposta = self.client.post(
                "/query/ask",
                json={"pergunta": "Quantos contratos foram assinados em 2026?"},
            )

        self.assertEqual(resposta.status_code, 422)

    def test_erro_estruturado_chega_como_422_com_a_mensagem_real(self) -> None:
        with patch(
            "app.modules.query.routes.executar_consulta_adaptativa_api",
            side_effect=StructuredQueryError("O campo 'x' não está disponível."),
        ):
            resposta = self.client.post(
                "/query/ask", json={"pergunta": "Contratações por mês em 2026"}
            )

        self.assertEqual(resposta.status_code, 422)
        self.assertIn("não está disponível", resposta.json()["detail"])


if __name__ == "__main__":
    unittest.main()
