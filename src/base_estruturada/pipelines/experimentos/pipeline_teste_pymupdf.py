"""Compara pypdf e PyMuPDF em uma amostra de PDFs do PNCP.

Este teste nao baixa documentos, nao altera manifestos, nao chama Docling e
nao gera chunks. Ele existe somente para decidir qual extrator rapido deve
alimentar a triagem antes do Docling.
"""

from __future__ import annotations

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
from src.base_estruturada.pncp.utils import (
    TIPOS_TESTE_EXTRACAO,
    normalizar_tipo_documento,
)


def _nome_seguro(texto: str) -> str:
    return re.sub(r'[<>:"/\\|?*]', "-", texto).strip() or "sem_nome"


def selecionar_pdfs(
    manifesto: list[dict[str, Any]],
    origem: str,
    tipos_permitidos: set[str],
    limite_originais: int,
    limite_expandidos: int,
) -> list[dict[str, str]]:
    """Seleciona a mesma mistura de PDFs diretos e expandidos de ZIP."""

    tipos_normalizados = {
        normalizar_tipo_documento(tipo) for tipo in tipos_permitidos
    }
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
            tipo = str(documento.get("tipoDocumentoNome") or "")
            if normalizar_tipo_documento(tipo) not in tipos_normalizados:
                continue

            titulo = str(documento.get("titulo") or "sem_titulo")
            sequencial = str(documento.get("sequencialDocumento") or "sem_sequencial")
            download = documento.get("download") or {}
            caminho_original = Path(str(download.get("caminho") or ""))

            if (
                download.get("formato") == "PDF"
                and caminho_original.is_file()
                and len(originais) < limite_originais
            ):
                caminho = str(caminho_original.resolve())
                if caminho not in caminhos_vistos:
                    originais.append(
                        {
                            "origem": origem,
                            "numeroControlePNCP": controle,
                            "sequencialDocumento": sequencial,
                            "tipoDocumento": tipo,
                            "titulo": titulo,
                            "tipoFonte": "pdf_original",
                            "caminho": caminho,
                        }
                    )
                    caminhos_vistos.add(caminho)

            extracao = documento.get("extracao") or {}
            for caminho_expandido in extracao.get("arquivos") or []:
                caminho_pdf = Path(str(caminho_expandido))
                caminho = str(caminho_pdf.resolve())
                if (
                    caminho_pdf.suffix.lower() != ".pdf"
                    or not caminho_pdf.is_file()
                    or caminho in caminhos_vistos
                    or len(expandidos) >= limite_expandidos
                ):
                    continue
                expandidos.append(
                    {
                        "origem": origem,
                        "numeroControlePNCP": controle,
                        "sequencialDocumento": sequencial,
                        "tipoDocumento": tipo,
                        "titulo": titulo,
                        "tipoFonte": "pdf_expandido_de_zip",
                        "caminho": caminho,
                    }
                )
                caminhos_vistos.add(caminho)

    return originais + expandidos


def extrair_pypdf(caminho: Path) -> tuple[str, dict[str, int]]:
    """Extracao textual simples com metricas de paginas."""

    from pypdf import PdfReader

    leitor = PdfReader(str(caminho))
    paginas = [pagina.extract_text() or "" for pagina in leitor.pages]
    return "\n\n".join(paginas), {
        "paginas": len(paginas),
        "paginasComTexto": sum(bool(pagina.strip()) for pagina in paginas),
        "paginasVazias": sum(not pagina.strip() for pagina in paginas),
        # pypdf nao expõe blocos de layout comparaveis aos do PyMuPDF.
        "blocosTexto": 0,
    }


def extrair_pymupdf(caminho: Path) -> tuple[str, dict[str, int]]:
    """Extrai texto ordenado e registra blocos de layout por pagina."""

    try:
        import fitz  # PyMuPDF
    except ImportError as exc:
        raise RuntimeError(
            "PyMuPDF nao esta instalado. Execute `pip install pymupdf`."
        ) from exc

    paginas: list[str] = []
    blocos_texto = 0
    with fitz.open(caminho) as pdf:
        for pagina in pdf:
            # sort=True melhora a ordem aproximada de leitura em layouts comuns.
            texto = pagina.get_text("text", sort=True)
            blocos = pagina.get_text("blocks", sort=True)
            blocos_texto += sum(
                1
                for bloco in blocos
                if len(bloco) > 4 and str(bloco[4]).strip()
            )
            paginas.append(texto)

    return "\n\n".join(paginas), {
        "paginas": len(paginas),
        "paginasComTexto": sum(bool(pagina.strip()) for pagina in paginas),
        "paginasVazias": sum(not pagina.strip() for pagina in paginas),
        "blocosTexto": blocos_texto,
    }


