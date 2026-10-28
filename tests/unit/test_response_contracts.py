"""Validação do contrato de respostas RAG fundamentadas."""

from __future__ import annotations

import unittest

from pydantic import ValidationError

from app.modules.query.response_contracts import (
    GroundedRagResponse,
    SourceEvidence,
)


def evidence() -> SourceEvidence:
    return SourceEvidence(
        chunk_id="abc123",
        source_file="data/chunks/exemplo.jsonl",
        source_url="https://pncp.gov.br/exemplo",
        quotation="Trecho literal que fundamenta a resposta.",
    )


class GroundedRagResponseTests(unittest.TestCase):
    def test_resposta_fundamentada_e_valida(self):
        response = GroundedRagResponse(
            answer="A habilitação exige os documentos previstos no edital [1].",
            confidence_level="alta",
            sources_used=[evidence()],
            is_refusal=False,
        )

        self.assertEqual(response.sources_used[0].chunk_id, "abc123")

    def test_resposta_normal_sem_evidencia_e_rejeitada(self):
        with self.assertRaises(ValidationError):
            GroundedRagResponse(
                answer="Resposta sem fonte.",
                confidence_level="baixa",
                sources_used=[],
                is_refusal=False,
            )

    def test_recusa_exige_motivo_confianca_e_sem_fontes(self):
        response = GroundedRagResponse(
            answer="Não há informação suficiente nas evidências recuperadas.",
            confidence_level="recusado",
            sources_used=[],
            is_refusal=True,
            refusal_reason="sem_evidencia",
        )
        self.assertTrue(response.is_refusal)

        for invalid in (
            {
                "answer": "Recusa com fonte.",
                "confidence_level": "recusado",
                "sources_used": [evidence()],
                "is_refusal": True,
                "refusal_reason": "sem_evidencia",
            },
            {
                "answer": "Recusa sem motivo.",
                "confidence_level": "recusado",
                "sources_used": [],
                "is_refusal": True,
            },
            {
                "answer": "Recusa com confiança incorreta.",
                "confidence_level": "baixa",
                "sources_used": [],
                "is_refusal": True,
                "refusal_reason": "fora_de_escopo",
            },
        ):
            with self.assertRaises(ValidationError):
                GroundedRagResponse(**invalid)

    def test_quotation_tem_limite_de_500_caracteres(self):
        with self.assertRaises(ValidationError):
            SourceEvidence(chunk_id="abc", quotation="x" * 501)
