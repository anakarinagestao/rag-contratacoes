from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

import requests
from streamlit.testing.v1 import AppTest

from app.streamlit_console import (
    _chamar_api,
    _exibir_resposta_chat,
    _mensagem_erro,
)


class StreamlitConsoleTests(unittest.TestCase):
    def test_chat_renderiza_e_administracao_permanece_acessivel(self):
        script = Path(__file__).resolve().parents[2] / "app" / "streamlit_console.py"
        app = AppTest.from_file(script, default_timeout=10).run()
        self.assertEqual(app.title[0].value, "Assistente PNCP/RJ")
        self.assertEqual(len(app.chat_input), 1)
        self.assertEqual(len(app.exception), 0)

        app.radio[0].set_value("Administração").run()
        self.assertEqual(app.title[0].value, "Administração e validação")
        self.assertEqual(len(app.exception), 0)

    def test_erros_publicos_sao_compreensiveis(self):
        self.assertIn("sessão expirou", _mensagem_erro(401, {}))
        self.assertIn("temporariamente indisponível", _mensagem_erro(503, {}))
        self.assertEqual(
            _mensagem_erro(
                422,
                {"detail": {"mensagem": "Informe a entidade ou o período."}},
            ),
            "Informe a entidade ou o período.",
        )

    def test_falha_de_conexao_nao_expoe_excecao_interna(self):
        with patch(
            "app.streamlit_console.requests.request",
            side_effect=requests.ConnectionError("/caminho/interno/secreto"),
        ):
            status, payload = _chamar_api(
                "POST", "http://127.0.0.1:8000/query/ask", token="token-ficticio"
            )
        self.assertIsNone(status)
        self.assertNotIn("/caminho/interno", str(payload))
        self.assertIn("FastAPI", str(payload))

    def test_resposta_202_exibe_tarefa_sem_tratar_como_erro(self):
        payload = {
            "processing_tasks": [
                {
                    "task_id": 11,
                    "status": "PENDENTE",
                    "status_url": "/documents/processing-tasks/11",
                }
            ]
        }
        with patch("app.streamlit_console.st") as streamlit:
            streamlit.expander.return_value.__enter__.return_value = streamlit
            _exibir_resposta_chat(202, payload)

        streamlit.error.assert_not_called()
        self.assertTrue(streamlit.info.called)


if __name__ == "__main__":
    unittest.main()
