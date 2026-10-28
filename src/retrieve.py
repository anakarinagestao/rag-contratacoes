"""Retrieval híbrido: SQLite FTS5 + FAISS incremental + RRF.



Esta etapa só consulta índices prontos. A criação de embeddings e a inclusão

de novos vetores pertencem à

``base_estruturada.pipelines.pipeline_indexacao_faiss``.

"""



from __future__ import annotations



import argparse

import json

import re
import unicodedata

from dataclasses import asdict, dataclass

from decimal import Decimal

from pathlib import Path

from typing import Callable



from sqlalchemy import Select, func, select

from sqlalchemy.orm import Session, selectinload

from app.core.logging import perf_stage

from app.core.config import resolver_caminho_indice_faiss



from app.core.text_normalization import normalizar_para_busca

from app.modules.documents.model import Document, DocumentChunk

from llm_ecoa import EMBEDDING_MODEL, gerar_embedding

from src.fts import buscar_fts5

from src.index import carregar_indice_faiss

from src.query.base_catalog import resolver_base_ids

from src.query.schemas import KnowledgeBase, QueryEntity, QueryFilters





EmbeddingFn = Callable[[str], list[float]]

DEFAULT_BM25_CANDIDATES = 100
DEFAULT_FAISS_CANDIDATES = 100
MAX_BM25_CANDIDATES = 500
MAX_FAISS_CANDIDATES = 500


@dataclass(frozen=True)
class RankedChunk:

    chunk_id: str

    content: str

    rrf_score: float

    bm25_rank: int | None

    bm25_score: float | None

    dense_rank: int | None

    dense_score: float | None

    metadata: dict[str, object]





def _score_decimal(valor: Decimal | None) -> float | None:

    return float(valor) if valor is not None else None





def _numero_controle_pncp(chunk: DocumentChunk) -> str | None:

    """Recupera o identificador PNCP do formato estável de ``source_file``."""



    if chunk.document.base_id != "pncp_rj_estruturada":

        return None

    partes = chunk.document.source_file.split("|")

    if len(partes) < 4 or not partes[2]:

        return None

    return partes[2]





def _metadata(chunk: DocumentChunk) -> dict[str, object]:

    return {

        "id": chunk.id,

        "doc_type": chunk.doc_type or chunk.document.doc_type,

        "base_id": chunk.base_id or chunk.document.base_id,

        "sensitivity": chunk.sensitivity or chunk.document.sensitivity,

        "orgao": chunk.orgao,

        "modalidade": chunk.modalidade,

        "municipio": chunk.municipio,

        "fornecedor": chunk.fornecedor,

        "objeto": chunk.objeto,

        "valor": _score_decimal(chunk.valor),

        "ano": chunk.ano,

        "data_publicacao": chunk.data_publicacao.isoformat()

        if chunk.data_publicacao

        else None,

        "vigencia": chunk.vigencia.isoformat() if chunk.vigencia else None,

        "document_id": chunk.document_id,

        "numeroControlePNCP": _numero_controle_pncp(chunk),

        "tituloDocumento": chunk.document.title,

        "urlFontePncp": chunk.document.source_url,

    }





def carregar_chunks_por_ids(

    db: Session,

    chunk_ids: list[str],

) -> dict[str, DocumentChunk]:

    """Carrega apenas os chunks vencedores dos índices."""



    if not chunk_ids:

        return {}

    consulta = (

        select(DocumentChunk)

        .options(selectinload(DocumentChunk.document))

        .where(DocumentChunk.chunk_id.in_(chunk_ids))

    )

    return {chunk.chunk_id: chunk for chunk in db.scalars(consulta)}





def buscar_dense(

    pergunta: str,

    top_k: int,

    cache_dir: Path,

    embedding_fn: EmbeddingFn = gerar_embedding,

    allowed_chunk_ids: set[str] | None = None,

) -> list[tuple[str, float]]:

    """Consulta somente um FAISS já indexado; nunca o reconstrói."""



    if top_k <= 0:

        return []

    with perf_stage("faiss_load"):

        indice = carregar_indice_faiss(
            cache_dir,
            modelo_esperado=(
                EMBEDDING_MODEL if embedding_fn is gerar_embedding else None
            ),
        )

    if allowed_chunk_ids is None:

        with perf_stage("embedding"):

            embedding = embedding_fn(pergunta)

        with perf_stage("faiss_search") as metrics:

            ranking = indice.search(embedding, top_k)

            metrics["candidatos"] = len(ranking)

        return ranking

    if not allowed_chunk_ids:

        return []



    # O IndexFlat do FAISS não oferece filtro por metadados. Para manter a

    # correção do recorte, ordenamos o índice e só então preservamos IDs

    # autorizados pelo SQLite. Não há reconstrução nem gravação do índice.

    with perf_stage("embedding"):

        embedding = embedding_fn(pergunta)

    with perf_stage("faiss_search") as metrics:

        ranking_completo = indice.search(embedding, len(indice.chunk_ids))

        metrics["candidatos"] = len(ranking_completo)

    with perf_stage("faiss_filter") as metrics:

        ranking = [

            (chunk_id, score)

            for chunk_id, score in ranking_completo

            if chunk_id in allowed_chunk_ids

        ][:top_k]

        metrics["candidatos"] = len(ranking)

    return ranking





