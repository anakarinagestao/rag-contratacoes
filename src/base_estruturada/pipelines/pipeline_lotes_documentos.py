"""Gerencia lotes rastreaveis de documentos para download e Docling.

O checkpoint mensal de documentos continua sendo a memoria tecnica do
download. Este modulo acrescenta a camada operacional: cada lote congela a
lista de documentos que lhe pertence, permite retry manual somente dos seus
erros e gera um ZIP portavel sem selecionar pastas manualmente.
"""

from __future__ import annotations

import copy
import os
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.base_estruturada.pipelines.pipeline_documentos import (
    PeriodoColeta,
    caminhos_do_periodo,
)
from src.base_estruturada.pipelines.pipeline_docling_lotes import (
    gerar_fila_docling,
)
from src.base_estruturada.pncp.repository import (
    carregar_checkpoint_documentos,
    carregar_json,
    extrair_zips_do_manifesto,
    identificar_arquivos_do_manifesto,
    materializar_manifesto_com_checkpoint,
    salvar_estado_documentos,
    salvar_json,
)
from src.base_estruturada.pncp.service import baixar_documentos
from src.base_estruturada.pncp.utils import (
    TIPOS_TESTE_EXTRACAO,
    normalizar_tipo_documento,
)


SCHEMA_LOTE = "lote_documentos_pncp_v1"


def _agora() -> str:
    return datetime.now(timezone.utc).isoformat()


def _chave_item(origem: str, controle: str, sequencial: Any) -> str:
    """Chave estavel do documento no controle operacional de lotes."""

    return f"{origem}|{controle}|{sequencial or 'sem_sequencial'}"


def caminhos_do_lote(periodo: PeriodoColeta, nome_lote: str) -> dict[str, Path]:
    """Caminhos do lote sem misturar seus estados com outro lote do mês."""

    if not nome_lote.startswith("lote_"):
        raise ValueError("nome_lote deve seguir o formato 'lote_001'.")

    base_lote = Path("data/checkpoints/lotes_documentos") / \
        periodo.identificador
    base_zip = Path("data/lotes_documentos") / periodo.identificador
    base_extracao = Path("data/checkpoints/extracao") / str(periodo.ano)
    return {
        "lote": base_lote / f"{nome_lote}.json",
        "zip": base_zip / f"documentos_{nome_lote}.zip",
        "fila_docling": (
            base_extracao /
            f"fila_docling_{periodo.identificador}_{nome_lote}.json"
        ),
        "estado_docling": (
            base_extracao /
            f"estado_docling_{periodo.identificador}_{nome_lote}.json"
        ),
        "relatorio_docling": (
            Path("data/normalized/inventarios") / str(periodo.ano)
            / f"extracao_docling_{periodo.identificador}_{nome_lote}.json"
        ),
    }


def carregar_lote(caminho: Path) -> dict[str, Any]:
    lote = carregar_json(caminho, padrao=None)
    if not isinstance(lote, dict) or lote.get("schemaVersion") != SCHEMA_LOTE:
        raise RuntimeError(f"Lote invalido ou inexistente: {caminho}")
    if not isinstance(lote.get("documentos"), list):
        raise RuntimeError(f"Lote sem lista de documentos: {caminho}")
    return lote


def _itens_atribuidos(pasta_lotes: Path) -> set[str]:
    """Retorna documentos reservados em lotes anteriores, abertos ou fechados."""

    atribuidos: set[str] = set()
    if not pasta_lotes.exists():
        return atribuidos
    for caminho in pasta_lotes.glob("lote_*.json"):
        lote = carregar_json(caminho, padrao={})
        if not isinstance(lote, dict):
            continue
        for item in lote.get("documentos") or []:
            if isinstance(item, dict) and item.get("chaveLote"):
                atribuidos.add(str(item["chaveLote"]))
    return atribuidos


