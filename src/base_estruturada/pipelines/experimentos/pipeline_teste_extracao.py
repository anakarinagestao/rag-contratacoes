"""Compara extracao rapida de PDF e Docling em uma amostra pequena.

Nao altera manifestos, nao baixa arquivos e nao gera chunks. As saidas servem
somente para a equipe escolher qual estrategia de extracao adotar.
"""

import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

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


def nome_seguro(texto: str) -> str:
    """Mantem o nome reconhecivel, removendo apenas caracteres invalidos."""

    return re.sub(r'[<>:"/\\|?*]', "-", texto).strip() or "sem_nome"


def selecionar_pdfs(
    manifesto: list[dict[str, Any]],
    origem: str,
    limite_originais: int,
    limite_expandidos: int,
) -> list[dict[str, str]]:
    """Seleciona PDFs diretos e PDFs que estavam dentro de ZIPs."""

    originais: list[dict[str, str]] = []
    expandidos: list[dict[str, str]] = []
    caminhos_vistos: set[str] = set()

    for entidade in manifesto:
        if not isinstance(entidade, dict):
            continue

        controle = str(entidade.get("numeroControlePNCP") or "sem_controle")
        for documento in entidade.get("documentos") or []:
            if not isinstance(documento, dict):
                continue

            titulo = str(documento.get("titulo") or "sem_titulo")
            download = documento.get("download") or {}
            caminho_original = Path(str(download.get("caminho") or ""))

            if (
                download.get("formato") == "PDF"
                and caminho_original.is_file()
                and len(originais) < limite_originais
            ):
                caminho_texto = str(caminho_original.resolve())
                if caminho_texto not in caminhos_vistos:
                    originais.append(
                        {
                            "origem": origem,
                            "numeroControlePNCP": controle,
                            "titulo": titulo,
                            "tipoFonte": "pdf_original",
                            "caminho": caminho_texto,
                        }
                    )
                    caminhos_vistos.add(caminho_texto)

            extracao = documento.get("extracao") or {}
            for caminho_expandido in extracao.get("arquivos") or []:
                caminho = Path(str(caminho_expandido))
                caminho_texto = str(caminho.resolve())
                if (
                    caminho.suffix.lower() != ".pdf"
                    or not caminho.is_file()
                    or caminho_texto in caminhos_vistos
                    or len(expandidos) >= limite_expandidos
                ):
                    continue

                expandidos.append(
                    {
                        "origem": origem,
                        "numeroControlePNCP": controle,
                        "titulo": titulo,
                        "tipoFonte": "pdf_expandido_de_zip",
                        "caminho": caminho_texto,
                    }
                )
                caminhos_vistos.add(caminho_texto)

    return originais + expandidos


def extrair_rapido(caminho: Path) -> str:
    """Extrai texto nativo de PDF com pypdf, sem OCR nem analise de layout."""

    from pypdf import PdfReader

    leitor = PdfReader(str(caminho))
    return "\n\n".join(pagina.extract_text() or "" for pagina in leitor.pages)


def criar_extrator_docling() -> Callable[[Path], str]:
    """Cria uma unica instancia do Docling para reutilizar na amostra."""

    from docling.document_converter import DocumentConverter

    conversor = DocumentConverter()

    def extrair(caminho: Path) -> str:
        resultado = conversor.convert(str(caminho))
        return resultado.document.export_to_markdown()

    return extrair


def executar_extrator(
    documento: dict[str, str],
    nome_extrator: str,
    extrator: Callable[[Path], str],
    pasta_saida: Path,
    extensao_saida: str,
) -> dict[str, Any]:
    """Executa um extrator e registra metricas suficientes para comparacao."""

    caminho_entrada = Path(documento["caminho"])
    controle = nome_seguro(documento["numeroControlePNCP"])
    nome_arquivo = nome_seguro(caminho_entrada.stem)
    destino = (
        pasta_saida
        / nome_extrator
        / documento["origem"]
        / controle
        / f"{documento['tipoFonte']}__{nome_arquivo}{extensao_saida}"
    )

    inicio = time.perf_counter()
    resultado: dict[str, Any] = {
        **documento,
        "extrator": nome_extrator,
        "caminhoSaida": str(destino),
    }

    try:
        texto = extrator(caminho_entrada)
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(texto, encoding="utf-8")
        resultado.update(
            {
                "status": "concluida",
                "quantidadeCaracteres": len(texto.strip()),
                "quantidadeLinhas": len(texto.splitlines()),
                "possivelOcr": len(texto.strip()) < 300,
            }
        )
    except Exception as exc:
        resultado.update(
            {
                "status": "erro",
                "erro": f"{type(exc).__name__}: {exc}",
            }
        )

    resultado["tempoSegundos"] = round(time.perf_counter() - inicio, 2)
    return resultado


