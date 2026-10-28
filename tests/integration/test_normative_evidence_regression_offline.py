"""Diagnóstico offline do retrieval real, sem corrigir regras de produção.

BM25/FTS5, filtros, busca local, RRF e reranking são os de produção, executados
em snapshot temporário completo do SQLite (preserva a estatística global BM25).
FAISS é simulado em dois cenários: sem candidatos e continuação 25:p2 primeiro.
Isso NÃO mede recall semântico real. Nenhum modelo/serviço externo é chamado.

python -B -m pytest tests/integration/test_normative_evidence_regression_offline.py \
    -q -s -p no:cacheprovider --basetemp <diretorio-temporario>

Os rastros JSON ficam somente no basetemp. PNCP_REGRESSION_SNAPSHOT permite
fornecer um snapshot já criado pelo executor isolado; nunca use o banco original.
"""
from __future__ import annotations

import builtins
import hashlib
import json
import os
import socket
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.database.readonly import create_readonly_sqlite_engine
from app.core.database.orm import register_models
from src import retrieve
from src.query.schemas import KnowledgeBase, QueryFilters


ROOT = Path(__file__).resolve().parents[2]
PREFIX = "editais_normativo:decreto-48817-2023-gestao-fiscalizacao:"
TARGETS = [PREFIX + part for part in ("24:p1", "25:p1", "25:p2", "26:p1")]
QUESTION = (
    "Quais são as competências comuns dos fiscais de contrato e as atribuições "
    "dos fiscais técnicos e administrativos previstas no Decreto Estadual RJ nº 48.817/2023?"
)


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def position(ranking, chunk_id):
    return next((i for i, (key, _) in enumerate(ranking, 1) if key == chunk_id), None)


@pytest.fixture(scope="module", autouse=True)
def no_network_or_models():
    """Bloqueia a rede mesmo que uma futura alteração acione um caminho novo."""
    with (
        patch.object(socket.socket, "connect", side_effect=AssertionError("Rede proibida")),
        patch.object(socket.socket, "connect_ex", side_effect=AssertionError("Rede proibida")),
        patch.object(socket, "create_connection", side_effect=AssertionError("Rede proibida")),
        patch("llm_ecoa.client.embeddings.create", side_effect=AssertionError("Embedding externo proibido")),
        patch("llm_ecoa.client.chat.completions.create", side_effect=AssertionError("LLM proibida")),
    ):
        yield


@pytest.fixture(scope="module")
def corpus(tmp_path_factory, no_network_or_models):
    original = ROOT / "app.db"
    folder = tmp_path_factory.mktemp("normative_regression")
    supplied = os.environ.get("PNCP_REGRESSION_SNAPSHOT")
    snapshot = Path(supplied).resolve() if supplied else folder / "snapshot.db"
    assert snapshot.resolve() != original.resolve()
    if supplied:
        assert snapshot.is_file()
    elif not original.is_file():
        pytest.skip("Diagnóstico real requer app.db local; não fabricar textos normativos")
    else:
        with closing(sqlite3.connect(original.resolve().as_uri() + "?mode=ro", uri=True)) as source:
            with closing(sqlite3.connect(snapshot)) as dest:
                source.backup(dest)
    register_models()
    engine = create_readonly_sqlite_engine("sqlite:///" + snapshot.as_posix())
    try:
        with Session(engine) as session:
            document = session.execute(text(
                "SELECT id FROM documents WHERE base_id='editais_normativo' "
                "AND source_file='editais_normativo|decreto-48817-2023-gestao-fiscalizacao.pdf'"
            )).scalar_one_or_none()
            if document is None and not supplied:
                pytest.skip(
                    "app.db local não contém o corpus normativo exigido pelo diagnóstico"
                )
            assert document is not None, "Snapshot informado não contém o decreto esperado"
            ids = set(session.scalars(text("SELECT chunk_id FROM document_chunks")))
            assert set(TARGETS) <= ids, "Snapshot não contém todos os quatro chunks reais"
            other_document = session.execute(text(
                "SELECT id FROM documents WHERE base_id='editais_normativo' "
                "AND source_file='editais_normativo|planalto_l14133.html'"
            )).scalar_one()
        traces = []
        before = digest(snapshot)
        yield {"engine": engine, "document": document, "other_document": other_document,
               "ids": sorted(ids), "traces": traces, "folder": folder}
        assert digest(snapshot) == before, "Teste alterou o snapshot usado somente para leitura"
        (folder / "trace.json").write_text(json.dumps(traces, ensure_ascii=False, indent=2), encoding="utf-8")
        compact = [{"bm25_limit": t["bm25_limit"], "top_k": t["top_k"],
                    "dense_simulation": t["dense_simulation"], "targets": t.get("targets", []),
                    "error": t.get("error")} for t in traces]
        print("\nOFFLINE_REGRESSION_TRACE=" + json.dumps(compact, ensure_ascii=True))
    finally:
        engine.dispose()


