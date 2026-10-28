"""Matriz diagnóstica da primeira versão do gate de evidências.

Estes testes não alteram o comportamento: registram o status observado e
identificam divergências entre a expectativa semântica e o classificador atual.
"""
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from src.generate import (
    MENSAGEM_SEM_EVIDENCIA,
    gerar_resposta_rag,
    verificar_suficiencia_evidencias,
)
from src.query.schemas import QueryEntity, StructuredQueryResult
from src.retrieve import RankedChunk


def _chunk(number: int, text: str) -> RankedChunk:
    return RankedChunk(f"doc:p{number}", text, 1.0, number, 1.0, number, 1.0, {})


CASES = [
    ("single", "Qual é o prazo?", "O prazo para recurso é de dez dias.", "sufficient"),
    ("all_aspects", "Quais são as competências e atribuições?", "Competências comuns e atribuições dos fiscais estão descritas.", "sufficient"),
    ("partial", "Quais são as competências e atribuições?", "As competências comuns estão descritas.", "partial"),
    ("synonym", "Qual é a responsabilidade do fiscal?", "Incumbe ao fiscal acompanhar e registrar a execução.", "sufficient"),
    ("coincidence", "Qual é a competência do fiscal?", "O contrato menciona competência territorial, sem atribuir função ao fiscal.", "partial"),
    ("compound", "Informe o órgão e o município do contrato.", "O órgão contratante é a Secretaria de Saúde do município do Rio de Janeiro.", "sufficient"),
    ("normative", "Quais são as competências e atribuições dos fiscais?", "Compete ao fiscal acompanhar a execução; incumbe ao fiscal técnico verificar a conformidade.", "sufficient"),
    ("distributed", "Quais são as competências e atribuições?", "Competências comuns dos fiscais.", "partial"),
    ("distributed_complete", "Quais são as competências e atribuições?", "Competências comuns dos fiscais. Atribuições do fiscal técnico.", "sufficient"),
    ("no_evidence", "Como funciona?", None, "absent"),
]


@pytest.mark.parametrize("name,question,content,expected", CASES)
def test_matriz_diagnostica_suficiencia(name, question, content, expected, capsys):
    chunks = [] if content is None else [_chunk(1, content)]
    obtained = verificar_suficiencia_evidencias(question, chunks)
    classification = "ok"
    if obtained.status != expected:
        classification = "false_positive" if obtained.sufficient else "false_negative"
    print(json.dumps({"case": name, "expected": expected, "obtained": obtained.status,
                      "reason": obtained.reason, "classification": classification}, ensure_ascii=False))
    assert obtained.status in {"absent", "partial", "sufficient"}


@pytest.mark.parametrize("rows,row_count", [([{"id": 1}], 1), ([], 0)])
def test_sql_nao_substitui_evidencia_documental(rows, row_count):
    result = StructuredQueryResult(
        entity=QueryEntity.DOCUMENTO, columns=["id"], rows=rows,
        row_count=row_count, limit_applied=10,
    )
    decision = verificar_suficiencia_evidencias("qual contrato?", [], result)
    assert decision.status == "absent"


def test_consulta_estruturada_com_resultado_zero_nao_chama_llm(tmp_path: Path):
    result = StructuredQueryResult(
        entity=QueryEntity.DOCUMENTO, columns=["id"], rows=[], row_count=0, limit_applied=10,
    )
    llm = Mock()
    resposta = gerar_resposta_rag(
        None, "qual contrato?", retrieval_fn=lambda **_: [], structured_result=result,
        llm_fn=llm, audit_dir=tmp_path,
    )
    assert resposta.answer != MENSAGEM_SEM_EVIDENCIA
    llm.assert_not_called()
    assert "Cobertura" in resposta.answer


def test_regressao_fiscal_administrativo_sem_suporte_bloqueia():
    pergunta = (
        "Quais são as competências comuns dos fiscais de contrato e quais são "
        "as atribuições específicas dos fiscais técnicos e administrativos, "
        "conforme o Decreto Estadual RJ nº 48.817/2023?"
    )
    chunks = [
        _chunk(25, "Compete ao fiscal técnico verificar a conformidade do objeto."),
        _chunk(22, "A fiscalização do contrato deve registrar ocorrências e comunicar irregularidades."),
        _chunk(23, "O fiscal acompanha a execução contratual e auxilia o gestor."),
    ]
    decision = verificar_suficiencia_evidencias(pergunta, chunks)
    assert decision.status == "partial"
    assert any("administrativos" in aspecto for aspecto in decision.missing_aspects)
    assert decision.evidence_by_aspect


def test_regressao_bloqueia_antes_da_llm(tmp_path: Path):
    pergunta = "Quais são as competências dos fiscais técnicos e administrativos?"
    llm = Mock()
    resposta = gerar_resposta_rag(
        None,
        pergunta,
        retrieval_fn=lambda **_: [_chunk(25, "Compete ao fiscal técnico verificar a conformidade.")],
        llm_fn=llm,
        audit_dir=tmp_path,
    )
    assert resposta.answer == MENSAGEM_SEM_EVIDENCIA
    llm.assert_not_called()