def executar_teste(
    periodo: PeriodoColeta,
    arquivo_manifesto_contratos: Path,
    arquivo_manifesto_contratacoes: Path,
    arquivo_checkpoint_contratos: Path,
    arquivo_checkpoint_contratacoes: Path,
    pasta_saida: Path,
    arquivo_relatorio: Path,
    limite_originais_por_origem: int,
    limite_expandidos_por_origem: int,
    executar_rapido: bool,
    executar_docling: bool,
) -> dict[str, Any]:
    """Executa os dois metodos sobre exatamente a mesma selecao de PDFs."""

    contratos_originais = carregar_json(arquivo_manifesto_contratos, padrao=[])
    contratacoes_originais = carregar_json(
        arquivo_manifesto_contratacoes, padrao=[]
    )
    if not isinstance(contratos_originais, list) or not isinstance(
        contratacoes_originais, list
    ):
        raise RuntimeError("Os manifestos precisam conter listas JSON.")
    contratos = materializar_manifesto_com_checkpoint(
        contratos_originais,
        carregar_checkpoint_documentos(arquivo_checkpoint_contratos),
    )
    contratacoes = materializar_manifesto_com_checkpoint(
        contratacoes_originais,
        carregar_checkpoint_documentos(arquivo_checkpoint_contratacoes),
    )

    documentos = selecionar_pdfs(
        contratos,
        "contratos",
        limite_originais_por_origem,
        limite_expandidos_por_origem,
    ) + selecionar_pdfs(
        contratacoes,
        "contratacoes",
        limite_originais_por_origem,
        limite_expandidos_por_origem,
    )

    relatorio: dict[str, Any] = {
        "periodo": periodo.identificador,
        "geradoEm": datetime.now(timezone.utc).isoformat(),
        "observacao": (
            "Teste comparativo: nao altera manifestos, nao baixa arquivos "
            "e nao gera chunks. possivelOcr e' apenas um sinal inicial "
            "baseado na quantidade de caracteres."
        ),
        "quantidadeDocumentosSelecionados": len(documentos),
        "documentosSelecionados": documentos,
        "resultados": [],
    }

    extratores: list[tuple[str, Callable[[Path], str], str]] = []
    if executar_rapido:
        extratores.append(("rapido_pypdf", extrair_rapido, ".txt"))

    if executar_docling:
        try:
            extratores.append(("docling", criar_extrator_docling(), ".md"))
        except Exception as exc:
            relatorio["erroInicializacaoDocling"] = (
                f"{type(exc).__name__}: {exc}"
            )

    for nome_extrator, extrator, extensao in extratores:
        print(f"\n=== Teste: {nome_extrator} | PDFs={len(documentos)} ===")
        for documento in documentos:
            resultado = executar_extrator(
                documento=documento,
                nome_extrator=nome_extrator,
                extrator=extrator,
                pasta_saida=pasta_saida,
                extensao_saida=extensao,
            )
            relatorio["resultados"].append(resultado)
            print(
                f"{resultado['status']} | {nome_extrator} | "
                f"caracteres={resultado.get('quantidadeCaracteres', 0)} | "
                f"tempo={resultado['tempoSegundos']}s | "
                f"arquivo={Path(documento['caminho']).name}"
            )

    salvar_json(arquivo_relatorio, relatorio)
    return relatorio


if __name__ == "__main__":
    PERIODO = PeriodoColeta(ano=2026, mes=8)
    CAMINHOS = caminhos_do_periodo(PERIODO)

    # Maximo inicial: ate 8 PDFs (2 diretos e 2 vindos de ZIP para cada
    # origem). Aumente apenas depois de avaliar os arquivos gerados.
    LIMITE_PDFS_ORIGINAIS_POR_ORIGEM = 2
    LIMITE_PDFS_EXPANDIDOS_POR_ORIGEM = 2
    EXECUTAR_RAPIDO = True
    EXECUTAR_DOCLING = True

    PASTA_SAIDA = Path(
        f"data/experiments/comparacoes/{PERIODO.ano}_{PERIODO.mes:02d}/pypdf_docling"
    )
    ARQUIVO_RELATORIO = Path(
        f"data/experiments/relatorios/{PERIODO.ano}/"
        f"teste_extracao_{PERIODO.identificador}.json"
    )

    executar_teste(
        periodo=PERIODO,
        arquivo_manifesto_contratos=CAMINHOS["manifesto_contratos"],
        arquivo_manifesto_contratacoes=CAMINHOS["manifesto_contratacoes"],
        arquivo_checkpoint_contratos=CAMINHOS[
            "checkpoint_documentos_contratos"
        ],
        arquivo_checkpoint_contratacoes=CAMINHOS[
            "checkpoint_documentos_contratacoes"
        ],
        pasta_saida=PASTA_SAIDA,
        arquivo_relatorio=ARQUIVO_RELATORIO,
        limite_originais_por_origem=LIMITE_PDFS_ORIGINAIS_POR_ORIGEM,
        limite_expandidos_por_origem=LIMITE_PDFS_EXPANDIDOS_POR_ORIGEM,
        executar_rapido=EXECUTAR_RAPIDO,
        executar_docling=EXECUTAR_DOCLING,
    )
