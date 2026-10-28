import json
import tempfile
import unittest
from pathlib import Path

from src.ingest import RawRepository, ingest_records
from src.ingestion.pncp_collector import ContractCollector
from src.schema import CollectedRecord, SourceConfig


class FakePNCPClient:
    def __init__(self, response):
        self.response = response

    def fetch_page(self, source, page):
        return self.response, f"{source.url}?pagina={page}"


class ContractRawIngestionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        path = Path(self.temp_dir.name) / "contracts.db"
        self.database_url = f"sqlite:///{path}"

    def tearDown(self):
        self.temp_dir.cleanup()

    @staticmethod
    def source():
        return SourceConfig(
            id="pncp_contratacoes_rj_2023_t1_m06_smoke",
            base="https://pncp.example",
            endpoint="/api/consulta/v1/contratacoes/publicacao",
            collector="pncp_contracts",
            resource_type="contratacao",
            params={
                "dataInicial": "20230101",
                "dataFinal": "20230331",
                "codigoModalidadeContratacao": 6,
                "uf": "RJ",
            },
            audit_context={
                "source_id": "pncp_contratacoes_rj",
                "ano": 2023,
                "trimestre": 1,
            },
            item_path="data",
            key_fields=("numeroControlePNCP",),
            max_pages=1,
            page_size=10,
        )

    def test_persists_page_context_window_and_complete_payload_idempotently(self):
        repository = RawRepository(self.database_url)
        payload = {
            "numeroControlePNCP": "compra-1",
            "unidadeOrgao": {"ufSigla": "RJ"},
            "objetoCompra": "Objeto completo",
            "estruturaOriginal": {"lista": [1, {"campo": "valor"}]},
        }
        query_params = {
            "source_id": "pncp_contratacoes_rj",
            "ano": 2023,
            "trimestre": 1,
            "dataInicial": "20230101",
            "dataFinal": "20230331",
            "codigoModalidadeContratacao": 6,
            "uf": "RJ",
            "pagina": 1,
            "tamanhoPagina": 10,
        }
        record = CollectedRecord(
            self.source().id,
            payload,
            "https://pncp.example?pagina=1",
            "1",
            1,
            external_id="compra-1",
            query_params=query_params,
        )

        first = ingest_records(
            repository,
            self.source().id,
            self.source().key_fields,
            [record],
            resource_type="contratacao",
            window_start="20230101",
            window_end="20230331",
        )
        second = ingest_records(
            repository,
            self.source().id,
            self.source().key_fields,
            [record],
            resource_type="contratacao",
            window_start="20230101",
            window_end="20230331",
        )

        self.assertEqual((first.new, second.unchanged), (1, 1))
        self.assertEqual(
            repository.connection.execute(
                "SELECT COUNT(*) FROM raw_asset"
            ).fetchone()[0],
            1,
        )
        asset = repository.connection.execute(
            "SELECT payload, query_params FROM raw_asset"
        ).fetchone()
        run = repository.connection.execute(
            "SELECT window_start, window_end FROM ingestion_run WHERE id = ?",
            (first.run_id,),
        ).fetchone()
        self.assertEqual(json.loads(asset["payload"]), payload)
        self.assertEqual(json.loads(asset["query_params"]), query_params)
        self.assertEqual(tuple(run), ("20230101", "20230331"))
        repository.close()

    def test_invalid_state_fails_run_without_raw_or_checkpoint(self):
        repository = RawRepository(self.database_url)
        response = {
            "data": [
                {
                    "numeroControlePNCP": "compra-sp",
                    "unidadeOrgao": {"ufSigla": "SP"},
                }
            ],
            "totalPaginas": 1,
        }
        records = ContractCollector(FakePNCPClient(response)).collect(
            self.source()
        )

        with self.assertRaisesRegex(ValueError, "recorte estadual"):
            ingest_records(
                repository,
                self.source().id,
                self.source().key_fields,
                records,
                resource_type="contratacao",
                window_start="20230101",
                window_end="20230331",
            )

        self.assertEqual(
            repository.connection.execute(
                "SELECT COUNT(*) FROM raw_asset"
            ).fetchone()[0],
            0,
        )
        self.assertEqual(
            repository.connection.execute(
                "SELECT COUNT(*) FROM sync_checkpoint"
            ).fetchone()[0],
            0,
        )
        run = repository.connection.execute(
            "SELECT status, records_failed FROM ingestion_run"
        ).fetchone()
        self.assertEqual(tuple(run), ("FAILED", 1))
        repository.close()


if __name__ == "__main__":
    unittest.main()
