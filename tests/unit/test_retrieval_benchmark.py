from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.database import create_sqlite_engine, init_db
from app.core.database.readonly import readonly_session
from app.modules.corpus.model import CorpusVersion
from app.modules.documents.model import Document, DocumentChunk
from eval.benchmark import executar_benchmark


def _criar_banco(tmp_path):
    database = tmp_path / "benchmark.db"
    engine = create_sqlite_engine(f"sqlite:///{database}")
    init_db(engine)
    with Session(engine) as db:
        document = Document(
            corpus_version=CorpusVersion(version="benchmark-v1", status="ready"),
            source_file="norma.html",
            base_id="editais_normativo",
            doc_type="lei",
            sensitivity="publico",
        )
        db.add_all(
            [
                DocumentChunk(
                    document=document,
                    chunk_id="editais_normativo:lei:1:p1",
                    content="A licitação exige planejamento anual.",
                ),
                DocumentChunk(
                    document=document,
                    chunk_id="editais_normativo:lei:2:p1",
                    content="O contrato possui prazo de vigência definido.",
                ),
            ]
        )
        db.commit()
    engine.dispose()
    return database


def test_benchmark_calcula_hit_recall_mrr_e_latencia(tmp_path):
    database = _criar_banco(tmp_path)
    payload = {
        "corpus": "teste-v1",
        "base": "editais_normativo",
        "questions": [
            {
                "id": "Q1",
                "tipo": "factual",
                "pergunta": "Qual o prazo de vigência do contrato?",
                "fontes_esperadas": [
                    {"chunk_id_prefixo": "editais_normativo:lei:2:"}
                ],
            },
            {
                "id": "Q2",
                "tipo": "rota",
                "pergunta": "Pergunta avaliada fora da recuperação",
                "fontes_esperadas": [],
            },
        ],
    }

    with readonly_session(f"sqlite:///{database}") as db:
        report = executar_benchmark(db, payload, top_ks=(1, 2))

    assert report["questions_total"] == 2
    assert report["questions_measured"] == 1
    assert report["questions_not_measured"] == 1
    assert report["aggregate"]["hit@1"] == 1.0
    assert report["aggregate"]["prefix_recall@1"] == 1.0
    assert report["aggregate"]["judged_precision@1"] == 1.0
    assert report["aggregate"]["reciprocal_rank"] == 1.0
    assert report["latency_ms"]["p95"] >= 0


def test_benchmark_preserva_falha_de_recuperacao(tmp_path):
    database = _criar_banco(tmp_path)
    payload = {
        "corpus": "teste-v1",
        "base": "editais_normativo",
        "questions": [
            {
                "id": "Q1",
                "pergunta": "conteúdo inexistente xyz",
                "fontes_esperadas": [
                    {"chunk_id_prefixo": "editais_normativo:lei:2:"}
                ],
            }
        ],
    }

    with readonly_session(f"sqlite:///{database}") as db:
        report = executar_benchmark(db, payload, top_ks=(1, 5))

    assert report["aggregate"]["hit@1"] == 0.0
    assert report["aggregate"]["prefix_recall@5"] == 0.0
    assert report["aggregate"]["reciprocal_rank"] == 0.0
    assert report["questions"][0]["ranking"] == []
