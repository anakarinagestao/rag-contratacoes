import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import create_sqlite_engine, init_db
from app.modules.corpus.model import CorpusVersion
from app.modules.documents.model import Document, DocumentChunk
from app.modules.documents.services import (
    persistir_chunks_jsonl_pncp,
    persistir_textos_ativos_pncp,
)


class DocumentIngestionTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_sqlite_engine("sqlite:///:memory:")
        init_db(self.engine)
        self.session = Session(self.engine)
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.session.close)
        corpus = CorpusVersion(version="teste-ingestao", status="ready")
        self.session.add(corpus)
        self.session.commit()
        self.corpus_version_id = corpus.id

    def _dados_pncp(
        self,
        caminho_texto: Path,
        caminho_texto_extra: Path | None = None,
    ):
        entidade = {
            "numeroControlePNCP": "123",
            "orgaoEntidade": {"razaoSocial": "Orgao Teste"},
            "unidadeOrgao": {"municipioNome": "Rio de Janeiro", "ufSigla": "RJ"},
            "anoContrato": 2026,
            "sequencialContrato": 7,
            "nomeRazaoSocialFornecedor": "Fornecedor SA",
            "objetoContrato": "Servico de teste",
            "valorGlobal": "123.45",
            "dataAssinatura": "2026-08-01",
            "dataVigenciaFim": "2026-12-31",
        }
        documento = {
            "sequencialDocumento": 1,
            "tipoDocumentoNome": "Contrato",
            "titulo": "Contrato de teste",
            "url": "https://pncp.test/documento",
            "processamentoTexto": [
                {
                    "tipoFonte": "pdf_original",
                    "textoAtivo": {
                        "origem": "rapido",
                        "caminho": str(caminho_texto),
                    },
                },
                {
                    "tipoFonte": "pdf_original",
                    "textoAtivo": {
                        "origem": "rapido",
                        "caminho": str(caminho_texto),
                    },
                },
            ],
        }
        documentos = [documento]
        if caminho_texto_extra is not None:
            documentos.append(
                {
                    "sequencialDocumento": 2,
                    "tipoDocumentoNome": "Contrato",
                    "titulo": "Contrato de teste extra",
                    "url": "https://pncp.test/documento-extra",
                    "processamentoTexto": [
                        {
                            "tipoFonte": "pdf_original",
                            "textoAtivo": {
                                "origem": "rapido",
                                "caminho": str(caminho_texto_extra),
                            },
                        }
                    ],
                }
            )
        return (
            {"contratos": [{**entidade, "documentos": documentos}]},
            {"contratos": [entidade]},
        )

    def test_persiste_documento_e_chunks_idempotente(self):
        with TemporaryDirectory() as pasta:
            caminho_texto = Path(pasta) / "contrato.txt"
            caminho_texto.write_text(
                "Objeto do contrato de teste.\n\n" * 20,
                encoding="utf-8",
            )
            caminho_texto_extra = Path(pasta) / "contrato-extra.txt"
            caminho_texto_extra.write_text(
                "Objeto diferente para outro contrato de teste.\n\n" * 20,
                encoding="utf-8",
            )
            manifestos, bases = self._dados_pncp(
                caminho_texto,
                caminho_texto_extra,
            )

            primeiro = persistir_textos_ativos_pncp(
                db=self.session,
                corpus_version_id=self.corpus_version_id,
                manifestos=manifestos,
                bases=bases,
                usar_langchain=False,
                raiz_projeto=Path(pasta),
            )
            segundo = persistir_textos_ativos_pncp(
                db=self.session,
                corpus_version_id=self.corpus_version_id,
                manifestos=manifestos,
                bases=bases,
                usar_langchain=False,
                raiz_projeto=Path(pasta),
            )

        documentos = self.session.scalars(select(Document)).all()
        chunks = self.session.scalars(select(DocumentChunk)).all()
        self.assertEqual(len(documentos), 2)
        self.assertEqual(len(chunks), 2)
        self.assertEqual(primeiro["documentosCriados"], 2)
        self.assertEqual(primeiro["chunksCriados"], 2)
        self.assertEqual(primeiro["chunksReutilizados"], 1)
        self.assertEqual(segundo["documentosReutilizados"], 2)
        self.assertEqual(segundo["chunksReutilizados"], 3)
        self.assertEqual(chunks[0].orgao, "Orgao Teste")
        self.assertEqual(chunks[0].base_id, "pncp_rj_estruturada")

    def test_persistencia_jsonl_informa_progresso(self):
        with TemporaryDirectory() as pasta:
            arquivo_chunks = Path(pasta) / "chunks.jsonl"
            arquivo_chunks.write_text(
                json.dumps(
                    {
                        "chunk_id": "chunk-jsonl-progresso",
                        "content": "Conteúdo do chunk de teste.",
                        "metadata": {
                            "baseId": "pncp_rj_estruturada",
                            "tipoEntidade": "contrato",
                            "numeroControlePNCP": "123",
                            "sequencialDocumento": 1,
                            "tituloDocumento": "Contrato de teste",
                            "urlFontePncp": "https://pncp.test/documento",
                        },
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            atualizacoes: list[tuple[int, int]] = []
            resumo = persistir_chunks_jsonl_pncp(
                db=self.session,
                corpus_version_id=self.corpus_version_id,
                arquivo_chunks=arquivo_chunks,
                progresso=lambda atual, total: atualizacoes.append((atual, total)),
            )

        self.assertEqual(resumo["chunksCriados"], 1)
        self.assertEqual(atualizacoes[0], (0, 1))
        self.assertEqual(atualizacoes[-1], (1, 1))


if __name__ == "__main__":
    unittest.main()
