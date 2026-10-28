from src.query.social import responder_interacao_social


def test_responde_saudacoes_sem_consultar_dados():
    resposta = responder_interacao_social("Olá, bom dia!")

    assert resposta is not None
    assert "assistente PNCP/RJ" in resposta
    assert "Quantos contratos" in resposta


def test_responde_ajuda_agradecimento_e_despedida():
    assert "linguagem natural" in responder_interacao_social("Pode me ajudar?")
    assert "Por nada" in responder_interacao_social("Muito obrigada!")
    assert "Até mais" in responder_interacao_social("tchau")


def test_saudacao_com_pergunta_de_dominio_segue_para_analyzer():
    assert (
        responder_interacao_social("Bom dia, quantos contratos existem em 2025?")
        is None
    )


def test_texto_fora_da_allowlist_segue_para_analyzer():
    assert responder_interacao_social("Conte os PCAs de 2024") is None
