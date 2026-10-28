"""Confere que o fluxo RAG fornece dados para a resposta fundamentada."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.modules.query.response_contracts import GroundedRagResponse, SourceEvidence
from src.generate import MENSAGEM_SEM_EVIDENCIA, gerar_resposta_rag
from src.retrieve import RankedChunk


def chunk() -> RankedChunk:
    return RankedChunk(
        chunk_id="chunk-evidencia-1",
        content="A proposta deve ser apresentada pelo sistema eletrônico.",
        rrf_score=0.05,
        bm25_rank=1,
        bm25_score=2.0,
        dense_rank=1,
        dense_score=0.9,
        metadata={
            "document_id": 1,
            "tituloDocumento": "Edital de teste",
            "urlFontePncp": "https://pncp.gov.br/documento-teste",
            "caminhoRelativo": "data/chunks/edital-teste.md",
        },
    )


class GroundedResponseReadinessTests(unittest.TestCase):
    def setUp(self):
        self.audit_tmp = self.enterContext(TemporaryDirectory())

    def test_resultado_rag_atual_fornece_origem_para_evidencia(self):
        result = gerar_resposta_rag(
            db=None,
            pergunta="Como a proposta deve ser apresentada?",
            retrieval_fn=lambda **_kwargs: [chunk()],
            llm_fn=lambda _prompt: (
                "A proposta deve ser apresentada pelo sistema eletrônico [1]."
            ),
            audit_dir=Path(self.audit_tmp),
        )
        source = result.sources[0]
        response = GroundedRagResponse(
            answer=result.answer,
            confidence_level="media",
            is_refusal=False,
            sources_used=[
                SourceEvidence(
                    chunk_id=source.chunk_id,
                    source_file=str(source.metadata["caminhoRelativo"]),
                    source_url=str(source.metadata["urlFontePncp"]),
                    quotation=source.content[:500],
                )
            ],
        )

        self.assertEqual(response.sources_used[0].chunk_id, "chunk-evidencia-1")
        self.assertIn("sistema eletrônico", response.sources_used[0].quotation)

    def test_ausencia_de_chunk_e_compativel_com_recusa_sem_evidencia(self):
        result = gerar_resposta_rag(
            db=None,
            pergunta="Pergunta sem documento indexado.",
            retrieval_fn=lambda **_kwargs: [],
            llm_fn=lambda _prompt: self.fail("A LLM não deve ser chamada sem chunks."),
            audit_dir=Path(self.audit_tmp),
        )
        response = GroundedRagResponse(
            answer=result.answer,
            confidence_level="recusado",
            sources_used=[],
            is_refusal=True,
            refusal_reason="sem_evidencia",
        )

        self.assertEqual(result.answer, MENSAGEM_SEM_EVIDENCIA)
        self.assertEqual(response.refusal_reason, "sem_evidencia")
