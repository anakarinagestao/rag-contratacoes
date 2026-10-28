from src.query.response_composer import compose_structured_answer
from src.query.schemas import (
    KnowledgeBase,
    QueryAnalysis,
    QueryEntity,
    QueryFilters,
    QueryIntent,
    QueryRoute,
    StructuredField,
    StructuredOperation,
    StructuredQueryResult,
    StructuredWarning,
)


def _analysis(**overrides):
    values = {
        "route": QueryRoute.STRUCTURED,
        "intent": QueryIntent.AGGREGATE,
        "entity": QueryEntity.CONTRATACAO,
        "bases": [KnowledgeBase.ESTRUTURADA],
        "filters": QueryFilters(ano=2024),
        "operations": [StructuredOperation.COUNT],
    }
    values.update(overrides)
    return QueryAnalysis(**values)


def test_compoe_contagem_sem_recalcular_sql():
    analysis = _analysis()
    result = StructuredQueryResult(
        entity=analysis.entity,
        columns=["count"],
        rows=[{"count": 1234}],
        row_count=1,
        limit_applied=20,
    )

    answer = compose_structured_answer(analysis, result)

    assert "quantidade: 1234" in answer
    assert "ano 2024" in answer


def test_compoe_agregacao_financeira_com_formato_brasileiro():
    analysis = _analysis(
        operations=[StructuredOperation.SUM],
        aggregation_field=StructuredField.VALOR_ESTIMADO,
    )
    result = StructuredQueryResult(
        entity=analysis.entity,
        columns=["sum_valor_estimado"],
        rows=[{"sum_valor_estimado": 1234567.89}],
        row_count=1,
        limit_applied=20,
    )

    answer = compose_structured_answer(analysis, result)

    assert "R$ 1.234.567,89" in answer


def test_resultado_vazio_e_explicito():
    analysis = _analysis(intent=QueryIntent.LOOKUP, operations=[StructuredOperation.LIST])
    result = StructuredQueryResult(
        entity=analysis.entity,
        columns=[],
        rows=[],
        row_count=0,
        limit_applied=20,
    )

    assert compose_structured_answer(analysis, result).startswith(
        "Nenhuma contratação foi encontrada (ano 2024)."
    )
    assert "não comprovam inexistência" in compose_structured_answer(analysis, result)


def test_listagem_resume_sem_afirmar_total_global():
    analysis = _analysis(intent=QueryIntent.LOOKUP, operations=[StructuredOperation.LIST])
    result = StructuredQueryResult(
        entity=analysis.entity,
        columns=["numero_controle_pncp", "valor_estimado"],
        rows=[
            {
                "numero_controle_pncp": "00000000000000-1-000001/2024",
                "valor_estimado": 2500,
            }
        ],
        row_count=1,
        limit_applied=20,
    )

    answer = compose_structured_answer(analysis, result)

    assert "A consulta retornou 1 contratação" in answer
    assert "R$ 2.500,00" in answer
    assert "total" not in answer.casefold()


def test_preserva_alertas_de_qualidade_na_resposta():
    analysis = _analysis(intent=QueryIntent.LOOKUP, operations=[StructuredOperation.LIST])
    result = StructuredQueryResult(
        entity=analysis.entity,
        columns=["valor_estimado"],
        rows=[{"valor_estimado": -0.22}],
        row_count=1,
        limit_applied=20,
        warnings=[
            StructuredWarning(
                code="negative_financial_value",
                message="Valor negativo preservado para revisão.",
                affected_rows=1,
            )
        ],
    )

    answer = compose_structured_answer(analysis, result)

    assert "Alertas de qualidade" in answer
    assert "Valor negativo preservado para revisão." in answer
