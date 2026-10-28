from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from sqlalchemy import func, select
from sqlalchemy.orm import Session

with patch.dict(os.environ, {"DATABASE_URL": "sqlite:///:memory:"}):
    from fastapi.testclient import TestClient

    from app.core.database import create_sqlite_engine, get_db, init_db
    from app.core.config import settings
    from app.core.logging import perf_stage
    from app.core.security import get_current_user
    from app.main import app
    from app.modules.query.model import Query, Response, ResponseSource
    from src.generate import MENSAGEM_SEM_EVIDENCIA, RagAnswer
    from src.faiss_errors import (
        FaissIndexCorruptError,
        FaissIndexIncompatibleError,
        FaissIndexMissingError,
    )
    from src.guardrails import MENSAGEM_RECUSA_PADRAO
    from src.retrieve import RankedChunk
    import src.generate as generate_module


class QueryRoutesTests(unittest.TestCase):
    def setUp(self):
        def test_db():
            yield object()

        def test_user():
            return object()

        app.dependency_overrides[get_db] = test_db
        app.dependency_overrides[get_current_user] = test_user
        self.addCleanup(app.dependency_overrides.clear)
        self.client = self.enterContext(TestClient(app))

    def test_generate_exige_usuario_autenticado(self):
        del app.dependency_overrides[get_current_user]

        response = self.client.post(
            "/query/generate",
            json={"pergunta": "qual e o objeto?"},
        )

        self.assertEqual(response.status_code, 401)

    def test_generate_request_valido_retorna_answer_e_sources(self):
        source = RankedChunk(
            chunk_id="chunk-1",
            content="Conteudo",
            rrf_score=0.032,
            bm25_rank=1,
            bm25_score=3.0,
            dense_rank=2,
            dense_score=0.8,
            metadata={"document_id": 10},
        )

        with patch(
            "app.modules.query.routes.gerar_resposta_rag",
            return_value=RagAnswer(answer="Resposta [1].", sources=[source]),
        ) as gerar:
            response = self.client.post(
                "/query/generate",
                json={
                    "pergunta": "qual e o objeto do contrato 09/2026?",
                    "top_k": 3,
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "answer": "Resposta [1].",
                "sources": [
                    {
                        "rank": 1,
                        "chunk_id": "chunk-1",
                        "relevance_score": 0.032,
                        "metadata": {"document_id": 10},
                    }
                ],
                "trace_id": None,
            },
        )
        gerar.assert_called_once()
        self.assertEqual(gerar.call_args.kwargs["pergunta"], "qual e o objeto do contrato 09/2026?")
        self.assertEqual(gerar.call_args.kwargs["top_k"], 3)
        self.assertIs(gerar.call_args.kwargs["persistir"], True)

    def test_generate_top_k_default_e_persistencia(self):
        with patch(
            "app.modules.query.routes.gerar_resposta_rag",
            return_value=RagAnswer(answer="Resposta.", sources=[]),
        ) as gerar:
            response = self.client.post(
                "/query/generate",
                json={"pergunta": "qual e o objeto?"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["answer"], "Resposta.")
        self.assertEqual(response.json()["sources"], [])
        self.assertEqual(gerar.call_args.kwargs["top_k"], 5)
        self.assertIs(gerar.call_args.kwargs["persistir"], True)

    def test_generate_sem_evidencias(self):
        with patch(
            "app.modules.query.routes.gerar_resposta_rag",
            return_value=RagAnswer(answer=MENSAGEM_SEM_EVIDENCIA, sources=[]),
        ):
            response = self.client.post(
                "/query/generate",
                json={"pergunta": "pergunta sem evidencias"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["answer"], MENSAGEM_SEM_EVIDENCIA)
        self.assertEqual(response.json()["sources"], [])

    def test_generate_faiss_indisponivel_retorna_503_seguro(self):
        cases = (
            (FaissIndexMissingError(), "faiss_index_missing"),
            (FaissIndexCorruptError(), "faiss_index_corrupt"),
            (FaissIndexIncompatibleError(), "faiss_index_incompatible"),
        )
        for error, expected_code in cases:
            with self.subTest(expected_code), patch(
                "app.modules.query.routes.gerar_resposta_rag",
                side_effect=error,
            ):
                response = self.client.post(
                    "/query/generate",
                    json={"pergunta": "pergunta sintetica"},
                )

            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json()["detail"]["code"], expected_code)
            self.assertEqual(
                response.json()["detail"]["message"],
                "Recuperação documental temporariamente indisponível.",
            )
            serialized = response.text
            self.assertNotIn("/home/", serialized)
            self.assertNotIn("Traceback", serialized)

    def test_generate_erro_inesperado_permanece_500(self):
        with TestClient(app, raise_server_exceptions=False) as client, patch(
            "app.modules.query.routes.gerar_resposta_rag",
            side_effect=AssertionError("segredo-nao-publicar"),
        ):
            response = client.post(
                "/query/generate",
                json={"pergunta": "pergunta sintetica"},
            )

        self.assertEqual(response.status_code, 500)
        self.assertNotIn("segredo-nao-publicar", response.text)

    def test_generate_503_preserva_observabilidade_minimizada(self):
        def unavailable(**_kwargs):
            with perf_stage("faiss_load"):
                raise FaissIndexMissingError()

        with patch.object(settings, "rag_perf_log_enabled", True), patch(
            "app.modules.query.routes.gerar_resposta_rag",
            side_effect=unavailable,
        ), self.assertLogs("uvicorn.error", level=logging.INFO) as captured:
            response = self.client.post(
                "/query/generate",
                json={"pergunta": "CPF sintetico 000.000.000-00"},
                headers={"Authorization": "Bearer segredo-sintetico"},
            )

        self.assertEqual(response.status_code, 503)
        messages = "\n".join(record.getMessage() for record in captured.records)
        self.assertIn("run_id=", messages)
        self.assertIn("etapa=faiss_load", messages)
        self.assertIn("status=erro", messages)
        self.assertIn("excecao=FaissIndexMissingError", messages)
        self.assertNotIn("000.000.000-00", messages)
        self.assertNotIn("segredo-sintetico", messages)

    def test_generate_guardrail_bloqueia_sem_retrieval_llm_ou_persistencia(self):
        engine = create_sqlite_engine("sqlite:///:memory:")
        init_db(engine)
        session = Session(engine)
        audit_tmp = self.enterContext(TemporaryDirectory())
        audit_dir = Path(audit_tmp)
        self.addCleanup(engine.dispose)
        self.addCleanup(session.close)

        def test_db():
            yield session

        def retrieval_nao_deve_rodar(**_kwargs):
            raise AssertionError("retrieval nao deveria ser chamado")

        def llm_nao_deve_rodar(_prompt: str) -> str:
            raise AssertionError("LLM nao deveria ser chamado")

        defaults_originais = generate_module.gerar_resposta_rag.__defaults__
        defaults_teste = list(defaults_originais)
        defaults_teste[2] = retrieval_nao_deve_rodar
        defaults_teste[3] = llm_nao_deve_rodar
        defaults_teste[7] = audit_dir
        generate_module.gerar_resposta_rag.__defaults__ = tuple(defaults_teste)
        self.addCleanup(
            setattr,
            generate_module.gerar_resposta_rag,
            "__defaults__",
            defaults_originais,
        )

        app.dependency_overrides[get_db] = test_db

        response = self.client.post(
            "/query/generate",
            json={
                "pergunta": "Qual empresa devo contratar para fornecer merenda escolar?"
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["answer"], MENSAGEM_RECUSA_PADRAO)
        self.assertEqual(payload["sources"], [])
        self.assertIsInstance(payload["trace_id"], str)
        self.assertEqual(session.scalar(select(func.count(Query.id))), 1)
        self.assertEqual(session.scalar(select(func.count(Response.id))), 1)
        self.assertEqual(session.scalar(select(func.count(ResponseSource.id))), 0)

        resposta_salva = session.scalar(select(Response))
        self.assertIsNotNone(resposta_salva)
        self.assertIs(resposta_salva.is_refusal, True)
        self.assertEqual(resposta_salva.answer, MENSAGEM_RECUSA_PADRAO)

        query_salva = session.scalar(select(Query))
        self.assertIsNotNone(query_salva)
        self.assertEqual(
            query_salva.text,
            "Qual empresa devo contratar para fornecer merenda escolar?",
        )

        traces = sorted(audit_dir.glob("*/*.json"))
        self.assertEqual(len(traces), 1)
        trace = json.loads(traces[0].read_text(encoding="utf-8"))
        self.assertEqual(trace["status"], "blocked")
        self.assertEqual(trace["stage"], "guardrail")
        self.assertIs(trace["guardrail"]["checked"], True)
        self.assertIs(trace["guardrail"]["triggered"], True)


if __name__ == "__main__":
    unittest.main()
