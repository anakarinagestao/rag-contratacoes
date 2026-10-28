"""Gera um inventario dos documentos ja listados/baixados no PNCP.

Esta pipeline e' somente de leitura dos manifestos. Ela nao consulta a API,
nao baixa arquivos e nao altera o status de documentos existentes.
"""

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.base_estruturada.pipelines.pipeline_documentos import (
    PeriodoColeta,
    caminhos_do_periodo,
)
from src.base_estruturada.pncp.repository import (
    carregar_checkpoint_documentos,
    carregar_json,
    materializar_manifesto_com_checkpoint,
    salvar_json,
)


DECISAO_POR_FORMATO = {
    "PDF": "extrair_texto",
    "DOCX": "extrair_texto",
    "ODT": "extrair_texto",
    "TXT": "extrair_texto",
    "CSV": "extrair_tabela",
    "XLSX": "extrair_tabela",
    "XLS": "extrair_tabela",
    "ZIP": "processar_conteudo_expandido",
    "RAR": "preservar_ate_haver_suporte_de_extracao",
    "PNG": "avaliar_ocr",
    "JPEG": "avaliar_ocr",
    "JPG": "avaliar_ocr",
    "DWG": "preservar_sem_faiss_inicialmente",
    "Binário desconhecido": "revisar_formato",
    "nao_identificado": "revisar_formato",
}


def _formato_por_extensao(caminho: str) -> str:
    """Classifica arquivos expandidos pela extensao, sem inferir conteudo."""

    extensao = Path(caminho).suffix.lower()
    formatos = {
        ".pdf": "PDF",
        ".docx": "DOCX",
        ".doc": "DOC",
        ".odt": "ODT",
        ".xlsx": "XLSX",
        ".xls": "XLS",
        ".csv": "CSV",
        ".txt": "TXT",
        ".zip": "ZIP",
        ".rar": "RAR",
        ".png": "PNG",
        ".jpg": "JPG",
        ".jpeg": "JPEG",
        ".dwg": "DWG",
    }
    return formatos.get(extensao, "sem_extensao_ou_desconhecido")


def _contador_para_dict(contador: Counter[str]) -> dict[str, int]:
    return dict(sorted(contador.items(), key=lambda item: (-item[1], item[0])))


def analisar_manifesto(
    manifesto: list[dict[str, Any]],
    origem: str,
) -> dict[str, Any]:
    """Resume tipos, formatos, downloads e expansoes de um manifesto."""

    tipos = Counter[str]()
    formatos = Counter[str]()
    formatos_expandidos = Counter[str]()
    status_download = Counter[str]()
    status_extracao_zip = Counter[str]()
    resumo = Counter[str]()

    resumo["entidades"] = len(manifesto)

    for entidade in manifesto:
        if not isinstance(entidade, dict):
            continue

        for documento in entidade.get("documentos") or []:
            if not isinstance(documento, dict):
                continue

            resumo["documentos_listados"] += 1
            tipo = documento.get("tipoDocumentoNome") or "tipo_nao_informado"
            tipos[str(tipo)] += 1

            download = documento.get("download") or {}
            status = str(download.get("status") or "nao_baixado")
            status_download[status] += 1

            if status in {"baixado", "identificado"}:
                resumo["documentos_baixados"] += 1

            if status == "identificado":
                resumo["documentos_identificados"] += 1
                formato = str(download.get("formato") or "nao_identificado")
                formatos[formato] += 1

                caminho = download.get("caminho")
                if caminho and not Path(str(caminho)).exists():
                    resumo["arquivos_ausentes_no_disco"] += 1

            extracao = documento.get("extracao") or {}
            if str(download.get("formato")) == "ZIP":
                status_zip = str(extracao.get("status") or "pendente")
                status_extracao_zip[status_zip] += 1

            # Arquivos internos ja registrados pela extracao segura de ZIP.
            for caminho_expandido in extracao.get("arquivos") or []:
                formatos_expandidos[_formato_por_extensao(
                    str(caminho_expandido))] += 1

    return {
        "origem": origem,
        "resumo": dict(resumo),
        "porTipoDocumento": _contador_para_dict(tipos),
        "porFormatoOriginal": _contador_para_dict(formatos),
        "porStatusDownload": _contador_para_dict(status_download),
        "extracaoZip": _contador_para_dict(status_extracao_zip),
        "porFormatoExpandido": _contador_para_dict(formatos_expandidos),
    }


