"""Testes offline para catálogo PNCP e fallback do Router, sem Ecoa/PNCP."""

from __future__ import annotations

import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database.orm import Base, create_sqlite_engine
# O service adaptativo importa Query/Response; registramos também os modelos
# relacionados antes de configurar os mappers do banco temporário.
import app.modules.auth.model  # noqa: F401
import app.modules.feedback.model  # noqa: F401
import app.modules.query.model  # noqa: F401
import app.modules.rag.model  # noqa: F401
from app.modules.corpus.model import CorpusVersion
from app.modules.documents.model import Document, DocumentCatalogEntry, DocumentChunk
from app.modules.documents.services import consultar_catalogo_documentos_pncp
from app.modules.query.services import executar_consulta_adaptativa_api
from src.query.schemas import (
    KnowledgeBase,
    QueryAnalysis,
    QueryEntity,
    QueryFilters,
    QueryIntent,
    QueryRoute,
)


class DocumentCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_sqlite_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.db.add(CorpusVersion(version="teste", status="ativo"))
        self.db.add(
            DocumentCatalogEntry(
                tipo_entidade="contratacao",
                numero_controle_pncp="00394502000144-1-009055/2026",
                cnpj_orgao="00394502000144",
                ano=2026,
                sequencial_entidade=9055,
                sequencial_documento=1,
                tipo_documento="Edital",
                titulo="Edital de manutenção de elevadores",
                url_download="https://pncp.gov.br:58945/temporaria",
                uri_original="https://pncp.gov.br:58945/temporaria",
                periodo_coleta="2026_08",
                manifesto_origem="data/normalized/contratacoes/2026/contratacoes_documentos_2026_08.json",
            )
        )
        self.db.commit()
        self.addCleanup(self.db.close)

    def test_consulta_catalogo_retorna_url_canonica_sem_rede(self) -> None:
        resultado = consultar_catalogo_documentos_pncp(
            self.db,
            numero_controle_pncp="00394502000144-1-009055/2026",
        )
        self.assertEqual(len(resultado), 1)
        self.assertEqual(resultado[0]["tipoDocumento"], "Edital")
        self.assertEqual(resultado[0]["statusProcessamento"], "PENDENTE")
        self.assertFalse(resultado[0]["documentoPersistido"])
        self.assertEqual(resultado[0]["chunksPersistidos"], 0)
        self.assertEqual(
            resultado[0]["urlDownload"],
            "https://pncp.gov.br/pncp-api/v1/orgaos/00394502000144/"
            "compras/2026/9055/arquivos/1",
        )

    def test_catalogo_indica_documento_processado_e_incompleto(self) -> None:
        corpus = self.db.query(CorpusVersion).one()
        documento = Document(
            corpus_version_id=corpus.id,
            source_file=(
                "pncp_rj_estruturada|contratacao|"
                "00394502000144-1-009055/2026|1"
            ),
            title="Edital de manutenção de elevadores",
            base_id="pncp_rj_estruturada",
            doc_type="contratacao",
            source_url="https://pncp.gov.br/documento",
            sensitivity="publico",
        )
        self.db.add(documento)
        self.db.commit()

        incompleto = consultar_catalogo_documentos_pncp(
            self.db,
            numero_controle_pncp="00394502000144-1-009055/2026",
        )[0]
        self.assertEqual(incompleto["statusProcessamento"], "INCOMPLETO")
        self.assertTrue(incompleto["documentoPersistido"])
        self.assertEqual(incompleto["chunksPersistidos"], 0)

        self.db.add(
            DocumentChunk(
                document_id=documento.id,
                chunk_id="chunk-catalogo-processado",
                content="Trecho processado do edital.",
                doc_type="contratacao",
                base_id="pncp_rj_estruturada",
                sensitivity="publico",
            )
        )
        self.db.commit()

        processado = consultar_catalogo_documentos_pncp(
            self.db,
            numero_controle_pncp="00394502000144-1-009055/2026",
        )[0]
        self.assertEqual(processado["statusProcessamento"], "PROCESSADO")
        self.assertTrue(processado["documentoPersistido"])
        self.assertEqual(processado["chunksPersistidos"], 1)

    def test_filtro_titulo_ignora_acentos_e_caixa(self) -> None:
        for titulo in ("manutencao", "MANUTENCAO", "MaNuTeNçÃo"):
            with self.subTest(titulo=titulo):
                resultado = consultar_catalogo_documentos_pncp(self.db, titulo=titulo)
                self.assertEqual(len(resultado), 1)
                self.assertEqual(resultado[0]["titulo"], "Edital de manutenção de elevadores")
        self.assertEqual(
            consultar_catalogo_documentos_pncp(self.db, titulo="inexistente"), []
        )

    def test_filtro_tipo_documento_ignora_acentos_e_caixa(self) -> None:
        documento = self.db.query(DocumentCatalogEntry).one()
        documento.tipo_documento = "Termo de Referência"
        self.db.commit()

        for tipo_documento in ("referencia", "REFERENCIA", "ReFeRêNcIa"):
            with self.subTest(tipo_documento=tipo_documento):
                resultado = consultar_catalogo_documentos_pncp(
                    self.db, tipo_documento=tipo_documento
                )
                self.assertEqual(len(resultado), 1)
                self.assertEqual(resultado[0]["tipoDocumento"], "Termo de Referência")
        self.assertEqual(
            consultar_catalogo_documentos_pncp(self.db, tipo_documento="inexistente"),
            [],
        )

    def test_fallback_expoe_catalogo_quando_rag_nao_tem_chunks(self) -> None:
        analysis = QueryAnalysis(
            route=QueryRoute.RAG,
            intent=QueryIntent.DOCUMENTAL,
            entity=QueryEntity.CONTRATACAO,
            bases=[KnowledgeBase.AGREGADA],
            filters=QueryFilters(
                numero_controle_pncp="00394502000144-1-009055/2026"
            ),
            semantic_query="serviços de manutenção",
            needs_document_retrieval=True,
        )
        rag_sem_fontes = type("RagResult", (), {"answer": "Sem evidências.", "sources": []})()

        resultado = executar_consulta_adaptativa_api(
            self.db,
            "Quais serviços constam no edital?",
            top_k=3,
            analyzer_fn=lambda _pergunta: analysis,
            rag_executor=lambda: rag_sem_fontes,
        )

        self.assertEqual(len(resultado.external_documents), 1)
        self.assertEqual(
            resultado.external_documents[0]["numeroControlePNCP"],
            "00394502000144-1-009055/2026",
        )
        self.assertEqual(len(resultado.processing_tasks), 1)
        self.assertEqual(
            resultado.processing_tasks[0]["catalog_entry_id"],
            resultado.external_documents[0]["id"],
        )

    def test_rota_catalogo_nao_executa_rag(self) -> None:
        analysis = QueryAnalysis(
            route=QueryRoute.CATALOG,
            intent=QueryIntent.LOOKUP,
            entity=QueryEntity.DOCUMENTO,
            bases=[KnowledgeBase.AGREGADA],
            filters=QueryFilters(
                numero_controle_pncp="00394502000144-1-009055/2026",
                tipo_documento="Edital",
            ),
            needs_document_retrieval=False,
        )

        resultado = executar_consulta_adaptativa_api(
            self.db,
            "Qual a URL do edital?",
            top_k=3,
            analyzer_fn=lambda _pergunta: analysis,
            rag_executor=lambda: self.fail("RAG não deveria executar"),
        )

        self.assertEqual(resultado.plan.route, QueryRoute.CATALOG)
        self.assertIsNone(resultado.rag_result)
        self.assertEqual(len(resultado.external_documents), 1)
        self.assertEqual(resultado.external_documents[0]["tipoDocumento"], "Edital")
        self.assertEqual(len(resultado.processing_tasks), 1)


if __name__ == "__main__":
    unittest.main()
