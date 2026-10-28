from langchain_text_splitters import RecursiveCharacterTextSplitter


"""Funcoes deterministicas para gerar chunks portaveis da base PNCP."""

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.base_estruturada.metadata_contract import normalizar_metadata_pncp


@dataclass(frozen=True)
class ConfiguracaoChunk:
    """Tamanhos em caracteres; aproximadamente 4 caracteres por token."""

    nome: str
    tamanho_maximo: int
    sobreposicao: int
    tamanho_minimo: int


# Cerca de 700-900 tokens por chunk e 100-125 tokens de sobreposicao. O perfil
# Markdown preserva titulos, que sao especialmente importantes em editais e
# contratos. Estes valores ficam centralizados para ajuste posterior.
CONFIGURACAO_MARKDOWN = ConfiguracaoChunk(
    nome="markdown_juridico_v1",
    tamanho_maximo=3_400,
    sobreposicao=450,
    tamanho_minimo=250,
)
CONFIGURACAO_TEXTO = ConfiguracaoChunk(
    nome="texto_juridico_v1",
    tamanho_maximo=3_200,
    sobreposicao=400,
    tamanho_minimo=250,
)
CONFIGURACAO_DOCUMENTO_CURTO = ConfiguracaoChunk(
    nome="documento_administrativo_curto_v1",
    tamanho_maximo=1_600,
    sobreposicao=180,
    tamanho_minimo=150,
)

TIPOS_DOCUMENTO_CURTO = {
    "Nota de Empenho",
    "Aviso de Contratação Direta",
    "Ato que autoriza a Contratação Direta",
}


def selecionar_configuracao(
    tipo_documento: str | None,
    caminho_texto: Path,
) -> ConfiguracaoChunk:
    if (tipo_documento or "") in TIPOS_DOCUMENTO_CURTO:
        return CONFIGURACAO_DOCUMENTO_CURTO
    if caminho_texto.suffix.lower() in {".md", ".markdown"}:
        return CONFIGURACAO_MARKDOWN
    return CONFIGURACAO_TEXTO


def _normalizar_texto(texto: str) -> str:
    texto = texto.replace("\r\n", "\n").replace("\r", "\n")
    texto = re.sub(r"[ \t]+\n", "\n", texto)
    texto = re.sub(r"\n{3,}", "\n\n", texto)
    return texto.strip()


def _dividir_texto(texto: str, configuracao: ConfiguracaoChunk) -> list[str]:
    """Divide por paragrafos/frases antes de recorrer a corte por tamanho."""

    texto = _normalizar_texto(texto)
    if len(texto) <= configuracao.tamanho_maximo:
        return [texto] if texto else []

    partes: list[str] = []
    restante = texto
    separadores = ("\n\n", "\n", ". ", " ")

    while len(restante) > configuracao.tamanho_maximo:
        janela = restante[: configuracao.tamanho_maximo]
        posicao_corte = 0
        for separador in separadores:
            posicao = janela.rfind(separador)
            if posicao > configuracao.tamanho_minimo:
                posicao_corte = posicao + len(separador)
                break

        if posicao_corte == 0:
            posicao_corte = configuracao.tamanho_maximo

        parte = restante[:posicao_corte].strip()
        if parte:
            partes.append(parte)

        inicio_sobreposicao = max(0, posicao_corte - configuracao.sobreposicao)
        restante = restante[inicio_sobreposicao:].strip()

    if restante:
        partes.append(restante)
    return partes


