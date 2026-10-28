"""Audita e indexa DocumentChunk em SQLite somente leitura e staging FAISS."""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import logging
import sys
import time
import traceback
from collections import defaultdict
from collections.abc import Iterator
from pathlib import Path

from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database.readonly import readonly_session
from app.modules.documents.model import Document, DocumentChunk
from src.index import (
    DEFAULT_CHECKPOINT_SIZE,
    auditar_indice_faiss,
    carregar_indice_faiss,
    compactar_checkpoints,
    indexar_chunks_incrementalmente,
    preparar_staging,
    restaurar_estado_compactacao,
    ProgressoIndexacaoFaiss,
)

logger = logging.getLogger(__name__)


def _locais_traceback(exc: BaseException) -> str:
    return " <- ".join(
        f"{frame.filename}:{frame.lineno} in {frame.name}"
        for frame in traceback.extract_tb(exc.__traceback__)
    )


def _ids_do_jsonl(caminho: Path) -> list[str]:
    if not caminho.is_file():
        raise FileNotFoundError(f"JSONL de chunks não encontrado: {caminho}")
    vistos: set[str] = set()
    ids: list[str] = []
    with caminho.open(encoding="utf-8") as arquivo:
        for numero_linha, linha in enumerate(arquivo, start=1):
            if not linha.strip():
                continue
            try:
                item = json.loads(linha)
            except json.JSONDecodeError as exc:
                raise ValueError(f"JSONL inválido na linha {numero_linha}: {caminho}") from exc
            chunk_id = item.get("chunk_id") if isinstance(item, dict) else None
            if isinstance(chunk_id, str) and chunk_id and chunk_id not in vistos:
                vistos.add(chunk_id)
                ids.append(chunk_id)
    return ids


def _consulta_ids(
    *,
    base_id: str | None,
    tipo: str | None,
    corpus_version_id: int | None,
) -> Select:
    consulta = (
        select(DocumentChunk.id, DocumentChunk.chunk_id)
        .join(Document, Document.id == DocumentChunk.document_id)
        .order_by(DocumentChunk.id)
    )
    if base_id:
        consulta = consulta.where(
            func.coalesce(DocumentChunk.base_id, Document.base_id) == base_id
        )
    if tipo:
        consulta = consulta.where(
            func.coalesce(DocumentChunk.doc_type, Document.doc_type) == tipo
        )
    if corpus_version_id is not None:
        consulta = consulta.where(Document.corpus_version_id == corpus_version_id)
    return consulta


def iterar_chunks_pendentes(
    db: Session,
    *,
    ids_indexados: set[str],
    arquivo_chunks: Path | None = None,
    base_id: str | None = None,
    tipo: str | None = None,
    corpus_version_id: int | None = None,
    limite_chunks_novos: int | None = None,
    tamanho_leitura: int = 300,
) -> Iterator[tuple[str, str]]:
    """Lê primeiro só IDs, filtra pendências e carrega conteúdo em blocos."""

    if tamanho_leitura <= 0:
        raise ValueError("tamanho_leitura deve ser maior que zero.")
    consulta = _consulta_ids(
        base_id=base_id,
        tipo=tipo,
        corpus_version_id=corpus_version_id,
    )
    if arquivo_chunks is None:
        cursor = db.execute(consulta.execution_options(yield_per=1000))
        candidatos = (
            (int(row.id), str(row.chunk_id))
            for row in cursor
            if row.chunk_id not in ids_indexados
        )
    else:
        ids_arquivo = _ids_do_jsonl(arquivo_chunks)
        linhas: list[tuple[int, str]] = []
        for inicio in range(0, len(ids_arquivo), 400):
            grupo = ids_arquivo[inicio : inicio + 400]
            linhas.extend(
                (int(row.id), str(row.chunk_id))
                for row in db.execute(consulta.where(DocumentChunk.chunk_id.in_(grupo)))
                if row.chunk_id not in ids_indexados
            )
        linhas.sort(key=lambda item: item[0])
        candidatos = iter(linhas)

    selecionados = 0
    while limite_chunks_novos is None or selecionados < limite_chunks_novos:
        grupo = []
        for _ in range(tamanho_leitura):
            try:
                grupo.append(next(candidatos))
            except StopIteration:
                break
        if not grupo:
            return
        ids = [chunk_id for _, chunk_id in grupo]
        por_id = {
            chunk.chunk_id: chunk.content
            for chunk in db.scalars(
                select(DocumentChunk).where(DocumentChunk.chunk_id.in_(ids))
            )
        }
        for _, chunk_id in grupo:
            conteudo = por_id.get(chunk_id)
            if conteudo is None:
                continue
            yield chunk_id, conteudo
            selecionados += 1
            if limite_chunks_novos is not None and selecionados >= limite_chunks_novos:
                return