def construir_consulta_chunks_elegiveis(

    filters: QueryFilters | None,

    base_ids: set[str] | None = None,

    document_id_restriction: int | None = None,

) -> Select | None:

    """Constrói o recorte compartilhado por FTS5 e FAISS, sem materializar IDs."""



    if (
        (filters is None or not filters.model_fields_set)
        and base_ids is None
        and document_id_restriction is None
    ):

        return None



    query = select(DocumentChunk.chunk_id).join(DocumentChunk.document)

    if base_ids is not None:

        query = query.where(Document.base_id.in_(sorted(base_ids)))

    if document_id_restriction is not None:
        query = query.where(DocumentChunk.document_id == document_id_restriction)

    if filters is None:

        return query



    if filters.numero_controle_pncp:

        query = query.where(

            Document.source_file.contains(f"|{filters.numero_controle_pncp}|")

        )

    if filters.document_id is not None:

        query = query.where(DocumentChunk.document_id == filters.document_id)

    if filters.tipo_entidade and filters.tipo_entidade is not QueryEntity.DOCUMENTO:

        query = query.where(Document.doc_type == filters.tipo_entidade.value)

    if filters.tipo_documento:

        query = query.where(

            func.lower(Document.title).like(f"%{filters.tipo_documento.lower()}%")

        )



    text_filters = (

        (DocumentChunk.orgao, filters.orgao),

        (DocumentChunk.municipio, filters.municipio),

        (DocumentChunk.fornecedor, filters.fornecedor),

        (DocumentChunk.modalidade, filters.modalidade),

        (DocumentChunk.objeto, filters.objeto),

    )

    for column, value in text_filters:

        if value:

            query = query.where(

                func.normalizar_busca(column).like(

                    f"%{normalizar_para_busca(value)}%"

                )

            )



    if filters.ano is not None:

        query = query.where(DocumentChunk.ano == filters.ano)

    if filters.period_start:

        query = query.where(DocumentChunk.data_publicacao >= filters.period_start)

    if filters.period_end:

        query = query.where(DocumentChunk.data_publicacao <= filters.period_end)

    if filters.valor_minimo is not None:

        query = query.where(DocumentChunk.valor >= filters.valor_minimo)

    if filters.valor_maximo is not None:

        query = query.where(DocumentChunk.valor <= filters.valor_maximo)



    return query





def resolver_chunk_ids_filtrados(

    db: Session,

    filters: QueryFilters | None,

    base_ids: set[str] | None = None,

) -> set[str] | None:

    """Resolve no SQLite os IDs de chunks permitidos pelos filtros da pergunta."""



    query = construir_consulta_chunks_elegiveis(filters, base_ids)

    return None if query is None else set(db.scalars(query))





def contar_chunks_por_base(

    db: Session,

    base_ids: set[str] | None,

) -> dict[str, int]:

    """Informa, para auditoria, quantos chunks existem em cada base permitida."""



    query = (

        select(Document.base_id, func.count(DocumentChunk.id))

        .join(DocumentChunk, DocumentChunk.document_id == Document.id)

        .group_by(Document.base_id)

    )

    if base_ids is not None:

        if not base_ids:

            return {}

        query = query.where(Document.base_id.in_(sorted(base_ids)))

    return {str(base_id): int(total) for base_id, total in db.execute(query)}





def reciprocal_rank_fusion(

    rankings: list[list[tuple[str, float]]],

    k: int = 60,

) -> dict[str, float]:

    scores: dict[str, float] = {}

    for ranking in rankings:

        seen: set[str] = set()

        for posicao, (chunk_id, _) in enumerate(ranking, start=1):

            if chunk_id in seen:

                continue

            seen.add(chunk_id)

            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + posicao)

    return scores





