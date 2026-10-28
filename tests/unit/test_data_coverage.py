import sqlite3
from pathlib import Path
from unittest.mock import Mock

import pytest

from src.query.coverage import avaliar_cobertura, acrescentar_avisos_cobertura
from src.query.normalized_structured_tool import executar_consulta_normalizada
from src.query.response_composer import compose_structured_answer
from src.query.schemas import QueryAnalysis
from src.generate import gerar_resposta_rag
from src.retrieve import RankedChunk


@pytest.fixture
def db():
    c = sqlite3.connect(":memory:")
    c.executescript((Path(__file__).parents[2] / "migrations/100_normalized_schema.sql").read_text(encoding="utf-8"))
    yield c
    c.close()


def analysis(entity="contratacao", operation="count", **kwargs):
    return QueryAnalysis(route="structured", intent="aggregate", entity=entity,
                         bases=["estruturada"], operations=[operation], **kwargs)


def metadata(db, resource="contratacao", status="COMPLETE", count=0,
             expected=None, processed=None, inventory=None, gaps=0):
    db.execute("INSERT INTO normalization_coverage "
               "(resource_type,coverage_status,record_count,expected_parent_count,"
               "processed_parent_count,inventory_id,known_gap_count,notes,updated_at) "
               "VALUES (?,?,?,?,?,?,?,'snapshot','now')",
               (resource, status, count, expected, processed, inventory, gaps))


@pytest.mark.parametrize("operation", ["count", "sum", "list"])
def test_empty_unproven_resource_always_warns(db, operation):
    metadata(db)
    a = analysis(operation=operation, aggregation_field="valor_global" if operation == "sum" else None)
    result = executar_consulta_normalizada(db, a)
    assert result.coverage.state == "unavailable"
    answer = compose_structured_answer(a, result)
    assert "não comprova inexistência" in answer
    assert result.model_dump()["coverage"]["resources"]


def test_missing_metadata_is_unknown(db):
    assert avaliar_cobertura(db, analysis()).state == "unknown"


def test_proven_inventory_zero_matches_is_bounded(db):
    metadata(db, expected=4, processed=4, inventory="inventory-test")
    result = executar_consulta_normalizada(db, analysis())
    assert result.coverage.state == "covered"
    assert "somente no inventário" in compose_structured_answer(analysis(), result)


def test_time_filter_not_proven_by_inventory(db):
    metadata(db, expected=4, processed=4, inventory="inventory-test")
    assert avaliar_cobertura(db, analysis(filters={"ano": 2025})).state != "covered"


@pytest.mark.parametrize("status,gaps", [("PARTIAL", 0), ("KNOWN_GAPS", 3), ("COMPLETE", 2)])
def test_partial_or_gaps(db, status, gaps):
    metadata(db, status=status, gaps=gaps)
    assert avaliar_cobertura(db, analysis()).state == "partial"


def test_inconsistent_counts_unknown(db):
    metadata(db, count=100)
    assert avaliar_cobertura(db, analysis()).state == "unknown"


def test_contract_dependency_included(db):
    metadata(db, resource="contrato", expected=1, processed=1, inventory="contracts")
    result = avaliar_cobertura(db, analysis("contrato"))
    assert result.state == "unknown"
    assert [r["resource"] for r in result.resources] == ["contrato", "contratacao"]


def test_hybrid_generation_cannot_drop_warning(db, tmp_path):
    metadata(db)
    result = executar_consulta_normalizada(db, analysis())
    llm = Mock(return_value="Resposta simulada.")
    chunk = RankedChunk("a", "Competências comuns e atribuições do fiscal.", 1., 1, 1., 1, 1., {})
    answer = gerar_resposta_rag(None, "competências e atribuições", structured_result=result,
                              retrieval_fn=lambda **_: [chunk], llm_fn=llm,
                              audit_dir=tmp_path)
    llm.assert_called_once()
    assert "não comprova inexistência" in answer.answer
    assert acrescentar_avisos_cobertura(answer.answer, result) == answer.answer


def test_hybrid_no_documents_blocks_generation_but_keeps_warning(db, tmp_path):
    result = executar_consulta_normalizada(db, analysis())
    llm = Mock()
    answer = gerar_resposta_rag(None, "Quais regras se aplicam?", structured_result=result,
                              retrieval_fn=lambda **_: [], llm_fn=llm, audit_dir=tmp_path)
    llm.assert_not_called()
    assert "Cobertura" in answer.answer
