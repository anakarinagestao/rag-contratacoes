"""Contratos para respostas RAG fundamentadas e recusas verificáveis."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


ConfidenceLevel = Literal["alta", "media", "baixa", "recusado"]
RefusalReason = Literal["lgpd", "fora_de_escopo", "sem_evidencia"]


class SourceEvidence(BaseModel):
    """Evidência recuperada, vinculada a um trecho rastreável do corpus."""

    model_config = ConfigDict(extra="forbid")

    chunk_id: str = Field(min_length=1)
    source_file: str | None = None
    source_url: str | None = None
    # Deve ser extraída literalmente do chunk pelo adaptador, nunca inventada
    # pela LLM.
    quotation: str = Field(min_length=1, max_length=500)


class GroundedRagResponse(BaseModel):
    """Resposta documental com evidência e recusa consistentes."""

    model_config = ConfigDict(extra="forbid")

    answer: str = Field(min_length=1)
    confidence_level: ConfidenceLevel
    sources_used: list[SourceEvidence] = Field(default_factory=list)
    is_refusal: bool
    refusal_reason: RefusalReason | None = None

    @model_validator(mode="after")
    def validar_consistencia(self) -> "GroundedRagResponse":
        if self.is_refusal:
            if self.sources_used:
                raise ValueError("Recusa não pode conter evidências.")
            if self.confidence_level != "recusado":
                raise ValueError("Recusa exige confidence_level='recusado'.")
            if self.refusal_reason is None:
                raise ValueError("Recusa exige refusal_reason.")
            return self

        if not self.sources_used:
            raise ValueError("Resposta RAG normal exige ao menos uma evidência.")
        if self.confidence_level == "recusado":
            raise ValueError("Resposta normal não pode ter confidence_level='recusado'.")
        if self.refusal_reason is not None:
            raise ValueError("Resposta normal não pode ter refusal_reason.")
        return self
