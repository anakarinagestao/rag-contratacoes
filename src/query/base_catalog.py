"""Mapeamento entre bases lógicas do Analyzer e ``base_id`` persistidos.

Para incluir uma base nova, basta registrar seu ``base_id`` neste catálogo.
O Analyzer continua trabalhando com nomes estáveis e compreensíveis, enquanto
o Retriever recebe os identificadores físicos que existem no banco.
"""

from __future__ import annotations

from collections.abc import Iterable

from .schemas import KnowledgeBase


BASE_IDS_POR_BASE: dict[KnowledgeBase, frozenset[str]] = {
    # Os documentos PNCP enriquecidos com metadados são a fonte documental
    # atual para perguntas sobre contratos e contratações.
    KnowledgeBase.ESTRUTURADA: frozenset({"pncp_rj_estruturada"}),
    KnowledgeBase.AGREGADA: frozenset({"pncp_rj_estruturada"}),
    # Base já usada pelos módulos normative/ e ingest/.
    KnowledgeBase.NORMATIVA: frozenset({"editais_normativo"}),
    # Reserva os IDs esperados para a base fictícia da equipe. Se outro nome
    # for adotado, basta incluí-lo aqui sem alterar o Retriever.
    KnowledgeBase.CONVERSACIONAL: frozenset({
        "conversacional",
        "conversacional_pncp",
    }),
}


def resolver_base_ids(
    bases: Iterable[KnowledgeBase] | None,
) -> set[str] | None:
    """Converte bases lógicas em IDs físicos permitidos.

    ``None`` preserva o comportamento legado de pesquisar todo o corpus,
    útil para chamadas internas antigas. Uma lista vazia nunca libera o
    corpus inteiro: devolve conjunto vazio.
    """

    if bases is None:
        return None
    base_ids: set[str] = set()
    for base in bases:
        base_ids.update(BASE_IDS_POR_BASE.get(base, frozenset()))
    return base_ids
