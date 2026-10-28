"""Gera JSONL de chunks a partir de ``textoAtivo`` dos manifestos PNCP."""

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from collections.abc import Callable
from typing import Any

from rich.progress import (
    BarColumn,
    Progress,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)

from src.base_estruturada.chunking import (
    caminho_relativo_portavel,
    gerar_chunk_id,
    gerar_chunks,
    montar_metadata_base,
    selecionar_configuracao,
)
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


def aplicar_estado_docling(
    manifestos: dict[str, list[dict[str, Any]]],
    estado_docling: dict[str, Any],
) -> None:
    """Aplica o checkpoint Docling somente em memoria, para gerar chunks.

    O manifesto original continua sendo a copia imutavel da resposta da API.
    Esta funcao cria/atualiza ``processamentoTexto`` apenas no objeto que esta
    em memoria durante esta execucao.
    """

    estados = estado_docling.get("documentos", {})
    if not isinstance(estados, dict):
        return

    por_controle: dict[tuple[str, str], dict[str, Any]] = {}
    for origem, manifesto in manifestos.items():
        for entidade in manifesto:
            if isinstance(entidade, dict) and entidade.get("numeroControlePNCP"):
                por_controle[(origem, str(
                    entidade["numeroControlePNCP"]))] = entidade

    for item in estados.values():
        if not isinstance(item, dict) or item.get("status") != "concluido":
            continue
        entidade = por_controle.get((
            str(item.get("origem")),
            str(item.get("numeroControlePNCP")),
        ))
        if entidade is None:
            continue

        for documento in entidade.get("documentos") or []:
            if (
                isinstance(documento, dict)
                and documento.get("sequencialDocumento") == item.get("sequencialDocumento")
            ):
                registros = documento.setdefault("processamentoTexto", [])
                relativo = str(item.get("caminhoFonteRelativo") or "")
                registro = next(
                    (
                        atual for atual in registros
                        if atual.get("caminhoFonteRelativo") == relativo
                    ),
                    None,
                )
                if registro is None:
                    registro = {
                        "origem": item.get("origem"),
                        "numeroControlePNCP": item.get("numeroControlePNCP"),
                        "sequencialDocumento": item.get("sequencialDocumento"),
                        "tipoFonte": item.get("tipoFonte"),
                        "caminhoFonteRelativo": relativo,
                    }
                    registros.append(registro)
                for campo in (
                    "arquivoPacote",
                    "arquivoOriginal",
                    "caminhoInternoPacote",
                    "categoriaInterna",
                ):
                    if item.get(campo) is not None:
                        registro[campo] = item[campo]
                registro["docling"] = {
                    "status": "concluida",
                    "caminho": item.get("caminhoMarkdown"),
                    "quantidadeCaracteres": item.get("quantidadeCaracteres"),
                    "executadoEm": item.get("atualizadoEm"),
                }
                registro["textoAtivo"] = {
                    "origem": "docling",
                    "caminho": item.get("caminhoMarkdown"),
                }
                break


def carregar_estados_docling_do_periodo(
    periodo: PeriodoColeta,
    nome_lote: str | None = None,
) -> tuple[dict[str, Any], list[Path]]:
    """Carrega checkpoints Docling mensais ou de um lote especifico.

    O Docling é executado no Colab por lote, mas os chunks são gerados uma vez
    por período no VS Code. Sem ``nome_lote``, a função reúne todos os
    documentos concluídos do mês. Com um lote, produz um JSONL isolado e
    portável para aquele lote.
    """

    pasta = Path("data/checkpoints/extracao") / str(periodo.ano)
    arquivos = []
    if nome_lote:
        if not nome_lote.startswith("lote_"):
            raise ValueError("nome_lote deve seguir o formato 'lote_001'.")
        caminho_lote = (
            pasta / f"estado_docling_{periodo.identificador}_{nome_lote}.json"
        )
        if caminho_lote.is_file():
            arquivos.append(caminho_lote)
    else:
        legado = pasta / f"estado_docling_{periodo.identificador}.json"
        if legado.is_file():
            arquivos.append(legado)
        arquivos.extend(sorted(
            pasta.glob(f"estado_docling_{periodo.identificador}_lote_*.json")
        ))

    estado_combinado: dict[str, Any] = {"documentos": {}}
    for caminho in arquivos:
        estado = carregar_json(caminho, padrao={})
        if not isinstance(estado, dict):
            continue
        documentos = estado.get("documentos")
        if isinstance(documentos, dict):
            estado_combinado["documentos"].update(documentos)
    return estado_combinado, arquivos


def _indice_por_controle(registros: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        str(registro["numeroControlePNCP"]): registro
        for registro in registros
        if isinstance(registro, dict) and registro.get("numeroControlePNCP")
    }


