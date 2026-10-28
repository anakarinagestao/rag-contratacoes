"""Reconciliação offline e versionada do catálogo documental PNCP."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

import app.modules.auth.model  # noqa: F401
import app.modules.feedback.model  # noqa: F401
import app.modules.query.model  # noqa: F401
import app.modules.rag.model  # noqa: F401
from app.core.database.orm import Base, create_sqlite_engine
from app.modules.corpus.model import CorpusVersion
from app.modules.documents.model import (
    Document,
    DocumentCatalogEntry,
    DocumentCatalogInventory,
    DocumentCatalogInventoryMember,
    DocumentCatalogState,
    DocumentChunk,
)
from app.modules.documents.services import (
    consultar_catalogo_documentos_pncp,
    persistir_catalogo_documentos_pncp,
)


class DocumentCatalogInventoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        engine = create_sqlite_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.db.add(CorpusVersion(version="teste", status="ativo"))
        self.db.commit()
        self.addCleanup(self.db.close)
        self.manifesto = Path(self.temp.name) / "contratacoes_documentos_2026_01.json"

    def _escrever(self, documentos: list[dict[str, object]]) -> None:
        payload = [{
            "numeroControlePNCP": "00394502000144-1-009055/2026",
            "cnpjOrgao": "00394502000144",
            "anoCompra": 2026,
            "sequencialCompra": 9055,
            "documentos": documentos,
        }]
        self.manifesto.write_text(json.dumps(payload), encoding="utf-8")

    @staticmethod
    def _documento(sequencial: int, titulo: str) -> dict[str, object]:
        return {
            "sequencialDocumento": sequencial,
            "titulo": titulo,
            "tipoDocumentoNome": "Edital",
            "url": (
                "https://pncp.gov.br/pncp-api/v1/orgaos/00394502000144/"
                f"compras/2026/9055/arquivos/{sequencial}"
            ),
            "dataPublicacaoPncp": "2026-01-05T10:00:00",
            "statusAtivo": True,
        }

    def _reconciliar(self, *, completo: bool = False) -> dict[str, int | str]:
        return persistir_catalogo_documentos_pncp(
            self.db,
            [("contratacao", "2026_01", self.manifesto)],
            tamanho_commit=2,
            authoritative_full_snapshot=completo,
        )

    def test_reexecucao_do_mesmo_snapshot_e_idempotente(self) -> None:
        self._escrever([self._documento(1, "Edital A")])

        primeira = self._reconciliar(completo=True)
        segunda = self._reconciliar(completo=True)

        self.assertEqual(primeira["inventoryId"], segunda["inventoryId"])
        self.assertEqual(
            self.db.scalar(select(func.count()).select_from(DocumentCatalogEntry)), 1
        )
        self.assertEqual(
            self.db.scalar(select(func.count()).select_from(DocumentCatalogInventory)), 1
        )
        self.assertEqual(
            self.db.scalar(
                select(func.count()).select_from(DocumentCatalogInventoryMember)
            ),
            1,
        )
        estado = self.db.scalar(select(DocumentCatalogState))
        self.assertIsNotNone(estado)
        self.assertEqual(estado.availability_status, "AVAILABLE")

    def test_retomada_ignora_membros_ja_confirmados(self) -> None:
        self._escrever([
            self._documento(1, "Edital A"),
            self._documento(2, "Edital B"),
        ])
        primeira = self._reconciliar(completo=True)

        segunda = self._reconciliar(completo=True)

        self.assertEqual(primeira["inventoryId"], segunda["inventoryId"])
        self.assertEqual(segunda["documentosLidos"], 2)
        self.assertEqual(segunda["inalterados"], 2)
        self.assertEqual(segunda["catalogados"], 0)
        inventario = self.db.get(DocumentCatalogInventory, primeira["inventoryId"])
        self.assertEqual(inventario.documents_seen, 2)
        self.assertEqual(inventario.documents_cataloged, 2)

    def test_mudanca_de_metadata_preserva_as_duas_observacoes(self) -> None:
        self._escrever([self._documento(1, "Edital A")])
        primeira = self._reconciliar(completo=True)
        estado = self.db.scalar(select(DocumentCatalogState))
        fingerprint_anterior = estado.current_fingerprint

        self._escrever([self._documento(1, "Edital revisado")])
        segunda = self._reconciliar(completo=True)

        self.assertNotEqual(primeira["inventoryId"], segunda["inventoryId"])
        self.assertEqual(segunda["metadadosAlterados"], 1)
        self.assertNotEqual(estado.current_fingerprint, fingerprint_anterior)
        self.assertEqual(
            self.db.scalar(
                select(func.count()).select_from(DocumentCatalogInventoryMember)
            ),
            2,
        )
        item = self.db.scalar(select(DocumentCatalogEntry))
        self.assertEqual(item.titulo, "Edital revisado")

    def test_snapshot_completo_marca_ausente_e_reaparecimento(self) -> None:
        self._escrever([
            self._documento(1, "Edital A"),
            self._documento(2, "Edital B"),
        ])
        self._reconciliar(completo=True)

        self._escrever([self._documento(2, "Edital B")])
        removido = self._reconciliar(completo=True)
        self.assertEqual(removido["ausentes"], 1)
        estados = {
            item.catalog_entry.sequencial_documento: item.availability_status
            for item in self.db.scalars(select(DocumentCatalogState)).all()
        }
        self.assertEqual(estados, {1: "REMOVED", 2: "AVAILABLE"})

        self._escrever([
            self._documento(1, "Edital A"),
            self._documento(2, "Edital B"),
        ])
        reaparecido = self._reconciliar(completo=True)
        self.assertEqual(reaparecido["reativados"], 1)

    def test_snapshot_parcial_nao_marca_documentos_ausentes(self) -> None:
        self._escrever([
            self._documento(1, "Edital A"),
            self._documento(2, "Edital B"),
        ])
        self._reconciliar(completo=True)

        self._escrever([self._documento(2, "Edital B")])
        parcial = self._reconciliar(completo=False)

        self.assertEqual(parcial["ausentes"], 0)
        self.assertEqual(
            set(self.db.scalars(select(DocumentCatalogState.availability_status))),
            {"AVAILABLE"},
        )

    def test_status_processado_reconhece_filho_extraido_de_zip(self) -> None:
        self._escrever([self._documento(1, "Edital A")])
        self._reconciliar(completo=True)
        corpus = self.db.scalar(select(CorpusVersion))
        documento = Document(
            corpus_version_id=corpus.id,
            source_file=(
                "pncp_rj_estruturada|contratacao|"
                "00394502000144-1-009055/2026|1|anexo.pdf"
            ),
            title="Anexo",
            base_id="pncp_rj_estruturada",
            doc_type="contratacao",
            sensitivity="publico",
        )
        self.db.add(documento)
        self.db.flush()
        self.db.add(
            DocumentChunk(
                document_id=documento.id,
                chunk_id="zip:anexo:1",
                content="Conteúdo do anexo.",
                doc_type="contratacao",
                base_id="pncp_rj_estruturada",
                sensitivity="publico",
            )
        )
        self.db.commit()

        resultado = consultar_catalogo_documentos_pncp(
            self.db,
            numero_controle_pncp="00394502000144-1-009055/2026",
        )[0]

        self.assertEqual(resultado["statusProcessamento"], "PROCESSADO")
        self.assertEqual(resultado["availabilityStatus"], "AVAILABLE")
        self.assertIsNotNone(resultado["inventoryId"])


if __name__ == "__main__":
    unittest.main()