def _manifestos_com_estado(periodo: PeriodoColeta) -> dict[str, list[dict[str, Any]]]:
    caminhos = caminhos_do_periodo(periodo)
    resultado: dict[str, list[dict[str, Any]]] = {}
    for origem, chave_manifesto, chave_checkpoint in (
        ("contratos", "manifesto_contratos", "checkpoint_documentos_contratos"),
        ("contratacoes", "manifesto_contratacoes",
         "checkpoint_documentos_contratacoes"),
    ):
        manifesto = carregar_json(caminhos[chave_manifesto], padrao=[])
        if not isinstance(manifesto, list):
            raise RuntimeError(
                f"Manifesto invalido: {caminhos[chave_manifesto]}")
        checkpoint = carregar_checkpoint_documentos(caminhos[chave_checkpoint])
        resultado[origem] = materializar_manifesto_com_checkpoint(
            manifesto,
            checkpoint,
        )
    return resultado


def criar_lote_documentos(
    periodo: PeriodoColeta,
    nome_lote: str,
    limite_documentos: int,
    tipos_permitidos: set[str] | None = None,
) -> dict[str, Any]:
    """Congela os proximos documentos ainda nao atribuidos a outro lote."""

    if limite_documentos <= 0:
        raise ValueError("limite_documentos deve ser maior que zero.")
    caminhos_lote = caminhos_do_lote(periodo, nome_lote)
    if caminhos_lote["lote"].exists():
        return carregar_lote(caminhos_lote["lote"])

    tipos_normalizados = (
        {normalizar_tipo_documento(tipo) for tipo in tipos_permitidos}
        if tipos_permitidos is not None
        else None
    )
    atribuídos = _itens_atribuidos(caminhos_lote["lote"].parent)
    candidatos: list[dict[str, Any]] = []

    for origem, manifesto in _manifestos_com_estado(periodo).items():
        for entidade in manifesto:
            if not isinstance(entidade, dict):
                continue
            controle = str(entidade.get("numeroControlePNCP") or "")
            if not controle:
                continue
            for documento in entidade.get("documentos") or []:
                if not isinstance(documento, dict):
                    continue
                tipo = str(documento.get("tipoDocumentoNome") or "")
                if (
                    tipos_normalizados is not None
                    and normalizar_tipo_documento(tipo) not in tipos_normalizados
                ):
                    continue
                if not (documento.get("url") or documento.get("uri")):
                    continue
                chave = _chave_item(
                    origem,
                    controle,
                    documento.get("sequencialDocumento"),
                )
                if chave in atribuídos:
                    continue
                candidatos.append(
                    {
                        "chaveLote": chave,
                        "origem": origem,
                        "numeroControlePNCP": controle,
                        "sequencialDocumento": documento.get("sequencialDocumento"),
                        "tipoDocumento": tipo,
                        "tituloDocumento": documento.get("titulo"),
                        "statusLote": "pendente",
                        "documento": copy.deepcopy(documento),
                    }
                )

    candidatos.sort(
        key=lambda item: (
            item["origem"],
            item["numeroControlePNCP"],
            str(item.get("sequencialDocumento") or ""),
            str(item.get("tituloDocumento") or ""),
        )
    )
    lote = {
        "schemaVersion": SCHEMA_LOTE,
        "periodo": periodo.identificador,
        "nomeLote": nome_lote,
        "criadoEm": _agora(),
        "limiteDocumentos": limite_documentos,
        "tiposPermitidos": sorted(tipos_permitidos) if tipos_permitidos else None,
        "documentos": candidatos[:limite_documentos],
    }
    # Um documento que já existe em disco pode ser formalizado em um lote sem
    # ser baixado novamente.
    _atualizar_status_lote(lote)
    lote["quantidadeSelecionada"] = len(lote["documentos"])
    salvar_json(caminhos_lote["lote"], lote)
    return lote


