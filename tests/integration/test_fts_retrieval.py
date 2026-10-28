import unittest

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import create_sqlite_engine, init_db
from app.modules.corpus.model import CorpusVersion
from app.modules.documents.model import Document, DocumentChunk
from src.fts import buscar_fts5, reconstruir_fts5


class FtsRetrievalTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_sqlite_engine("sqlite:///:memory:")
        init_db(self.engine)
        self.session = Session(self.engine)
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.session.close)

        documento = Document(
            corpus_version=CorpusVersion(version="fts-test", status="ready"),
            source_file="teste",
            base_id="teste",
            doc_type="contrato",
            sensitivity="publico",
        )
        self.chunk = DocumentChunk(
            document=documento,
            chunk_id="fts-c1",
            content="O prazo de vigência contratual é de doze meses.",
        )
        self.session.add(self.chunk)
        self.session.commit()

    def test_trigger_indexa_novo_chunk(self):
        resultado = buscar_fts5(self.session, "vigência contratual", top_k=5)
        self.assertEqual(resultado[0][0], "fts-c1")

    def test_reconstrucao_preserva_indice(self):
        total = reconstruir_fts5(self.engine)
        self.assertEqual(total, 1)
        total_tabela = self.session.execute(
            text("SELECT COUNT(*) FROM document_chunks_fts")
        ).scalar_one()
        self.assertEqual(total_tabela, 1)


if __name__ == "__main__":
    unittest.main()
