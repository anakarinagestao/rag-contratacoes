"""Contrato de domínio da indisponibilidade do índice FAISS."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import faiss
import numpy as np

from src.faiss_errors import (
    FaissIndexCorruptError,
    FaissIndexIncompatibleError,
    FaissIndexMissingError,
    faiss_public_detail,
)
from src.index import (
    ARQUIVO_ESTADO,
    ESQUEMA_CHECKPOINTS,
    FaissChunkIndex,
    carregar_indice_faiss,
    indexar_chunks_incrementalmente,
    validar_indice_faiss,
)


class FaissUnavailabilityTests(unittest.TestCase):
    def test_indice_ausente_tem_codigo_estavel_sem_caminho(self) -> None:
        with TemporaryDirectory() as directory:
            secret_path = Path(directory) / "segredo" / "indice"
            with self.assertRaises(FaissIndexMissingError) as raised:
                carregar_indice_faiss(secret_path)

        detail = faiss_public_detail(raised.exception)
        self.assertEqual(detail["code"], "faiss_index_missing")
        self.assertNotIn(str(secret_path), str(raised.exception))
        self.assertNotIn(str(secret_path), str(detail))

    def test_indice_corrompido_e_diferente_de_ausente(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "chunks.faiss").write_bytes(b"nao-e-faiss")
            (path / "chunk_ids.json").write_text(
                json.dumps(["chunk-1"]),
                encoding="utf-8",
            )
            with self.assertRaises(FaissIndexCorruptError) as raised:
                carregar_indice_faiss(path)

        self.assertEqual(raised.exception.error_code, "faiss_index_corrupt")

    def test_indice_incompativel_por_modelo(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory)
            indexar_chunks_incrementalmente(
                [("chunk-1", "conteudo")],
                lambda _: [1.0, 0.0],
                path,
                modelo_embedding="modelo-a",
            )
            with self.assertRaises(FaissIndexIncompatibleError) as raised:
                carregar_indice_faiss(path, modelo_esperado="modelo-b")

        self.assertEqual(raised.exception.error_code, "faiss_index_incompatible")

    def test_indice_valido_sem_resultados_nao_e_indisponibilidade(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory)
            (path / ARQUIVO_ESTADO).write_text(
                json.dumps(
                    {
                        "formato": ESQUEMA_CHECKPOINTS,
                        "modeloEmbedding": "modelo-vazio",
                        "dimensao": 2,
                        "totalVetores": 0,
                        "base": None,
                        "checkpoints": [],
                    }
                ),
                encoding="utf-8",
            )
            index = carregar_indice_faiss(
                path,
                modelo_esperado="modelo-vazio",
            )

        self.assertEqual(index.search([1.0, 0.0], 5), [])

    def test_dimensao_de_consulta_incompativel_e_classificada(self) -> None:
        index = faiss.IndexFlatIP(2)
        index.add(np.array([[1.0, 0.0]], dtype="float32"))
        wrapped = FaissChunkIndex(
            index=index,
            chunk_ids=["chunk-1"],
            content_hashes={},
        )

        with self.assertRaises(FaissIndexIncompatibleError):
            wrapped.search([1.0, 0.0, 0.0], 1)

    def test_erro_inesperado_de_programacao_nao_e_mascarado(self) -> None:
        operations = {
            "carregar": lambda: carregar_indice_faiss(Path("nao-utilizado")),
            "validar": lambda: validar_indice_faiss(
                Path("nao-utilizado"), modelo_esperado="modelo-teste"
            ),
        }
        for operation_name, operation in operations.items():
            for exception_type in (AssertionError, RuntimeError, ValueError):
                with self.subTest(
                    operation=operation_name,
                    exception_type=exception_type.__name__,
                ):
                    unexpected = exception_type("falha-interna-sintetica")
                    with patch("src.index.auditar_indice_faiss", side_effect=unexpected):
                        with self.assertRaises(exception_type) as raised:
                            operation()

                    self.assertIs(raised.exception, unexpected)

    def test_erro_inesperado_na_busca_nao_e_convertido_em_indisponibilidade(self) -> None:
        native_index = Mock(d=2)
        unexpected = RuntimeError("falha-interna-sintetica")
        native_index.search.side_effect = unexpected
        wrapped = FaissChunkIndex(
            index=native_index,
            chunk_ids=["chunk-1"],
            content_hashes={},
        )

        with self.assertRaises(RuntimeError) as raised:
            wrapped.search([1.0, 0.0], 1)

        self.assertIs(raised.exception, unexpected)


if __name__ == "__main__":
    unittest.main()
