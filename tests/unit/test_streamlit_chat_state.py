from __future__ import annotations

import unittest

from app.streamlit_chat_state import (
    CHAT_CONVERSATIONS_KEY,
    conversa_ativa,
    criar_conversa,
    inicializar_chat,
    montar_requisicao,
    registrar_pergunta,
    registrar_resposta,
    selecionar_conversa,
)


class StreamlitChatStateTests(unittest.TestCase):
    def test_cria_alterna_e_isola_conversas_na_sessao(self):
        state = {}
        first_id = inicializar_chat(state)
        first = conversa_ativa(state)
        registrar_pergunta(first, "Quantos contratos existem em 2025?")
        registrar_resposta(first, 200, {"answer": "10", "analysis": {"route": "structured"}})

        second_id = criar_conversa(state)
        self.assertNotEqual(first_id, second_id)
        self.assertEqual(conversa_ativa(state)["messages"], [])
        selecionar_conversa(state, first_id)
        self.assertEqual(len(conversa_ativa(state)["messages"]), 2)
        self.assertEqual(len(state[CHAT_CONVERSATIONS_KEY]), 2)

    def test_requisicao_envia_somente_pergunta_top_k_e_ultimo_plano(self):
        conversation = {
            "context": {"route": "structured", "filters": {"ano": 2025}},
            "messages": [{"role": "user", "content": "conteúdo não reenviado"}],
        }
        body = montar_requisicao(conversation, "E somente os estaduais?", 5)

        self.assertEqual(body["pergunta"], "E somente os estaduais?")
        self.assertEqual(body["top_k"], 5)
        self.assertEqual(body["contexto"]["filters"]["ano"], 2025)
        self.assertNotIn("messages", body)

    def test_erro_nao_substitui_contexto_validado(self):
        conversation = {"messages": [], "context": {"route": "structured"}}
        registrar_resposta(conversation, 503, {"detail": "indisponível"})
        self.assertEqual(conversation["context"], {"route": "structured"})


if __name__ == "__main__":
    unittest.main()
