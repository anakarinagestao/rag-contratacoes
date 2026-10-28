"""Avaliação factual offline; anotações são propostas e exigem revisão humana."""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database.orm import register_models
from app.core.database.readonly import create_readonly_sqlite_engine
from app.modules.documents.model import DocumentChunk
from src.fts import buscar_fts5
from src.retrieve import _buscar_no_documento, _identificar_norma, construir_consulta_chunks_elegiveis

ROOT = Path(__file__).resolve().parents[2]
CASES = [
    {"id": "normativa_fiscais", "question": "Quais são as competências comuns dos fiscais de contrato e as atribuições específicas dos fiscais técnicos e administrativos?", "aspects": ["competências comuns", "atribuições do fiscal técnico", "atribuições do fiscal administrativo"]},
    {"id": "edital_obrigacoes", "question": "Quais obrigações o edital impõe ao fornecedor e ao órgão?", "aspects": ["obrigações do fornecedor", "obrigações do órgão"]},
    {"id": "contrato_clausulas", "question": "Quais cláusulas do contrato tratam de prazo e pagamento?", "aspects": ["prazo", "pagamento"]},
    {"id": "pncp_publicacoes", "question": "Quais documentos PNCP foram publicados em 2025?", "aspects": ["documentos publicados em 2025"]},
]


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    target = tmp_path_factory.mktemp("factual_eval") / "snapshot.db"
    source = sqlite3.connect(f"file:{(ROOT / 'app.db').resolve()}?mode=ro", uri=True)
    dest = sqlite3.connect(target); source.backup(dest); source.close(); dest.close()
    register_models()
    engine = create_readonly_sqlite_engine("sqlite:///" + target.as_posix())
    with Session(engine) as session:
        yield session
    engine.dispose()


def _candidates(db, question: str, limit: int = 10):
    document_id = _identificar_norma(db, question)
    eligible = construir_consulta_chunks_elegiveis(None, {"editais_normativo", "pncp_rj_estruturada"})
    rows = buscar_fts5(db, question, 100, eligible_chunks=eligible)
    allowed = set(db.scalars(eligible))
    local = _buscar_no_documento(db, question, document_id, allowed, limit) if document_id else []
    ids = []
    for chunk_id, score in [*rows[:limit], *local[:limit]]:
        if chunk_id not in ids:
            ids.append(chunk_id)
    chunks = {c.chunk_id: c for c in db.scalars(select(DocumentChunk).where(DocumentChunk.chunk_id.in_(ids)))}
    result = []
    for rank, chunk_id in enumerate(ids, 1):
        chunk = chunks.get(chunk_id)
        if chunk:
            result.append({"rank_candidate": rank, "chunk_id": chunk_id, "document_id": chunk.document_id, "document_title": chunk.document.title if chunk.document else None, "text": chunk.content, "proposed_label": "pending_review", "aspect": None, "review_status": "pending"})
    return result


def test_gera_anotacoes_pendentes_com_conteudo_real(db, tmp_path):
    output = Path(os.environ.get("FACTUAL_ANNOTATIONS_OUTPUT", str(tmp_path / "factual_evidence_annotations.json")))
    payload = {"schema_version": 1, "review_policy": "human_validation_required", "cases": []}
    for case in CASES:
        payload["cases"].append({**case, "candidates": _candidates(db, case["question"])})
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    loaded = json.loads(output.read_text(encoding="utf-8"))
    assert loaded["review_policy"] == "human_validation_required"
    assert all("aspects" in case and "candidates" in case for case in loaded["cases"])
    assert all(item["review_status"] == "pending" for case in loaded["cases"] for item in case["candidates"])


def test_metricas_so_contam_anotacoes_aprovadas():
    annotations = [{"chunk_id": "a", "aspect": "x", "review_status": "approved", "label": "direct"}, {"chunk_id": "b", "aspect": "y", "review_status": "pending", "label": "direct"}]
    approved = [item for item in annotations if item["review_status"] == "approved"]
    assert len(approved) == 1
    assert not any(item["review_status"] == "approved" and item["label"] == "pending_review" for item in annotations)


def _metrics(ranking, approved, aspects):
    """Métricas só sobre rótulos aprovados; ausência de rótulo é desconhecida."""
    by_id = {item["chunk_id"]: item for item in approved if item["review_status"] == "approved"}
    relevant = {cid for cid, item in by_id.items() if item["label"] in {"direct", "complementary"}}
    result = {}
    for k in (5, 10):
        top = ranking[:k]
        hits = [cid for cid in top if cid in relevant]
        covered = {by_id[cid]["aspect"] for cid in hits if by_id[cid].get("aspect")}
        docs = {by_id[cid].get("document_id") for cid in hits}
        result[f"recall@{k}"] = len(set(hits)) / len(relevant) if relevant else None
        result[f"precision@{k}"] = len(hits) / len(top) if top else None
        result[f"aspect_coverage@{k}"] = len(covered) / len(aspects) if aspects else None
        result[f"redundancy@{k}"] = len(hits) - len(covered)
        result[f"document_contamination@{k}"] = max(0, len(docs) - 1)
    return result


def test_metricas_comparativas_ignoram_anotacoes_pendentes():
    approved = [
        {"chunk_id": "a", "aspect": "x", "label": "direct", "review_status": "approved", "document_id": 1},
        {"chunk_id": "b", "aspect": "y", "label": "complementary", "review_status": "approved", "document_id": 1},
        {"chunk_id": "c", "aspect": "x", "label": "irrelevant", "review_status": "approved", "document_id": 2},
        {"chunk_id": "d", "aspect": "y", "label": "direct", "review_status": "pending", "document_id": 1},
    ]
    metrics = _metrics(["a", "c", "d", "b"], approved, ["x", "y"])
    assert metrics["recall@5"] == pytest.approx(1.0)
    assert metrics["precision@5"] == pytest.approx(2 / 4)
    assert metrics["aspect_coverage@5"] == pytest.approx(1.0)
    assert metrics["document_contamination@5"] == 0

