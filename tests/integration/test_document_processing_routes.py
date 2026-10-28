"""Contrato HTTP assíncrono da fila documental, usando somente SQLite temporário."""

from __future__ import annotations

import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.core.database import Base, create_sqlite_engine, get_db
from app.core.database.orm import register_models
from app.core.security import get_current_user
from app.modules.documents.model import DocumentCatalogEntry
from app.modules.documents.routes import router


class DocumentProcessingRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_sqlite_engine("sqlite:///:memory:")
        register_models()
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        with self.Session() as db:
            item = DocumentCatalogEntry(
                tipo_entidade="contratacao",
                numero_controle_pncp="00394502000144-1-009055/2026",
                sequencial_documento=1,
                tipo_documento="Edital",
                periodo_coleta="fixture",
                manifesto_origem="fixture.json",
            )
            db.add(item)
            db.commit()
            self.catalog_entry_id = item.id

        app = FastAPI()
        app.include_router(router)

        def temporary_db():
            with self.Session() as db:
                yield db

        app.dependency_overrides[get_db] = temporary_db
        app.dependency_overrides[get_current_user] = lambda: object()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        self.engine.dispose()

    def test_criacao_retorna_202_task_id_e_status_url(self) -> None:
        response = self.client.post(
            f"/documents/catalog/{self.catalog_entry_id}/processing-tasks",
            json={"motivo": "teste_controlado"},
        )

        self.assertEqual(response.status_code, 202)
        body = response.json()
        self.assertEqual(body["task_id"], body["id"])
        self.assertEqual(
            body["status_url"],
            f"/documents/processing-tasks/{body['task_id']}",
        )
        self.assertTrue(body["created"])
        self.assertEqual(body["status"], "PENDENTE")

        status_response = self.client.get(body["status_url"])
        self.assertEqual(status_response.status_code, 200)
        self.assertEqual(status_response.json()["task_id"], body["task_id"])

    def test_repeticao_reutiliza_tarefa_e_continua_assincrona(self) -> None:
        url = f"/documents/catalog/{self.catalog_entry_id}/processing-tasks"
        first = self.client.post(url, json={})
        repeated = self.client.post(url, json={})

        self.assertEqual(first.status_code, 202)
        self.assertEqual(repeated.status_code, 202)
        self.assertEqual(first.json()["task_id"], repeated.json()["task_id"])
        self.assertFalse(repeated.json()["created"])

    def test_consulta_inexistente_retorna_404_sem_detalhes_internos(self) -> None:
        response = self.client.get("/documents/processing-tasks/999999")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json(),
            {"detail": "Tarefa de processamento não encontrada."},
        )


if __name__ == "__main__":
    unittest.main()
