"""Índice e busca FAISS reduzidos para validar o MVP sem tocar no corpus todo.

Esta pipeline lê uma amostra de ``DocumentChunk`` já persistidos no SQLite.
Ela não altera documentos, chunks, manifestos ou JSONL; salva somente um índice
de teste separado em ``data/index/faiss_mvp``.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database.readonly import readonly_session
from app.modules.documents.model import DocumentChunk
from llm_ecoa import EMBEDDING_MODEL, gerar_embedding
from src.fts import buscar_fts5
from src.index import FaissChunkIndex, indexar_chunks_incrementalmente
from src.retrieve import reciprocal_rank_fusion


def carregar_amostra(
    db: Session,
    limite_chunks: int,
    tipo: str,
) -> list[DocumentChunk]:
    """Retorna uma amostra determinística de chunks para o MVP."""

    if limite_chunks <= 0:
        raise ValueError("limite_chunks deve ser maior que zero.")

    consulta = select(DocumentChunk).order_by(DocumentChunk.id)
    if tipo != "todos":
        consulta = consulta.where(DocumentChunk.doc_type == tipo)
    return list(db.scalars(consulta.limit(limite_chunks)))


def criar_indice(
    chunks: list[DocumentChunk],
    diretorio_indice: Path,
) -> FaissChunkIndex:
    if not chunks:
        raise RuntimeError("Nenhum chunk encontrado para a amostra solicitada.")

    print(
        f"Indexando {len(chunks)} chunks em {diretorio_indice}... "
        "isso gera um embedding por chunk."
    )
    indice, _ = indexar_chunks_incrementalmente(
        [(chunk.chunk_id, chunk.content) for chunk in chunks],
        gerar_embedding,
        diretorio_indice,
        modelo_embedding=EMBEDDING_MODEL,
    )
    print(
        "Índice MVP pronto | "
        f"chunks={len(indice.chunk_ids)} | diretorio={diretorio_indice}"
    )
    return indice


def buscar(
    db: Session,
    pergunta: str,
    chunks: list[DocumentChunk],
    indice: FaissChunkIndex,
    top_k: int,
) -> list[dict[str, object]]:
    """Executa FTS5 + FAISS + RRF somente sobre a amostra selecionada."""

    por_id = {chunk.chunk_id: chunk for chunk in chunks}
    ids_amostra = {chunk.chunk_id for chunk in chunks}
    bm25 = [
        (chunk_id, score)
        for chunk_id, score in buscar_fts5(db, pergunta, top_k=top_k * 10)
        if chunk_id in ids_amostra
    ][: top_k * 3]
    dense = indice.search(gerar_embedding(pergunta), top_k=top_k * 3)
    rrf = reciprocal_rank_fusion([bm25, dense])
    ranking_bm25 = {chunk_id: posicao for posicao, (chunk_id, _) in enumerate(bm25, 1)}
    ranking_dense = {chunk_id: posicao for posicao, (chunk_id, _) in enumerate(dense, 1)}

    resultados = []
    for chunk_id, score in sorted(
        rrf.items(), key=lambda item: item[1], reverse=True
    )[:top_k]:
        chunk = por_id[chunk_id]
        resultados.append(
            {
                "chunkId": chunk.chunk_id,
                "rrfScore": round(score, 6),
                "bm25Rank": ranking_bm25.get(chunk_id),
                "denseRank": ranking_dense.get(chunk_id),
                "tipo": chunk.doc_type,
                "orgao": chunk.orgao,
                "objeto": chunk.objeto,
                "conteudo": chunk.content,
            }
        )
    return resultados


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Cria e consulta uma amostra FAISS para o MVP."
    )
    parser.add_argument("--acao", choices=["indexar", "buscar"], required=True)
    parser.add_argument("--pergunta")
    parser.add_argument("--limite-chunks", type=int, default=100)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument(
        "--tipo",
        choices=["contrato", "contratacao", "todos"],
        default="todos",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path("data/index/faiss/staging/mvp"),
    )
    args = parser.parse_args()

    if args.acao == "buscar" and not args.pergunta:
        parser.error("--pergunta é obrigatório quando --acao buscar.")

    with readonly_session(settings.database_url) as db:
        chunks = carregar_amostra(db, args.limite_chunks, args.tipo)
        indice = criar_indice(chunks, args.cache_dir)
        if args.acao == "buscar":
            resultados = buscar(db, args.pergunta, chunks, indice, args.top_k)
            print(json.dumps(resultados, ensure_ascii=False, indent=2))