def _identificar_norma(db: Session, pergunta: str) -> int | None:

    """Localiza norma explicitamente citada, usando tipo e número do título."""

    texto = normalizar_para_busca(pergunta)

    match = re.search(

        r"\b(lei|decreto)"

        r"(?:\s+(?:estadual|federal|municipal|rj))*"

        # O normalizador pode representar o ordinal como ``?``; aceite apenas marcadores conhecidos.
        r"\s+(?:n[\u00ba\u00b0.?o\ufffd]*\s*)?"

        r"(\d{1,3}(?:\.\d{3})+|\d{4,6})\b",

        texto,

    )

    if not match:

        return None



    tipo, numero = match.groups()

    numero = str(int(numero.replace(".", "")))

    documentos = db.scalars(

        select(Document).where(

            Document.base_id == "editais_normativo",

            Document.doc_type == tipo,

        )

    ).all()

    encontrados = []

    for documento in documentos:

        titulo = normalizar_para_busca(documento.title or "")

        numeros = re.findall(r"\d+(?:\.\d{3})*", titulo)

        if any(str(int(n.replace(".", ""))) == numero for n in numeros):

            encontrados.append(documento.id)

    return encontrados[0] if len(encontrados) == 1 else None





def _relevancia_textual(pergunta: str, conteudo: str) -> float:
    """Pontuação contextual, sem depender de artigos ou decretos específicos."""
    import unicodedata

    def norm(s: str) -> str:
        s = unicodedata.normalize("NFKD", s or "")
        return "".join(c for c in s if not unicodedata.combining(c)).lower()

    equivalencias = {
        "fiscais": "fiscal", "fiscalizacao": "fiscal",
        "atribuicoes": "competencia", "atribuicao": "competencia",
        "competencias": "competencia", "compete": "competencia",
        "responsabilidades": "competencia", "responsabilidade": "competencia",
        "deveres": "competencia", "dever": "competencia",
    }
    ignorar = {
        "quais", "qual", "como", "que", "dos", "das", "nos", "nas",
        "sao", "previstas", "previstos", "sobre", "para", "pela", "pelo",
        "contrato", "contratos", "decreto", "estadual", "lei", "numero", "rj",
        "art", "ano", "no", "na", "do", "da", "de", "em", "uma", "um",
    }
    pergunta_limpa = re.sub(
        r"\b(?:lei|decreto)(?:\s+(?:estadual|federal|municipal|rj))*"
        r"\s+(?:n[º°o.]*)?\s*\d{1,3}(?:\.\d{3})*(?:/\d{4})?",
        " ", norm(pergunta),
    )
    def palavras(s: str) -> set[str]:
        return {equivalencias.get(t, t) for t in re.findall(r"[a-z]{3,}", s)}

    termos = palavras(pergunta_limpa) - ignorar
    if not termos:
        return 0.0
    texto = norm(conteudo)
    corpo = palavras(texto)
    cabecalho = re.sub(r"^\s*(?:\[)?art\.?\s*\d+[^a-z]*", "", texto)
    cabecalho = cabecalho[:260]
    inicio = palavras(cabecalho)
    comuns = termos & corpo
    score = sum(2.0 if len(t) >= 6 else 1.0 for t in comuns)
    score += 5.0 * len(termos & inicio)
    if "competencia" in termos and "competencia" in inicio:
        score += 8.0
    return score





def _reordenar_normativa(

    pergunta: str,

    candidatos: list[tuple[str, float]],

    chunks: dict[str, DocumentChunk],

    documento_id: int,

) -> list[tuple[str, float]]:

    """Prioriza a fonte solicitada; mantém o score RRF intacto."""

    return sorted(

        candidatos,

        key=lambda item: (

            int(chunks[item[0]].document_id == documento_id),

            _relevancia_textual(pergunta, chunks[item[0]].content),

            item[1],

            item[0],

        ),

        reverse=True,

    )





def _continuidade_chunk(chunk_id: str, candidatos: set[str]) -> int:
    """Indica se existe um vizinho imediato entre os candidatos do RRF."""
    match = re.match(r"^(.*):p(\d+)$", chunk_id)
    if not match:
        return 0
    prefixo, pagina = match.groups()
    numero = int(pagina)
    return int(
        f"{prefixo}:p{numero - 1}" in candidatos
        or f"{prefixo}:p{numero + 1}" in candidatos
    )