def dividir_markdown(texto: str, configuracao: ConfiguracaoChunk) -> list[str]:
    """Preserva o ultimo titulo Markdown como contexto de cada fragmento."""

    linhas = _normalizar_texto(texto).splitlines()
    secoes: list[tuple[str | None, list[str]]] = []
    titulo_atual: str | None = None
    linhas_secao: list[str] = []

    for linha in linhas:
        if re.match(r"^#{1,6}\s+\S", linha):
            if linhas_secao:
                secoes.append((titulo_atual, linhas_secao))
            titulo_atual = linha.strip()
            linhas_secao = []
        else:
            linhas_secao.append(linha)

    if linhas_secao or titulo_atual:
        secoes.append((titulo_atual, linhas_secao))

    chunks: list[str] = []
    for titulo, conteudo_linhas in secoes:
        conteudo = "\n".join(conteudo_linhas).strip()
        prefixo = f"{titulo}\n\n" if titulo else ""
        tamanho_disponivel = configuracao.tamanho_maximo - len(prefixo)
        config_secao = ConfiguracaoChunk(
            nome=configuracao.nome,
            tamanho_maximo=max(tamanho_disponivel,
                               configuracao.tamanho_minimo),
            sobreposicao=configuracao.sobreposicao,
            tamanho_minimo=configuracao.tamanho_minimo,
        )
        partes = _dividir_texto(conteudo, config_secao) if conteudo else [""]
        chunks.extend(f"{prefixo}{parte}".strip() for parte in partes)

    return [chunk for chunk in chunks if len(chunk) >= configuracao.tamanho_minimo]


def _importar_splitters_langchain() -> tuple[Any, Any]:
    """Importacao tardia para nao exigir LangChain nas demais pipelines."""

    try:
        from langchain_text_splitters import (
            MarkdownHeaderTextSplitter,
            RecursiveCharacterTextSplitter,
        )
    except ImportError as exc:
        raise RuntimeError(
            "LangChain nao esta instalado no ambiente atual. Instale as "
            "dependencias de RAG antes de usar USAR_LANGCHAIN=True."
        ) from exc
    return MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter


def dividir_texto_langchain(
    texto: str,
    configuracao: ConfiguracaoChunk,
) -> list[str]:
    """Usa o divisor recursivo padrao do LangChain para texto simples."""

    _, RecursiveCharacterTextSplitter = _importar_splitters_langchain()
    splitter = RecursiveCharacterTextSplitter(
        separators=["\n\n", "\n", ". ", " ", ""],
        chunk_size=configuracao.tamanho_maximo,
        chunk_overlap=configuracao.sobreposicao,
        length_function=len,
        add_start_index=True,
    )
    return [
        documento.page_content.strip()
        for documento in splitter.create_documents([_normalizar_texto(texto)])
        if len(documento.page_content.strip()) >= configuracao.tamanho_minimo
    ]


def dividir_markdown_langchain(
    texto: str,
    configuracao: ConfiguracaoChunk,
) -> list[str]:
    """Separa titulos Markdown e limita cada secao com divisor recursivo."""

    MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter = (
        _importar_splitters_langchain()
    )
    separador_cabecalhos = MarkdownHeaderTextSplitter(
        headers_to_split_on=[
            ("#", "cabecalho_1"),
            ("##", "cabecalho_2"),
            ("###", "cabecalho_3"),
        ],
        strip_headers=False,
    )
    separador_recursivo = RecursiveCharacterTextSplitter(
        separators=["\n\n", "\n", ". ", " ", ""],
        chunk_size=configuracao.tamanho_maximo,
        chunk_overlap=configuracao.sobreposicao,
        length_function=len,
        add_start_index=True,
    )

    chunks: list[str] = []
    for secao in separador_cabecalhos.split_text(_normalizar_texto(texto)):
        for documento in separador_recursivo.create_documents(
            [secao.page_content],
            metadatas=[secao.metadata],
        ):
            conteudo = documento.page_content.strip()
            if len(conteudo) >= configuracao.tamanho_minimo:
                chunks.append(conteudo)
    return chunks


def gerar_chunks(
    texto: str,
    configuracao: ConfiguracaoChunk,
    usar_langchain: bool = True,
) -> list[str]:
    """Gera chunks por LangChain ou pelo fallback manual configuravel."""

    if usar_langchain:
        if configuracao == CONFIGURACAO_MARKDOWN:
            return dividir_markdown_langchain(texto, configuracao)
        return dividir_texto_langchain(texto, configuracao)

    if configuracao == CONFIGURACAO_MARKDOWN:
        return dividir_markdown(texto, configuracao)
    return _dividir_texto(texto, configuracao)


def caminho_relativo_portavel(caminho: Path, raiz_projeto: Path) -> str:
    """Nunca grava caminho absoluto local no metadata compartilhavel."""

    try:
        return caminho.resolve().relative_to(raiz_projeto.resolve()).as_posix()
    except ValueError:
        return caminho.name


