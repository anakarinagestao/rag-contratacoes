from __future__ import annotations

from pathlib import Path
from typing import Callable

from sqlalchemy.orm import Session

from app.core.config import resolver_caminho_indice_faiss
from src.retrieve import RankedChunk, retrieve_hibrido
from src.query.schemas import KnowledgeBase, QueryFilters


EmbeddingFn = Callable[[str], list[float]]


def recuperar_chunks_ranqueados(
    db: Session,
    pergunta: str,
    top_k: int = 10,
    cache_dir: Path | None = None,
    embedding_fn: EmbeddingFn | None = None,
    filters: QueryFilters | None = None,
    bases: list[KnowledgeBase] | None = None,
    trace_collector: dict[str, object] | None = None,
) -> list[RankedChunk]:
    kwargs = {"cache_dir": resolver_caminho_indice_faiss(cache_dir)}
    if embedding_fn is not None:
        kwargs["embedding_fn"] = embedding_fn
    if filters is not None:
        kwargs["filters"] = filters
    if bases is not None:
        kwargs["bases"] = bases
    if trace_collector is not None:
        kwargs["trace_collector"] = trace_collector
    return retrieve_hibrido(db=db, pergunta=pergunta, top_k=top_k, **kwargs)
