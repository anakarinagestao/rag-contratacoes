import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest
from fastapi import FastAPI

from app.core.config import settings
from app.core.faiss_startup import validar_faiss_no_startup
from src.embedding_identity import identidades_embedding_compativeis
from src.faiss_errors import FaissIndexIncompatibleError
from src.index import (
    carregar_indice_faiss,
    indexar_chunks_incrementalmente,
    validar_indice_faiss,
)

LEGACY = "xagi/qwen3-embedding:0.6b-q4_k_m"
CURRENT = f"ecoa:{LEGACY}:d1024"
ALIAS = "ecoa/qwen3-embedding:0.6b-q4_k_m"


@pytest.mark.parametrize("left,right", [(LEGACY, CURRENT), (CURRENT, LEGACY),
                                         (LEGACY, ALIAS), (CURRENT, CURRENT)])
def test_known_identity(left, right):
    assert identidades_embedding_compativeis(left, right, 1024)


@pytest.mark.parametrize("left,right,dimension", [
    (LEGACY, CURRENT, 768),
    (CURRENT, CURRENT, 768),
    (LEGACY, CURRENT, None),
    (LEGACY, f"openai:{LEGACY}:d1024", 1024),
    (LEGACY, "ecoa:outro-modelo:d1024", 1024),
    (LEGACY, f"ecoa:{LEGACY}:d768", 1024),
    ("desconhecido", "ecoa:desconhecido:d1024", 1024),
    (None, None, 1024),
])
def test_incompatible_identity(left, right, dimension):
    assert not identidades_embedding_compativeis(left, right, dimension)


def test_opaque_names_keep_exact_equality():
    assert identidades_embedding_compativeis("modelo-teste", "modelo-teste", 2)


def test_startup_load_and_incremental_ingestion_preserve_legacy_identity():
    with TemporaryDirectory() as directory:
        path = Path(directory)
        vector = [1.0] + [0.0] * 1023
        indexar_chunks_incrementalmente(
            [("one", "primeiro")], lambda _: vector, path,
            modelo_embedding=LEGACY,
        )
        with patch.object(settings, "rag_faiss_index_dir", path), \
                patch("app.core.faiss_startup.EMBEDDING_MODEL", CURRENT):
            app = FastAPI()
            assert validar_faiss_no_startup(app)
            assert app.state.faiss_index_ready
        assert validar_indice_faiss(path, modelo_esperado=CURRENT).total_vetores == 1
        assert carregar_indice_faiss(path, modelo_esperado=CURRENT).index.ntotal == 1
        indexar_chunks_incrementalmente(
            [("two", "segundo")], lambda _: vector, path,
            modelo_embedding=CURRENT, dimensao_embedding=1024,
        )
        assert carregar_indice_faiss(path, modelo_esperado=CURRENT).index.ntotal == 2
        assert json.loads((path / "estado_indexacao.json").read_text())["modeloEmbedding"] == LEGACY
        for incompatible in [f"openai:{LEGACY}:d1024", "ecoa:outro:d1024",
                             f"ecoa:{LEGACY}:d768"]:
            with pytest.raises(FaissIndexIncompatibleError):
                validar_indice_faiss(path, modelo_esperado=incompatible)
            with pytest.raises(FaissIndexIncompatibleError):
                carregar_indice_faiss(path, modelo_esperado=incompatible)
            with pytest.raises(RuntimeError, match="modelo de embedding difere"):
                indexar_chunks_incrementalmente(
                    [], lambda _: pytest.fail("Não deve gerar embeddings"), path,
                    modelo_embedding=incompatible,
                )
