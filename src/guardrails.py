"""
Módulo de Verificação de Guardrails e Regras de Escopo.
Intercepta perguntas fora do escopo do RAG (como recomendações comerciais - P3.3).
"""

import re

from src.prompts import SYSTEM_PROMPT_BASE

# Termos que indicam solicitação de recomendação comercial ou escolha de empresa
TERMOS_RECUSA_COMERCIAL = [
    "qual empresa devo contratar",
    "qual empresa contratar",
    "me indique uma empresa",
    "me recomende uma empresa",
    "qual o melhor fornecedor para contratar",
    "qual marca devo comprar",
    "indique um fornecedor",
]

MENSAGEM_RECUSA_PADRAO = (
    "Como assistente de dados públicos do Estado do Rio de Janeiro, não posso "
    "recomendar ou indicar empresas/fornecedores específicos. Posso apenas "
    "fornecer dados históricos de contratações, licitações ativas e estatísticas do setor público."
)

# Pedidos de recomendação escritos de outras formas ("melhor empresa", "qual
# fornecedor devo escolher"). Exigem um verbo ou adjetivo de recomendação junto
# de empresa/fornecedor/marca, para não bloquear perguntas de dados como
# "qual fornecedor mais recebeu em 2025?".
_PADROES_RECUSA_COMERCIAL = [
    re.compile(
        r"\b(melhor|melhores|recomend\w*|indiqu\w*|sugir\w*|sugest\w*)\b"
        r".{0,40}\b(empresas?|fornecedor(?:es)?|marcas?)\b"
    ),
    re.compile(
        r"\b(empresas?|fornecedor(?:es)?|marcas?)\b"
        r".{0,40}\b(devo|deveria|posso|vale a pena)\b"
        r".{0,25}\b(contratar|escolher|comprar)\b"
    ),
]

# Vocabulário do domínio: se nenhum termo aparece, a pergunta não é sobre
# contratações públicas nem legislação de licitações.
_TERMOS_DO_DOMINIO = (
    "contrat", "licit", "edital", "editais", "pncp", "lei ", "leis ", "decreto",
    "ata ", "atas ", "registro de preço", "pca", "plano de contrat", "pregão",
    "pregao", "dispensa", "inexigib", "órgão", "orgao", "fornecedor", "município",
    "municipio", "prefeitura", "estado do rio", " rj", "valor", "modalidade",
    "homolog", "adjudic", "item", "itens", "aquisi", "compra", "obra", "serviço",
    "servico", "artigo", "art.", "sanção", "sancao", "impugna", "recurso",
    "termo de referência", "termo de referencia", "habilitação", "habilitacao",
    "proposta", "empenho", "unidade", "secretaria",
)

MENSAGEM_FORA_DO_ESCOPO = (
    "Essa pergunta está fora do escopo deste assistente. Posso ajudar com "
    "contratações públicas do Estado do Rio de Janeiro (contratos, atas, PCA, "
    "valores e modalidades) e com a legislação de licitações, como a Lei "
    "14.133/2021. Tente, por exemplo: \"Quantas contratações existem por "
    "modalidade em 2026?\""
)


def verificar_guardrails_pergunta(pergunta: str) -> tuple[bool, str | None]:
    """
    Verifica se a pergunta viola algum guardrail de escopo antes de acionar a LLM.
    
    Retorna:
        tuple[bool, str | None]: 
            - (True, mensagem_de_recusa) se a pergunta for bloqueada.
            - (False, None) se a pergunta for válida e puder prosseguir.
    """
    pergunta_lower = pergunta.lower().strip()
    
    for termo in TERMOS_RECUSA_COMERCIAL:
        if termo in pergunta_lower:
            return True, MENSAGEM_RECUSA_PADRAO

    for padrao in _PADROES_RECUSA_COMERCIAL:
        if padrao.search(pergunta_lower):
            return True, MENSAGEM_RECUSA_PADRAO

    return False, None


def pergunta_fora_do_dominio(pergunta: str) -> bool:
    """Indica pergunta sem nenhum termo de contratações públicas ou licitações.

    Só deve ser usada quando o Query Analyzer já falhou: uma pergunta de
    domínio com plano inválido continua sendo um erro explicável, e não uma
    recusa por escopo.
    """

    texto = f" {pergunta.lower().strip()} "
    return not any(termo in texto for termo in _TERMOS_DO_DOMINIO)