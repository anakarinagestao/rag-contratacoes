"""
Testes do log estruturado (app/core/logging.py).

Rodam sem internet e sem tocar na pasta logs/ do projeto: o monkeypatch
aponta a pasta para um diretório temporário.

    pytest tests/test_logging.py -v
"""

import json

from app.core import logging as obs


def _eventos(pasta):
    arquivo = list(pasta.glob("pipeline-*.jsonl"))[0]
    return [json.loads(l) for l in arquivo.read_text(encoding="utf-8").splitlines()]


def test_evento_tem_os_campos_do_enunciado(tmp_path, monkeypatch):
    monkeypatch.setattr(obs, "PASTA_LOGS", tmp_path)
    run_id = obs.novo_run_id()

    with obs.etapa(run_id, obs.STAGE_COLETA, entrada="https://exemplo.gov.br/a.pdf") as ev:
        ev["saida"] = "842103 bytes"

    evento = _eventos(tmp_path)[0]

    assert {"ts", "run_id", "query_id", "stage", "status", "latencia_ms",
            "input", "output", "tokens", "modelo", "extra"} <= set(evento)
    assert evento["run_id"] == run_id
    assert evento["stage"] == "coleta"
    assert evento["status"] == "ok"
    assert evento["latencia_ms"] is not None
    assert evento["input"] == {
        "type": "text",
        "length": len("https://exemplo.gov.br/a.pdf"),
    }
    assert evento["output"] == {
        "type": "text",
        "length": len("842103 bytes"),
    }
    # Coleta e ingestão não chamam o LLM: os tokens ficam zerados, mas o campo
    # existe em toda etapa, para quem instrumentar a geração só preencher.
    assert evento["tokens"] == {"prompt": 0, "completion": 0, "total": 0}


def test_o_mesmo_run_id_amarra_as_etapas(tmp_path, monkeypatch):
    # É o run_id que liga coleta e ingestão de uma mesma execução.
    # Sem ele, como diz o enunciado, os logs são ruído.
    monkeypatch.setattr(obs, "PASTA_LOGS", tmp_path)
    run_id = obs.novo_run_id()

    obs.registrar(run_id, obs.STAGE_COLETA, entrada="a.pdf")
    obs.registrar(run_id, obs.STAGE_INGESTAO, entrada="a.pdf")

    eventos = _eventos(tmp_path)
    assert {e["run_id"] for e in eventos} == {run_id}
    assert [e["stage"] for e in eventos] == ["coleta", "ingestao"]


def test_excecao_vira_status_erro_e_nao_e_engolida(tmp_path, monkeypatch):
    # Um log que esconde falha é pior do que não ter log: o evento é gravado
    # com status "erro" e a exceção continua subindo.
    monkeypatch.setattr(obs, "PASTA_LOGS", tmp_path)
    run_id = obs.novo_run_id()

    try:
        with obs.etapa(run_id, obs.STAGE_INGESTAO, entrada="quebrado.pdf"):
            raise ValueError("pdf ilegivel")
    except ValueError:
        pass
    else:
        raise AssertionError("a exceção deveria continuar subindo")

    evento = _eventos(tmp_path)[0]
    assert evento["status"] == "erro"
    assert evento["output"] is None
    assert evento["extra"]["error_type"] == "ValueError"


def test_falha_registrada_sem_abortar(tmp_path, monkeypatch):
    # A coleta registra URL morta como "falhou" e segue adiante, como o
    # enunciado exige: falha vira linha no log, não exceção que mata a coleta.
    monkeypatch.setattr(obs, "PASTA_LOGS", tmp_path)
    obs.registrar(
        obs.novo_run_id(),
        obs.STAGE_COLETA,
        "falhou",
        entrada="https://exemplo.gov.br/morto.pdf",
        error_code="http_404",
        erro="resposta bruta que não deve ser persistida",
    )

    evento = _eventos(tmp_path)[0]
    assert evento["status"] == "falhou"
    assert evento["extra"] == {"error_code": "http_404"}
