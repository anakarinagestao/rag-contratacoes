"""Compatibilidade explícita de identidades, sem reescrever metadados."""

import re


_QWEN_ECOA_1024 = (
    "ecoa", "xagi/qwen3-embedding:0.6b-q4_k_m", 1024
)
_KNOWN_ALIASES = {
    "xagi/qwen3-embedding:0.6b-q4_k_m": _QWEN_ECOA_1024,
    "ecoa/qwen3-embedding:0.6b-q4_k_m": _QWEN_ECOA_1024,
}


def identidades_embedding_compativeis(
    registrada: str | None, esperada: str | None, dimensao: int | None
) -> bool:
    """Aceita aliases conhecidos apenas com dimensão real comprovada.

    Nomes opacos desconhecidos conservam somente a igualdade histórica.
    Identidades qualificadas devem concordar com a dimensão do índice.
    """
    def identidade(valor: str | None):
        if valor in _KNOWN_ALIASES:
            return _KNOWN_ALIASES[valor]
        match = re.fullmatch(r"(ecoa|openai):(.+):d([1-9][0-9]*)", valor or "")
        if match:
            return match[1], match[2], int(match[3])
        return None

    origem, destino = identidade(registrada), identidade(esperada)
    if origem is not None or destino is not None:
        return origem is not None and origem == destino and origem[2] == dimensao
    return bool(registrada) and registrada == esperada
