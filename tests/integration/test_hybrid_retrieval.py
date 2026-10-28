from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from sqlalchemy.orm import Session

from app.core.database import create_sqlite_engine, init_db
from app.modules.corpus.model import CorpusVersion
from app.modules.documents.model import Document, DocumentChunk
from src.fts import buscar_fts5
from src.index import indexar_chunks_incrementalmente
from src.retrieve import buscar_dense, reciprocal_rank_fusion, retrieve_hibrido


def fake_embedding(texto: str) -> list[float]:
    texto = texto.lower()
    if "alpha" in texto:
        return [1.0, 0.0]
    if "beta" in texto:
        return [0.0, 1.0]
    return [0.5, 0.5]


class HybridRetrievalTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_sqlite_engine("sqlite:///:memory:")
        init_db(self.engine)
        self.session = Session(self.engine)
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.session.close)

        corpus = CorpusVersion(version="retrieval-test", status="ready")
        document = Document(
            corpus_version=corpus,
            source_file="pncp_rj_estruturada|contrato|12345678000199-2-000007/2026|1",
            title="Contrato de teste",
            base_id="pncp_rj_estruturada",
            doc_type="contrato",
            source_url="https://pncp.test/contrato/7",
            sensitivity="publico",
        )
        self.chunks = [
            DocumentChunk(document=document, chunk_id="c1", content="alpha alpha contrato"),
            DocumentChunk(document=document, chunk_id="c2", content="beta compra"),
            DocumentChunk(document=document, chunk_id="c3", content="gamma edital"),
        ]
        self.session.add_all(self.chunks)
        self.session.commit()

    def test_fts5_rankeia_por_conteudo(self):
        resultado = buscar_fts5(self.session, "alpha", top_k=2)
        self.assertEqual(resultado[0][0], "c1")

    def test_dense_rankeia_com_faiss(self):
        with TemporaryDirectory() as pasta:
            indexar_chunks_incrementalmente(
                [(chunk.chunk_id, chunk.content) for chunk in self.chunks],
                fake_embedding,
                Path(pasta),
                modelo_embedding="fake-v1",
            )
            resultado = buscar_dense(
                "beta",
                top_k=2,
                cache_dir=Path(pasta),
                embedding_fn=fake_embedding,
            )
        self.assertEqual(resultado[0][0], "c2")

    def test_rrf_e_retrieval_hibrido(self):
        fused = reciprocal_rank_fusion(
            [[("c1", 10.0), ("c2", 1.0)], [("c2", 0.9), ("c1", 0.8)]],
            k=60,
        )
        self.assertAlmostEqual(fused["c1"], fused["c2"])

        with TemporaryDirectory() as pasta:
            indexar_chunks_incrementalmente(
                [(chunk.chunk_id, chunk.content) for chunk in self.chunks],
                fake_embedding,
                Path(pasta),
                modelo_embedding="fake-v1",
            )
            resultado = retrieve_hibrido(
                self.session,
                "alpha",
                top_k=2,
                cache_dir=Path(pasta),
                embedding_fn=fake_embedding,
            )
        self.assertEqual(resultado[0].chunk_id, "c1")
        self.assertEqual(resultado[0].metadata["base_id"], "pncp_rj_estruturada")
        self.assertEqual(
            resultado[0].metadata["numeroControlePNCP"],
            "12345678000199-2-000007/2026",
        )
        self.assertEqual(resultado[0].metadata["tituloDocumento"], "Contrato de teste")
        self.assertEqual(
            resultado[0].metadata["urlFontePncp"],
            "https://pncp.test/contrato/7",
        )


if __name__ == "__main__":
    unittest.main()