class SimulatedFaiss:
    """Ranking controlado; nenhum vetor ou arquivo FAISS real é usado."""

    def __init__(self, ids, scenario):
        self.chunk_ids = list(ids)
        self.scenario = scenario
        order = [TARGETS[2], TARGETS[0], TARGETS[1], TARGETS[3]]
        self.ranking = [(key, 1.0 / i) for i, key in enumerate(
            order + [key for key in ids if key not in TARGETS], 1
        )]

    def search(self, embedding, top_k):
        assert embedding == [1.0, 0.0], "Somente embedding de teste é permitido"
        return [] if self.scenario == "empty" else self.ranking[:top_k]


def run_trace(corpus, limit, top_k, scenario="empty", *, filters=None,
              bases=None, question=QUESTION):
    trace = {"bm25_limit": limit, "dense_limit": 100, "top_k": top_k,
             "dense_simulation": scenario, "real_stages": ["filters", "bm25_fts5", "local", "rrf", "rerank"],
             "simulated_stages": ["faiss", "embedding"], "question": question,
             "stages": [], "lexical": [], "dense": [], "local": [], "local_full": [],
             "rrf_order": [], "rerank_order": [], "filtered_targets": [],
             "document_identified": None}
    stack = []
    actual_fts = retrieve.buscar_fts5
    actual_dense = retrieve.buscar_dense
    actual_local = retrieve._buscar_no_documento
    actual_rrf = retrieve.reciprocal_rank_fusion
    actual_identify = retrieve._identificar_norma
    fake = SimulatedFaiss(corpus["ids"], scenario)
    bases = [KnowledgeBase.NORMATIVA] if bases is None else bases

    @contextmanager
    def observe_stage(name):
        trace["stages"].append(name)
        stack.append(name)
        try:
            yield {}
        finally:
            stack.pop()

    def observe_sorted(values, *args, **kwargs):
        # Captura a ordenação REAL antes de [:top_k], sem reproduzir sua chave.
        result = builtins.sorted(values, *args, **kwargs)
        if stack and stack[-1] == "normative_rerank":
            trace["rerank_order"] = list(result)
        return result

    def observe_fts(db, question, k, **kwargs):
        trace["lexical"] = actual_fts(db, question, k, **kwargs)
        trace["bm25_full"] = actual_fts(db, question, 1_000_000, **kwargs)
        return trace["lexical"]

    def observe_dense(*args, **kwargs):
        allowed = kwargs.get("allowed_chunk_ids")
        trace["filtered_targets"] = [key for key in TARGETS if allowed is not None and key not in allowed]
        trace["dense"] = actual_dense(*args, **kwargs)
        return trace["dense"]

    def observe_local(db, question, doc_id, allowed, k, **kwargs):
        trace["local_limit"] = k
        try:
            # Primeiro a chamada efetiva de produção; só depois a referência sem corte.
            trace["local"] = actual_local(db, question, doc_id, allowed, k, **kwargs)
        except OperationalError as exc:
            connection = db.connection().connection.driver_connection
            trace["error"] = {"stage": "normative_local", "type": type(exc).__name__,
                "message": str(exc.orig), "eligible_ids": len(allowed) if allowed is not None else None,
                "sqlite_variable_limit": connection.getlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER)}
            corpus["traces"].append(trace)
            raise
        trace["local_full"] = actual_local(db, question, doc_id, allowed, 1_000_000, **kwargs)
        return trace["local"]

    def observe_identify(*args):
        trace["document_identified"] = actual_identify(*args)
        return trace["document_identified"]

    def observe_rrf(rankings, k=60):
        fused = actual_rrf(rankings, k=k)
        # Referência independente para conferir contribuições e não duplicação.
        expected = {}
        for ranking in rankings:
            seen = set()
            for rank, (key, _) in enumerate(ranking, 1):
                if key not in seen:
                    expected[key] = expected.get(key, 0.0) + 1 / (k + rank)
                    seen.add(key)
        assert fused == pytest.approx(expected)
        trace["rrf_order"] = builtins.sorted(fused.items(), key=lambda x: x[1], reverse=True)
        return fused

    def fake_embedding(_):
        return [1.0, 0.0]

    with (
        Session(corpus["engine"]) as session,
        patch.object(retrieve, "perf_stage", side_effect=observe_stage),
        patch.object(retrieve, "sorted", side_effect=observe_sorted, create=True),
        patch.object(retrieve, "buscar_fts5", side_effect=observe_fts),
        patch.object(retrieve, "buscar_dense", side_effect=observe_dense),
        patch.object(retrieve, "_buscar_no_documento", side_effect=observe_local),
        patch.object(retrieve, "_identificar_norma", side_effect=observe_identify),
        patch.object(retrieve, "reciprocal_rank_fusion", side_effect=observe_rrf),
        patch.object(retrieve, "carregar_indice_faiss", return_value=fake) as loader,
    ):
        results = retrieve.retrieve_hibrido(session, question, top_k=top_k,
            bm25_k=limit, dense_k=100, embedding_fn=fake_embedding, filters=filters,
            bases=bases, cache_dir=corpus["folder"] / "simulated_index_not_written")
        final = [(r.chunk_id, r.rrf_score) for r in results]
        assert len(final) <= top_k and len({key for key, _ in final}) == len(final)
        eligible = retrieve.construir_consulta_chunks_elegiveis(filters, retrieve.resolver_base_ids(bases))
        allowed_ids = set(session.scalars(eligible)) if eligible is not None else set(corpus["ids"])
        assert {key for key, _ in final} <= allowed_ids
        trace["filtered_targets"] = [key for key in TARGETS if key not in allowed_ids]
        trace["loader_calls"] = loader.call_count
        trace["final"] = final
        trace["targets"] = []
        for key in TARGETS:
            positions = {name: position(trace.get(name, []), key) for name in (
                "bm25_full", "lexical", "dense", "local_full", "local", "rrf_order", "rerank_order", "final")}
            content = session.execute(text("SELECT content FROM document_chunks WHERE chunk_id=:key"), {"key": key}).scalar_one()
            if key in trace["filtered_targets"]:
                reason = "excluded_by_filters"
            elif positions["final"] is not None:
                reason = "selected"
            elif positions["rrf_order"] is None:
                reason = "absent_from_all_candidate_channels"
            elif positions["rerank_order"] is not None and positions["rerank_order"] > top_k:
                reason = "rerank_then_final_cut" if positions["rrf_order"] <= top_k else "below_final_cut_after_rerank"
            else:
                reason = "rrf_then_final_cut"
            trace["targets"].append({"chunk_id": key, **positions,
                "text_relevance": retrieve._relevancia_textual(question, content),
                "bm25_cut": positions["bm25_full"] is not None and positions["lexical"] is None,
                "local_cut": positions["local_full"] is not None and positions["local"] is None,
                "reason": reason})
    corpus["traces"].append(trace)
    return trace


