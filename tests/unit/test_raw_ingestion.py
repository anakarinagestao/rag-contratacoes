import json
import tempfile
import unittest
from pathlib import Path

from src.ingest import RawRepository, ingest_records
from app.core.database.repositories.raw_repository import (
    RawRepository as AssetRepository,
)
from app.core.database.repositories.run_repository import RunRepository
from app.core.database import initialize_database
from src.schema import CollectionCheckpoint, CollectedRecord


class RawIngestionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_url = f"sqlite:///{Path(self.temp_dir.name) / 'raw.db'}"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_new_unchanged_and_updated_preserve_raw_fields(self):
        repository = RawRepository(self.database_url)
        first = CollectedRecord(
            "pncp",
            {"valorUnitario": 6.25, "descricaoItem": "Arroz tipo 1"},
            "url",
            "1",
            1,
        )
        result_one = ingest_records(
            repository, "pncp", ("descricaoItem",), [first]
        )
        result_two = ingest_records(
            repository, "pncp", ("descricaoItem",), [first]
        )
        changed = CollectedRecord(
            "pncp",
            {"valorUnitario": 7.0, "descricaoItem": "Arroz tipo 1"},
            "url",
            "1",
            1,
        )
        result_three = ingest_records(
            repository, "pncp", ("descricaoItem",), [changed]
        )

        self.assertEqual(result_one.new, 1)
        self.assertEqual(result_two.unchanged, 1)
        self.assertEqual(result_three.updated, 1)
        rows = repository.connection.execute(
            "SELECT payload FROM raw_asset ORDER BY created_at"
        ).fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual(json.loads(rows[0]["payload"])["valorUnitario"], 6.25)
        self.assertEqual(
            json.loads(rows[1]["payload"])["descricaoItem"], "Arroz tipo 1"
        )
        versions = repository.connection.execute(
            "SELECT id, payload_hash, previous_version_id "
            "FROM raw_asset ORDER BY rowid"
        ).fetchall()
        self.assertIsNone(versions[0]["previous_version_id"])
        self.assertEqual(versions[1]["previous_version_id"], versions[0]["id"])
        repository.close()

    def test_raw_repository_is_append_only(self):
        connection = initialize_database(self.database_url)
        runs = RunRepository(connection)
        assets = AssetRepository(connection)
        run_id = runs.start_run("pncp", "records")
        first = CollectedRecord("pncp", {"id": "1", "valor": 1}, "url", "1", 1)
        second = CollectedRecord(
            "pncp", {"id": "1", "valor": 2}, "url", "1", 1
        )

        first_id = assets.insert_raw(run_id, first, "1", "hash-a")
        second_id = assets.insert_raw(run_id, second, "1", "hash-b")
        rows = connection.execute(
            "SELECT id, previous_version_id FROM raw_asset ORDER BY rowid"
        ).fetchall()

        self.assertEqual((first_id, second_id), (rows[0]["id"], rows[1]["id"]))
        self.assertEqual(rows[1]["previous_version_id"], first_id)
        self.assertEqual(
            assets.find_latest("pncp", "records", "1")["id"], second_id
        )
        connection.close()

    def test_record_external_id_overrides_payload_key(self):
        repository = RawRepository(self.database_url)
        result = ingest_records(
            repository,
            "pncp_itens_contratacao",
            ("numeroItem",),
            [
                CollectedRecord(
                    "pncp_itens_contratacao",
                    {"numeroItem": 1, "descricao": "Item original"},
                    "https://pncp.example/itens",
                    "1",
                    1,
                    external_id="compra-123|1",
                )
            ],
            resource_type="contratacao_item",
        )

        row = repository.connection.execute(
            "SELECT external_id, payload FROM raw_asset "
            "WHERE ingestion_run_id = ?",
            (result.run_id,),
        ).fetchone()

        self.assertEqual(row["external_id"], "compra-123|1")
        self.assertEqual(json.loads(row["payload"])["descricao"], "Item original")
        repository.close()

    def test_latest_payloads_returns_one_version_per_external_id(self):
        repository = RawRepository(self.database_url)
        run_id = repository.start_run("pncp_contratacoes", "contratacao")
        first = CollectedRecord(
            "pncp_contratacoes",
            {"numeroControlePNCP": "compra-1", "versao": 1},
            "url",
            "1",
            1,
        )
        changed = CollectedRecord(
            "pncp_contratacoes",
            {"numeroControlePNCP": "compra-1", "versao": 2},
            "url",
            "1",
            2,
        )
        repository.persist_record(
            run_id,
            first,
            ("numeroControlePNCP",),
            resource_type="contratacao",
        )
        repository.persist_record(
            run_id,
            changed,
            ("numeroControlePNCP",),
            resource_type="contratacao",
        )

        payloads = repository.latest_payloads(
            "pncp_contratacoes", "contratacao"
        )

        self.assertEqual(payloads, [{"numeroControlePNCP": "compra-1", "versao": 2}])
        repository.close()

    def test_latest_targets_extracts_only_latest_fields_and_keeps_booleans(self):
        repository = RawRepository(self.database_url)
        run_id = repository.start_run(
            "pncp_itens_contratacao", "contratacao_item"
        )
        first = CollectedRecord(
            "pncp_itens_contratacao",
            {"numeroItem": 1, "temResultado": True, "extra": "antigo"},
            "url",
            "1",
            1,
            external_id="compra-1|1",
        )
        changed = CollectedRecord(
            "pncp_itens_contratacao",
            {"numeroItem": 1, "temResultado": False, "extra": "novo"},
            "url",
            "1",
            2,
            external_id="compra-1|1",
        )
        repository.persist_record(
            run_id,
            first,
            ("numeroItem",),
            resource_type="contratacao_item",
        )
        repository.persist_record(
            run_id,
            changed,
            ("numeroItem",),
            resource_type="contratacao_item",
        )

        targets = list(
            repository.latest_targets(
                "pncp_itens_contratacao",
                "contratacao_item",
                {
                    "numero_controle": "@parent_external_id",
                    "numero_item": "numeroItem",
                    "tem_resultado": "temResultado",
                },
            )
        )

        self.assertEqual(
            targets,
            [
                {
                    "numero_controle": "compra-1",
                    "numero_item": 1,
                    "tem_resultado": False,
                }
            ],
        )
        repository.close()

    def test_repository_versions_distinct_pca_payloads_without_merging_them(self):
        # A RAW não deve adivinhar se dois JSONs são fragmentos. O coletor
        # de PCA é responsável por entregar apenas o plano completo.
        repository = RawRepository(self.database_url)
        page_one = CollectedRecord(
            "pncp_pca_rj",
            {"idPcaPncp": "pca-1", "itens": [{"numeroItem": 1}]},
            "https://pncp.example?p=1",
            "1",
            1,
        )
        page_two = CollectedRecord(
            "pncp_pca_rj",
            {"idPcaPncp": "pca-1", "itens": [{"numeroItem": 2}]},
            "https://pncp.example?p=2",
            "2",
            2,
        )

        first = ingest_records(
            repository,
            "pncp_pca_rj",
            ("idPcaPncp",),
            [page_one, page_two],
            resource_type="pca",
        )
        second = ingest_records(
            repository,
            "pncp_pca_rj",
            ("idPcaPncp",),
            [page_one, page_two],
            resource_type="pca",
        )

        self.assertEqual((first.new, first.updated), (1, 1))
        self.assertEqual(
            (second.new, second.updated, second.unchanged), (0, 0, 2)
        )
        raw_count = repository.connection.execute(
            "SELECT COUNT(*) FROM raw_asset WHERE resource_type = 'pca'"
        ).fetchone()[0]
        self.assertEqual(raw_count, 2)
        repository.close()

    def test_run_repository_fail_run_records_error_and_counts(self):
        connection = initialize_database(self.database_url)
        runs = RunRepository(connection)
        run_id = runs.start_run("pncp", "records")
        counts = {
            "records_seen": 3,
            "NEW": 1,
            "UPDATED": 1,
            "UNCHANGED": 1,
            "FAILED": 0,
        }

        runs.fail_run(run_id, counts, pages_processed=2, error="timeout")
        row = connection.execute(
            "SELECT status, pages_processed, records_received, "
            "records_inserted, records_updated, records_unchanged, "
            "records_failed, error_message FROM ingestion_run WHERE id = ?",
            (run_id,),
        ).fetchone()

        self.assertEqual(
            tuple(row), ("FAILED", 2, 3, 1, 1, 1, 0, "timeout")
        )
        connection.close()

    def test_schema_is_sqlite_and_run_is_auditable(self):
        repository = RawRepository(self.database_url)
        result = ingest_records(
            repository,
            "pncp",
            ("id",),
            [
                CollectedRecord(
                    "pncp",
                    {"id": "1", "campoPNCP": "original"},
                    "url",
                    "1",
                    1,
                )
            ],
        )
        tables = {
            row[0]
            for row in repository.connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        self.assertEqual(
            tables,
            {
                "ingestion_run",
                "sync_checkpoint",
                "raw_asset",
                "sync_job",
                "sync_window",
                "sync_target_progress",
                "sync_reconciliation_target",
                "sync_coverage_plan",
                "sync_coverage_window",
                "sync_coverage_member",
                "sync_coverage_watermark",
                "sync_watermark",
                "raw_observation",
            },
        )
        run = repository.connection.execute(
            "SELECT status, records_received FROM ingestion_run WHERE id = ?",
            (result.run_id,),
        ).fetchone()
        self.assertEqual((run[0], run[1]), ("SUCCESS", 1))
        checkpoint = repository.connection.execute(
            "SELECT source, resource_type, last_page, status "
            "FROM sync_checkpoint"
        ).fetchone()
        self.assertEqual(
            tuple(checkpoint), ("pncp", "records", 1, "SUCCESS")
        )
        asset = repository.connection.execute(
            "SELECT processing_status, ingestion_run_id, payload "
            "FROM raw_asset"
        ).fetchone()
        self.assertEqual(asset["processing_status"], "RAW")
        self.assertEqual(asset["ingestion_run_id"], result.run_id)
        self.assertEqual(json.loads(asset["payload"])["campoPNCP"], "original")
        repository.close()

    def test_checkpoint_does_not_advance_when_run_fails(self):
        repository = RawRepository(self.database_url)

        def failing_records():
            yield CollectedRecord(
                "pncp", {"id": "1"}, "url", "1", 1
            )
            raise RuntimeError("falha de coleta")

        with self.assertRaises(RuntimeError):
            ingest_records(
                repository,
                "pncp",
                ("id",),
                failing_records(),
            )

        checkpoint = repository.connection.execute(
            "SELECT COUNT(*) FROM sync_checkpoint"
        ).fetchone()[0]
        self.assertEqual(checkpoint, 0)
        run = repository.connection.execute(
            "SELECT status, records_failed FROM ingestion_run"
        ).fetchone()
        self.assertEqual(tuple(run), ("FAILED", 1))
        repository.close()

    def test_completed_target_progress_survives_later_failure(self):
        repository = RawRepository(self.database_url)

        def failing_after_completed_target():
            yield CollectionCheckpoint("4")
            raise RuntimeError("falha após alvo completo")

        with self.assertRaises(RuntimeError):
            ingest_records(
                repository,
                "pncp_itens",
                ("id",),
                failing_after_completed_target(),
                resource_type="contratacao_item",
            )

        checkpoint = repository.connection.execute(
            "SELECT last_page, status FROM sync_checkpoint WHERE source = ?",
            ("pncp_itens",),
        ).fetchone()
        self.assertEqual(tuple(checkpoint), (4, "FAILED"))
        repository.close()

    def test_keyboard_interrupt_marks_run_and_progress_as_failed(self):
        repository = RawRepository(self.database_url)

        def interrupted_records():
            yield CollectionCheckpoint("2")
            raise KeyboardInterrupt()

        with self.assertRaises(KeyboardInterrupt):
            ingest_records(
                repository,
                "pncp_itens",
                ("id",),
                interrupted_records(),
                resource_type="contratacao_item",
            )

        run = repository.connection.execute(
            "SELECT status, error_message FROM ingestion_run"
        ).fetchone()
        checkpoint = repository.connection.execute(
            "SELECT last_page, status FROM sync_checkpoint"
        ).fetchone()
        self.assertEqual(tuple(run), ("FAILED", "KeyboardInterrupt"))
        self.assertEqual(tuple(checkpoint), (2, "FAILED"))
        repository.close()

    def test_target_checkpoint_advances_without_records_and_is_retained(self):
        repository = RawRepository(self.database_url)

        first = ingest_records(
            repository,
            "pncp_resultados",
            ("id",),
            [CollectionCheckpoint("3")],
            resource_type="resultado_item",
        )
        second = ingest_records(
            repository,
            "pncp_resultados",
            ("id",),
            [],
            resource_type="resultado_item",
            initial_checkpoint=3,
        )

        checkpoint = repository.connection.execute(
            "SELECT last_page FROM sync_checkpoint WHERE source = ?",
            ("pncp_resultados",),
        ).fetchone()[0]
        first_run = repository.connection.execute(
            "SELECT records_received, pages_processed FROM ingestion_run "
            "WHERE id = ?",
            (first.run_id,),
        ).fetchone()
        second_run = repository.connection.execute(
            "SELECT records_received, pages_processed FROM ingestion_run "
            "WHERE id = ?",
            (second.run_id,),
        ).fetchone()

        self.assertEqual(checkpoint, 3)
        self.assertEqual(tuple(first_run), (0, 1))
        self.assertEqual(tuple(second_run), (0, 0))
        repository.close()


if __name__ == "__main__":
    unittest.main()
