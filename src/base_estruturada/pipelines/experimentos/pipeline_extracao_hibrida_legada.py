"""Extracao mensal de texto em dois modos manuais: rapido e Docling.

Modo ``rapido``: extrai texto nativo dos PDFs e gera uma fila de candidatos
ao Docling. Modo ``docling``: processa somente essa fila. Nenhum dos modos
baixa documentos ou gera chunks.
"""

import argparse
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from src.base_estruturada.pipelines.pipeline_documentos import (
    PeriodoColeta,
    caminhos_do_periodo,
)
from src.base_estruturada.pncp.repository import (
    carregar_checkpoint_documentos,
    carregar_json,
    materializar_manifesto_com_checkpoint,
    salvar_estado_documentos,
    salvar_json,
)


MINIMO_CARACTERES_TEXTO_NATIVO = 300


def _periodo(valor: str) -> PeriodoColeta:
    ano, mes = valor.replace("-", "/").split("/", maxsplit=1)
    return PeriodoColeta(ano=int(ano), mes=int(mes))


def _agora() -> str:
    return datetime.now(timezone.utc).isoformat()


def _nome_seguro(texto: str) -> str:
    return re.sub(r'[<>:"/\\|?*]', "-", texto).strip() or "sem_nome"


def _iterar_fontes_pdf(
    manifesto: list[dict[str, Any]],
    origem: str,
) -> Iterator[tuple[dict[str, Any], dict[str, Any], dict[str, str]]]:
    """Produz PDFs diretos e PDFs extraidos de ZIPs do mesmo manifesto."""

    caminhos_vistos: set[str] = set()

    for entidade in manifesto:
        if not isinstance(entidade, dict):
            continue

        controle = str(entidade.get("numeroControlePNCP") or "sem_controle")
        for documento in entidade.get("documentos") or []:
            if not isinstance(documento, dict):
                continue

            download = documento.get("download") or {}
            caminho_original = Path(str(download.get("caminho") or ""))
            if download.get("formato") == "PDF" and caminho_original.is_file():
                caminho = str(caminho_original.resolve())
                caminhos_vistos.add(caminho)
                yield entidade, documento, {
                    "origem": origem,
                    "numeroControlePNCP": controle,
                    "tipoFonte": "pdf_original",
                    "caminhoFonte": caminho,
                }

            extracao = documento.get("extracao") or {}
            for caminho_expandido in extracao.get("arquivos") or []:
                caminho_pdf = Path(str(caminho_expandido))
                caminho = str(caminho_pdf.resolve())
                if (
                    not caminho_pdf.is_file()
                    or caminho_pdf.suffix.lower() != ".pdf"
                    or caminho in caminhos_vistos
                ):
                    continue

                caminhos_vistos.add(caminho)
                yield entidade, documento, {
                    "origem": origem,
                    "numeroControlePNCP": controle,
                    "tipoFonte": "pdf_expandido_de_zip",
                    "caminhoFonte": caminho,
                }


def _registro_texto(
    documento: dict[str, Any],
    fonte: dict[str, str],
) -> dict[str, Any]:
    """Obtem ou cria o registro de processamento de um arquivo fisico."""

    registros = documento.setdefault("processamentoTexto", [])
    for registro in registros:
        if registro.get("caminhoFonte") == fonte["caminhoFonte"]:
            return registro

    registro = {
        **fonte,
        "tituloDocumentoPncp": documento.get("titulo"),
        "sequencialDocumento": documento.get("sequencialDocumento"),
        "rapido": {"status": "pendente"},
        "docling": {"status": "nao_avaliado"},
        "textoAtivo": None,
    }
    registros.append(registro)
    return registro


def _destino_texto(
    pasta_base: Path,
    fonte: dict[str, str],
    extensao: str,
) -> Path:
    caminho_fonte = Path(fonte["caminhoFonte"])
    return (
        pasta_base
        / fonte["origem"]
        / _nome_seguro(fonte["numeroControlePNCP"])
        / f"{fonte['tipoFonte']}__{_nome_seguro(caminho_fonte.stem)}{extensao}"
    )


def _extrair_pdf_rapido(caminho: Path) -> str:
    """Leitura de texto nativo, sem OCR nem analise de layout."""

    from pypdf import PdfReader

    leitor = PdfReader(str(caminho))
    return "\n\n".join(pagina.extract_text() or "" for pagina in leitor.pages)


