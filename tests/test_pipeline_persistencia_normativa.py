"""
Testes do comando src/base_estruturada/pipelines/pipeline_persistencia_normativa.py.

O comando virou uma porta de entrada para app/modules/normative/persistencia.py.
Estes testes conferem só a ligação: que cada argumento chega ao carregador e que
o padrão é a versão normativo-<versao>, e não mais "o corpus de id 1". Rodam
sem banco: o carregador é trocado por um registrador de chamadas.

    pytest tests/test_pipeline_persistencia_normativa.py -v
"""

import os
import runpy
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

with patch.dict(os.environ, {"DATABASE_URL": "sqlite:///:memory:"}):
    from app.modules.normative import persistencia

PIPELINE = (
    Path(__file__).resolve().parents[1]
    / "src" / "base_estruturada" / "pipelines" / "pipeline_persistencia_normativa.py"
)


@pytest.fixture
def chamadas(monkeypatch):
    """Troca persistir() por um registrador e devolve a lista de chamadas."""
    registradas = []

    def falso(**kwargs):
        registradas.append(kwargs)
        return {"corpus_version_id": 0}

    monkeypatch.setattr(persistencia, "persistir", falso)
    return registradas


def rodar_comando(monkeypatch, *argumentos):
    monkeypatch.setattr(sys, "argv", [str(PIPELINE), *argumentos])
    runpy.run_path(str(PIPELINE), run_name="__main__")


def test_sem_argumentos_usa_a_versao_v1_e_deixa_o_carregador_escolher(
    monkeypatch, chamadas,
):
    rodar_comando(monkeypatch)

    assert chamadas == [{
        "versao": "v1",
        "arquivo": None,
        "inventario": None,
        "corpus_version_id": None,   # None = normativo-v1, resolvida pelo nome
    }]


def test_o_padrao_nao_e_mais_o_corpus_de_id_1(monkeypatch, chamadas):
    rodar_comando(monkeypatch)

    assert chamadas[0]["corpus_version_id"] is None


def test_os_argumentos_antigos_continuam_valendo(monkeypatch, chamadas):
    rodar_comando(
        monkeypatch,
        "--versao", "v2",
        "--arquivo-chunks", "dados/chunks.jsonl",
        "--inventario", "dados/inventario.json",
    )

    assert chamadas[0]["versao"] == "v2"
    assert chamadas[0]["arquivo"] == Path("dados/chunks.jsonl")
    assert chamadas[0]["inventario"] == Path("dados/inventario.json")


def test_versao_de_corpus_pode_ser_informada(monkeypatch, chamadas):
    rodar_comando(monkeypatch, "--corpus-version-id", "7")

    assert chamadas[0]["corpus_version_id"] == 7
