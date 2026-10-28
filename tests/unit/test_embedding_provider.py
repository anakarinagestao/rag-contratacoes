"""Seleção segura e explícita do provedor de embeddings."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.core.config import Settings, settings
import llm_ecoa


class EmbeddingProviderTests(unittest.TestCase):
    def test_configuracao_padrao_preserva_ecoa_e_dimensao(self) -> None:
        isolated = Settings(database_url="sqlite:///:memory:", _env_file=None)

        self.assertEqual(isolated.embedding_provider, "ecoa")
        self.assertEqual(isolated.embedding_dimensions, 1024)
        self.assertEqual(isolated.embedding_model, "")

    def test_openai_usa_modelo_dimensao_e_cliente_explicitos(self) -> None:
        embeddings = Mock()
        embeddings.create.return_value = SimpleNamespace(
            data=[SimpleNamespace(embedding=[1.0, 0.0])],
            usage=None,
        )
        cliente = SimpleNamespace(embeddings=embeddings)

        with (
            patch.object(settings, "embedding_provider", "openai"),
            patch.object(llm_ecoa, "EMBEDDING_API_MODEL", "modelo-openai-teste"),
            patch.object(llm_ecoa, "EMBEDDING_DIMENSIONS", 2),
            patch.object(llm_ecoa, "_cliente_embedding_openai", return_value=cliente),
        ):
            vetor = llm_ecoa.gerar_embedding("conteúdo público sintético")

        self.assertEqual(vetor, [1.0, 0.0])
        embeddings.create.assert_called_once_with(
            model="modelo-openai-teste",
            input="conteúdo público sintético",
            dimensions=2,
        )

    def test_dimensao_incompatível_falha_antes_de_indexar(self) -> None:
        cliente = SimpleNamespace(
            embeddings=SimpleNamespace(
                create=lambda **_: SimpleNamespace(
                    data=[SimpleNamespace(embedding=[1.0, 0.0, 0.0])],
                    usage=None,
                )
            )
        )
        with (
            patch.object(settings, "embedding_provider", "openai"),
            patch.object(llm_ecoa, "EMBEDDING_API_MODEL", "modelo-openai-teste"),
            patch.object(llm_ecoa, "EMBEDDING_DIMENSIONS", 2),
            patch.object(llm_ecoa, "_cliente_embedding_openai", return_value=cliente),
        ):
            with self.assertRaisesRegex(RuntimeError, "dimensão.*incompatível"):
                llm_ecoa.gerar_embedding("conteúdo")

    def test_texto_vazio_nao_chama_provedor(self) -> None:
        with patch.object(llm_ecoa, "_cliente_embedding_openai") as cliente:
            with self.assertRaisesRegex(ValueError, "não pode ser vazio"):
                llm_ecoa.gerar_embedding("   ")
        cliente.assert_not_called()


if __name__ == "__main__":
    unittest.main()