def executar_extrator(
    documento: dict[str, str],
    nome_extrator: str,
    extrator: Callable[[Path], tuple[str, dict[str, int]]],
    pasta_saida: Path,
) -> dict[str, Any]:
    """Executa um extrator, salva sua saida e registra dados comparaveis."""

    caminho = Path(documento["caminho"])
    destino = (
        pasta_saida
        / nome_extrator
        / documento["origem"]
        / _nome_seguro(documento["numeroControlePNCP"])
        / f"{documento['tipoFonte']}__{_nome_seguro(caminho.stem)}.txt"
    )
    resultado: dict[str, Any] = {
        **documento,
        "extrator": nome_extrator,
        "caminhoSaida": str(destino),
    }
    inicio = time.perf_counter()

    try:
        texto, metricas = extrator(caminho)
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(texto, encoding="utf-8")
        paginas = metricas["paginas"]
        resultado.update(
            {
                "status": "concluida",
                "quantidadeCaracteres": len(texto.strip()),
                "quantidadeLinhas": len(texto.splitlines()),
                "caracteresPorPagina": round(len(texto.strip()) / paginas, 2)
                if paginas else 0,
                **metricas,
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
    caminhos: dict[str, Path],
    pasta_saida: Path,
    arquivo_relatorio: Path,
    tipos_permitidos: set[str],
    limite_originais_por_origem: int,
    limite_expandidos_por_origem: int,
) -> dict[str, Any]:
    """Compara ambos os extratores sobre exatamente a mesma amostra."""

    manifestos = {
        "contratos": materializar_manifesto_com_checkpoint(
            carregar_json(caminhos["manifesto_contratos"], padrao=[]),
            carregar_checkpoint_documentos(
                caminhos["checkpoint_documentos_contratos"]
            ),
        ),
        "contratacoes": materializar_manifesto_com_checkpoint(
            carregar_json(caminhos["manifesto_contratacoes"], padrao=[]),
            carregar_checkpoint_documentos(
                caminhos["checkpoint_documentos_contratacoes"]
            ),
        ),
    }
    if not all(isinstance(manifesto, list) for manifesto in manifestos.values()):
        raise RuntimeError("Manifestos precisam conter listas JSON.")

    documentos = [
        documento
        for origem, manifesto in manifestos.items()
        for documento in selecionar_pdfs(
            manifesto=manifesto,
            origem=origem,
            tipos_permitidos=tipos_permitidos,
            limite_originais=limite_originais_por_origem,
            limite_expandidos=limite_expandidos_por_origem,
        )
    ]

    relatorio: dict[str, Any] = {
        "schemaVersion": "teste_pypdf_pymupdf_v1",
        "periodo": periodo.identificador,
        "geradoEm": datetime.now(timezone.utc).isoformat(),
        "tiposPermitidos": sorted(tipos_permitidos),
        "quantidadeDocumentosSelecionados": len(documentos),
        "documentosSelecionados": documentos,
        "resultados": [],
        "observacao": (
            "A comparacao automatica mede sinais de extracao; a qualidade "
            "de ordem de leitura e tabelas ainda deve ser revisada em amostra."
        ),
    }

    for nome, extrator in (("pypdf", extrair_pypdf), ("pymupdf", extrair_pymupdf)):
        print(f"\n=== {nome} | PDFs={len(documentos)} ===")
        for documento in documentos:
            resultado = executar_extrator(
                documento=documento,
                nome_extrator=nome,
                extrator=extrator,
                pasta_saida=pasta_saida,
            )
            relatorio["resultados"].append(resultado)
            print(
                f"{resultado['status']} | {nome} | "
                f"paginas={resultado.get('paginas', 0)} | "
                f"chars={resultado.get('quantidadeCaracteres', 0)} | "
                f"tempo={resultado['tempoSegundos']}s | "
                f"arquivo={Path(documento['caminho']).name}"
            )

    salvar_json(arquivo_relatorio, relatorio)
    return relatorio


if __name__ == "__main__":
    PERIODO = PeriodoColeta(ano=2026, mes=8)
    CAMINHOS = caminhos_do_periodo(PERIODO)

    # Mantem o recorte inicial do projeto. Aumente a amostra somente depois
    # de comparar visualmente os dois textos gerados para cada PDF.
    TIPOS_PERMITIDOS = TIPOS_TESTE_EXTRACAO
    LIMITE_PDFS_ORIGINAIS_POR_ORIGEM = 3
    LIMITE_PDFS_EXPANDIDOS_POR_ORIGEM = 3

    executar_teste(
        periodo=PERIODO,
        caminhos=CAMINHOS,
        pasta_saida=Path(
            f"data/experiments/comparacoes/{PERIODO.identificador}/pypdf_pymupdf"
        ),
        arquivo_relatorio=Path(
            f"data/experiments/relatorios/{PERIODO.ano}/"
            f"teste_pypdf_pymupdf_{PERIODO.identificador}.json"
        ),
        tipos_permitidos=TIPOS_PERMITIDOS,
        limite_originais_por_origem=LIMITE_PDFS_ORIGINAIS_POR_ORIGEM,
        limite_expandidos_por_origem=LIMITE_PDFS_EXPANDIDOS_POR_ORIGEM,
    )