def _criar_extrator_docling() -> Callable[[Path], str]:
    """Instancia o Docling uma vez por execucao em lote."""

    from docling.document_converter import DocumentConverter

    conversor = DocumentConverter()

    def extrair(caminho: Path) -> str:
        return conversor.convert(str(caminho)).document.export_to_markdown()

    return extrair


def _executar_extracao(
    extrator: Callable[[Path], str],
    caminho_fonte: Path,
    destino: Path,
) -> dict[str, Any]:
    inicio = time.perf_counter()
    try:
        texto = extrator(caminho_fonte)
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(texto, encoding="utf-8")
        return {
            "status": "concluida",
            "caminho": str(destino),
            "quantidadeCaracteres": len(texto.strip()),
            "quantidadeLinhas": len(texto.splitlines()),
            "executadoEm": _agora(),
            "tempoSegundos": round(time.perf_counter() - inicio, 2),
        }
    except Exception as exc:
        return {
            "status": "erro",
            "erro": f"{type(exc).__name__}: {exc}",
            "executadoEm": _agora(),
            "tempoSegundos": round(time.perf_counter() - inicio, 2),
        }


def _candidato_docling(registro: dict[str, Any]) -> dict[str, Any] | None:
    rapido = registro.get("rapido") or {}
    if rapido.get("status") == "erro":
        motivo = "erro_na_extracao_rapida"
    elif (
        rapido.get("status") == "concluida"
        and int(rapido.get("quantidadeCaracteres", 0))
        < MINIMO_CARACTERES_TEXTO_NATIVO
    ):
        motivo = "texto_nativo_insuficiente"
    else:
        return None

    docling = registro.get("docling") or {}
    return {
        "origem": registro["origem"],
        "numeroControlePNCP": registro["numeroControlePNCP"],
        "sequencialDocumento": registro.get("sequencialDocumento"),
        "caminhoFonte": registro["caminhoFonte"],
        "tipoFonte": registro["tipoFonte"],
        "motivo": motivo,
        "statusDocling": docling.get("status", "pendente"),
        "caminhoMarkdown": docling.get("caminho"),
    }


def _gerar_checkpoint_docling(
    periodo: PeriodoColeta,
    manifestos: dict[str, list[dict[str, Any]]],
    caminho_checkpoint: Path,
) -> dict[str, Any]:
    candidatos: list[dict[str, Any]] = []
    for manifesto in manifestos.values():
        for entidade in manifesto:
            if not isinstance(entidade, dict):
                continue
            for documento in entidade.get("documentos") or []:
                if not isinstance(documento, dict):
                    continue
                for registro in documento.get("processamentoTexto") or []:
                    candidato = _candidato_docling(registro)
                    if candidato is not None:
                        candidatos.append(candidato)

    checkpoint = {
        "periodo": periodo.identificador,
        "geradoEm": _agora(),
        "criterio": (
            f"erro na extracao rapida ou menos de "
            f"{MINIMO_CARACTERES_TEXTO_NATIVO} caracteres"
        ),
        "candidatos": candidatos,
    }
    salvar_json(caminho_checkpoint, checkpoint)
    return checkpoint


