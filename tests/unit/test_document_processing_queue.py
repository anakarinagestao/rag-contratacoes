"""Testes offline da fila persistente de processamento documental."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy.orm import sessionmaker

from app.core.database.orm import Base, create_sqlite_engine
import app.modules.auth.model  # noqa: F401
import app.modules.feedback.model  # noqa: F401
import app.modules.query.model  # noqa: F401
import app.modules.rag.model  # noqa: F401
from app.modules.corpus.model import CorpusVersion
from app.modules.documents.model import (
    Document,
    DocumentCatalogEntry,
    DocumentChunk,
    DocumentProcessingTaskStatus,
)
from app.modules.documents.services import (
    consultar_tarefa_processamento,
    concluir_tarefa_processamento,
    falhar_tarefa_processamento,
    reservar_proxima_tarefa_processamento,
    renovar_lease_tarefa_processamento,
    retentar_tarefa_processamento,
    solicitar_processamento_catalogo,
)
from app.modules.documents.worker import (
    _arquivos_processaveis,
    executar_uma_tarefa,
    normalizar_url_download_pncp,
)
from app.modules.query.services import _agendar_processamento_documental


class DocumentProcessingQueueTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_sqlite_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine)
        self.db = self.Session()
        self.db.add(CorpusVersion(version="teste", status="ativo"))
        self.item = DocumentCatalogEntry(
            tipo_entidade="contratacao",
            numero_controle_pncp="00394502000144-1-009055/2026",
            sequencial_documento=1,
            tipo_documento="Edital",
            periodo_coleta="2026_08",
            manifesto_origem="teste.json",
        )
        self.db.add(self.item)
        self.db.commit()
        self.addCleanup(self.db.close)

    def test_cria_tarefa_pendente_para_documento_catalogado(self) -> None:
        tarefa, criada = solicitar_processamento_catalogo(self.db, self.item.id)

        self.assertTrue(criada)
        self.assertEqual(tarefa.status, DocumentProcessingTaskStatus.PENDENTE.value)
        self.assertEqual(tarefa.tentativas, 0)
        self.assertEqual(tarefa.catalog_entry_id, self.item.id)

    def test_repetir_solicitacao_reaproveita_tarefa_ativa(self) -> None:
        primeira, criada_primeira = solicitar_processamento_catalogo(self.db, self.item.id)
        segunda, criada_segunda = solicitar_processamento_catalogo(self.db, self.item.id)

        self.assertTrue(criada_primeira)
        self.assertFalse(criada_segunda)
        self.assertEqual(primeira.id, segunda.id)

    def test_consulta_adaptativa_agenda_e_reutiliza_tarefa_elegivel(self) -> None:
        self.item.url_download = (
            "https://pncp.gov.br/pncp-api/v1/orgaos/00394502000144/"
            "compras/2026/9055/arquivos/1"
        )
        self.db.commit()
        referencia = {
            "id": self.item.id,
            "availabilityStatus": "AVAILABLE",
            "statusProcessamento": "PENDENTE",
            "urlDownload": self.item.url_download,
        }

        primeira = _agendar_processamento_documental(
            self.db, [referencia], limite=1
        )
        segunda = _agendar_processamento_documental(
            self.db, [referencia], limite=1
        )

        self.assertEqual(primeira[0]["task_id"], segunda[0]["task_id"])
        self.assertTrue(primeira[0]["created"])
        self.assertFalse(segunda[0]["created"])
        self.assertEqual(
            primeira[0]["status_url"],
            f"/documents/processing-tasks/{primeira[0]['task_id']}",
        )

    def test_consulta_adaptativa_nao_agenda_documento_inelegivel(self) -> None:
        referencias = [
            {
                "id": self.item.id,
                "availabilityStatus": "REMOVED",
                "statusProcessamento": "PENDENTE",
                "urlDownload": "https://pncp.gov.br/arquivo.pdf",
            },
            {
                "id": self.item.id,
                "availabilityStatus": "AVAILABLE",
                "statusProcessamento": "PROCESSADO",
                "urlDownload": "https://pncp.gov.br/arquivo.pdf",
            },
        ]

        self.assertEqual(
            _agendar_processamento_documental(self.db, referencias, limite=2),
            [],
        )

    def test_consulta_tarefa_por_id(self) -> None:
        criada, _ = solicitar_processamento_catalogo(self.db, self.item.id)
        encontrada = consultar_tarefa_processamento(self.db, criada.id)

        self.assertEqual(encontrada.id, criada.id)

    def test_retentativa_reabre_apenas_tarefa_com_erro(self) -> None:
        tarefa, _ = solicitar_processamento_catalogo(self.db, self.item.id)
        falhar_tarefa_processamento(self.db, tarefa.id, "falha transitória")

        reaberta = retentar_tarefa_processamento(self.db, tarefa.id)

        self.assertEqual(reaberta.id, tarefa.id)
        self.assertEqual(reaberta.status, DocumentProcessingTaskStatus.PENDENTE.value)
        self.assertIsNone(reaberta.erro)
        with self.assertRaisesRegex(ValueError, "Somente tarefas com erro"):
            retentar_tarefa_processamento(self.db, tarefa.id)

    def test_recusa_nova_tarefa_para_documento_ja_processado(self) -> None:
        corpus = self.db.query(CorpusVersion).one()
        documento = Document(
            corpus_version_id=corpus.id,
            source_file=(
                "pncp_rj_estruturada|contratacao|"
                "00394502000144-1-009055/2026|1"
            ),
            title="Edital",
            base_id="pncp_rj_estruturada",
            doc_type="contratacao",
            source_url=None,
            sensitivity="publico",
        )
        self.db.add(documento)
        self.db.flush()
        self.db.add(
            DocumentChunk(
                document_id=documento.id,
                chunk_id="chunk-processado-fila",
                content="Conteúdo processado.",
                doc_type="contratacao",
                base_id="pncp_rj_estruturada",
                sensitivity="publico",
            )
        )
        self.db.commit()

        with self.assertRaisesRegex(ValueError, "já está processado"):
            solicitar_processamento_catalogo(self.db, self.item.id)

    def test_worker_reserva_e_conclui_sem_rede(self) -> None:
        tarefa, _ = solicitar_processamento_catalogo(self.db, self.item.id)
        chamados: list[int] = []

        def processador(tarefa_id: int, _db: object) -> dict[str, int]:
            chamados.append(tarefa_id)
            return {"chunks": 1}

        executou = executar_uma_tarefa(self.Session, processador)
        self.assertTrue(executou)
        self.assertEqual(chamados, [tarefa.id])

        self.db.expire_all()
        atualizada = consultar_tarefa_processamento(self.db, tarefa.id)
        self.assertEqual(atualizada.status, DocumentProcessingTaskStatus.CONCLUIDO.value)

    def test_worker_registra_erro_sem_deixar_tarefa_em_execucao(self) -> None:
        tarefa, _ = solicitar_processamento_catalogo(self.db, self.item.id)

        def processador(_tarefa_id: int, _db: object) -> dict[str, int]:
            raise RuntimeError("falha simulada de download")

        self.assertTrue(executar_uma_tarefa(self.Session, processador))
        self.db.expire_all()
        atualizada = consultar_tarefa_processamento(self.db, tarefa.id)
        self.assertEqual(atualizada.status, DocumentProcessingTaskStatus.ERRO.value)
        self.assertEqual(atualizada.error_code, "runtime_error")
        self.assertIsNone(atualizada.erro)

    def test_lease_expirado_e_recuperado_com_fencing(self) -> None:
        tarefa, _ = solicitar_processamento_catalogo(self.db, self.item.id)
        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        primeira = reservar_proxima_tarefa_processamento(
            self.db, worker_id="worker-a", lease_seconds=10, now=base
        )
        self.assertEqual(primeira.id, tarefa.id)
        token_antigo = primeira.lease_token

        self.assertIsNone(
            reservar_proxima_tarefa_processamento(
                self.db,
                worker_id="worker-b",
                lease_seconds=10,
                now=base + timedelta(seconds=9),
            )
        )
        recuperada = reservar_proxima_tarefa_processamento(
            self.db,
            worker_id="worker-b",
            lease_seconds=10,
            now=base + timedelta(seconds=11),
        )
        self.assertNotEqual(recuperada.lease_token, token_antigo)
        with self.assertRaisesRegex(ValueError, "perdeu o lease"):
            concluir_tarefa_processamento(
                self.db,
                tarefa.id,
                token_antigo or "",
                now=base + timedelta(seconds=12),
            )
        concluida = concluir_tarefa_processamento(
            self.db,
            tarefa.id,
            recuperada.lease_token or "",
            now=base + timedelta(seconds=12),
        )
        self.assertEqual(concluida.status, DocumentProcessingTaskStatus.CONCLUIDO.value)

    def test_renova_lease_valido(self) -> None:
        solicitar_processamento_catalogo(self.db, self.item.id)
        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        reservada = reservar_proxima_tarefa_processamento(
            self.db, worker_id="worker-a", lease_seconds=10, now=base
        )
        renovada = renovar_lease_tarefa_processamento(
            self.db,
            reservada.id,
            reservada.lease_token or "",
            lease_seconds=20,
            now=base + timedelta(seconds=5),
        )
        self.assertEqual(
            renovada.lease_expires_at,
            base.replace(tzinfo=None) + timedelta(seconds=25),
        )

    def test_mensagem_bruta_nao_e_persistida(self) -> None:
        tarefa, _ = solicitar_processamento_catalogo(self.db, self.item.id)
        falha = falhar_tarefa_processamento(
            self.db,
            tarefa.id,
            "RuntimeError: cpf 123.456.789-00 token secreto",
        )
        self.assertEqual(falha.error_code, "processing_failed")
        self.assertIsNone(falha.erro)

    def test_normaliza_url_pncp_removendo_porta_do_catalogo(self) -> None:
        url = (
            "https://pncp.gov.br:58066/pncp-api/v1/orgaos/42354068000119/"
            "compras/2026/69/arquivos/1?versao=1"
        )

        normalizada = normalizar_url_download_pncp(url)

        self.assertEqual(
            normalizada,
            "https://pncp.gov.br/pncp-api/v1/orgaos/42354068000119/"
            "compras/2026/69/arquivos/1?versao=1",
        )

    def test_recusa_url_fora_do_dominio_pncp(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "domínio seguro"):
            normalizar_url_download_pncp("https://exemplo.gov.br/arquivo.pdf")

    def test_documento_docx_direto_nao_e_tratado_como_zip_de_anexos(self) -> None:
        with TemporaryDirectory() as diretorio:
            caminho_docx = Path(diretorio) / "autorizacao.docx"
            # A assinatura PK representa o formato ZIP interno de um DOCX.
            caminho_docx.write_bytes(b"PK\x03\x04conteudo-simulado")

            fontes = _arquivos_processaveis(
                caminho_docx,
                Path(diretorio) / "expandido",
                tarefa_id=5,
            )

        self.assertEqual(fontes, [(caminho_docx, None)])


if __name__ == "__main__":
    unittest.main()