def _contar_textos_processaveis(
    manifestos: dict[str, list[dict[str, Any]]],
    indices: dict[str, dict[str, dict[str, Any]]],
    limite_textos: int | None,
) -> int:
    """Conta Markdown elegível antes de iniciar o chunking.

    A contagem permite exibir progresso real sem carregar o conteúdo dos
    arquivos em memória duas vezes. Textos ausentes ou sem registro
    estruturado não entram no total porque não podem gerar chunks.
    """

    total = 0
    for origem, manifesto in manifestos.items():
        for entidade_manifesto in manifesto:
            if not isinstance(entidade_manifesto, dict):
                continue
            controle = str(entidade_manifesto.get("numeroControlePNCP") or "")
            if controle not in indices.get(origem, {}):
                continue
            for documento in entidade_manifesto.get("documentos") or []:
                if not isinstance(documento, dict):
                    continue
                for registro_texto in documento.get("processamentoTexto") or []:
                    ativo = registro_texto.get("textoAtivo") or {}
                    caminho = Path(str(ativo.get("caminho") or ""))
                    if not caminho.is_file():
                        continue
                    total += 1
                    if limite_textos is not None and total >= limite_textos:
                        return total
    return total


def gerar_chunks_do_periodo(
    manifestos: dict[str, list[dict[str, Any]]],
    bases: dict[str, list[dict[str, Any]]],
    arquivo_saida: Path,
    arquivo_relatorio: Path,
    limite_textos: int | None,
    usar_langchain: bool,
    progresso: Callable[[int, int], None] | None = None,
) -> dict[str, int]:
    """Gera um JSONL reproduzivel sem alterar manifestos nem arquivos fonte."""

    raiz_projeto = Path.cwd()
    indices = {
        origem: _indice_por_controle(registros)
        for origem, registros in bases.items()
    }
    linhas: list[dict[str, Any]] = []
    resumo = {
        "textosLidos": 0,
        "chunksGerados": 0,
        "textosSemArquivo": 0,
        "textosSemRegistroEstruturado": 0,
    }
    total_textos = _contar_textos_processaveis(
        manifestos,
        indices,
        limite_textos,
    )
    if progresso is not None:
        progresso(0, total_textos)

    for origem, manifesto in manifestos.items():
        tipo_entidade = "contrato" if origem == "contratos" else "contratacao"
        for entidade_manifesto in manifesto:
            if not isinstance(entidade_manifesto, dict):
                continue

            controle = str(entidade_manifesto.get("numeroControlePNCP") or "")
            entidade = indices[origem].get(controle)
            if entidade is None:
                resumo["textosSemRegistroEstruturado"] += 1
                continue

            for documento in entidade_manifesto.get("documentos") or []:
                if not isinstance(documento, dict):
                    continue
                for registro_texto in documento.get("processamentoTexto") or []:
                    ativo = registro_texto.get("textoAtivo") or {}
                    caminho_texto = Path(str(ativo.get("caminho") or ""))
                    if not caminho_texto.is_file():
                        if ativo:
                            resumo["textosSemArquivo"] += 1
                        continue
                    if limite_textos is not None and resumo["textosLidos"] >= limite_textos:
                        break

                    texto = caminho_texto.read_text(
                        encoding="utf-8", errors="replace")
                    configuracao = selecionar_configuracao(
                        documento.get("tipoDocumentoNome"),
                        caminho_texto,
                    )
                    metadata = montar_metadata_base(
                        entidade=entidade,
                        documento=documento,
                        registro_texto=registro_texto,
                        tipo_entidade=tipo_entidade,
                        caminho_relativo=caminho_relativo_portavel(
                            caminho_texto,
                            raiz_projeto,
                        ),
                    )

                    for indice, conteudo in enumerate(
                        gerar_chunks(
                            texto,
                            configuracao,
                            usar_langchain=usar_langchain,
                        )
                    ):
                        linhas.append(
                            {
                                "chunk_id": gerar_chunk_id(metadata, indice, conteudo),
                                "content": conteudo,
                                "metadata": {
                                    **metadata,
                                    "indiceChunk": indice,
                                    "configuracaoChunk": configuracao.nome,
                                },
                            }
                        )

                    resumo["textosLidos"] += 1
                    if progresso is not None:
                        progresso(resumo["textosLidos"], total_textos)

            if limite_textos is not None and resumo["textosLidos"] >= limite_textos:
                break

    arquivo_saida.parent.mkdir(parents=True, exist_ok=True)
    temporario = arquivo_saida.with_suffix(".tmp")
    with temporario.open("w", encoding="utf-8") as arquivo:
        for linha in linhas:
            arquivo.write(json.dumps(linha, ensure_ascii=False) + "\n")
    os.replace(temporario, arquivo_saida)

    resumo["chunksGerados"] = len(linhas)
    relatorio = {
        "geradoEm": datetime.now(timezone.utc).isoformat(),
        "arquivoChunks": str(arquivo_saida),
        "usarLangChain": usar_langchain,
        "resumo": resumo,
    }
    salvar_json(arquivo_relatorio, relatorio)
    return resumo


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Gera JSONL canônico de chunks a partir dos Markdown do Docling."
    )
    parser.add_argument("--periodo", help="Período YYYY/MM.")
    parser.add_argument(
        "--lote",
        help="Nome do lote, por exemplo lote_001. Omitir combina o mês inteiro.",
    )
    parser.add_argument("--versao", default="v1")
    parser.add_argument("--limite-textos", type=int)
    parser.add_argument("--sem-langchain", action="store_true")
    parser.add_argument(
        "--tipo",
        choices=["contratos", "contratacoes", "todos"],
        default="todos",
    )
    args = parser.parse_args()

    PERIODO = PeriodoColeta(ano=2026, mes=8)
    if args.periodo:
        ano, mes = args.periodo.replace("-", "/").split("/", maxsplit=1)
        PERIODO = PeriodoColeta(ano=int(ano), mes=int(mes))
    CAMINHOS = caminhos_do_periodo(PERIODO)

    # O JSONL é o artefato canônico: ele pode ser auditado, compartilhado e
    # importado no banco sem gerar chunks outra vez.
    VERSAO_CHUNKS = args.versao
    NOME_LOTE: str | None = args.lote
    LIMITE_TEXTOS: int | None = args.limite_textos
    # True usa RecursiveCharacterTextSplitter e MarkdownHeaderTextSplitter.
    # False preserva o divisor manual como contingencia temporaria.
    USAR_LANGCHAIN = not args.sem_langchain

    manifestos = {
        "contratos": materializar_manifesto_com_checkpoint(
            carregar_json(CAMINHOS["manifesto_contratos"], padrao=[]),
            carregar_checkpoint_documentos(
                CAMINHOS["checkpoint_documentos_contratos"]
            ),
        ),
        "contratacoes": materializar_manifesto_com_checkpoint(
            carregar_json(CAMINHOS["manifesto_contratacoes"], padrao=[]),
            carregar_checkpoint_documentos(
                CAMINHOS["checkpoint_documentos_contratacoes"]
            ),
        ),
    }
    if args.tipo in {"contratos", "contratacoes"}:
        manifestos = {args.tipo: manifestos[args.tipo]}

    # O checkpoint do Colab e' uma camada separada do manifesto original.
    # Aplicamos seus Markdown apenas em memoria para esta geracao de chunks.
    estado_docling, arquivos_estado = carregar_estados_docling_do_periodo(
        PERIODO,
        nome_lote=NOME_LOTE,
    )
    if NOME_LOTE and not arquivos_estado:
        raise FileNotFoundError(
            "Checkpoint Docling do lote não encontrado para "
            f"{PERIODO.identificador}/{NOME_LOTE}."
        )
    print(f"Checkpoints Docling encontrados: {len(arquivos_estado)}")
    aplicar_estado_docling(manifestos, estado_docling)
    bases = {
        "contratos": carregar_json(CAMINHOS["contratos"], padrao=[]),
        "contratacoes": carregar_json(CAMINHOS["contratacoes"], padrao=[]),
    }
    if args.tipo in {"contratos", "contratacoes"}:
        bases = {args.tipo: bases[args.tipo]}
    if not all(isinstance(valor, list) for valor in [*manifestos.values(), *bases.values()]):
        raise RuntimeError(
            "Manifestos e bases estruturadas devem conter listas JSON.")

    sufixo_lote = f"_{NOME_LOTE}" if NOME_LOTE else ""
    pasta_saida = Path(f"data/chunks/{VERSAO_CHUNKS}/{PERIODO.ano}")
    arquivo_saida = pasta_saida / (
        f"chunks_{PERIODO.identificador}{sufixo_lote}.jsonl"
    )
    arquivo_relatorio = pasta_saida / (
        f"relatorio_{PERIODO.identificador}{sufixo_lote}.json"
    )
    colunas_progresso = [
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TextColumn("{task.percentage:>5.1f}%"),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
    ]
    with Progress(*colunas_progresso) as progresso_visual:
        tarefa = progresso_visual.add_task("Gerando chunks", total=1)

        def atualizar_progresso(processados: int, total: int) -> None:
            if total == 0:
                progresso_visual.update(
                    tarefa,
                    description="Nenhum Markdown elegível",
                    total=1,
                    completed=1,
                )
                return
            progresso_visual.update(
                tarefa,
                total=total,
                completed=processados,
            )

        resumo = gerar_chunks_do_periodo(
            manifestos=manifestos,
            bases=bases,
            arquivo_saida=arquivo_saida,
            arquivo_relatorio=arquivo_relatorio,
            limite_textos=LIMITE_TEXTOS,
            usar_langchain=USAR_LANGCHAIN,
            progresso=atualizar_progresso,
        )
        if resumo["textosLidos"]:
            progresso_visual.update(
                tarefa,
                description="Chunks gerados",
            )
    print(f"Chunks gerados | {resumo}")
