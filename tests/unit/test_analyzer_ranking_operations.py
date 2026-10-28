"""Regressão offline dos aliases de ordenação/limite devolvidos pela LLM."""

import json
from unittest.mock import patch

import pytest

from src.query.analyzer import QueryAnalysisError, analisar_pergunta
from src.query.planner import planejar_execucao
from src.query.prompts import montar_prompt_analise


QUESTION = "Quais são os cinco contratos de maior valor global assinados em 2026, quais órgãos os contrataram e quem são os fornecedores?"


def plan(operations):
    return {
        "route": "structured", "intent": "lookup", "entity": "contrato",
        "bases": ["estruturada"], "operations": operations,
        "filters": {"campo_data": "data_assinatura", "period_start": "2026-01-01",
                    "period_end": "2026-12-31"},
        "order_by": [{"field": "valor_global", "direction": "desc"}], "limit": 5,
    }


def analyze(payload):
    with patch("src.query.analyzer.registrar"):
        return analisar_pergunta(QUESTION, llm_fn=lambda _: json.dumps(payload))


@pytest.mark.parametrize("operations", [
    ["list", "sort"], ["list", "order"], ["list", "limit"],
    ["list", "top"], ["list", "top_k"],
    ["list", "sort", "order_by", "limit"],
])
def test_known_ranking_aliases_preserve_validated_fields(operations):
    result = analyze(plan(operations))
    assert [op.value for op in result.operations] == ["list", "order_by"]
    assert result.limit == 5
    assert result.order_by[0].field.value == "valor_global"
    assert result.order_by[0].direction.value == "desc"
    assert str(result.filters.period_start) == "2026-01-01"
    assert result.filters.campo_data.value == "data_assinatura"
    assert result.filters.ano is None
    assert [tool.value for tool in planejar_execucao(result).tools] == ["structured_database"]


@pytest.mark.parametrize("operation", ["arbitrary", "delete", "filter"])
def test_unknown_operation_is_still_rejected(operation):
    with pytest.raises(QueryAnalysisError):
        analyze(plan(["list", operation]))


@pytest.mark.parametrize("limit", [None, True, 0, 101, "5"])
def test_limit_alias_without_valid_limit_is_rejected(limit):
    payload = plan(["list", "limit"])
    payload["limit"] = limit
    with pytest.raises(QueryAnalysisError):
        analyze(payload)


def test_sort_without_sort_fields_is_rejected():
    payload = plan(["list", "sort"])
    payload["order_by"] = []
    with pytest.raises(QueryAnalysisError):
        analyze(payload)


def test_prompt_explains_ranking_and_signature_dates():
    prompt = montar_prompt_analise(QUESTION)
    assert 'nunca use "limit"' in prompt
    assert 'campo_data "data_assinatura"' in prompt
