"""Contrato versionado de metadados dos chunks PNCP.

O JSONL é a fonte compartilhável entre extração, persistência e indexação. Este
módulo mantém uma identidade de documento que não depende do computador que
executou o lote e permite representar arquivos internos de ZIP/RAR.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any


SCHEMA_CHUNKS_PNCP_V2 = "chunks_pncp_v2"


def _texto_portavel(valor: object) -> str | None:
    if not isinstance(valor, str) or not valor.strip():
        return None
    texto = valor.strip().replace("\\", "/")
    # Caminho absoluto não pode participar de identidade compartilhada: ele
    # muda entre VS Code, Colab e computadores da equipe.
    if texto.startswith("/") or re.match(r"^[A-Za-z]:/", texto):
        return None
    return texto.lstrip("./")


def classificar_categoria_interna(
    arquivo_original: str | None,
    titulo_documento: str | None,
) -> str:
    """Sugere um papel para recuperação; não substitui classificação humana."""

    texto = " ".join(valor for valor in (arquivo_original, titulo_documento) if valor)
    texto = texto.casefold()
    if "termo de refer" in texto:
        return "termo_referencia"
    if "edital" in texto:
        return "edital_principal"
    if "ata" in texto and "registro" in texto:
        return "ata_registro_preco"
    if "contrato" in texto:
        return "contrato_principal"
    if "anexo" in texto:
        return "anexo"
    return "documento_pncp"


def montar_identificador_documento_fonte(metadata: dict[str, Any]) -> str:
    """Monta a chave canônica de um arquivo físico dentro do documento PNCP.

    Para um PDF direto, ``caminhoRelativo`` diferencia o arquivo de origem.
    Para um pacote, ``caminhoInternoPacote`` é preferido porque é independente
    de onde o ZIP foi extraído. Enquanto manifestos antigos não possuírem essa
    informação, o caminho relativo portável continua como fallback.
    """

    tipo = str(metadata.get("tipoEntidade") or "")
    controle = str(metadata.get("numeroControlePNCP") or "")
    sequencial = str(metadata.get("sequencialDocumento") or "")
    pacote = _texto_portavel(metadata.get("arquivoPacote")) or "direto"
    artefato = (
        _texto_portavel(metadata.get("caminhoInternoPacote"))
        or _texto_portavel(metadata.get("caminhoRelativo"))
        or _texto_portavel(metadata.get("arquivoOriginal"))
        or "sem_arquivo"
    )
    return "|".join((tipo, controle, sequencial, pacote, artefato))


def normalizar_metadata_pncp(metadata: dict[str, Any]) -> dict[str, Any]:
    """Aplica o contrato v2 sem remover os campos legados úteis ao projeto."""

    normalizado = dict(metadata)
    caminho_relativo = _texto_portavel(normalizado.get("caminhoRelativo"))
    if caminho_relativo is not None:
        normalizado["caminhoRelativo"] = caminho_relativo
    else:
        normalizado.pop("caminhoRelativo", None)

    arquivo_original = _texto_portavel(normalizado.get("arquivoOriginal"))
    if arquivo_original is None and caminho_relativo:
        arquivo_original = PurePosixPath(caminho_relativo).name
    if arquivo_original:
        normalizado["arquivoOriginal"] = arquivo_original

    for campo in ("arquivoPacote", "caminhoInternoPacote"):
        valor = _texto_portavel(normalizado.get(campo))
        if valor is None:
            normalizado.pop(campo, None)
        else:
            normalizado[campo] = valor

    normalizado["schemaVersion"] = SCHEMA_CHUNKS_PNCP_V2
    normalizado["categoriaInterna"] = str(
        normalizado.get("categoriaInterna")
        or classificar_categoria_interna(
            arquivo_original,
            normalizado.get("tituloDocumento"),
        )
    )
    normalizado["identificadorDocumentoFonte"] = montar_identificador_documento_fonte(
        normalizado
    )
    return {chave: valor for chave, valor in normalizado.items() if valor is not None}
