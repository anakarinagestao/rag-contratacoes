"""Persiste os JSONs mensais do PNCP na camada RAW estruturada.

Esta etapa importa contratos e contratacoes que ja foram coletados em
``data/normalized``. Ela nao consulta a API, nao baixa documentos e nao altera
os JSONs de origem. Cada payload completo e armazenado em ``raw_asset`` com
``numeroControlePNCP`` como chave estavel.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterable

from src.base_estruturada.pipelines.pipeline_documentos import (
    PeriodoColeta,
    caminhos_do_periodo,
)
from src.base_estruturada.pncp.repository import carregar_json
from src.ingest import RawRepository, ingest_records
from src.obs import JsonlLogger
from src.schema import CollectedRecord, IngestionResult


BASE_ID = "pncp_rj_estruturada"
SENSIBILIDADE = "publico"


def _periodo(valor: str) -> PeriodoColeta:
    ano, mes = valor.replace("-", "/").split("/", maxsplit=1)
    return PeriodoColeta(ano=int(ano), mes=int(mes))


def _periodos_do_intervalo(
    ano: int,
    mes_inicial: int,
    mes_final: int,
) -> list[PeriodoColeta]:
    if mes_final < mes_inicial:
        raise ValueError("mes_final deve ser maior ou igual a mes_inicial.")
    return [
        PeriodoColeta(ano=ano, mes=mes)
        for mes in range(mes_inicial, mes_final + 1)
    ]


def _registros_do_arquivo(caminho: object) -> list[dict]:
    registros = carregar_json(caminho, padrao=[])
    if not isinstance(registros, list):
        raise RuntimeError(f"Base estruturada deveria conter uma lista: {caminho}")
    return [registro for registro in registros if isinstance(registro, dict)]


def _records(
    registros: Iterable[dict],
    *,
    source_id: str,
    origem: str,
    periodo: PeriodoColeta,
) -> Iterable[CollectedRecord]:
    """Converte os JSONs mensais para o formato append-only da camada RAW."""

    endpoint_logico = (
        f"pncp://coleta-estruturada/{origem}/{periodo.identificador}"
    )
    for ordinal, payload in enumerate(registros, start=1):
        controle = str(payload.get("numeroControlePNCP") or "")
        if not controle:
            raise RuntimeError(
                f"{origem} sem numeroControlePNCP no periodo "
                f"{periodo.identificador}, registro {ordinal}."
            )
        yield CollectedRecord(
            source_id=source_id,
            payload=payload,
            source_url=endpoint_logico,
            # O arquivo mensal ja e o recorte fechado. Cursor 1 representa
            # essa unica unidade de importacao, nao uma pagina da API.
            cursor="1",
            ordinal=ordinal,
            external_id=controle,
            query_params={
                "origem": origem,
                "periodo": periodo.identificador,
                "fonte": "json_mensal_coletado",
            },
        )


def persistir_periodo(
    repository: RawRepository,
    periodo: PeriodoColeta,
    origem: str,
    versao_corpus: str,
    logger: JsonlLogger,
) -> IngestionResult:
    """Importa contratos ou contratacoes de um mes para ``raw_asset``."""

    if origem not in {"contratos", "contratacoes"}:
        raise ValueError("origem deve ser 'contratos' ou 'contratacoes'.")

    caminhos = caminhos_do_periodo(periodo)
    arquivo = caminhos[origem]
    registros = _registros_do_arquivo(arquivo)
    source_id = f"pncp_json_{origem}_{periodo.identificador}"
    resource_type = "contrato" if origem == "contratos" else "contratacao"

    resultado = ingest_records(
        repository=repository,
        source_id=source_id,
        key_fields=("numeroControlePNCP",),
        records=_records(
            registros,
            source_id=source_id,
            origem=origem,
            periodo=periodo,
        ),
        resource_type=resource_type,
        base_id=BASE_ID,
        natureza="estruturada",
        query_params={
            "origem": origem,
            "periodo": periodo.identificador,
            "fonte": "json_mensal_coletado",
        },
        corpus_version=versao_corpus,
        sensitivity=SENSIBILIDADE,
        initial_checkpoint=0,
        window_start=periodo.data_inicial,
        window_end=periodo.data_final,
        logger=logger,
    )
    print(
        f"Persistencia estruturada | tipo={origem} | "
        f"periodo={periodo.identificador} | "
        f"lidos={resultado.records_seen} | novos={resultado.new} | "
        f"atualizados={resultado.updated} | inalterados={resultado.unchanged}"
    )
    return resultado


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Importa JSONs mensais de contratos/contratacoes no raw_asset."
    )
    parser.add_argument(
        "--tipo",
        choices=["contratos", "contratacoes", "todos"],
        default="todos",
    )
    parser.add_argument(
        "--periodo",
        action="append",
        help="Periodo YYYY/MM. Pode ser informado mais de uma vez.",
    )
    parser.add_argument("--ano", type=int, default=2026)
    parser.add_argument("--mes-inicial", type=int)
    parser.add_argument("--mes-final", type=int)
    parser.add_argument("--versao-corpus", default="pncp_rj_estruturada_v1")
    parser.add_argument("--database-url")
    args = parser.parse_args()

    if args.periodo:
        periodos = [_periodo(valor) for valor in args.periodo]
    elif args.mes_inicial is not None and args.mes_final is not None:
        periodos = _periodos_do_intervalo(
            args.ano,
            args.mes_inicial,
            args.mes_final,
        )
    else:
        parser.error(
            "Informe --periodo YYYY/MM ou --ano, --mes-inicial e --mes-final."
        )

    origens = (
        [args.tipo]
        if args.tipo in {"contratos", "contratacoes"}
        else ["contratos", "contratacoes"]
    )
    logger = JsonlLogger("logs/persistencia_estruturada.jsonl")
    repository = RawRepository(args.database_url)
    try:
        for periodo in periodos:
            for origem in origens:
                persistir_periodo(
                    repository,
                    periodo,
                    origem,
                    args.versao_corpus,
                    logger,
                )
    finally:
        repository.close()