def contar_chunks_pendentes(
    db: Session,
    *,
    ids_indexados: set[str],
    arquivo_chunks: Path | None = None,
    base_id: str | None = None,
    tipo: str | None = None,
    corpus_version_id: int | None = None,
) -> int:
    """Conta candidatos lendo apenas IDs e respeitando os mesmos filtros."""

    consulta = _consulta_ids(
        base_id=base_id,
        tipo=tipo,
        corpus_version_id=corpus_version_id,
    )
    permitidos = set(_ids_do_jsonl(arquivo_chunks)) if arquivo_chunks is not None else None
    return sum(
        1
        for row in db.execute(consulta)
        if row.chunk_id not in ids_indexados
        and (permitidos is None or row.chunk_id in permitidos)
    )


def auditar_cobertura(db: Session, indice_dir: Path) -> dict[str, object]:
    """Calcula cobertura, integridade e status documental sem carregar conteúdo."""

    problemas_indice: list[str] = []
    try:
        auditoria_indice = auditar_indice_faiss(indice_dir)
        problemas_indice.extend(auditoria_indice.inconsistencias)
        ids_indice_lista = auditoria_indice.chunk_ids
        hashes = auditoria_indice.hashes_conteudo
        total_vetores = auditoria_indice.total_vetores
        estado = auditoria_indice.estado
        dimensao = auditoria_indice.dimensao
        modelo = auditoria_indice.modelo_embedding
    except (FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
        ids_indice_lista = []
        hashes = {}
        total_vetores = 0
        estado = {}
        dimensao = None
        modelo = None
        problemas_indice.append(str(exc))

    ids_indice = set(ids_indice_lista)
    duplicados = len(ids_indice_lista) - len(ids_indice)
    ids_db: set[str] = set()
    status_documentos: dict[tuple[str, int], list[int]] = defaultdict(lambda: [0, 0])
    totais_base: dict[str, int] = defaultdict(int)
    indexados_base: dict[str, int] = defaultdict(int)
    ids_para_verificar_conteudo: list[str] = []

    consulta = (
        select(
            Document.base_id,
            Document.id.label("document_id"),
            DocumentChunk.chunk_id,
        )
        .join(DocumentChunk, DocumentChunk.document_id == Document.id)
        .order_by(Document.base_id, Document.id, DocumentChunk.id)
    )
    for base, document_id, chunk_id in db.execute(consulta):
        base_name = str(base)
        chunk_name = str(chunk_id)
        ids_db.add(chunk_name)
        totais_base[base_name] += 1
        status_documentos[base_name, int(document_id)][0] += 1
        if chunk_name in ids_indice:
            indexados_base[base_name] += 1
            status_documentos[base_name, int(document_id)][1] += 1
        if chunk_name in hashes:
            ids_para_verificar_conteudo.append(chunk_name)

    mismatch_conteudo = 0
    for inicio in range(0, len(ids_para_verificar_conteudo), 300):
        ids = ids_para_verificar_conteudo[inicio : inicio + 300]
        for chunk_id, content in db.execute(
            select(DocumentChunk.chunk_id, DocumentChunk.content).where(
                DocumentChunk.chunk_id.in_(ids)
            )
        ):
            if hashes.get(str(chunk_id)) != hashlib.sha256(
                str(content).encode("utf-8")
            ).hexdigest():
                mismatch_conteudo += 1
    conteudos_nao_verificaveis = len((ids_indice & ids_db) - hashes.keys())

    duplicados_db = [
        row[0]
        for row in db.execute(
            select(DocumentChunk.chunk_id)
            .group_by(DocumentChunk.chunk_id)
            .having(func.count() > 1)
        )
    ]
    orfaos = len(ids_indice - ids_db)
    if duplicados:
        problemas_indice.append(f"{duplicados} IDs duplicados no FAISS.")
    if orfaos:
        problemas_indice.append(f"{orfaos} IDs FAISS sem chunk correspondente no SQLite.")
    if duplicados_db:
        problemas_indice.append(f"{len(duplicados_db)} IDs duplicados no SQLite.")
    if mismatch_conteudo:
        problemas_indice.append(
            f"{mismatch_conteudo} chunks mudaram de conteúdo desde a indexação."
        )

    bases: dict[str, object] = {}
    for base_name in sorted(totais_base):
        completos = parciais = sem_vetores = 0
        for (doc_base, _), (total, indexados) in status_documentos.items():
            if doc_base != base_name:
                continue
            if total and indexados == total:
                completos += 1
            elif indexados:
                parciais += 1
            else:
                sem_vetores += 1
        total = totais_base[base_name]
        vetorizados = indexados_base[base_name]
        bases[base_name] = {
            "chunks_persistidos": total,
            "chunks_indexados": vetorizados,
            "chunks_pendentes": total - vetorizados,
            "cobertura_percentual": round(vetorizados * 100 / total, 2) if total else 100.0,
            "documentos_completos": completos,
            "documentos_parciais": parciais,
            "documentos_sem_vetores": sem_vetores,
        }

    if estado.get("totalVetores") is not None and estado["totalVetores"] != total_vetores:
        problemas_indice.append("totalVetores do estado diverge da contagem de vetores.")
    return {
        "indice": str(indice_dir),
        "modelo_embedding": modelo,
        "dimensao": dimensao,
        "total_vetores_faiss": total_vetores,
        "total_ids_no_mapa": len(ids_indice_lista),
        "ids_duplicados_faiss": duplicados,
        "ids_orfaos_faiss": orfaos,
        "ids_duplicados_sqlite": len(duplicados_db),
        "conteudos_alterados": mismatch_conteudo,
        "conteudos_sem_hash_historico": conteudos_nao_verificaveis,
        "bases": bases,
        "inconsistencias": sorted(set(problemas_indice)),
        "estado": estado,
    }


def _argumentos() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audita cobertura ou indexa DocumentChunk em staging FAISS."
    )
    modos = parser.add_mutually_exclusive_group()
    modos.add_argument("--auditar", action="store_true")
    modos.add_argument("--preparar-staging", action="store_true")
    modos.add_argument("--compactar-checkpoints", action="store_true")
    modos.add_argument("--restaurar-estado-anterior", action="store_true")
    parser.add_argument("--base-indice-dir", type=Path)
    parser.add_argument("--indice-dir", type=Path, default=Path("data/index/faiss/staging/v1"))
    parser.add_argument("--arquivo-chunks", type=Path)
    parser.add_argument("--base-id")
    parser.add_argument("--tipo", help="Valor doc_type exato, sem lista fixa.")
    parser.add_argument("--corpus-version-id", type=int)
    parser.add_argument(
        "--limite-chunks",
        "--limite-chunks-novos",
        dest="limite_chunks_novos",
        type=int,
        help="Máximo de IDs pendentes após aplicar seleção e filtros.",
    )
    parser.add_argument("--tamanho-lote", type=int, default=50)
    parser.add_argument(
        "--tamanho-checkpoint",
        type=int,
        default=DEFAULT_CHECKPOINT_SIZE,
        help=(
            "Chunks confirmados por shard persistente (lotes de embedding continuam "
            "controlados por --tamanho-lote)."
        ),
    )
    parser.add_argument(
        "--ate-concluir",
        action="store_true",
        help="Processa os pendentes selecionados até esgotar a fila; o limite opcional é total.",
    )
    parser.add_argument("--origem")
    return parser


