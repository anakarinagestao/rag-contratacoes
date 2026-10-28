"""Baixa e extrai documentos de manifestos mensais do PNCP.

Esta etapa nao consulta contratos nem monta o manifesto: ela trabalha somente
com os documentos que ja foram listados em ``pipeline_listagem_documentos``.
"""

import argparse
from pathlib import Path
from typing import Any

from src.base_estruturada.pipelines.pipeline_documentos import (
    PeriodoColeta,
    caminhos_do_periodo,
)
from src.base_estruturada.pncp.repository import (
    carregar_checkpoint_documentos,
    carregar_json,
    extrair_zips_do_manifesto,
    identificar_arquivos_do_manifesto,
    materializar_manifesto_com_checkpoint,
    salvar_estado_documentos,
)
from src.base_estruturada.pncp.service import baixar_documentos_lista


def _periodo(valor: str) -> PeriodoColeta:
    ano, mes = valor.replace("-", "/").split("/", maxsplit=1)
    return PeriodoColeta(ano=int(ano), mes=int(mes))


def baixar_e_extrair_manifesto(
    nome_etapa: str,
    arquivo_manifesto: Path,
    arquivo_checkpoint: Path,
    pasta_originais: Path,
    pasta_extraidos: Path,
    limite_entidades: int | None,
    limite_documentos_por_entidade: int | None,
    somente_tipos_prioritarios: bool,
    tipos_permitidos: set[str] | None = None,
) -> dict[str, int]:
    """Baixa novos documentos, identifica o formato e extrai apenas ZIPs."""

    manifesto_original = carregar_json(arquivo_manifesto, padrao=[])
    if not isinstance(manifesto_original, list):
        raise RuntimeError(
            f"O manifesto deveria conter uma lista: {arquivo_manifesto}"
        )
    checkpoint = carregar_checkpoint_documentos(arquivo_checkpoint)
    manifesto = materializar_manifesto_com_checkpoint(
        manifesto_original,
        checkpoint,
    )

    print(f"\n=== {nome_etapa}: download e extracao ===")
    print(f"Entidades no manifesto: {len(manifesto)}")

    baixados = baixar_documentos_lista(
        manifesto=manifesto,
        pasta_base=str(pasta_originais),
        limite_contratos=limite_entidades,
        limite_documentos_por_contrato=limite_documentos_por_entidade,
        somente_tipos_prioritarios=somente_tipos_prioritarios,
        tipos_permitidos=tipos_permitidos,
    )
    identificados = identificar_arquivos_do_manifesto(manifesto)
    extraidos = extrair_zips_do_manifesto(
        manifesto=manifesto,
        pasta_base=pasta_extraidos,
    )
    salvar_estado_documentos(
        caminho=arquivo_checkpoint,
        checkpoint=checkpoint,
        manifesto_processado=manifesto,
    )

    resumo = {
        "baixados": baixados,
        "identificados": identificados,
        "zips_extraidos": extraidos,
    }
    print(f"Resumo | {resumo}")
    return resumo


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Baixa documentos PNCP listados nos manifestos mensais."
    )
    parser.add_argument("--tipo", choices=["contratos", "contratacoes", "todos"])
    parser.add_argument("--periodo")
    parser.add_argument("--limite", type=int)
    parser.add_argument("--limite-documentos-por-entidade", type=int)
    parser.add_argument("--somente-tipos-prioritarios", action="store_true")
    args = parser.parse_args()

    # Deve ser o mesmo mes ja usado na coleta e na listagem.
    PERIODO = PeriodoColeta(ano=2026, mes=8)
    if args.periodo:
        PERIODO = _periodo(args.periodo)
    CAMINHOS = caminhos_do_periodo(PERIODO)

    # Configuracao inicial de teste. None percorre tudo que ainda nao tiver
    # sido baixado no manifesto.
    LIMITE_NOVOS_CONTRATOS = 20
    LIMITE_NOVAS_CONTRATACOES = 20
    LIMITE_DOCUMENTOS_POR_ENTIDADE = None
    if args.limite is not None:
        LIMITE_NOVOS_CONTRATOS = args.limite
        LIMITE_NOVAS_CONTRATACOES = args.limite
    if args.limite_documentos_por_entidade is not None:
        LIMITE_DOCUMENTOS_POR_ENTIDADE = args.limite_documentos_por_entidade

    # False baixa todos os tipos listados, inclusive DWG e outros formatos.
    # O filtro para texto/FAISS sera aplicado somente em etapa futura.
    SOMENTE_TIPOS_PRIORITARIOS = False
    if args.somente_tipos_prioritarios:
        SOMENTE_TIPOS_PRIORITARIOS = True

    EXECUTAR_CONTRATOS = True
    EXECUTAR_CONTRATACOES = True
    if args.tipo == "contratos":
        EXECUTAR_CONTRATOS = True
        EXECUTAR_CONTRATACOES = False
    elif args.tipo == "contratacoes":
        EXECUTAR_CONTRATOS = False
        EXECUTAR_CONTRATACOES = True
    elif args.tipo == "todos":
        EXECUTAR_CONTRATOS = True
        EXECUTAR_CONTRATACOES = True

    # O ano/mes representam o lote de coleta. O servico de download continua
    # acrescentando abaixo dessas pastas a entidade ``numeroControlePNCP`` e
    # preserva o nome original informado pelo PNCP.
    PASTA_ORIGINAIS_CONTRATOS = (
        Path("data/documents/original/contratos")
        / str(PERIODO.ano)
        / f"{PERIODO.mes:02d}"
    )
    PASTA_EXTRAIDOS_CONTRATOS = (
        Path("data/documents/expanded/contratos")
        / str(PERIODO.ano)
        / f"{PERIODO.mes:02d}"
    )
    PASTA_ORIGINAIS_CONTRATACOES = (
        Path("data/documents/original/contratacoes")
        / str(PERIODO.ano)
        / f"{PERIODO.mes:02d}"
    )
    PASTA_EXTRAIDOS_CONTRATACOES = (
        Path("data/documents/expanded/contratacoes")
        / str(PERIODO.ano)
        / f"{PERIODO.mes:02d}"
    )

    if EXECUTAR_CONTRATOS:
        baixar_e_extrair_manifesto(
            nome_etapa="Contratos",
            arquivo_manifesto=CAMINHOS["manifesto_contratos"],
            arquivo_checkpoint=CAMINHOS["checkpoint_documentos_contratos"],
            pasta_originais=PASTA_ORIGINAIS_CONTRATOS,
            pasta_extraidos=PASTA_EXTRAIDOS_CONTRATOS,
            limite_entidades=LIMITE_NOVOS_CONTRATOS,
            limite_documentos_por_entidade=LIMITE_DOCUMENTOS_POR_ENTIDADE,
            somente_tipos_prioritarios=SOMENTE_TIPOS_PRIORITARIOS,
        )

    if EXECUTAR_CONTRATACOES:
        baixar_e_extrair_manifesto(
            nome_etapa="Contratacoes",
            arquivo_manifesto=CAMINHOS["manifesto_contratacoes"],
            arquivo_checkpoint=CAMINHOS[
                "checkpoint_documentos_contratacoes"
            ],
            pasta_originais=PASTA_ORIGINAIS_CONTRATACOES,
            pasta_extraidos=PASTA_EXTRAIDOS_CONTRATACOES,
            limite_entidades=LIMITE_NOVAS_CONTRATACOES,
            limite_documentos_por_entidade=LIMITE_DOCUMENTOS_POR_ENTIDADE,
            somente_tipos_prioritarios=SOMENTE_TIPOS_PRIORITARIOS,
        )
