from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.modules.query.model import Query


# Canonical stages from the requirements document.
PIPELINE_STAGES = (
    "query_analysis",
    "roteamento",
    "retrieval_denso",
    "retrieval_bm25",
    "fusao_rrf",
    "rerank",
    "geracao",
    "validacao",
    "feedback",
)


class PipelineEvent(Base):
    __tablename__ = "pipeline_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    query_id: Mapped[int] = mapped_column(ForeignKey("queries.id"), index=True)
    run_id: Mapped[str] = mapped_column(String, index=True)
    stage: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)
    latency_ms: Mapped[int | None]
    model: Mapped[str | None] = mapped_column(String)
    prompt_tokens: Mapped[int | None]
    completion_tokens: Mapped[int | None]
    total_tokens: Mapped[int | None]
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp()
    )

    query: Mapped[Query] = relationship(back_populates="pipeline_events")