def gerar_inventario_do_periodo(
    periodo: PeriodoColeta,
    arquivo_manifesto_contratos: Path,
    arquivo_manifesto_contratacoes: Path,
    arquivo_checkpoint_contratos: Path,
    arquivo_checkpoint_contratacoes: Path,
    arquivo_saida: Path,
) -> dict[str, Any]:
    """Gera inventario consolidado de contratos e contratacoes de um mes."""

    contratos_originais = carregar_json(arquivo_manifesto_contratos, padrao=[])
    contratacoes_originais = carregar_json(
        arquivo_manifesto_contratacoes, padrao=[]
    )
    if not isinstance(contratos_originais, list) or not isinstance(
        contratacoes_originais, list
    ):
        raise RuntimeError("Os dois manifestos devem conter listas JSON.")

    contratos = materializar_manifesto_com_checkpoint(
        contratos_originais,
        carregar_checkpoint_documentos(arquivo_checkpoint_contratos),
    )
    contratacoes = materializar_manifesto_com_checkpoint(
        contratacoes_originais,
        carregar_checkpoint_documentos(arquivo_checkpoint_contratacoes),
    )

    inventario_contratos = analisar_manifesto(contratos, "contratos")
    inventario_contratacoes = analisar_manifesto(contratacoes, "contratacoes")

    formatos_totais = Counter(
        inventario_contratos["porFormatoOriginal"]
    ) + Counter(inventario_contratacoes["porFormatoOriginal"])
    formatos_expandidos_totais = Counter(
        inventario_contratos["porFormatoExpandido"]
    ) + Counter(inventario_contratacoes["porFormatoExpandido"])

    formatos_encontrados = set(formatos_totais) | set(
        formatos_expandidos_totais)
    decisao_processamento = {
        formato: DECISAO_POR_FORMATO.get(
            formato,
            "revisar_formato",
        )
        for formato in sorted(formatos_encontrados)
    }

    inventario = {
        "periodo": periodo.identificador,
        "geradoEm": datetime.now(timezone.utc).isoformat(),
        "observacao": (
            "Inventario gerado a partir da listagem original e do checkpoint; "
            "nenhuma chamada ao PNCP foi realizada."
        ),
        "porOrigem": {
            "contratos": inventario_contratos,
            "contratacoes": inventario_contratacoes,
        },
        "consolidado": {
            "porFormatoOriginal": _contador_para_dict(formatos_totais),
            "porFormatoExpandido": _contador_para_dict(
                formatos_expandidos_totais
            ),
            "decisaoInicialPorFormato": decisao_processamento,
        },
    }

    salvar_json(arquivo_saida, inventario)
    return inventario


if __name__ == "__main__":
    # Use o mesmo periodo dos manifestos que deseja analisar.
    PERIODO = PeriodoColeta(ano=2026, mes=8)
    CAMINHOS = caminhos_do_periodo(PERIODO)
    ARQUIVO_SAIDA = Path(
        f"data/normalized/inventarios/{PERIODO.ano}/"
        f"inventario_documentos_{PERIODO.identificador}.json"
    )

    inventario = gerar_inventario_do_periodo(
        periodo=PERIODO,
        arquivo_manifesto_contratos=CAMINHOS["manifesto_contratos"],
        arquivo_manifesto_contratacoes=CAMINHOS["manifesto_contratacoes"],
        arquivo_checkpoint_contratos=CAMINHOS[
            "checkpoint_documentos_contratos"
        ],
        arquivo_checkpoint_contratacoes=CAMINHOS[
            "checkpoint_documentos_contratacoes"
        ],
        arquivo_saida=ARQUIVO_SAIDA,
    )

    print(f"Inventario salvo em: {ARQUIVO_SAIDA}")
    print(inventario["consolidado"]["porFormatoOriginal"])
