from __future__ import annotations

import json
from uuid import UUID

import pytest

from app.core import logging as obs
from src.audit.logger import write_audit_trace
from src.query import analyzer


SENSITIVE_VALUES = (
    "529.982.247-25",
    "pessoa.teste@example.com",
    "Bearer token-ficticio-nao-persistir-123456",
    "banco 001 agencia 1234 conta 98765-4",
    "saida bruta privada da LLM",
)


def _pipeline_event(path):
    [log_file] = path.glob("pipeline-*.jsonl")
    return json.loads(log_file.read_text(encoding="utf-8"))


def test_jsonl_minimiza_conteudo_e_preserva_telemetria(tmp_path, monkeypatch):
    monkeypatch.setattr(obs, "PASTA_LOGS", tmp_path)
    question = " | ".join(SENSITIVE_VALUES[:-1])
    llm_output = SENSITIVE_VALUES[-1]

    obs.registrar(
        "run-privacy-1",
        "query_analyzer",
        status="ok",
        latencia_ms=321,
        entrada={"pergunta": question},
        saida=llm_output,
        tokens={"prompt": 12, "completion": 4, "total": 16},
        modelo="model/test:v1",
        query_id=42,
        route="structured",
        result_count=3,
        respostaLlm=llm_output,
        authorization=SENSITIVE_VALUES[2],
        email=SENSITIVE_VALUES[1],
    )

    raw = next(tmp_path.glob("pipeline-*.jsonl")).read_text(encoding="utf-8")
    for value in SENSITIVE_VALUES:
        assert value not in raw

    event = _pipeline_event(tmp_path)
    assert event["run_id"] == "run-privacy-1"
    assert event["query_id"] == 42
    assert event["status"] == "ok"
    assert event["latencia_ms"] == 321
    assert event["tokens"] == {"prompt": 12, "completion": 4, "total": 16}
    assert event["modelo"] == "model/test:v1"
    assert event["input"] == {"type": "object", "field_count": 1}
    assert event["output"] == {"type": "text", "length": len(llm_output)}
    assert event["extra"] == {"route": "structured", "result_count": 3}


def test_jsonl_de_erro_omite_mensagem_da_excecao(tmp_path, monkeypatch):
    monkeypatch.setattr(obs, "PASTA_LOGS", tmp_path)
    error_message = "erro com 529.982.247-25 e pessoa.teste@example.com"

    with pytest.raises(RuntimeError, match="erro com"):
        with obs.etapa("run-privacy-error", "generation", entrada=error_message):
            raise RuntimeError(error_message)

    raw = next(tmp_path.glob("pipeline-*.jsonl")).read_text(encoding="utf-8")
    assert error_message not in raw
    assert "529.982.247-25" not in raw
    assert "pessoa.teste@example.com" not in raw
    event = _pipeline_event(tmp_path)
    assert event["run_id"] == "run-privacy-error"
    assert event["status"] == "erro"
    assert event["extra"]["error_type"] == "RuntimeError"