def main() -> None:
    parser = _argumentos()
    args = parser.parse_args()
    if args.limite_chunks_novos is not None and args.limite_chunks_novos <= 0:
        parser.error("--limite-chunks-novos deve ser maior que zero.")
    if args.tamanho_lote <= 0:
        parser.error("--tamanho-lote deve ser maior que zero.")
    if args.tamanho_checkpoint <= 0:
        parser.error("--tamanho-checkpoint deve ser maior que zero.")

    if args.preparar_staging:
        if args.base_indice_dir is None:
            parser.error("--preparar-staging exige --base-indice-dir.")
        preparar_staging(args.base_indice_dir, args.indice_dir)
        print(f"Staging preparado sem alterar a origem: {args.indice_dir}")
        return

    if args.compactar_checkpoints:
        compactados = compactar_checkpoints(args.indice_dir)
        if compactados:
            print(
                f"Compactação concluída: {compactados} checkpoints ativos "
                "consolidados em um; originais e estado anterior preservados."
            )
        else:
            print("Compactação não necessária: há no máximo um checkpoint ativo.")
        return

    if args.restaurar_estado_anterior:
        checkpoints = restaurar_estado_compactacao(args.indice_dir)
        print(
            "Estado anterior restaurado e validado; "
            f"checkpoints ativos={checkpoints}. O estado substituído permanece arquivado."
        )
        return

    if args.auditar:
        with readonly_session(settings.database_url) as db:
            resultado = auditar_cobertura(db, args.indice_dir)
        print(json.dumps(resultado, ensure_ascii=False, indent=2))
        return

    try:
        indice = carregar_indice_faiss(args.indice_dir)
        ids_indexados = set(indice.chunk_ids)
    except KeyboardInterrupt:
        logger.warning("FAISS indexing interrupted stage=validacao_indice")
        print(
            "\nInterrupção solicitada durante a validação do índice; "
            "nenhum checkpoint foi removido.",
            file=sys.stderr,
        )
        raise SystemExit(130) from None
    except FileNotFoundError:
        ids_indexados = set()
    except Exception as exc:
        logger.error(
            "FAISS indexing failed stage=validacao_indice error_type=%s",
            type(exc).__name__,
        )
        logger.error(
            "FAISS indexing traceback locations (source lines omitted): %s",
            _locais_traceback(exc),
        )
        print(
            f"Falha técnica durante a validação do índice ({type(exc).__name__}); "
            "detalhes seguros foram registrados no log.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None

    limite_execucao = args.limite_chunks_novos
    if limite_execucao is None and not args.ate_concluir:
        limite_execucao = args.tamanho_checkpoint

    estagio_atual = "selecao"
    progresso_atual = ProgressoIndexacaoFaiss(
        estagio="selecao",
        chunks_selecionados=0,
        embeddings_gerados=0,
        vetores_persistidos=0,
        total_esperado=None,
    )
    try:
        with readonly_session(settings.database_url) as db:
            with Progress(
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TextColumn("{task.completed} embeddings gerados"),
                TextColumn("{task.fields[monitor]}"),
                TimeElapsedColumn(),
            ) as progresso:
                tarefa = progresso.add_task(
                    "Estágio: seleção",
                    total=None,
                    monitor="contando pendências elegíveis no SQLite somente leitura",
                )
                total_pendentes = contar_chunks_pendentes(
                    db,
                    ids_indexados=ids_indexados,
                    arquivo_chunks=args.arquivo_chunks,
                    base_id=args.base_id,
                    tipo=args.tipo,
                    corpus_version_id=args.corpus_version_id,
                )
                total_execucao = (
                    min(total_pendentes, limite_execucao)
                    if limite_execucao
                    else total_pendentes
                )
                progresso.update(
                    tarefa,
                    total=total_execucao,
                    description="Estágio: seleção",
                    monitor=f"{total_pendentes} chunks pendentes elegíveis",
                )
                chunks = iterar_chunks_pendentes(
                    db,
                    ids_indexados=ids_indexados,
                    arquivo_chunks=args.arquivo_chunks,
                    base_id=args.base_id,
                    tipo=args.tipo,
                    corpus_version_id=args.corpus_version_id,
                    limite_chunks_novos=limite_execucao,
                )
                primeiro = next(chunks, None)
                if primeiro is None:
                    print("Nenhum chunk pendente para indexar.")
                    return
                chunks = itertools.chain((primeiro,), chunks)
                from llm_ecoa import EMBEDDING_MODEL, gerar_embedding

                try:
                    auditoria = auditar_indice_faiss(args.indice_dir)
                    quantidade_checkpoints = len(
                        auditoria.estado.get("checkpoints", [])
                    )
                except FileNotFoundError:
                    quantidade_checkpoints = 0
                inicio = time.monotonic()
                erros = 0
                recuperacoes = 0

                def atualizar_detalhado(
                    evento: ProgressoIndexacaoFaiss,
                ) -> None:
                    nonlocal estagio_atual, progresso_atual
                    estagio_atual = evento.estagio
                    progresso_atual = evento
                    decorrido = time.monotonic() - inicio
                    taxa = (
                        evento.embeddings_gerados / decorrido
                        if decorrido > 0
                        else 0.0
                    )
                    restantes = max(
                        (evento.total_esperado or 0) - evento.embeddings_gerados,
                        0,
                    )
                    eta = (
                        f"ETA {restantes / taxa:.0f}s"
                        if evento.embeddings_gerados > 0
                        and restantes > 0
                        and taxa > 0
                        else "ETA indisponível"
                    )
                    checkpoints_visiveis = quantidade_checkpoints + (
                        (
                            evento.vetores_persistidos
                            + args.tamanho_checkpoint
                            - 1
                        )
                        // args.tamanho_checkpoint
                    )
                    restantes_persistir = max(
                        (evento.total_esperado or 0) - evento.vetores_persistidos,
                        0,
                    )
                    progresso.update(
                        tarefa,
                        completed=evento.embeddings_gerados,
                        total=evento.total_esperado,
                        description=f"Estágio: {evento.estagio}",
                        monitor=(
                            f"selecionados={evento.chunks_selecionados} | "
                            f"gerados={evento.embeddings_gerados} | "
                            f"persistidos={evento.vetores_persistidos} | "
                            f"restantes={restantes_persistir} | "
                            f"{taxa:.2f} embeddings/s | {eta} | "
                            f"checkpoints={checkpoints_visiveis} | "
                            f"erros={erros} | recuperados={recuperacoes}"
                        ),
                    )

                def embedding_monitorado(texto: str) -> list[float]:
                    nonlocal erros
                    try:
                        return gerar_embedding(texto)
                    except Exception:
                        erros += 1
                        progresso.update(
                            tarefa,
                            monitor=(
                                f"erros={erros} | checkpoints recuperados={recuperacoes}"
                            ),
                        )
                        raise

                def registrar_recuperacao(quantidade: int) -> None:
                    nonlocal recuperacoes
                    recuperacoes += quantidade
                    progresso.update(
                        tarefa,
                        monitor=(
                            f"recuperando shards confirmados | erros={erros} | "
                            f"checkpoints recuperados={recuperacoes}"
                        ),
                    )

                _, resumo = indexar_chunks_incrementalmente(
                    chunks,
                    embedding_monitorado,
                    args.indice_dir,
                    tamanho_lote=args.tamanho_lote,
                    tamanho_checkpoint=args.tamanho_checkpoint,
                    modelo_embedding=EMBEDDING_MODEL,
                    dimensao_embedding=settings.embedding_dimensions,
                    origem=(
                        args.origem
                        or args.base_id
                        or (args.arquivo_chunks.name if args.arquivo_chunks else "selecao_por_filtros")
                    ),
                    progresso_detalhado=atualizar_detalhado,
                    recuperacao=registrar_recuperacao,
                    total_esperado=total_execucao,
                )
    except KeyboardInterrupt:
        logger.warning(
            "FAISS indexing interrupted stage=%s selected=%d embeddings_generated=%d "
            "vectors_persisted=%d; published checkpoints are retained",
            estagio_atual,
            progresso_atual.chunks_selecionados,
            progresso_atual.embeddings_gerados,
            progresso_atual.vetores_persistidos,
        )
        print(
            "\nInterrupção solicitada. Checkpoints já publicados foram preservados; "
            "execute novamente para retomar.",
            file=sys.stderr,
        )
        raise SystemExit(130) from None
    except Exception as exc:
        logger.error(
            "FAISS indexing failed stage=%s error_type=%s selected=%d "
            "embeddings_generated=%d vectors_persisted=%d",
            estagio_atual,
            type(exc).__name__,
            progresso_atual.chunks_selecionados,
            progresso_atual.embeddings_gerados,
            progresso_atual.vetores_persistidos,
        )
        logger.error(
            "FAISS indexing traceback locations (source lines omitted): %s",
            _locais_traceback(exc),
        )
        print(
            f"Falha técnica durante a indexação ({type(exc).__name__}); "
            "detalhes seguros foram registrados no log.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
    print(
        "Indexação concluída | "
        f"selecionados={resumo.selecionados} | já_indexados={resumo.ja_indexados} | "
        f"novos={resumo.novos} | total_indice={resumo.total_no_indice} | "
        f"embeddings_gerados={resumo.embeddings_gerados} | "
        f"checkpoints={resumo.checkpoints} | "
        f"recuperados={resumo.checkpoints_recuperados}"
    )


if __name__ == "__main__":
    main()
