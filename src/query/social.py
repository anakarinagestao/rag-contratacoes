"""Interações sociais determinísticas do assistente PNCP/RJ.

Somente enunciados inteiros e inequívocos são tratados aqui. Uma saudação
acompanhada de pergunta de domínio continua no Analyzer normal.
"""

from __future__ import annotations

import re
import unicodedata


def _normalizar(texto: str) -> str:
    sem_acentos = "".join(
        caractere
        for caractere in unicodedata.normalize("NFKD", texto)
        if not unicodedata.combining(caractere)
    )
    return " ".join(re.sub(r"[^a-zA-Z0-9]+", " ", sem_acentos.lower()).split())


_SAUDACOES = {
    "oi",
    "ola",
    "bom dia",
    "boa tarde",
    "boa noite",
    "e ai",
    "oi bom dia",
    "oi boa tarde",
    "oi boa noite",
    "ola bom dia",
    "ola boa tarde",
    "ola boa noite",
    "tudo bem",
}

_AGRADECIMENTOS = {
    "obrigado",
    "obrigada",
    "muito obrigado",
    "muito obrigada",
    "valeu",
    "agradecido",
    "agradecida",
}

_AJUDA = {
    "ajuda",
    "me ajude",
    "pode me ajudar",
    "como voce pode me ajudar",
    "o que voce faz",
    "quem e voce",
}

_DESPEDIDAS = {"tchau", "ate logo", "ate mais"}


def responder_interacao_social(pergunta: str) -> str | None:
    """Retorna resposta social segura ou ``None`` para seguir ao Analyzer."""

    normalizada = _normalizar(pergunta)
    if normalizada in _SAUDACOES:
        return (
            "Olá! Sou o assistente PNCP/RJ. Posso ajudar com perguntas sobre "
            "contratações, contratos, itens, resultados homologados, atas de "
            "registro de preços e Planos de Contratações Anuais. Por exemplo: "
            '"Quantos contratos foram assinados em 2025?"'
        )
    if normalizada in _AGRADECIMENTOS:
        return (
            "Por nada! Se quiser, faça outra pergunta sobre os dados do PNCP/RJ."
        )
    if normalizada in _AJUDA:
        return (
            "Posso consultar dados do PNCP/RJ em linguagem natural: contagens, "
            "valores, modalidades, órgãos, contratos, itens, resultados "
            "homologados, atas e PCAs. Também posso manter filtros em perguntas "
            "de continuação, como: \"E somente os estaduais?\""
        )
    if normalizada in _DESPEDIDAS:
        return "Até mais! Quando precisar, posso ajudar a consultar o PNCP/RJ."
    return None