@pytest.mark.parametrize("limit", [20, 100, 500])
@pytest.mark.parametrize("top_k", [5, 10])
@pytest.mark.parametrize("scenario", ["empty", "continuation_first"])
def test_trace_real_stages_with_simulated_dense(corpus, limit, top_k, scenario):
    trace = run_trace(corpus, limit, top_k, scenario)
    assert trace["document_identified"] == corpus["document"]
    assert trace["local_limit"] == max(20, top_k * 4)
    assert len(trace["lexical"]) <= limit and len(trace["dense"]) <= 100
    # Quando a norma foi identificada, os canais globais recebem o mesmo
    # recorte documental antes do RRF.
    assert set(key for key, _ in trace["lexical"]) <= set(
        retrieve_ids_for_document(corpus, trace["document_identified"])
    )
    assert set(key for key, _ in trace["dense"]) <= set(
        retrieve_ids_for_document(corpus, trace["document_identified"])
    )
    assert trace["rerank_order"], "A captura precisa observar o reranking efetivamente chamado"
    assert trace["final"] == trace["rerank_order"][:top_k]
    assert all(t["local_full"] is not None for t in trace["targets"])
    if scenario == "empty":
        assert not trace["dense"]
    else:
        assert trace["targets"][2]["dense"] == 1
    # Diagnostica a cobertura; não transforma a omissão conhecida em comportamento desejado.
    assert all(t["reason"] != "absent_from_all_candidate_channels" or
               (t["lexical"] is None and t["dense"] is None and t["local"] is None)
               for t in trace["targets"])