def montar_metadata_base(
    entidade: dict[str, Any],
    documento: dict[str, Any],
    registro_texto: dict[str, Any],
    tipo_entidade: str,
    caminho_relativo: str,
) -> dict[str, Any]:
    """Monta somente metadados portaveis e uteis a filtro/rastreabilidade."""

    orgao = entidade.get("orgaoEntidade") or {}
    unidade = entidade.get("unidadeOrgao") or {}
    ano = entidade.get(
        "anoContrato") if tipo_entidade == "contrato" else entidade.get("anoCompra")
    sequencial = (
        entidade.get("sequencialContrato")
        if tipo_entidade == "contrato"
        else entidade.get("sequencialCompra")
    )

    metadata: dict[str, Any] = {
        # O contrato v2 é aplicado ao final, preservando chaves já consumidas
        # pelos lotes existentes e acrescentando a identidade de arquivo.
        "schemaVersion": "chunks_pncp_v2",
        "baseId": "pncp_rj_estruturada",
        "tipoEntidade": tipo_entidade,
        "numeroControlePNCP": entidade.get("numeroControlePNCP"),
        "cnpjOrgao": orgao.get("cnpj"),
        "orgao": orgao.get("razaoSocial"),
        "unidadeOrgao": unidade.get("nomeUnidade"),
        "uf": unidade.get("ufSigla"),
        "municipio": unidade.get("municipioNome"),
        "ano": ano,
        "sequencial": sequencial,
        "sequencialDocumento": documento.get("sequencialDocumento"),
        "tipoDocumento": documento.get("tipoDocumentoNome"),
        "tituloDocumento": documento.get("titulo"),
        "urlFontePncp": documento.get("url") or documento.get("uri"),
        "caminhoRelativo": caminho_relativo,
        "tipoFonte": registro_texto.get("tipoFonte"),
        "extrator": (registro_texto.get("textoAtivo") or {}).get("origem"),
        "arquivoPacote": registro_texto.get("arquivoPacote"),
        "arquivoOriginal": registro_texto.get("arquivoOriginal"),
        "caminhoInternoPacote": registro_texto.get("caminhoInternoPacote"),
        "categoriaInterna": registro_texto.get("categoriaInterna"),
    }

    if tipo_entidade == "contrato":
        metadata.update(
            {
                "fornecedor": entidade.get("nomeRazaoSocialFornecedor"),
                "objeto": entidade.get("objetoContrato"),
                "valorGlobal": entidade.get("valorGlobal"),
                "dataAssinatura": entidade.get("dataAssinatura"),
                "dataVigenciaInicio": entidade.get("dataVigenciaInicio"),
                "dataVigenciaFim": entidade.get("dataVigenciaFim"),
            }
        )
    else:
        metadata.update(
            {
                "objeto": entidade.get("objetoCompra"),
                "modalidade": entidade.get("modalidadeNome"),
                "modalidadeId": entidade.get("modalidadeId"),
                "valorTotalEstimado": entidade.get("valorTotalEstimado"),
                "dataAberturaProposta": entidade.get("dataAberturaProposta"),
                "dataEncerramentoProposta": entidade.get("dataEncerramentoProposta"),
                "situacao": entidade.get("situacaoCompraNome"),
            }
        )

    return normalizar_metadata_pncp(metadata)


def gerar_chunk_id(metadata: dict[str, Any], indice: int, conteudo: str) -> str:
    # Só substituímos a chave legada pelo identificador canônico quando a
    # entrada do pacote é conhecida. Assim, reprocessar os JSONLs v1 atuais
    # mantém os mesmos IDs; arquivos internos de ZIP futuros ganham identidade
    # própria e estável entre o worker adaptativo e os lotes.
    identidade_fonte = metadata.get("caminhoRelativo", "")
    if metadata.get("caminhoInternoPacote"):
        identidade_fonte = metadata.get(
            "identificadorDocumentoFonte",
            identidade_fonte,
        )
    chave = "|".join(
        [
            str(metadata.get("numeroControlePNCP", "")),
            str(metadata.get("sequencialDocumento", "")),
            str(identidade_fonte),
            str(indice),
            conteudo,
        ]
    )
    return hashlib.sha256(chave.encode("utf-8")).hexdigest()[:24]