def _manifesto_do_lote_por_origem(
    lote: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    """Agrupa referências mutáveis dos itens para reutilizar funções existentes."""

    por_origem: dict[str, dict[str, dict[str, Any]]] = {
        "contratos": {},
        "contratacoes": {},
    }
    for item in lote["documentos"]:
        origem = item["origem"]
        controle = item["numeroControlePNCP"]
        entidade = por_origem[origem].setdefault(
            controle,
            {"numeroControlePNCP": controle, "documentos": []},
        )
        entidade["documentos"].append(item["documento"])
    return {
        origem: list(entidades.values())
        for origem, entidades in por_origem.items()
    }


def _atualizar_status_lote(lote: dict[str, Any]) -> None:
    for item in lote["documentos"]:
        download = item["documento"].get("download") or {}
        caminho = Path(str(download.get("caminho") or ""))
        if download.get("status") == "identificado" and caminho.is_file():
            item["statusLote"] = "baixado"
        elif download.get("status") in {"erro", "arquivo_nao_encontrado"}:
            item["statusLote"] = "erro_download"
        else:
            item["statusLote"] = "pendente"


def baixar_lote_documentos(
    periodo: PeriodoColeta,
    nome_lote: str,
    somente_erros: bool = False,
) -> dict[str, int]:
    """Baixa somente o lote informado; retry nunca seleciona documentos novos."""

    caminhos_lote = caminhos_do_lote(periodo, nome_lote)
    lote = carregar_lote(caminhos_lote["lote"])
    manifestos_lote = _manifesto_do_lote_por_origem(lote)
    tentados = 0
    baixados = 0

    for item in lote["documentos"]:
        status = item.get("statusLote")
        if status == "baixado" or (somente_erros and status != "erro_download"):
            continue
        documento = item["documento"]
        origem = item["origem"]
        pasta = (
            Path("data/documents/original") / origem / str(periodo.ano)
            / f"{periodo.mes:02d}"
        )
        tentados += 1
        caminho = baixar_documentos(
            numero_controle=item["numeroControlePNCP"],
            documento=documento,
            pasta_base=pasta,
        )
        anterior = documento.get("download") or {}
        if caminho is None:
            documento["download"] = {
                "status": "erro",
                "tentativas": int(anterior.get("tentativas", 0)) + 1,
                "ultimoErro": "falha_no_download",
            }
            item["statusLote"] = "erro_download"
        else:
            documento["download"] = {
                "status": "baixado",
                "caminho": str(caminho),
                "tentativas": int(anterior.get("tentativas", 0)) + 1,
            }
            baixados += 1

    identificados = 0
    extraidos = 0
    caminhos_periodo = caminhos_do_periodo(periodo)
    for origem, manifesto in manifestos_lote.items():
        identificados += identificar_arquivos_do_manifesto(manifesto)
        extraidos += extrair_zips_do_manifesto(
            manifesto,
            Path("data/documents/expanded") / origem / str(periodo.ano)
            / f"{periodo.mes:02d}",
        )
        chave_checkpoint = f"checkpoint_documentos_{origem}"
        checkpoint = carregar_checkpoint_documentos(
            caminhos_periodo[chave_checkpoint])
        salvar_estado_documentos(
            caminhos_periodo[chave_checkpoint],
            checkpoint,
            manifesto,
        )

    _atualizar_status_lote(lote)
    lote["atualizadoEm"] = _agora()
    lote["ultimaExecucao"] = {
        "somenteErros": somente_erros,
        "tentados": tentados,
        "baixadosNestaExecucao": baixados,
        "identificados": identificados,
        "zipsExtraidos": extraidos,
    }
    salvar_json(caminhos_lote["lote"], lote)
    return {
        "tentados": tentados,
        "baixados": baixados,
        "identificados": identificados,
        "zips_extraidos": extraidos,
        "erros": sum(
            item.get("statusLote") == "erro_download"
            for item in lote["documentos"]
        ),
    }


def _caminho_no_zip(caminho: Path) -> str | None:
    """Converte caminho local em caminho portável abaixo de data/documents."""

    texto = str(caminho).replace("\\", "/")
    marcador = "/data/documents/"
    indice = texto.lower().find(marcador)
    if indice >= 0:
        return texto[indice + len(marcador):]
    texto = texto.removeprefix("data/documents/").lstrip("/")
    return texto if texto.startswith(("original/", "expanded/")) else None


def gerar_zip_lote(periodo: PeriodoColeta, nome_lote: str) -> dict[str, int]:
    """Gera ZIP somente com arquivos concluídos e extraídos daquele lote."""

    caminhos_lote = caminhos_do_lote(periodo, nome_lote)
    lote = carregar_lote(caminhos_lote["lote"])
    arquivos: dict[str, Path] = {}
    ignorados = 0
    for item in lote["documentos"]:
        if item.get("statusLote") != "baixado":
            continue
        documento = item["documento"]
        candidatos = [
            Path(str((documento.get("download") or {}).get("caminho") or "")),
            *[
                Path(str(caminho))
                for caminho in (documento.get("extracao") or {}).get("arquivos") or []
            ],
        ]
        for caminho in candidatos:
            if not caminho.is_file():
                ignorados += 1
                continue
            relativo = _caminho_no_zip(caminho)
            if relativo is None:
                ignorados += 1
                continue
            arquivos[relativo] = caminho

    destino = caminhos_lote["zip"]
    destino.parent.mkdir(parents=True, exist_ok=True)
    temporario = destino.with_suffix(".tmp")
    with zipfile.ZipFile(temporario, "w", compression=zipfile.ZIP_DEFLATED) as arquivo_zip:
        for relativo, caminho in sorted(arquivos.items()):
            arquivo_zip.write(caminho, arcname=relativo)
    os.replace(temporario, destino)

    lote["zip"] = {
        "status": "gerado",
        "caminho": str(destino),
        "arquivosIncluidos": len(arquivos),
        "arquivosIgnorados": ignorados,
        "geradoEm": _agora(),
    }
    lote["atualizadoEm"] = _agora()
    salvar_json(caminhos_lote["lote"], lote)
    return {"arquivos_incluidos": len(arquivos), "arquivos_ignorados": ignorados}


def gerar_fila_docling_do_lote(
    lote: dict[str, Any],
    caminho_fila: Path,
) -> dict[str, Any]:
    """Cria fila Docling estritamente a partir de um lote já congelado."""

    manifestos = _manifesto_do_lote_por_origem(lote)
    return gerar_fila_docling(
        periodo=f"{lote['periodo']}_{lote['nomeLote']}",
        manifestos=manifestos,
        caminho_fila=caminho_fila,
    )


if __name__ == "__main__":
    PERIODO = PeriodoColeta(ano=2026, mes=8)
    NOME_LOTE = "lote_005"
    LIMITE_DOCUMENTOS = 500
    TIPOS_PERMITIDOS = TIPOS_TESTE_EXTRACAO

    # Opções: criar, criar_e_baixar, reprocessar_erros, gerar_zip.
    # "criar" apenas congela a lista; é útil para preparar o trabalho da equipe.
    ACAO = "criar_e_baixar"

    if ACAO in {"criar", "criar_e_baixar"}:
        lote = criar_lote_documentos(
            periodo=PERIODO,
            nome_lote=NOME_LOTE,
            limite_documentos=LIMITE_DOCUMENTOS,
            tipos_permitidos=TIPOS_PERMITIDOS,
        )
        print(
            f"Lote criado/carregado | documentos={lote['quantidadeSelecionada']}")
        if ACAO == "criar_e_baixar":
            print(baixar_lote_documentos(PERIODO, NOME_LOTE))
            print(gerar_zip_lote(PERIODO, NOME_LOTE))
    elif ACAO == "reprocessar_erros":
        print(baixar_lote_documentos(PERIODO, NOME_LOTE, somente_erros=True))
        print(gerar_zip_lote(PERIODO, NOME_LOTE))
    elif ACAO == "gerar_zip":
        print(gerar_zip_lote(PERIODO, NOME_LOTE))
    else:
        raise ValueError("ACAO invalida.")