def executar_extracao_rapida(
    periodo: PeriodoColeta,
    manifestos: dict[str, list[dict[str, Any]]],
    pasta_saida: Path,
    limite_arquivos: int | None,
) -> dict[str, int]:
    """Extrai PDFs nativos e define ``textoAtivo`` quando o texto e' bom."""

    resumo = {"processados": 0, "aprovados": 0,
              "candidatos_docling": 0, "pulados": 0}

    for origem, manifesto in manifestos.items():
        for _, documento, fonte in _iterar_fontes_pdf(manifesto, origem):
            if limite_arquivos is not None and resumo["processados"] >= limite_arquivos:
                return resumo

            registro = _registro_texto(documento, fonte)
            rapido_anterior = registro.get("rapido") or {}
            caminho_anterior = Path(str(rapido_anterior.get("caminho") or ""))
            if (
                rapido_anterior.get("status") == "concluida"
                and caminho_anterior.is_file()
            ):
                resumo["pulados"] += 1
                continue

            resultado = _executar_extracao(
                extrator=_extrair_pdf_rapido,
                caminho_fonte=Path(fonte["caminhoFonte"]),
                destino=_destino_texto(pasta_saida, fonte, ".txt"),
            )
            registro["rapido"] = resultado
            resumo["processados"] += 1

            if resultado["status"] == "concluida" and resultado["quantidadeCaracteres"] >= MINIMO_CARACTERES_TEXTO_NATIVO:
                registro["docling"] = registro.get(
                    "docling") or {"status": "nao_necessario"}
                if registro["docling"].get("status") == "nao_avaliado":
                    registro["docling"] = {"status": "nao_necessario"}
                registro["textoAtivo"] = {
                    "origem": "rapido",
                    "caminho": resultado["caminho"],
                }
                resumo["aprovados"] += 1
            else:
                registro["docling"] = {"status": "pendente"}
                registro["textoAtivo"] = None
                resumo["candidatos_docling"] += 1

            print(
                f"Rapido | {resultado['status']} | "
                f"caracteres={resultado.get('quantidadeCaracteres', 0)} | "
                f"arquivo={Path(fonte['caminhoFonte']).name}"
            )

    return resumo


def _encontrar_registro(
    manifestos: dict[str, list[dict[str, Any]]],
    candidato: dict[str, Any],
) -> dict[str, Any] | None:
    for entidade in manifestos.get(candidato["origem"], []):
        if not isinstance(entidade, dict):
            continue
        for documento in entidade.get("documentos") or []:
            if not isinstance(documento, dict):
                continue
            for registro in documento.get("processamentoTexto") or []:
                if registro.get("caminhoFonte") == candidato["caminhoFonte"]:
                    return registro
    return None


