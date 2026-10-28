"""Benchmark lexical offline do corpus documental do PNCP/RJ.

O benchmark usa somente perguntas com fontes esperadas revisadas e abre o
SQLite em modo estritamente somente leitura. Os prefixos esperados são
julgamentos parciais: por isso ``prefix_recall`` e ``judged_precision`` não
devem ser interpretados como cobertura exaustiva de todo o corpus.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.core.database.readonly import readonly_session
from app.modules.documents.model import Document, DocumentChunk
from src.fts import buscar_fts5

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_QUESTIONS = ROOT / "eval" / "questions.json"
DEFAULT_DATABASE_URL = "sqlite:///./data/debug/pncp_normative_demo.db"
DEFAULT_TOP_KS = (1, 3, 5, 10)


def carregar_perguntas(path: Path) -> dict[str, Any]:
    """Carrega e valida o contrato mínimo do conjunto de perguntas."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    questions = payload.get("questions")
    if not isinstance(questions, list):
        raise ValueError("O arquivo de avaliação deve conter uma lista 'questions'.")
    for question in questions:
        if not isinstance(question, dict) or not question.get("id") or not question.get(
            "pergunta"
        ):
            raise ValueError("Cada pergunta deve possuir 'id' e 'pergunta'.")
    return payload


def _prefixos_esperados(question: dict[str, Any]) -> tuple[str, ...]:
    return tuple(
        str(source["chunk_id_prefixo"])
        for source in question.get("fontes_esperadas", [])
        if isinstance(source, dict) and source.get("chunk_id_prefixo")
    )


def _consulta_elegivel(base_id: str | None) -> Select[Any] | None:
    if not base_id:
        return None
    return (
        select(DocumentChunk.chunk_id)
        .join(DocumentChunk.document)
        .where(Document.base_id == base_id)
    )


def _percentil(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _avaliar_ranking(
    ranking: Sequence[str],
    expected_prefixes: Sequence[str],
    top_ks: Sequence[int],
) -> dict[str, float]:
    metrics: dict[str, float] = {}
    for k in top_ks:
        top = ranking[:k]
        matched_prefixes = {
            prefix
            for prefix in expected_prefixes
            if any(chunk_id.startswith(prefix) for chunk_id in top)
        }
        judged_hits = [
            chunk_id
            for chunk_id in top
            if any(chunk_id.startswith(prefix) for prefix in expected_prefixes)
        ]
        metrics[f"hit@{k}"] = float(bool(matched_prefixes))
        metrics[f"prefix_recall@{k}"] = len(matched_prefixes) / len(
            expected_prefixes
        )
        metrics[f"judged_precision@{k}"] = (
            len(judged_hits) / len(top) if top else 0.0
        )

    first_relevant_rank = next(
        (
            rank
            for rank, chunk_id in enumerate(ranking, start=1)
            if any(chunk_id.startswith(prefix) for prefix in expected_prefixes)
        ),
        None,
    )
    metrics["reciprocal_rank"] = (
        1.0 / first_relevant_rank if first_relevant_rank is not None else 0.0
    )
    return metrics


def executar_benchmark(
    db: Session,
    payload: dict[str, Any],
    *,
    top_ks: Sequence[int] = DEFAULT_TOP_KS,
) -> dict[str, Any]:
    """Executa a linha de base BM25/FTS5 sem alterar corpus ou índice."""

    normalized_top_ks = tuple(sorted(set(int(k) for k in top_ks if int(k) > 0)))
    if not normalized_top_ks:
        raise ValueError("Informe ao menos um top-k positivo.")

    measurable = [
        question
        for question in payload["questions"]
        if _prefixos_esperados(question)
    ]
    if not measurable:
        raise ValueError("Não há perguntas com fontes esperadas para medir recuperação.")

    max_k = max(normalized_top_ks)
    eligible = _consulta_elegivel(payload.get("base"))
    results: list[dict[str, Any]] = []
    latencies: list[float] = []

    for question in measurable:
        started = time.perf_counter()
        rows = buscar_fts5(
            db,
            str(question["pergunta"]),
            max_k,
            eligible_chunks=eligible,
        )
        latency_ms = (time.perf_counter() - started) * 1000
        latencies.append(latency_ms)
        ranking = [chunk_id for chunk_id, _score in rows]
        expected = _prefixos_esperados(question)
        metrics = _avaliar_ranking(ranking, expected, normalized_top_ks)
        results.append(
            {
                "id": question["id"],
                "type": question.get("tipo"),
                "expected_prefixes": list(expected),
                "ranking": ranking,
                "latency_ms": round(latency_ms, 3),
                "metrics": {key: round(value, 6) for key, value in metrics.items()},
            }
        )

    aggregate: dict[str, float] = {}
    for key in results[0]["metrics"]:
        aggregate[key] = round(
            sum(result["metrics"][key] for result in results) / len(results), 6
        )

    return {
        "schema_version": 1,
        "retriever": "sqlite_fts5_bm25",
        "corpus": payload.get("corpus"),
        "base": payload.get("base"),
        "label_policy": "reviewed_expected_chunk_prefixes",
        "limitations": [
            "Os julgamentos por prefixo são parciais e não enumeram todo chunk relevante.",
            "Esta execução mede a linha de base lexical; FAISS e RRF exigem avaliação separada.",
            "Perguntas sem fontes esperadas avaliam rota, metadado ou recusa e não entram nas métricas de recuperação.",
        ],
        "questions_total": len(payload["questions"]),
        "questions_measured": len(results),
        "questions_not_measured": len(payload["questions"]) - len(results),
        "top_ks": list(normalized_top_ks),
        "aggregate": aggregate,
        "latency_ms": {
            "mean": round(sum(latencies) / len(latencies), 3),
            "p50": round(_percentil(latencies, 0.50) or 0.0, 3),
            "p95": round(_percentil(latencies, 0.95) or 0.0, 3),
            "max": round(max(latencies), 3),
        },
        "questions": results,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Mede a linha de base lexical offline do corpus PNCP/RJ."
    )
    parser.add_argument("--database-url", default=DEFAULT_DATABASE_URL)
    parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS)
    parser.add_argument("--top-k", nargs="+", type=int, default=DEFAULT_TOP_KS)
    parser.add_argument(
        "--output",
        type=Path,
        help="Arquivo JSON opcional; sem esta opção, escreve apenas no stdout.",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    payload = carregar_perguntas(args.questions)
    with readonly_session(args.database_url) as db:
        report = executar_benchmark(db, payload, top_ks=args.top_k)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
