from urllib.parse import urlsplit, urlunsplit, urlparse
from pathlib import Path
import re
import unicodedata
from typing import Any


# Referencia para a futura etapa de extracao textual e FAISS. Estes documentos
# costumam ter mais contexto semantico sobre objeto, regras, obrigacoes e
# decisoes. A lista nao controla o download enquanto
# SOMENTE_TIPOS_PRIORITARIOS estiver False no pipeline de download.
TIPOS_PRIORITARIOS_RAG = {
    "Edital",
    "Termo de Referência",
    "Estudo Técnico Preliminar",
    "Minuta do Contrato",
    "Contrato",
    "Termo Aditivo",
    "Termo de Apostilamento",
    "Ata de Registro de Preços",
    "Termo de Rescisão",
    "Relatório Final de Contrato",
    "Documento de Formalização da Demanda",
    "Mapa de Riscos",
    "Termo/Ato de Homologação",
    "Aviso de Contratação Direta",
    "Ato que autoriza a Contratação Direta",
}

# Referencia para a futura base estruturada. Nota de Empenho entra aqui porque
# normalmente oferece campos financeiros e administrativos importantes, mas
# tende a ter pouco texto para busca semantica. Ainda nao ha codigo usando
# esta lista: ela e' somente uma decisao de organizacao para a proxima etapa.
TIPOS_PRIORITARIOS_ESTRUTURADOS = {
    "Nota de Empenho",
    "Contrato",
    "Termo Aditivo",
    "Termo de Apostilamento",
    "Termo de Rescisão",
    "Ata de Registro de Preços",
    "Termo/Ato de Homologação",
    "Ato que autoriza a Contratação Direta",
}

# Compatibilidade com a funcao de filtro ja existente. Quando o filtro for
# ativado no futuro, ele usara a selecao semantica/RAG por padrao.
TIPOS_PRIORITARIOS = TIPOS_PRIORITARIOS_RAG

# Recorte inicial para validar extracao, qualidade do Markdown e chunking.
# Esta lista nao substitui TIPOS_PRIORITARIOS_RAG; ela e' usada somente quando
# uma pipeline informa explicitamente ``tipos_permitidos``.
TIPOS_TESTE_EXTRACAO = {
    "Contrato",
    "Edital",
    "Termo de Referência",
    "Ata de Registro de Preços",
}


def normalizar_tipo_documento(tipo: str | None) -> str:
    """Compara tipos sem depender de acento, caixa ou espacos extras."""

    texto = unicodedata.normalize("NFKD", str(tipo or ""))
    texto = "".join(caractere for caractere in texto if not unicodedata.combining(caractere))
    return " ".join(texto.casefold().split())


def url_pura(url: str) -> str:
    """
    Remove formatos Markdown e limpa portas numéricas inválidas (como :46790)
    das URLs retornadas pela API do PNCP para evitar erros de timeout.
    """
    if not url:
        return ""

    url = url.strip()

    # 1. Trata se a URL vier encapsulada em formato Markdown [Texto](http://...)
    if url.startswith("[") and "](" in url and url.endswith(")"):
        url = url.rsplit("](", 1)[1][:-1]

    # 2. Desestrutura a URL para remover a porta do host
    partes = urlsplit(url)
    if not partes.hostname:
        return url

    # Reconstroi a URL sem a porta (partes.hostname ignora a porta automaticamente)
    url_limpa = urlunsplit((
        partes.scheme,
        partes.hostname,  # <-- Remove a porta de forma nativa e segura
        partes.path,
        partes.query,
        partes.fragment
    ))

    return url_limpa


def nome_seguro(texto: str) -> str:
    return re.sub(r'[<>:"/\\|?*]', "-", texto)


def extensao_por_content_type(
        content_type: str,
        url: str
) -> str:
    content_type = content_type.lower().split(";")[0].strip()

    extensoes = {
        "application/pdf": ".pdf",
        "application/zip": ".zip",
        "application/x-rar-compressed": ".rar",
        "application/vnd.rar": ".rar",
        "application/msword": ".doc",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
        "application/vnd.oasis.opendocument.text": ".odt",
        "image/jpeg": ".jpg",
        "image/png": ".png",
    }

    if content_type in extensoes:
        return extensoes[content_type]

    extensao_url = Path(urlparse(url).path).suffix

    if extensao_url:
        return extensao_url.lower()

    return ".bin"


def obter_tipo_documento(
    documento: dict[str, Any],
    tipos_por_id: dict[int, str],
) -> str | None:
    """
    Obtém o tipo do documento considerando as diferentes
    estruturas retornadas pelo PNCP.
    """

    # Algumas respostas já trazem o nome do tipo
    tipo_nome = documento.get("tipoDocumentoNome")
    if tipo_nome:
        return str(tipo_nome).strip()

    # Outras podem trazer somente o ID
    tipo_id = documento.get("tipoDocumentoId")

    if tipo_id is not None:
        return tipos_por_id.get(int(tipo_id))

    return None


def deve_baixar_documento(
    documento: dict[str, Any],
    tipos_por_id: dict[int, str],
    tipos_permitidos: set[str] | None = None,
) -> bool:

    tipo = obter_tipo_documento(
        documento,
        tipos_por_id
    )

    if not tipo:
        return False

    tipos = tipos_permitidos if tipos_permitidos is not None else TIPOS_PRIORITARIOS
    tipos_normalizados = {normalizar_tipo_documento(item) for item in tipos}
    return normalizar_tipo_documento(tipo) in tipos_normalizados
