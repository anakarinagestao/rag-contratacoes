"""Protótipo offline de recuperação por subperguntas; não altera produção."""
from __future__ import annotations

import json
import os
import shutil
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
TRACE = ROOT / "logs/queries/2026-10-22/a981f0a8-eacb-4bfc-bb6c-372ea3b481c5.json"
PREFIX = "editais_normativo:decreto-48817-2023-gestao-fiscalizacao:"
ORIGINAL = "Quais são as competências comuns dos fiscais de contrato e quais são as atribuições específicas dos fiscais técnicos e administrativos, conforme o Decreto Estadual RJ nº 48.817/2023?"
SUBQUESTIONS = {
    "competencias_comuns": "Quais são as competências comuns dos fiscais de contrato?",
    "fiscal_tecnico": "Quais são as atribuições específicas do fiscal técnico?",
    "fiscal_administrativo": "Quais são as atribuições específicas do fiscal administrativo?",
}


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    target = tmp_path_factory.mktemp("multi_query") / "snapshot.db"
    configured = os.environ.get("PNCP_REGRESSION_SNAPSHOT")
    source_path = Path(configured).resolve() if configured else (ROOT / "app.db").resolve()
    if not source_path.is_file():
        pytest.skip("Diagnóstico real requer um snapshot SQLite local")
    source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    dest = sqlite3.connect(target); source.backup(dest); source.close(); dest.close()
    register_models()
    engine = create_readonly_sqlite_engine("sqlite:///" + target.as_posix())
    try:
        with Session(engine) as session:
            if _identificar_norma(session, ORIGINAL) is None:
                if configured:
                    pytest.fail("Snapshot informado não contém o decreto esperado")
                pytest.skip(
                    "app.db local não contém o corpus normativo exigido pelo diagnóstico"
                )
            yield session
    finally:
        engine.dispose()


def _run(db, question: str, document_id: int, limit: int = 20):
    eligible = construir_consulta_chunks_elegiveis(None, {"editais_normativo"})
    lexical = buscar_fts5(db, question, 100, eligible_chunks=eligible)
    allowed = set(db.scalars(eligible))
    local = _buscar_no_documento(db, question, document_id, allowed, limit)
    return {"bm25": lexical, "local": local}


def _rrf(runs: dict[str, dict[str, list[tuple[str, float]]]], k: int = 60):
    scores: dict[str, float] = {}
    provenance: dict[str, dict[str, list[str]]] = {}
    for subquestion, channels in runs.items():
        for channel, ranking in channels.items():
            seen: set[str] = set()
            for rank, (chunk_id, score) in enumerate(ranking, 1):
                if chunk_id in seen:
                    continue
                seen.add(chunk_id)
                scores[chunk_id] = scores.get(chunk_id, 0.0) + 1 / (k + rank)
                provenance.setdefault(chunk_id, {"subquestions": [], "channels": []})
                if subquestion not in provenance[chunk_id]["subquestions"]:
                    provenance[chunk_id]["subquestions"].append(subquestion)
                if channel not in provenance[chunk_id]["channels"]:
                    provenance[chunk_id]["channels"].append(channel)
    order = sorted(scores, key=lambda cid: scores[cid], reverse=True)
    return [(cid, scores[cid], provenance[cid]) for cid in order]


def _balanced(runs, global_rrf, limit):
    selected = []
    seen = set()
    # Uma passagem por subpergunta preserva recall de aspectos sem impor
    # limites por artigo, documento ou ID.
    for name, channels in runs.items():
        candidates = [cid for ranking in channels.values() for cid, _ in ranking]
        for cid in candidates:
            if cid not in seen:
                selected.append(cid); seen.add(cid); break
    for cid, _, _ in global_rrf:
        if len(selected) >= limit:
            break
        if cid not in seen:
            selected.append(cid); seen.add(cid)
    return selected[:limit]


def test_rrf_deduplica_ids_dentro_do_canal_e_preserva_proveniencia():
    runs = {
        "a": {"bm25": [("x", 10.0), ("x", 9.0), ("y", 8.0)]},
        "b": {"bm25": [("x", 7.0), ("z", 6.0)]},
    }
    fused = _rrf(runs)
    by_id = {item[0]: item for item in fused}
    assert by_id["x"][1] == pytest.approx(1 / 61 + 1 / 61)
    assert by_id["x"][2] == {"subquestions": ["a", "b"], "channels": ["bm25"]}


def test_subperguntas_reais_preservam_filtros_e_registram_origem(db, capsys):
    document_id = _identificar_norma(db, ORIGINAL)
    assert document_id is not None
    original = _run(db, ORIGINAL, document_id)
    original_rerank = []
    if TRACE.is_file():
        trace = json.loads(TRACE.read_text(encoding="utf-8"))
        original_rerank = trace["retrieval"]["ranking_stages"]["rerank"]
    runs = {name: _run(db, question, document_id) for name, question in SUBQUESTIONS.items()}
    union: dict[str, set[str]] = {}
    for name, result in runs.items():
        union[name] = {cid for ranking in result.values() for cid, _ in ranking}
    combined = set().union(*union.values())
    fused = _rrf(runs)
    balanced5 = _balanced(runs, fused, 5)
    balanced10 = _balanced(runs, fused, 10)
    print(json.dumps({
        "original_trace_top5": [item["chunk_id"] for item in original_rerank[:5]],
        "original_trace_top10": [item["chunk_id"] for item in original_rerank[:10]],
        "original_local_top5": [cid for cid, _ in original["local"][:5]],
        "subquestions_top5": {name: [cid for cid, _ in result["local"][:5]] for name, result in runs.items()},
        "rrf_top5": [cid for cid, _, _ in fused[:5]],
        "rrf_top10": [cid for cid, _, _ in fused[:10]],
        "balanced_top5": balanced5,
        "balanced_top10": balanced10,
        "source_subquestion": {cid: item for cid, _, item in fused[:10]},
        "coverage": {name: bool(ids) for name, ids in union.items()},
        "candidate_recall": len(combined),
        "noise_top5_rrf": sum(not cid.startswith(PREFIX) for cid, _, _ in fused[:5]),
        "redundancy_top5_rrf": sum(len(item[2]["subquestions"]) > 1 for item in fused[:5]),
        "selection_note": "balanced is a diagnostic baseline; factual sufficiency remains inconclusive",
        "factual_sufficiency": "inconclusive_without_document-level validation",
        "faiss": "not executed: compatible local embedding invocation is not guaranteed without external model/service",
    }, ensure_ascii=False))
    assert combined
    assert all(name in union for name in SUBQUESTIONS)
    assert len(balanced5) == 5 and len(balanced10) == 10
    assert all(item[2]["subquestions"] for item in fused[:10])


@pytest.mark.parametrize("question", [
    "Quais obrigações o edital impõe ao fornecedor e ao órgão?",
    "Quais cláusulas do contrato tratam de prazo e pagamento?",
    "Quais documentos PNCP foram publicados em 2025?",
])
def test_fixtures_editais_contratos_pncp_sao_deterministicas(db, question):
    document_id = _identificar_norma(db, ORIGINAL)
    first = _run(db, question, document_id)
    second = _run(db, question, document_id)
    assert first == second