def retrieve_ids_for_document(corpus, document_id):
    with Session(corpus["engine"]) as session:
        return session.scalars(text(
            "SELECT chunk_id FROM document_chunks WHERE document_id=:document_id"
        ), {"document_id": document_id}).all()


def test_final_limit_changes_local_candidate_budget_too(corpus):
    five = run_trace(corpus, 100, 5)
    ten = run_trace(corpus, 100, 10)
    assert five["lexical"] == ten["lexical"]
    assert five["local"] == five["local_full"][:20]
    assert ten["local"] == ten["local_full"][:40]


def test_simulated_dense_can_supply_a_chunk_omitted_by_real_local_cut(corpus):
    trace = run_trace(corpus, 20, 5, "continuation_first")
    target = trace["targets"][2]
    assert target["dense"] == 1 and target["rrf_order"] is not None
    assert target["local_full"] is not None
    assert target["local_cut"] == (target["local_full"] > trace["local_limit"])


def test_explicit_other_document_filter_disables_local_rescue(corpus):
    trace = run_trace(corpus, 100, 5, "continuation_first",
                      filters=QueryFilters(document_id=corpus["other_document"]))
    assert not trace["local"] and "normative_rerank" not in trace["stages"]
    assert all(t["reason"] == "excluded_by_filters" for t in trace["targets"])


def test_base_filter_cannot_be_overridden_by_norm_identification(corpus):
    try:
        trace = run_trace(corpus, 100, 5, "continuation_first", bases=[KnowledgeBase.ESTRUTURADA])
    except OperationalError as exc:
        if "too many SQL variables" not in str(exc.orig):
            raise
        error = corpus["traces"][-1]["error"]
        assert error["eligible_ids"] + 1 > error["sqlite_variable_limit"]
        pytest.xfail("Falha conhecida: busca local expande IDs elegíveis além do limite SQLite; "
                     f"{error['eligible_ids']} IDs, limite {error['sqlite_variable_limit']}")
    assert not trace["local"]
    assert all(t["reason"] == "excluded_by_filters" for t in trace["targets"])


def test_empty_bases_stop_before_fts_faiss_and_local(corpus):
    trace = run_trace(corpus, 100, 5, bases=[])
    assert not trace["final"] and trace["loader_calls"] == 0
    assert "fts5" not in trace["stages"] and "normative_local" not in trace["stages"]


def test_question_without_explicit_norm_does_not_activate_local_rerank(corpus):
    trace = run_trace(corpus, 100, 5,
        question="Quais são as atribuições dos fiscais técnicos e administrativos na legislação estadual do Rio de Janeiro?")
    assert trace["document_identified"] is None
    assert not trace["local"] and "normative_rerank" not in trace["stages"]
    assert trace["final"] == trace["rrf_order"][:5]


@pytest.mark.parametrize("marker", ["n\u00ba", "n\u00b0", "no", "n.", "n?"])
def test_identifica_norma_com_variacoes_do_marcador_numerico(corpus, marker):
    question = f"Quais regras constam do Decreto Estadual RJ {marker} 48.817/2023?"
    with Session(corpus["engine"]) as session:
        assert retrieve._identificar_norma(session, question) == corpus["document"]


def test_identifica_norma_pergunta_utf8_exata(corpus):
    question = (
        "Quais são as competências comuns dos fiscais de contrato e quais são as "
        "atribuições específicas dos fiscais técnicos e administrativos, conforme "
        "o Decreto Estadual RJ nº 48.817/2023?"
    )
    with Session(corpus["engine"]) as session:
        assert retrieve._identificar_norma(session, question) == corpus["document"]


"""
    "Cobertura desejada ainda não atendida: a continuação 25:p2 é removida pelo "
    "reranking mesmo com BM25=500 e FAISS simulado priorizando o chunk. "
    "Remover o marcador quando uma correção de produção for autorizada."
"""
@pytest.mark.parametrize("top_k", [
    pytest.param(5, marks=pytest.mark.xfail(
        strict=True,
        reason="Top 5 ainda não garante cobertura completa dos quatro candidatos",
    )),
    10,
])
def test_desired_complete_evidence_includes_continuation(corpus, top_k):
    trace = run_trace(corpus, 500, top_k, "continuation_first")
    assert set(TARGETS) <= {key for key, _ in trace["final"]}