def executar_docling_pendente(
    manifestos: dict[str, list[dict[str, Any]]],
    checkpoint: dict[str, Any],
    pasta_saida: Path,
    limite_candidatos: int | None,
) -> dict[str, int]:
    """Converte somente candidatos pendentes e aponta o Markdown como ativo."""

    resumo = {"processados": 0, "concluidos": 0, "erros": 0, "pulados": 0}
    candidatos = checkpoint.get("candidatos", [])
    if not isinstance(candidatos, list):
        return resumo

    try:
        extrator = _criar_extrator_docling()
    except Exception as exc:
        raise RuntimeError(
            f"Nao foi possivel inicializar o Docling: {exc}") from exc

    for candidato in candidatos:
        if not isinstance(candidato, dict) or candidato.get("statusDocling") != "pendente":
            continue
        if limite_candidatos is not None and resumo["processados"] >= limite_candidatos:
            break

        registro = _encontrar_registro(manifestos, candidato)
        caminho_fonte = Path(str(candidato.get("caminhoFonte") or ""))
        if registro is None or not caminho_fonte.is_file():
            candidato["statusDocling"] = "erro"
            candidato["erro"] = "registro_ou_arquivo_nao_encontrado"
            resumo["erros"] += 1
            continue

        fonte = {
            "origem": str(registro["origem"]),
            "numeroControlePNCP": str(registro["numeroControlePNCP"]),
            "tipoFonte": str(registro["tipoFonte"]),
            "caminhoFonte": str(registro["caminhoFonte"]),
        }
        resultado = _executar_extracao(
            extrator=extrator,
            caminho_fonte=caminho_fonte,
            destino=_destino_texto(pasta_saida, fonte, ".md"),
        )
        registro["docling"] = resultado
        candidato["statusDocling"] = resultado["status"]
        candidato["caminhoMarkdown"] = resultado.get("caminho")
        resumo["processados"] += 1

        if resultado["status"] == "concluida":
            registro["textoAtivo"] = {
                "origem": "docling",
                "caminho": resultado["caminho"],
            }
            resumo["concluidos"] += 1
        else:
            candidato["erro"] = resultado.get("erro")
            resumo["erros"] += 1

        print(
            f"Docling | {resultado['status']} | "
            f"caracteres={resultado.get('quantidadeCaracteres', 0)} | "
            f"arquivo={caminho_fonte.name}"
        )

    checkpoint["atualizadoEm"] = _agora()
    return resumo


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Extrai texto dos PDFs PNCP ja baixados."
    )
    parser.add_argument("--tipo", choices=["contratos", "contratacoes", "todos"])
    parser.add_argument("--periodo")
    parser.add_argument("--modo", choices=["rapido", "docling"])
    parser.add_argument("--limite", type=int)
    args = parser.parse_args()

    PERIODO = PeriodoColeta(ano=2026, mes=8)
    if args.periodo:
        PERIODO = _periodo(args.periodo)
    CAMINHOS = caminhos_do_periodo(PERIODO)

    # Execute primeiro "rapido". Depois revise o checkpoint e altere para
    # "docling" para processar apenas os candidatos pendentes.
    MODO_EXECUCAO = "docling"  # "rapido" ou "docling"
    LIMITE_ARQUIVOS_RAPIDOS: int | None = 10
    LIMITE_CANDIDATOS_DOCLING: int | None = 3
    if args.modo:
        MODO_EXECUCAO = args.modo
    if args.limite is not None:
        LIMITE_ARQUIVOS_RAPIDOS = args.limite
        LIMITE_CANDIDATOS_DOCLING = args.limite

    # Pipeline arquivada: seus artefatos nao devem se misturar ao corpus oficial.
    ARQUIVO_CHECKPOINT = Path(
        f"data/experiments/checkpoints/{PERIODO.ano}/"
        f"candidatos_docling_{PERIODO.identificador}.json"
    )
    PASTA_RAPIDO = Path("data/experiments/extracoes_legadas/rapido")
    PASTA_DOCLING = Path("data/experiments/extracoes_legadas/docling")

    manifestos_originais = {
        "contratos": carregar_json(CAMINHOS["manifesto_contratos"], padrao=[]),
        "contratacoes": carregar_json(CAMINHOS["manifesto_contratacoes"], padrao=[]),
    }
    if args.tipo in {"contratos", "contratacoes"}:
        manifestos_originais = {
            args.tipo: manifestos_originais[args.tipo],
        }
    if not all(
        isinstance(manifesto, list)
        for manifesto in manifestos_originais.values()
    ):
        raise RuntimeError(
            "Os manifestos de contratos e contratacoes devem ser listas.")
    checkpoints_documentos = {
        origem: carregar_checkpoint_documentos(
            CAMINHOS[f"checkpoint_documentos_{origem}"]
        )
        for origem in manifestos_originais
    }
    manifestos = {
        origem: materializar_manifesto_com_checkpoint(
            manifestos_originais[origem],
            checkpoints_documentos[origem],
        )
        for origem in manifestos_originais
    }

    if MODO_EXECUCAO == "rapido":
        resumo = executar_extracao_rapida(
            periodo=PERIODO,
            manifestos=manifestos,
            pasta_saida=PASTA_RAPIDO,
            limite_arquivos=LIMITE_ARQUIVOS_RAPIDOS,
        )
        for origem in manifestos:
            salvar_estado_documentos(
                CAMINHOS[f"checkpoint_documentos_{origem}"],
                checkpoints_documentos[origem],
                manifestos[origem],
            )
        checkpoint = _gerar_checkpoint_docling(
            periodo=PERIODO,
            manifestos=manifestos,
            caminho_checkpoint=ARQUIVO_CHECKPOINT,
        )
        print(
            f"Resumo rapido | {resumo} | candidatos={len(checkpoint['candidatos'])}")
    elif MODO_EXECUCAO == "docling":
        checkpoint = carregar_json(
            ARQUIVO_CHECKPOINT, padrao={"candidatos": []})
        if not isinstance(checkpoint, dict):
            raise RuntimeError(f"Checkpoint invalido: {ARQUIVO_CHECKPOINT}")
        resumo = executar_docling_pendente(
            manifestos=manifestos,
            checkpoint=checkpoint,
            pasta_saida=PASTA_DOCLING,
            limite_candidatos=LIMITE_CANDIDATOS_DOCLING,
        )
        for origem in manifestos:
            salvar_estado_documentos(
                CAMINHOS[f"checkpoint_documentos_{origem}"],
                checkpoints_documentos[origem],
                manifestos[origem],
            )
        salvar_json(ARQUIVO_CHECKPOINT, checkpoint)
        print(f"Resumo Docling | {resumo}")
    else:
        raise ValueError("MODO_EXECUCAO deve ser 'rapido' ou 'docling'.")