def _buscar_no_documento(
    db: Session,
    pergunta: str,
    documento_id: int,
    allowed_chunk_ids: set[str] | None,
    limite: int,
    allowed_chunk_query: Select | None = None,
) -> list[tuple[str, float]]:
    """Busca local sem escrita; ordena todos os chunks elegíveis por relevância."""
    if limite <= 0:
        return []
    consulta = (
        select(DocumentChunk)
        .where(DocumentChunk.document_id == documento_id)
        .order_by(DocumentChunk.id)
    )
    if allowed_chunk_query is not None:
        # Keep eligibility predicates in SQLite instead of expanding a large
        # materialized ID set into one bind parameter per chunk.
        consulta = consulta.where(DocumentChunk.chunk_id.in_(allowed_chunk_query))
    elif allowed_chunk_ids is not None:
        consulta = consulta.where(DocumentChunk.chunk_id.in_(allowed_chunk_ids))
    candidatos = list(db.scalars(consulta))
    pontuados = [
        (chunk, _relevancia_textual(pergunta, chunk.content or ""))
        for chunk in candidatos
    ]
    pontuados = [(chunk, score) for chunk, score in pontuados if score > 0]
    pontuados.sort(key=lambda item: (-item[1], item[0].id))
    return [(chunk.chunk_id, score) for chunk, score in pontuados[:limite]]





