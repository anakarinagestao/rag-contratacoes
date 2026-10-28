from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, String, Text, false, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.modules.documents.model import DocumentChunk
    from app.modules.feedback.model import Feedback
    from app.modules.rag.model import PipelineEvent


class Query(Base):
    __tablename__ = "queries"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(String, unique=True)
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp()
    )

    response: Mapped[Response | None] = relationship(back_populates="query")
    feedback: Mapped[list[Feedback]] = relationship(back_populates="query")
    pipeline_events: Mapped[list[PipelineEvent]] = relationship(back_populates="query")


class Response(Base):
    __tablename__ = "responses"

    id: Mapped[int] = mapped_column(primary_key=True)
    query_id: Mapped[int] = mapped_column(ForeignKey("queries.id"), unique=True)
    answer: Mapped[str] = mapped_column(Text)
    is_refusal: Mapped[bool] = mapped_column(Boolean, server_default=false())
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp()
    )

    query: Mapped[Query] = relationship(back_populates="response")
    sources: Mapped[list[ResponseSource]] = relationship(back_populates="response")


class ResponseSource(Base):
    __tablename__ = "response_sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    response_id: Mapped[int] = mapped_column(ForeignKey("responses.id"), index=True)
    # Internal integer FK, distinct from DocumentChunk.chunk_id (external string).
    chunk_id: Mapped[int] = mapped_column(ForeignKey("document_chunks.id"), index=True)
    rank: Mapped[int | None]
    relevance_score: Mapped[Decimal | None] = mapped_column(Numeric)

    response: Mapped[Response] = relationship(back_populates="sources")
    chunk: Mapped[DocumentChunk] = relationship(back_populates="response_sources")
