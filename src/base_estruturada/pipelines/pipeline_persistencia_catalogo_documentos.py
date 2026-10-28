"""Persiste as listagens originais de documentos PNCP no catálogo SQLite.

Não chama o PNCP e não baixa arquivos. É seguro rodar durante uma indisponibilidade
externa, desde que os manifestos mensais já estejam em ``data/normalized``.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from sqlalchemy import inspect
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.core.database import init_db
from app.core.database.orm import create_sqlite_engine, register_models
from app.modules.documents.services import persistir_catalogo_documentos_pncp
from src.base_estruturada.pipelines.pipeline_documentos import (
    PeriodoColeta,
    caminhos_do_periodo,
)


def _periodo(valor: str) -> PeriodoColeta:
    ano, mes = valor.replace("-", "/").split("/", maxsplit=1)
    return PeriodoColeta(ano=int(ano), mes=int(mes))


def _manifestos(periodos: list[PeriodoColeta], tipo: str) -> list[tuple[str, str, Path]]:
    resultado: list[tuple[str, str, Path]] = []
    for periodo in periodos:
        caminhos = caminhos_do_periodo(periodo)
        if tipo in {"contratos", "todos"}:
            resultado.append(("contrato", periodo.identificador, caminhos["manifesto_contratos"]))
        if tipo in {"contratacoes", "todos"}:
            resultado.append(("contratacao", periodo.identificador, caminhos["manifesto_contratacoes"]))
    return resultado


_PERIODO_NO_NOME = re.compile(r"_(\d{4})_(\d{2})\.json$")


def _todos_manifestos_locais(tipo: str) -> list[tuple[str, str, Path]]:
    raiz = Path("data/normalized")
    candidatos: list[tuple[str, Path]] = []
    if tipo in {"contratos", "todos"}:
        candidatos.extend(
            ("contrato", caminho)
            for caminho in raiz.glob("contratos/*/contratos_documentos_*.json")
        )
    if tipo in {"contratacoes", "todos"}:
        candidatos.extend(
            ("contratacao", caminho)
            for caminho in raiz.glob(
                "contratacoes/*/contratacoes_documentos_*.json"
            )
        )
    resultado = []
    for tipo_entidade, caminho in sorted(
        candidatos, key=lambda item: (item[0], item[1].as_posix())
    ):
        correspondencia = _PERIODO_NO_NOME.search(caminho.name)
        if correspondencia is None:
            continue
        resultado.append(
            (
                tipo_entidade,
                f"{correspondencia.group(1)}_{correspondencia.group(2)}",
                caminho,
            )
        )
    return resultado


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Persiste manifestos PNCP como catálogo local de URLs."
    )
    origem = parser.add_mutually_exclusive_group(required=True)
    origem.add_argument(
        "--periodo",
        action="append",
        help="Repita para cada mês, por exemplo: --periodo 2026/06 --periodo 2026/07",
    )
    origem.add_argument(
        "--todos-manifestos-locais",
        action="store_true",
        help="Reconcilia todos os manifestos mensais existentes em data/normalized.",
    )
    parser.add_argument(
        "--tipo", choices=["contratos", "contratacoes", "todos"], default="todos"
    )
    parser.add_argument(
        "--snapshot-completo",
        action="store_true",
        help=(
            "Declara o conjunto como autoritativo e marca como removidos os "
            "documentos ausentes. Exige --todos-manifestos-locais --tipo todos."
        ),
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="SQLite de destino. Omita para usar DATABASE_URL do .env.",
    )
    parser.add_argument(
        "--initialize-schema",
        action="store_true",
        help="Cria tabelas ausentes. Use somente em banco temporário ou cópia validada.",
    )
    parser.add_argument(
        "--tamanho-commit",
        type=int,
        default=2_000,
        help="Documentos confirmados por lote SQLite (padrão: 2000).",
    )
    args = parser.parse_args()

    if args.snapshot_completo and not (
        args.todos_manifestos_locais and args.tipo == "todos"
    ):
        parser.error("--snapshot-completo exige --todos-manifestos-locais --tipo todos")
    manifestos = (
        _todos_manifestos_locais(args.tipo)
        if args.todos_manifestos_locais
        else _manifestos([_periodo(valor) for valor in args.periodo], args.tipo)
    )
    ausentes = [str(caminho) for _, _, caminho in manifestos if not caminho.is_file()]
    if ausentes:
        raise FileNotFoundError("Manifestos não encontrados:\n- " + "\n- ".join(ausentes))

    database_url = args.database_url or settings.database_url
    register_models()
    engine = create_sqlite_engine(database_url)
    if args.initialize_schema:
        init_db(bind=engine)
    tabelas = set(inspect(engine).get_table_names())
    obrigatorias = {
        "document_catalog_entries",
        "document_catalog_inventories",
        "document_catalog_inventory_members",
        "document_catalog_states",
    }
    faltantes = sorted(obrigatorias - tabelas)
    if faltantes:
        raise RuntimeError(
            "Esquema do catálogo v2 ausente: " + ", ".join(faltantes)
            + ". Em cópia isolada, use --initialize-schema."
        )
    Session = sessionmaker(bind=engine)
    with Session() as db:
        resumo = persistir_catalogo_documentos_pncp(
            db,
            manifestos,
            tamanho_commit=args.tamanho_commit,
            authoritative_full_snapshot=args.snapshot_completo,
        )
    print(f"Catálogo PNCP persistido no SQLite | {resumo}")
