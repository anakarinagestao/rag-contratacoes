"""HTTP/SQL local sem lifespan, banco operacional ou serviços externos."""

import sqlite3
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.database import get_db
from app.core.security import get_current_user
from app.modules.query.routes import router
from src.generate import RagAnswer
from src.query.schemas import QueryAnalysis


def test_structured_and_hybrid_preserve_positive_snapshot_warning(tmp_path):
    path = tmp_path / "normalized.db"
    with sqlite3.connect(path) as db:
        db.executescript((Path(__file__).parents[2] / "migrations/100_normalized_schema.sql").read_text(encoding="utf-8"))
        db.execute("INSERT INTO orgao VALUES ('12345678000199','Orgao','E','E','raw','hash','now')")
        db.execute("INSERT INTO unidade VALUES ('12345678000199','1','Unidade',NULL,'Municipio','RJ',NULL,'raw','hash','now')")
        db.execute("INSERT INTO contratacao (numero_controle_pncp,orgao_cnpj,codigo_unidade,"
                   "ano_compra,sequencial_compra,modalidade_id,srp,current_source_raw_asset_id,"
                   "current_source_payload_hash,normalized_at) "
                   "VALUES ('purchase','12345678000199','1',2025,1,6,0,'raw','hash','now')")
        db.execute("INSERT INTO normalization_coverage "
                   "(resource_type,coverage_status,record_count,notes,updated_at) "
                   "VALUES ('contratacao','COMPLETE',1,'snapshot','now')")
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: object()
    app.dependency_overrides[get_current_user] = lambda: object()
    structured = QueryAnalysis(route="structured", intent="aggregate", entity="contratacao",
                               bases=["estruturada"], operations=["count"])
    hybrid = QueryAnalysis(route="hybrid", intent="comparison", entity="contratacao",
                           bases=["estruturada", "normativa"], operations=["count"],
                           needs_document_retrieval=True, semantic_query="regras")
    with patch.object(settings, "normalized_database_url", f"sqlite:///{path}"), TestClient(app) as client:
        with patch("app.modules.query.services.analisar_pergunta_estruturada", return_value=structured):
            response = client.post("/query/structured", json={"pergunta": "Quantas contratações?"})
        assert response.status_code == 200
        body = response.json()
        assert body["structured_result"]["rows"] == [{"count": 1}]
        assert body["structured_result"]["coverage"]["state"] == "partial"
        assert "Cobertura parcial" in body["answer"]
        with patch("app.modules.query.services.analisar_pergunta", return_value=hybrid), \
                patch("src.generate.gerar_resposta_rag", return_value=RagAnswer("Resposta sem ressalvas.", [])) as generation:
            response = client.post("/query/ask", json={"pergunta": "Compare quantidade e regras"})
        assert response.status_code == 200
        assert response.json()["structured_result"]["coverage"]["state"] == "partial"
        assert "Cobertura parcial" in response.json()["answer"]
        assert generation.call_args.kwargs["structured_result"].coverage.state == "partial"
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM contratacao").fetchone()[0] == 1
