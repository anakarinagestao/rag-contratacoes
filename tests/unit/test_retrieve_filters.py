"""Filtros estruturados aplicados antes da fusão FTS5 + FAISS."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from datetime import date
import sqlite3
import unittest
from unittest.mock import patch

from sqlalchemy import bindparam, event, insert, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.database import Base, create_sqlite_engine
import app.modules.auth.model  # noqa: F401
import app.modules.feedback.model  # noqa: F401
import app.modules.query.model  # noqa: F401
import app.modules.rag.model  # noqa: F401
from app.modules.corpus.model import CorpusVersion
from app.modules.documents.model import Document, DocumentChunk
from src.index import indexar_chunks_incrementalmente
from src.fts import buscar_fts5, inicializar_fts5
from src.query.base_catalog import resolver_base_ids
from src.query.schemas import KnowledgeBase, QueryEntity, QueryFilters
from src.retrieve import (
    DEFAULT_BM25_CANDIDATES,
    DEFAULT_FAISS_CANDIDATES,
    MAX_BM25_CANDIDATES,
    MAX_FAISS_CANDIDATES,
    buscar_dense,
    construir_consulta_chunks_elegiveis,
    contar_chunks_por_base,
    reciprocal_rank_fusion,
    resolver_chunk_ids_filtrados,
    retrieve_hibrido,
)


def fake_embedding(texto: str) -> list[float]:
    return [1.0, 0.0] if "alpha" in texto.lower() else [0.0, 1.0]


class RetrieveFiltersTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_sqlite_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        inicializar_fts5(self.engine)
        self.session = Session(self.engine)
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.session.close)

        corpus = CorpusVersion(version="filters-test", status="ready")
        documento_a = Document(
            corpus_version=corpus,
            source_file="pncp_rj_estruturada|contrato|111-2-000001/2026|1",
            title="Contrato Alpha",
            base_id="pncp_rj_estruturada",
            doc_type="contrato",
            sensitivity="publico",
        )
        documento_b = Document(
            corpus_version=corpus,
            source_file="pncp_rj_estruturada|contrato|222-2-000001/2025|1",
            title="Contrato Beta",
            base_id="pncp_rj_estruturada",
            doc_type="contrato",
            sensitivity="publico",
        )
        documento_normativo = Document(
            corpus_version=corpus,
            source_file="lei_14133.pdf",
            title="Lei 14.133/2021",
            base_id="editais_normativo",
            doc_type="norma",
            sensitivity="publico",
        )
        documento_conversacional = Document(
            corpus_version=corpus,
            source_file="email_teste.md",
            title="E-mail simulado",
            base_id="conversacional_pncp",
            doc_type="conversa",
            sensitivity="publico",
        )
        self.chunk_a = DocumentChunk(
            document=documento_a,
            chunk_id="chunk-a",
            content="alpha manutenção contratual",
            ano=2026,
            modalidade="Pregão Eletrônico",
            municipio="Rio de Janeiro",
            orgao="Órgão Alpha",
            fornecedor="Fornecedor Único",
            objeto="Manutenção de elevadores",
            valor=100.0,
            data_publicacao=date(2026, 8, 15),
        )
        self.chunk_b = DocumentChunk(
            document=documento_b,
            chunk_id="chunk-b",
            content="alpha manutenção contratual",
            ano=2025,
            modalidade="Dispensa",
            municipio="Niterói",
            objeto="Material hospitalar",
            valor=200.0,
        )
        self.chunk_normativo = DocumentChunk(
            document=documento_normativo,
            chunk_id="chunk-normativo",
            content="alpha requisitos de habilitação na Lei 14.133",
        )
        self.chunk_conversacional = DocumentChunk(
            document=documento_conversacional,
            chunk_id="chunk-conversacional",
            content="alpha troca de e-mails sobre habilitação",
        )
        self.session.add_all([
            self.chunk_a,
            self.chunk_b,
            self.chunk_normativo,
            self.chunk_conversacional,
        ])
        self.session.commit()

    def test_resolve_apenas_chunks_do_numero_pncp_solicitado(self) -> None:
        allowed = resolver_chunk_ids_filtrados(
            self.session,
            QueryFilters(numero_controle_pncp="111-2-000001/2026"),
        )

        self.assertEqual(allowed, {"chunk-a"})

    def test_retrieval_nao_mistura_chunks_de_outro_contrato(self) -> None:
        with TemporaryDirectory() as directory:
            indexar_chunks_incrementalmente(
                [
                    (self.chunk_a.chunk_id, self.chunk_a.content),
                    (self.chunk_b.chunk_id, self.chunk_b.content),
                ],
                fake_embedding,
                Path(directory),
                modelo_embedding="fake-v1",
            )
            results = retrieve_hibrido(
                self.session,
                "alpha manutenção",
                top_k=5,
                cache_dir=Path(directory),
                embedding_fn=fake_embedding,
                filters=QueryFilters(numero_controle_pncp="111-2-000001/2026"),
            )

        self.assertEqual([item.chunk_id for item in results], ["chunk-a"])
        self.assertTrue(
            all(
                item.metadata["numeroControlePNCP"] == "111-2-000001/2026"
                for item in results
            )
        )

    def test_combina_ano_modalidade_e_faixa_de_valor(self) -> None:
        allowed = resolver_chunk_ids_filtrados(
            self.session,
            QueryFilters(
                ano=2026,
                modalidade="pregão",
                valor_minimo=90,
                valor_maximo=110,
            ),
        )

        self.assertEqual(allowed, {"chunk-a"})

    def test_filtra_por_objeto(self) -> None:
        allowed = resolver_chunk_ids_filtrados(
            self.session,
            QueryFilters(objeto="elevadores"),
        )

        self.assertEqual(allowed, {"chunk-a"})

    def test_restringe_chunks_a_base_logica_solicitada(self) -> None:
        normativos = resolver_chunk_ids_filtrados(
            self.session,
            filters=None,
            base_ids=resolver_base_ids([KnowledgeBase.NORMATIVA]),
        )
        conversacionais = resolver_chunk_ids_filtrados(
            self.session,
            filters=None,
            base_ids=resolver_base_ids([KnowledgeBase.CONVERSACIONAL]),
        )
        agregados = resolver_chunk_ids_filtrados(
            self.session,
            filters=None,
            base_ids=resolver_base_ids([KnowledgeBase.AGREGADA]),
        )

        self.assertEqual(normativos, {"chunk-normativo"})
        self.assertEqual(conversacionais, {"chunk-conversacional"})
        self.assertEqual(agregados, {"chunk-a", "chunk-b"})

    def test_fts_preserva_todos_os_filtros(self) -> None:
        casos = [
            ({"numero_controle_pncp": "111-2-000001/2026"}, {"chunk-a"}),
            ({"document_id": self.chunk_a.document_id}, {"chunk-a"}),
            ({"tipo_entidade": QueryEntity.CONTRATO}, {"chunk-a", "chunk-b"}),
            ({"tipo_documento": "ALPHA"}, {"chunk-a"}),
            ({"orgao": "ORGAO ALPHA"}, {"chunk-a"}),
            ({"municipio": "RIO DE JANEIRO"}, {"chunk-a"}),
            ({"fornecedor": "UNICO"}, {"chunk-a"}),
            ({"modalidade": "PREGAO"}, {"chunk-a"}),
            ({"objeto": "MANUTENCAO"}, {"chunk-a"}),
            ({"ano": 2026}, {"chunk-a"}),
            ({"period_start": date(2026, 8, 1)}, {"chunk-a"}),
            ({"period_end": date(2026, 8, 31)}, {"chunk-a"}),
            ({"valor_minimo": 150}, {"chunk-b"}),
            ({"valor_maximo": 150}, {"chunk-a"}),
            ({"ano": 2026, "modalidade": "PREGAO", "valor_maximo": 110}, {"chunk-a"}),
            ({"document_id": self.chunk_a.document_id, "ano": 2025}, set()),
        ]
        for valores, esperado in casos:
            with self.subTest(filters=valores):
                consulta = construir_consulta_chunks_elegiveis(QueryFilters(**valores))
                resultado = buscar_fts5(self.session, "alpha", 20, consulta)
                self.assertEqual({chunk_id for chunk_id, _ in resultado}, esperado)

    def test_fts_isola_bases_e_preserva_none_e_vazio(self) -> None:
        casos = [
            (None, {"chunk-a", "chunk-b", "chunk-normativo", "chunk-conversacional"}),
            ([], set()),
            ([KnowledgeBase.NORMATIVA], {"chunk-normativo"}),
            ([KnowledgeBase.CONVERSACIONAL], {"chunk-conversacional"}),
            ([KnowledgeBase.AGREGADA], {"chunk-a", "chunk-b"}),
        ]
        for bases, esperado in casos:
            with self.subTest(bases=bases):
                consulta = construir_consulta_chunks_elegiveis(None, resolver_base_ids(bases))
                resultado = buscar_fts5(self.session, "alpha", 20, consulta)
                self.assertEqual({chunk_id for chunk_id, _ in resultado}, esperado)

        # A fonte de elegibilidade continua sendo Document.base_id.
        self.chunk_a.base_id = "editais_normativo"
        self.session.commit()
        consulta = construir_consulta_chunks_elegiveis(None, {"editais_normativo"})
        self.assertEqual(
            [chunk_id for chunk_id, _ in buscar_fts5(self.session, "alpha", 1, consulta)],
            ["chunk-normativo"],
        )

    def test_fts_preserva_bm25_e_limite_global(self) -> None:
        self.chunk_a.content = "alpha alpha alpha"
        self.chunk_b.content = "alpha beta beta beta beta beta"
        self.session.commit()
        consulta = construir_consulta_chunks_elegiveis(None, {"pncp_rj_estruturada"})
        referencia = text("""
            SELECT chunks.chunk_id, bm25(document_chunks_fts) AS score
            FROM document_chunks_fts
            JOIN document_chunks AS chunks ON chunks.id = document_chunks_fts.rowid
            WHERE document_chunks_fts MATCH :consulta AND chunks.chunk_id IN :ids
            ORDER BY score ASC LIMIT :limite
        """).bindparams(bindparam("ids", expanding=True))
        for limite in (1, 2):
            with self.subTest(limite=limite):
                esperado = self.session.execute(
                    referencia, {"consulta": '"alpha"', "ids": ["chunk-a", "chunk-b"], "limite": limite}
                ).all()
                resultado = buscar_fts5(self.session, "alpha", limite, consulta)
                self.assertEqual(resultado, [(row.chunk_id, float(row.score)) for row in esperado])
        self.assertEqual(buscar_fts5(self.session, "alpha", 1, consulta)[0][0], "chunk-a")

    def test_fts_cem_mil_chunks_nao_expande_parametros(self) -> None:
        for inicio in range(0, 100_000, 1_000):
            self.session.execute(insert(DocumentChunk), [
                {"document_id": self.chunk_a.document_id, "chunk_id": f"large-{i}",
                 "content": "omega reajuste contratual"}
                for i in range(inicio, inicio + 1_000)
            ])
        self.session.commit()
        connection = self.session.connection().connection.driver_connection
        anterior = connection.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 64)
        self.addCleanup(connection.setlimit, sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, anterior)

        # Confirma que a configuração reproduz a falha da antiga expansão.
        with self.assertRaises(OperationalError):
            self.session.execute(
                text("SELECT 1 WHERE 1 IN :ids").bindparams(bindparam("ids", expanding=True)),
                {"ids": list(range(100))},
            )
        parametros = []
        def registrar_parametros(conn, cursor, statement, parameters, context, executemany):
            parametros.append(len(parameters))
        event.listen(self.engine, "before_cursor_execute", registrar_parametros)
        self.addCleanup(event.remove, self.engine, "before_cursor_execute", registrar_parametros)
        consulta = construir_consulta_chunks_elegiveis(None, {"pncp_rj_estruturada"})
        resultado = buscar_fts5(self.session, "omega", 20, consulta)
        self.assertEqual(len(resultado), 20)
        self.assertTrue(all(chunk_id.startswith("large-") for chunk_id, _ in resultado))
        self.assertEqual(len(parametros), 1)
        self.assertLessEqual(parametros[0], 4)

    def test_hibrido_bases_vazias_nao_consulta_faiss(self) -> None:
        with patch("src.retrieve.buscar_dense") as dense:
            self.assertEqual(retrieve_hibrido(self.session, "alpha", bases=[]), [])
            dense.assert_not_called()

    def test_hibrido_usa_limites_de_candidatos_configuraveis_e_preserva_top_k(self) -> None:
        with (
            patch("src.retrieve.buscar_fts5", return_value=[("chunk-a", -1.0)]) as lexical,
            patch("src.retrieve.buscar_dense", return_value=[("chunk-b", 0.9)]) as dense,
            patch("src.retrieve._identificar_norma", return_value=None),
        ):
            resultado = retrieve_hibrido(
                self.session,
                "alpha",
                top_k=1,
                bases=[KnowledgeBase.AGREGADA],
            )

        self.assertEqual(lexical.call_args.args[2], DEFAULT_BM25_CANDIDATES)
        self.assertEqual(dense.call_args.args[1], DEFAULT_FAISS_CANDIDATES)
        self.assertEqual(len(resultado), 1)

    def test_hibrido_limita_candidatos_e_preserva_filtros_de_elegibilidade(self) -> None:
        with (
            patch("src.retrieve.buscar_fts5", return_value=[("chunk-a", -1.0)]) as lexical,
            patch("src.retrieve.buscar_dense", return_value=[("chunk-a", 0.9)]) as dense,
            patch("src.retrieve._identificar_norma", return_value=None),
        ):
            resultado = retrieve_hibrido(
                self.session,
                "alpha",
                top_k=1,
                bm25_k=MAX_BM25_CANDIDATES + 100,
                dense_k=MAX_FAISS_CANDIDATES + 100,
                filters=QueryFilters(numero_controle_pncp="111-2-000001/2026"),
            )

        self.assertEqual(lexical.call_args.args[2], MAX_BM25_CANDIDATES)
        self.assertIsNotNone(lexical.call_args.kwargs["eligible_chunks"])
        self.assertEqual(dense.call_args.args[1], MAX_FAISS_CANDIDATES)
        self.assertEqual(dense.call_args.kwargs["allowed_chunk_ids"], {"chunk-a"})
        self.assertEqual([chunk.chunk_id for chunk in resultado], ["chunk-a"])
        self.assertEqual(len(resultado), 1)

    def test_rrf_deduplica_ids_repetidos_por_ranking_sem_perder_fusao(self) -> None:
        fused = reciprocal_rank_fusion(
            [
                [("chunk-a", 0.0), ("chunk-a", 0.0), ("chunk-b", 0.0)],
                [("chunk-a", 0.0)],
            ],
            k=60,
        )

        self.assertAlmostEqual(fused["chunk-a"], 2 / 61)
        self.assertAlmostEqual(fused["chunk-b"], 1 / 63)

    def test_hibrido_preserva_ranking_rrf_e_limite_lexical(self) -> None:
        self.chunk_a.content = "alpha alpha alpha"
        self.chunk_b.content = "alpha beta beta beta beta beta"
        self.session.commit()
        consulta = construir_consulta_chunks_elegiveis(None, {"pncp_rj_estruturada"})
        lexical = buscar_fts5(self.session, "alpha", 2, consulta)
        with TemporaryDirectory() as directory:
            cache_dir = Path(directory)
            indexar_chunks_incrementalmente(
                [(chunk.chunk_id, chunk.content) for chunk in (
                    self.chunk_a, self.chunk_b, self.chunk_normativo, self.chunk_conversacional
                )],
                fake_embedding, cache_dir,
                modelo_embedding="fake-v1",
            )
            dense = buscar_dense(
                "alpha", 2, cache_dir, fake_embedding, {"chunk-a", "chunk-b"}
            )
            esperado = {}
            for ranking in (lexical, dense):
                for rank, (chunk_id, _) in enumerate(ranking, start=1):
                    esperado[chunk_id] = esperado.get(chunk_id, 0.0) + 1 / (60 + rank)
            with patch("src.retrieve.buscar_fts5", wraps=buscar_fts5) as fts:
                resultado = retrieve_hibrido(
                    self.session, "alpha", top_k=1, bm25_k=2, dense_k=2,
                    cache_dir=cache_dir, embedding_fn=fake_embedding,
                    bases=[KnowledgeBase.AGREGADA],
                )
                self.assertEqual(fts.call_args.args[2], 2)
                self.assertIsNotNone(fts.call_args.kwargs["eligible_chunks"])
            self.assertEqual(len(resultado), 1)
            self.assertEqual(resultado[0].chunk_id, max(esperado, key=esperado.get))
            self.assertAlmostEqual(resultado[0].rrf_score, esperado[resultado[0].chunk_id])
            self.assertEqual(resultado[0].bm25_rank, 1)

    def test_conta_chunks_por_base_para_auditoria(self) -> None:
        totais = contar_chunks_por_base(
            self.session,
            {"pncp_rj_estruturada", "editais_normativo"},
        )

        self.assertEqual(totais, {
            "editais_normativo": 1,
            "pncp_rj_estruturada": 2,
        })


if __name__ == "__main__":
    unittest.main()