def retrieve_hibrido(

    db: Session,
    pergunta: str,
    top_k: int = 10,
    bm25_k: int = DEFAULT_BM25_CANDIDATES,
    dense_k: int = DEFAULT_FAISS_CANDIDATES,

    rrf_k: int = 60,

    cache_dir: Path | None = None,

    embedding_fn: EmbeddingFn = gerar_embedding,

    filters: QueryFilters | None = None,

    bases: list[KnowledgeBase] | None = None,
    trace_collector: dict[str, object] | None = None,

) -> list[RankedChunk]:
    """Combina ranking lexical persistente e ranking semântico vetorial."""

    bm25_k = min(bm25_k, MAX_BM25_CANDIDATES)
    dense_k = min(dense_k, MAX_FAISS_CANDIDATES)
    local_limit = max(20, top_k * 4)
    if trace_collector is not None:
        trace_collector["parameters"].update({
            "top_k": top_k, "bm25_k": bm25_k, "dense_k": dense_k,
            "local_limit": local_limit,
        })
    cache_dir = resolver_caminho_indice_faiss(cache_dir)

    documento_normativo_id = _identificar_norma(db, pergunta)
    if (
        documento_normativo_id is not None
        and filters is not None
        and filters.document_id is not None
        and filters.document_id != documento_normativo_id
    ):
        documento_normativo_id = None

    with perf_stage("base_filters"):

        eligible_chunks = construir_consulta_chunks_elegiveis(

            filters,

            base_ids=resolver_base_ids(bases),

            document_id_restriction=documento_normativo_id,

        )

    allowed_chunk_ids = None

    if eligible_chunks is not None:

        with perf_stage("chunk_ids") as metrics:

            allowed_chunk_ids = set(db.scalars(eligible_chunks))

            metrics["chunks_elegiveis"] = len(allowed_chunk_ids)

    if allowed_chunk_ids is not None and not allowed_chunk_ids:

        return []



    with perf_stage("fts5") as metrics:

        lexical = buscar_fts5(

            db,

            pergunta,

            bm25_k,

            eligible_chunks=eligible_chunks,

        )

        metrics["candidatos"] = len(lexical)
    if trace_collector is not None:
        trace_collector["ranking_stages"]["bm25"] = [
            {"chunk_id": cid, "rank": i, "score": score}
            for i, (cid, score) in enumerate(lexical, 1)
        ]

    with perf_stage("faiss") as metrics:

        dense = buscar_dense(

            pergunta,

            dense_k,

            cache_dir,

            embedding_fn,

            allowed_chunk_ids=allowed_chunk_ids,

        )

        metrics["candidatos"] = len(dense)
    if trace_collector is not None:
        trace_collector["ranking_stages"]["faiss"] = [
            {"chunk_id": cid, "rank": i, "score": score}
            for i, (cid, score) in enumerate(dense, 1)
        ]

    local: list[tuple[str, float]] = []

    if documento_normativo_id is not None:

        with perf_stage("normative_local") as metrics:

            local = _buscar_no_documento(

                db, pergunta, documento_normativo_id,

                allowed_chunk_ids, local_limit,
                allowed_chunk_query=eligible_chunks,

            )

            metrics["candidatos"] = len(local)
        if trace_collector is not None:
            trace_collector["ranking_stages"]["local"] = [
                {"chunk_id": cid, "rank": i, "score": score}
                for i, (cid, score) in enumerate(local, 1)
            ]



    with perf_stage("rrf") as metrics:

        rankings = [lexical, dense]

        if local:

            rankings.append(local)

        fused = reciprocal_rank_fusion(rankings, k=rrf_k)

        metrics["candidatos"] = len(fused)
    if trace_collector is not None:
        trace_collector["ranking_stages"]["rrf"] = [
            {"chunk_id": cid, "rank": i, "score": score}
            for i, (cid, score) in enumerate(
                sorted(fused.items(), key=lambda item: item[1], reverse=True), 1
            )
        ]



    bm25_rank = {cid: i for i, (cid, _) in enumerate(lexical, 1)}

    bm25_score = dict(lexical)

    dense_rank = {cid: i for i, (cid, _) in enumerate(dense, 1)}

    dense_score = dict(dense)



    candidatos = sorted(fused.items(), key=lambda item: item[1], reverse=True)

    if documento_normativo_id is None:

        ordenados = candidatos[:top_k]

        with perf_stage("source_load"):

            chunks = carregar_chunks_por_ids(db, [cid for cid, _ in ordenados])

    else:

        with perf_stage("source_load"):

            chunks = carregar_chunks_por_ids(db, [cid for cid, _ in candidatos])

        validos = [(cid, score) for cid, score in candidatos if cid in chunks]

        if any(chunks[cid].document_id == documento_normativo_id for cid, _ in validos):

            local_ranks = {cid: i for i, (cid, _) in enumerate(local, 1)}
            candidatos_normativos = {
                cid for cid, _ in validos
                if chunks[cid].document_id == documento_normativo_id
            }
            continuidade_normativa = candidatos_normativos.intersection(
                set(cid for cid, _ in lexical)
                | set(cid for cid, _ in dense)
                | set(cid for cid, _ in local)
            )

            with perf_stage("normative_rerank"):

                # Fonte solicitada primeiro; depois evidências específicas

                # encontradas localmente; por último o RRF global.

                ordenados = sorted(

                    validos,

                    key=lambda item: (

                        int(chunks[item[0]].document_id == documento_normativo_id),
                        _continuidade_chunk(item[0], continuidade_normativa)
                        if chunks[item[0]].document_id == documento_normativo_id
                        else 0,
                        item[1],
                        int(item[0] in local_ranks),
                        _relevancia_textual(pergunta, chunks[item[0]].content or ""),

                    ),

                    reverse=True,

                )[:top_k]
                if trace_collector is not None:
                    trace_collector["ranking_stages"]["rerank"] = [
                        {"chunk_id": cid, "rank": i, "score": score}
                        for i, (cid, score) in enumerate(
                            sorted(validos, key=lambda item: (
                                int(chunks[item[0]].document_id == documento_normativo_id),
                                _continuidade_chunk(item[0], continuidade_normativa)
                                if chunks[item[0]].document_id == documento_normativo_id else 0,
                                item[1], int(item[0] in local_ranks),
                                _relevancia_textual(pergunta, chunks[item[0]].content or ""),
                            ), reverse=True), 1
                        )
                    ]

        else:

            ordenados = validos[:top_k]



    resultados: list[RankedChunk] = []

    for chunk_id, rrf_score in ordenados:

        chunk = chunks.get(chunk_id)

        # Um ID pode permanecer em um FAISS antigo se o banco foi limpo ou se

        # houve troca de corpus. Ignorar evita retornar evidência inexistente.

        if chunk is None:

            continue

        resultados.append(

            RankedChunk(

                chunk_id=chunk.chunk_id,

                content=chunk.content,

                rrf_score=rrf_score,

                bm25_rank=bm25_rank.get(chunk_id),

                bm25_score=bm25_score.get(chunk_id),

                dense_rank=dense_rank.get(chunk_id),

                dense_score=dense_score.get(chunk_id),

                metadata=_metadata(chunk),

            )

        )

    return resultados





if __name__ == "__main__":

    from app.core.database import SessionLocal, init_db



    parser = argparse.ArgumentParser(description="Executa retrieval híbrido sobre DocumentChunk.")

    parser.add_argument("pergunta")

    parser.add_argument("--top-k", type=int, default=5)

    parser.add_argument("--cache-dir")

    args = parser.parse_args()



    init_db()

    with SessionLocal() as db:

        resultados = retrieve_hibrido(

            db=db,

            pergunta=args.pergunta,

            top_k=args.top_k,

            cache_dir=Path(args.cache_dir) if args.cache_dir else None,

        )

    print(json.dumps([asdict(item) for item in resultados], ensure_ascii=False, indent=2))
