"""Normalização estável para comparações textuais no SQLite."""

from __future__ import annotations

import unicodedata


def normalizar_para_busca(valor: object | None) -> str:
    """Remove acentos e padroniza caixa sem alterar o dado persistido."""

    if valor is None:
        return ""
    decomposed = unicodedata.normalize("NFKD", str(valor))
    sem_acentos = "".join(
        caractere
        for caractere in decomposed
        if not unicodedata.combining(caractere)
    )
    return sem_acentos.casefold()
