from __future__ import annotations

import unittest

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import Base, create_sqlite_engine
from app.core.database.orm import register_models
from app.modules.corpus.model import CorpusVersion
from app.modules.documents.model import Document, DocumentChunk
from app.modules.query.model import Query, Response, ResponseSource
from app.modules.query.services import persistir_resultado_rag
from src.retrieve import RankedChunk


class QueryServicesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_sqlite_engine("sqlite:///:memory:")
        register_models()
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        corpus = CorpusVersion(version="query-service-test", status="ready")
        document = Document(
            corpus_version=corpus,
            source_file="synthetic.json",
            title="Documento sintético",
            base_id="test_base",
            doc_type="contrato",
            sensitivity="publico",
        )
        self.db.add(
            DocumentChunk(
                document=document,
                chunk_id="synthetic-chunk",
                content="Conteúdo sintético para o teste.",
            )
        )
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()

    def test_persistir_resultado_rag(self):
        chunk = self.db.scalar(
            select(DocumentChunk).order_by(DocumentChunk.id)
        )

        self.assertIsNotNone(chunk)

        source = RankedChunk(
            chunk_id=chunk.chunk_id,
            content=chunk.content,
            rrf_score=0.031,
            bm25_rank=1,
            bm25_score=5.0,
            dense_rank=2,
            dense_score=0.8,
            metadata={
                "id": chunk.id,
                "document_id": chunk.document_id,
            },
        )

        query, response = persistir_resultado_rag(
            db=self.db,
            pergunta="Pergunta de teste",
            resposta="Resposta de teste [1].",
            sources=[source],
        )

        self.assertIsNotNone(query.id)
        self.assertIsNotNone(response.id)

        response_source = self.db.scalar(
            select(ResponseSource).where(
                ResponseSource.response_id == response.id
            )
        )

        self.assertIsNotNone(response_source)
        self.assertEqual(response_source.chunk_id, chunk.id)
        self.assertEqual(response_source.rank, 1)


if __name__ == "__main__":
    unittest.main()
