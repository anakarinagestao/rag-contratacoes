"""
Testes unitários para validação de Guardrails (Escopo e P3.3).
"""

from src.guardrails import verificar_guardrails_pergunta, MENSAGEM_RECUSA_PADRAO


def test_guardrail_recusa_recomendacao_comercial():
    """Valida se perguntas de indicação de empresa (P3.3) são bloqueadas."""
    pergunta_invalida = "Qual empresa devo contratar para fornecer merenda escolar?"
    bloqueado, mensagem = verificar_guardrails_pergunta(pergunta_invalida)
    
    assert bloqueado is True
    assert mensagem == MENSAGEM_RECUSA_PADRAO


def test_guardrail_pergunta_valida():
    """Valida se perguntas sobre dados públicos passam normalmente."""
    pergunta_valida = "Qual foi o valor total gasto em licitações de saúde em 2023?"
    bloqueado, mensagem = verificar_guardrails_pergunta(pergunta_valida)
    
    assert bloqueado is False
    assert mensagem is None