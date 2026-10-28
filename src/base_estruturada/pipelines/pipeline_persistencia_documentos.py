"""Persiste no banco o JSONL canônico de chunks da base PNCP.

Esta etapa não lê Markdown, não chama Docling e não divide textos novamente.
O JSONL gerado por ``pipeline_chunks`` é sua única entrada documental.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from rich.progress import (
    BarColumn,
    Progress,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)

from app.core.database import SessionLocal, init_db
from app.modules.documents.services import persistir_chunks_jsonl_pncp
from src.base_estruturada.pipelines.pipeline_documentos import PeriodoColeta


def _periodo(valor: str) -> PeriodoColeta:
    ano, mes = valor.replace("-", "/").split("/", maxsplit=1)
    return PeriodoColeta(ano=int(ano), mes=int(mes))


def _arquivo_chunks_padrao(
    periodo: PeriodoColeta,
    versao: str,
    nome_lote: str | None,
) -> Path:
    sufixo_lote = f"_{nome_lote}" if nome_lote else ""
    return Path(
        f"data/chunks/{versao}/{periodo.ano}/"
        f"chunks_{periodo.identificador}{sufixo_lote}.jsonl"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Persiste Document/DocumentChunk a partir do JSONL canônico."
    )
    parser.add_argument(
        "--tipo",
        choices=["contratos", "contratacoes", "todos"],
        default="todos",
    )
    parser.add_argument("--periodo", default="2026/08")
    parser.add_argument("--lote", help="Ex.: lote_001")
    parser.add_argument("--versao", default="v1")
    parser.add_argument(
        "--arquivo-chunks",
        type=Path,
        help="Substitui o caminho padrão do JSONL.",
    )
    parser.add_argument(
        "--limite-chunks-novos",
        type=int,
        help="Limita inserções novas; execuções seguintes avançam pelos pendentes.",
    )
    parser.add_argument("--corpus-version-id", type=int, default=1)
    args = parser.parse_args()

    periodo = _periodo(args.periodo)
    arquivo_chunks = args.arquivo_chunks or _arquivo_chunks_padrao(
        periodo,
        args.versao,
        args.lote,
    )
    tipos_entidade = (
        {"contrato" if args.tipo == "contratos" else "contratacao"}
        if args.tipo != "todos"
        else None
    )

    init_db()
    with SessionLocal() as db:
        colunas_progresso = [
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total}"),
            TextColumn("{task.percentage:>5.1f}%"),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
        ]
        with Progress(*colunas_progresso) as progresso_visual:
            tarefa = progresso_visual.add_task("Persistindo chunks", total=1)

            def atualizar_progresso(processados: int, total: int) -> None:
                if total == 0:
                    progresso_visual.update(
                        tarefa,
                        description="JSONL sem linhas",
                        total=1,
                        completed=1,
                    )
                    return
                progresso_visual.update(
                    tarefa,
                    total=total,
                    completed=processados,
                )

            resumo = persistir_chunks_jsonl_pncp(
                db=db,
                corpus_version_id=args.corpus_version_id,
                arquivo_chunks=arquivo_chunks,
                tipos_entidade=tipos_entidade,
                limite_chunks_novos=args.limite_chunks_novos,
                progresso=atualizar_progresso,
            )
            descricao = (
                "Limite de inserções atingido"
                if args.limite_chunks_novos is not None
                and resumo["chunksCriados"] >= args.limite_chunks_novos
                else "Persistência concluída"
            )
            progresso_visual.update(tarefa, description=descricao)
    print(f"Persistência JSONL → Document/DocumentChunk | {resumo}")
