"""Testes offline do contrato v2 de metadados de chunks PNCP."""

from __future__ import annotations

import unittest

from app.modules.documents.services import _document_source_file
from src.base_estruturada.chunking import gerar_chunk_id, montar_metadata_base


class MetadataContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.entidade = {
            "numeroControlePNCP": "00394502000144-1-009055/2026",
            "anoCompra": 2026,
            "sequencialCompra": 9055,
            "orgaoEntidade": {"cnpj": "00394502000144", "razaoSocial": "Órgão"},
            "unidadeOrgao": {"municipioNome": "Rio de Janeiro", "ufSigla": "RJ"},
        }
        self.documento = {
            "sequencialDocumento": 1,
            "tipoDocumentoNome": "Edital",
            "titulo": "Edital de manutenção",
            "url": "https://pncp.gov.br/arquivo",
        }

    def test_pdf_direto_recebe_contrato_v2_e_sem_caminho_absoluto(self) -> None:
        metadata = montar_metadata_base(
            self.entidade,
            self.documento,
            {"textoAtivo": {"origem": "docling"}},
            "contratacao",
            "data/processed/edital.md",
        )

        self.assertEqual(metadata["schemaVersion"], "chunks_pncp_v2")
        self.assertEqual(metadata["arquivoOriginal"], "edital.md")
        self.assertEqual(metadata["categoriaInterna"], "edital_principal")
        self.assertNotIn("C:", metadata["identificadorDocumentoFonte"])

    def test_mesmo_arquivo_interno_tem_identidade_e_chunk_estaveis(self) -> None:
        base = {
            "textoAtivo": {"origem": "docling"},
            "arquivoPacote": "Main Elevadores.zip",
            "arquivoOriginal": "EDITAL.pdf",
            "caminhoInternoPacote": "documentos/EDITAL.pdf",
        }
        primeiro = montar_metadata_base(
            self.entidade, self.documento, base, "contratacao", "expanded/a/EDITAL.pdf"
        )
        segundo = montar_metadata_base(
            self.entidade,
            self.documento,
            base,
            "contratacao",
            "outro-computador/EDITAL.pdf",
        )

        self.assertEqual(
            primeiro["identificadorDocumentoFonte"],
            segundo["identificadorDocumentoFonte"],
        )
        self.assertEqual(
            gerar_chunk_id(primeiro, 0, "conteúdo"),
            gerar_chunk_id(segundo, 0, "conteúdo"),
        )
        self.assertEqual(
            _document_source_file(primeiro), _document_source_file(segundo)
        )

    def test_arquivos_distintos_no_mesmo_zip_nao_colidem(self) -> None:
        primeiro = montar_metadata_base(
            self.entidade,
            self.documento,
            {
                "textoAtivo": {"origem": "docling"},
                "arquivoPacote": "pacote.zip",
                "arquivoOriginal": "EDITAL.pdf",
                "caminhoInternoPacote": "EDITAL.pdf",
            },
            "contratacao",
            "expanded/EDITAL.pdf",
        )
        segundo = montar_metadata_base(
            self.entidade,
            self.documento,
            {
                "textoAtivo": {"origem": "docling"},
                "arquivoPacote": "pacote.zip",
                "arquivoOriginal": "ANEXO_I.pdf",
                "caminhoInternoPacote": "ANEXO_I.pdf",
            },
            "contratacao",
            "expanded/ANEXO_I.pdf",
        )

        self.assertNotEqual(
            primeiro["identificadorDocumentoFonte"],
            segundo["identificadorDocumentoFonte"],
        )
        self.assertNotEqual(
            gerar_chunk_id(primeiro, 0, "conteúdo"),
            gerar_chunk_id(segundo, 0, "conteúdo"),
        )

    def test_documento_sem_entrada_de_pacote_preserva_id_legado(self) -> None:
        metadata = montar_metadata_base(
            self.entidade,
            self.documento,
            {"textoAtivo": {"origem": "docling"}},
            "contratacao",
            "data/processed/edital.md",
        )
        metadata_legado = dict(metadata)
        metadata_legado.pop("identificadorDocumentoFonte")
        metadata_legado.pop("arquivoOriginal")
        metadata_legado["schemaVersion"] = "chunks_pncp_v1"

        self.assertEqual(
            gerar_chunk_id(metadata, 0, "conteúdo"),
            gerar_chunk_id(metadata_legado, 0, "conteúdo"),
        )
