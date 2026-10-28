"""Seleciona o corpus documental prioritario antes do download e da extracao.

O endpoint do PNCP sempre lista todos os documentos de cada entidade. Esta
pipeline preserva essa resposta no manifesto e faz o filtro somente antes do
download, quando ele de fato reduz tempo, espaco e custo de processamento.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.base_estruturada.pipelines.pipeline_documentos import (
    PeriodoColeta,
    caminhos_do_periodo,
)
from src.base_estruturada.pipelines.pipeline_download_documentos import (
    baixar_e_extrair_manifesto,
)
from src.base_estruturada.pncp.repository import carregar_json, salvar_json
from src.base_estruturada.pncp.utils import (
    TIPOS_TESTE_EXTRACAO,
    normalizar_tipo_documento,
)


def inventariar_selecao_prioritaria(
    manifesto: list[dict[str, Any]],
    tipos_permitidos: set[str],
) -> dict[str, Any]:
    """Conta todos os tipos e mostra exatamente o que entraria no download."""

    tipos_normalizados = {
        normalizar_tipo_documento(tipo) for tipo in tipos_permitidos
    }
    todos_por_tipo: Counter[str] = Counter()
    selecionados_por_tipo: Counter[str] = Counter()
    entidades_com_selecao: set[str] = set()
    total_documentos = 0
    total_selecionados = 0

    for entidade in manifesto:
        if not isinstance(entidade, dict):
            continue
        controle = str(entidade.get("numeroControlePNCP") or "sem_controle")
        for documento in entidade.get("documentos") or []:
            if not isinstance(documento, dict):
                continue
            total_documentos += 1
            tipo = str(documento.get("tipoDocumentoNome")
                       or "tipo_nao_informado")
            todos_por_tipo[tipo] += 1
            if normalizar_tipo_documento(tipo) in tipos_normalizados:
                selecionados_por_tipo[tipo] += 1
                entidades_com_selecao.add(controle)
                total_selecionados += 1

    # Mantem inclusive os tipos que tiveram zero ocorrencias: isso revela
    # rapidamente divergencias de nomenclatura na resposta do PNCP.
    selecao_solicitada = {
        tipo: sum(
            quantidade
            for encontrado, quantidade in selecionados_por_tipo.items()
            if normalizar_tipo_documento(encontrado) == normalizar_tipo_documento(tipo)
        )
        for tipo in sorted(tipos_permitidos)
    }

    return {
        "entidadesNoManifesto": len(manifesto),
        "documentosListados": total_documentos,
        "documentosSelecionados": total_selecionados,
        "entidadesComDocumentoSelecionado": len(entidades_com_selecao),
        "tiposSolicitados": sorted(tipos_permitidos),
        "selecionadosPorTipo": selecao_solicitada,
        "todosPorTipo": dict(sorted(todos_por_tipo.items())),
    }


def gerar_relatorio_do_periodo(
    periodo: PeriodoColeta,
    caminhos: dict[str, Path],
    tipos_permitidos: set[str],
    caminho_relatorio: Path,
) -> dict[str, Any]:
    """Gera um retrato do recorte sem baixar qualquer arquivo."""

    manifestos: dict[str, list[dict[str, Any]]] = {}
    for origem, chave in (
        ("contratos", "manifesto_contratos"),
        ("contratacoes", "manifesto_contratacoes"),
    ):
        manifesto = carregar_json(caminhos[chave], padrao=[])
        if not isinstance(manifesto, list):
            raise RuntimeError(f"Manifesto invalido: {caminhos[chave]}")
        manifestos[origem] = manifesto

    por_origem = {
        origem: inventariar_selecao_prioritaria(manifesto, tipos_permitidos)
        for origem, manifesto in manifestos.items()
    }
    relatorio = {
        "schemaVersion": "selecao_documentos_pncp_v1",
        "geradoEm": datetime.now(timezone.utc).isoformat(),
        "periodo": periodo.identificador,
        "porOrigem": por_origem,
        "totalSelecionado": sum(
            valor["documentosSelecionados"] for valor in por_origem.values()
        ),
    }
    salvar_json(caminho_relatorio, relatorio)
    return relatorio


if __name__ == "__main__":
    PERIODO = PeriodoColeta(ano=2026, mes=8)
    CAMINHOS = caminhos_do_periodo(PERIODO)

    # Politica inicial do corpus: reduz volume sem alterar o manifesto original.
    TIPOS_PERMITIDOS = TIPOS_TESTE_EXTRACAO

    # Esta pipeline serve para inventariar a selecao. Para download oficial
    # em lotes, use pipeline_lotes_documentos.py; assim nenhum lote fica sem
    # manifesto e ZIP próprios.
    EXECUTAR_DOWNLOAD = False
    LIMITE_ENTIDADES: int | None = None
    LIMITE_DOCUMENTOS_POR_ENTIDADE: int | None = None

    relatorio = gerar_relatorio_do_periodo(
        periodo=PERIODO,
        caminhos=CAMINHOS,
        tipos_permitidos=TIPOS_PERMITIDOS,
        caminho_relatorio=Path(
            f"data/normalized/inventarios/{PERIODO.ano}/"
            f"selecao_prioritaria_{PERIODO.identificador}.json"
        ),
    )
    print("Selecao para download:", relatorio["totalSelecionado"])
    for origem, resumo in relatorio["porOrigem"].items():
        print(f"{origem}: {resumo['selecionadosPorTipo']}")

    if EXECUTAR_DOWNLOAD:
        for nome, chave_manifesto, chave_checkpoint in (
            ("Contratos", "manifesto_contratos",
             "checkpoint_documentos_contratos"),
            ("Contratacoes", "manifesto_contratacoes",
             "checkpoint_documentos_contratacoes"),
        ):
            pasta_origem = Path(
                f"data/documents/original/{nome.lower()}/"
                f"{PERIODO.ano}/{PERIODO.mes:02d}"
            )
            pasta_expandida = Path(
                f"data/documents/expanded/{nome.lower()}/"
                f"{PERIODO.ano}/{PERIODO.mes:02d}"
            )
            baixar_e_extrair_manifesto(
                nome_etapa=nome,
                arquivo_manifesto=CAMINHOS[chave_manifesto],
                arquivo_checkpoint=CAMINHOS[chave_checkpoint],
                pasta_originais=pasta_origem,
                pasta_extraidos=pasta_expandida,
                limite_entidades=LIMITE_ENTIDADES,
                limite_documentos_por_entidade=LIMITE_DOCUMENTOS_POR_ENTIDADE,
                somente_tipos_prioritarios=False,
                tipos_permitidos=TIPOS_PERMITIDOS,
            )