def test_analyzer_reutiliza_run_id_sem_persistir_pergunta_ou_saida(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(obs, "PASTA_LOGS", tmp_path)
    token = obs._perf_run_id.set("run-http-correlated")
    try:
        analyzer._registrar_normalizacao(
            "CPF 529.982.247-25",
            "saida bruta privada da LLM",
            ["route.ajustada"],
        )
    finally:
        obs._perf_run_id.reset(token)

    raw = next(tmp_path.glob("pipeline-*.jsonl")).read_text(encoding="utf-8")
    assert "529.982.247-25" not in raw
    assert "saida bruta privada da LLM" not in raw
    event = _pipeline_event(tmp_path)
    assert event["run_id"] == "run-http-correlated"
    assert event["extra"]["question_length"] == len("CPF 529.982.247-25")
    assert event["extra"]["llm_output_length"] == len(
        "saida bruta privada da LLM"
    )
    assert event["extra"]["correcoes"] == ["route.ajustada"]


def test_trace_de_auditoria_omite_conteudo_bruto_e_preserva_metricas(tmp_path):
    question, email, token, bank, llm_output = SENSITIVE_VALUES
    trace = {
        "trace_id": "87b0cff5-4d12-4b6c-b745-2865ccf3fd9a",
        "started_at": "2026-10-21T10:00:00+00:00",
        "finished_at": "2026-10-21T10:00:01+00:00",
        "elapsed_ms": 1000,
        "status": "error",
        "stage": "generation",
        "request": {
            "question": f"{question} {email} {token} {bank}",
            "top_k": 5,
            "filters": {"ano": 2025, "fornecedor": email},
            "bases": ["estruturada"],
            "structured_result": {
                "entity": "contrato",
                "columns": ["ano", "fornecedor"],
                "rows": [{"fornecedor": email}],
                "row_count": 1,
                "limit_applied": 20,
            },
        },
        "guardrail": {
            "checked": True,
            "triggered": False,
            "reason": None,
            "message": None,
        },
        "retrieval": {
            "strategy": "hybrid",
            "base_ids_permitidos": [1],
            "chunks": [{
                "rank": 1,
                "chunk_id": "chunk-1",
                "document_id": 2,
                "rrf_score": 0.1,
                "metadata": {"email": email},
            }],
        },
        "generation": {
            "model": "model/test:v1",
            "provider": "custom",
            "fallback_used": False,
            "max_tokens": 100,
            "contexto": f"contexto {bank}",
            "prompt": f"prompt {token}",
            "answer": llm_output,
            "elapsed_ms": 800,
        },
        "persistence": {"enabled": False},
        "error": {"type": "RuntimeError", "message": f"{token} {email}"},
    }

    path = write_audit_trace(trace, tmp_path)
    raw = path.read_text(encoding="utf-8")
    for value in SENSITIVE_VALUES:
        assert value not in raw
    assert "contexto" not in raw
    assert '"prompt"' not in raw
    assert '"answer"' not in raw
    assert '"rows"' not in raw
    assert '"metadata"' not in raw

    persisted = json.loads(raw)
    assert persisted["trace_id"] == "87b0cff5-4d12-4b6c-b745-2865ccf3fd9a"
    assert persisted["elapsed_ms"] == 1000
    assert persisted["status"] == "error"
    assert persisted["stage"] == "generation"
    assert persisted["request"]["question_length"] > 0
    assert persisted["request"]["filter_fields"] == ["ano", "fornecedor"]
    assert persisted["request"]["structured_result"]["row_count"] == 1
    assert persisted["retrieval"]["evidence_count"] == 1
    assert persisted["generation"]["answer_length"] == len(llm_output)
    assert persisted["error"] == {"type": "RuntimeError"}


@pytest.mark.parametrize(
    "field,value",
    [
        ("run_id", "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ0ZXN0ZSJ9.assinaturaficticia"),
        ("modelo", "sk-proj-abcdefghijklmnop"),
        ("error_code", "529.982.247-25"),
        ("provider", "Bearer-token-ficticio-123456"),
        ("modelo", "001-1234-98765-4"),
    ],
)
def test_jsonl_rejeita_segredos_em_campos_tecnicos(
    field, value, tmp_path, monkeypatch
):
    monkeypatch.setattr(obs, "PASTA_LOGS", tmp_path)
    kwargs = {field: value}
    if field == "run_id":
        obs.registrar(value, "generation")
    else:
        obs.registrar("abc123ef", "generation", **kwargs)

    raw = next(tmp_path.glob("pipeline-*.jsonl")).read_text(encoding="utf-8")
    assert value not in raw


def test_perf_event_rejeita_identificadores_e_excecoes_sensiveis(monkeypatch):
    messages = []
    monkeypatch.setattr(obs._perf_logger, "info", messages.append)

    token = obs._perf_run_id.set("abc123ef")
    try:
        obs.perf_event(
            "generation",
            "erro",
            exception="sk-proj-abcdefghijklmnop",
            provedor="Bearer-token-ficticio-123456",
        )
    finally:
        obs._perf_run_id.reset(token)

    assert len(messages) == 1
    assert "sk-proj" not in messages[0]
    assert "Bearer" not in messages[0]
    assert "excecao=" not in messages[0]
    assert "provedor=" not in messages[0]


def test_trace_invalido_nao_controla_caminho_da_auditoria(tmp_path):
    trace = {
        "trace_id": "../../sk-proj-abcdefghijklmnop",
        "started_at": "../../diretorio-privado",
        "status": "error",
        "stage": "generation",
    }

    path = write_audit_trace(trace, tmp_path)

    assert path.parent == tmp_path / "unknown-date"
    assert path.is_relative_to(tmp_path)
    assert UUID(path.stem)
    assert "sk-proj" not in str(path)


def test_trace_rejeita_segredos_em_metadados_permitidos(tmp_path):
    sensitive = {
        "model": "sk-proj-abcdefghijklmnop",
        "provider": "Bearer-token-ficticio-123456",
        "chunk_id": "cabecalhoficticio.payloadficticio.assinaturaficticia",
        "error_type": "529.982.247-25",
        "bank": "001-1234-98765-4",
    }
    trace = {
        "trace_id": "87b0cff5-4d12-4b6c-b745-2865ccf3fd9a",
        "started_at": "2026-10-21T10:00:00+00:00",
        "finished_at": "2026-10-21T10:00:01+00:00",
        "status": "error",
        "stage": "generation",
        "retrieval": {"chunks": [{"chunk_id": sensitive["chunk_id"]}]},
        "generation": {
            "model": sensitive["model"],
            "provider": sensitive["provider"],
            "fallback_reason": sensitive["bank"],
        },
        "error": {"type": sensitive["error_type"]},
    }

    path = write_audit_trace(trace, tmp_path)
    raw = path.read_text(encoding="utf-8")

    for value in sensitive.values():
        assert value not in raw
