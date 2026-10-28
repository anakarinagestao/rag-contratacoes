from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from src.ingest import RawRepository, ingest_records
from src.ingestion.identity import CanonicalIdentity
from src.schema import CollectedRecord


class SilentLogger:
    def event(self, event, **fields):
        pass


class RawAtomicityTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "atomicity.db"
        self.database_url = f"sqlite:///{self.database_path}"
        self.logger = SilentLogger()

    def tearDown(self):
        self.temp_dir.cleanup()

    @staticmethod
    def record(state, timestamp, *, source="pncp_incremental"):
        return CollectedRecord(
            source,
            {
                "numeroControlePNCP": "compra-1",
                "estado": state,
                "dataAtualizacaoGlobal": timestamp,
            },
            "offline://fixture",
            "1",
            1,
            external_id="compra-1",
        )

    def ingest(self, repository, record, window):
        return ingest_records(
            repository,
            record.source_id,
            ("numeroControlePNCP",),
            [record],
            resource_type="contratacao",
            window_start=window,
            window_end=window,
            logger=self.logger,
        )

    def seed(self):
        repository = RawRepository(self.database_url)
        self.ingest(
            repository,
            self.record("A", "2023-04-01T10:00:00Z"),
            "2023-04-01",
        )
        repository.close()

    def concurrent_ingest(self, inputs):
        start_together = threading.Barrier(len(inputs))
        results = []
        failures = []
        result_lock = threading.Lock()

        def worker(record, window):
            repository = RawRepository(self.database_url)
            try:
                original_begin = repository._begin_immediate

                def synchronized_begin():
                    start_together.wait(timeout=5)
                    original_begin()

                repository._begin_immediate = synchronized_begin
                result = self.ingest(repository, record, window)
                with result_lock:
                    results.append(result)
            except BaseException as error:
                with result_lock:
                    failures.append(error)
            finally:
                repository.close()

        threads = [
            threading.Thread(target=worker, args=value) for value in inputs
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        if failures:
            raise failures[0]
        return results

    def test_concurrent_different_updates_form_linear_chain(self):
        self.seed()

        results = self.concurrent_ingest(
            (
                (
                    self.record("B", "2023-04-02T10:00:00Z"),
                    "2023-04-02",
                ),
                (
                    self.record("C", "2023-04-03T10:00:00Z"),
                    "2023-04-03",
                ),
            )
        )

        repository = RawRepository(self.database_url)
        rows = repository.connection.execute(
            "SELECT id, payload, previous_version_id "
            "FROM raw_asset ORDER BY rowid"
        ).fetchall()
        by_state = {json.loads(row["payload"])["estado"]: row for row in rows}
        current = repository.assets.find_current(
            CanonicalIdentity("pncp", "contratacao", "compra-1")
        )

        self.assertEqual(len(rows), 3)
        self.assertEqual(sum(result.updated for result in results), 2)
        self.assertIsNone(by_state["A"]["previous_version_id"])
        parents = {
            by_state["B"]["previous_version_id"],
            by_state["C"]["previous_version_id"],
        }
        self.assertIn(by_state["A"]["id"], parents)
        self.assertEqual(len(parents), 2)
        self.assertTrue(
            parents - {by_state["A"]["id"]}
            <= {by_state["B"]["id"], by_state["C"]["id"]}
        )
        self.assertNotEqual(
            by_state["B"]["previous_version_id"],
            by_state["C"]["previous_version_id"],
        )
        self.assertEqual(json.loads(current["payload"])["estado"], "C")
        repository.close()

    def test_same_concurrent_observation_converges_to_unchanged(self):
        self.seed()
        record = self.record("B", "2023-04-02T10:00:00Z")

        results = self.concurrent_ingest(
            ((record, "2023-04-02"), (record, "2023-04-02"))
        )

        repository = RawRepository(self.database_url)
        counts = sorted(
            (result.updated, result.unchanged) for result in results
        )
        self.assertEqual(counts, [(0, 1), (1, 0)])
        self.assertEqual(
            repository.connection.execute(
                "SELECT COUNT(*) FROM raw_asset"
            ).fetchone()[0],
            2,
        )
        self.assertEqual(
            repository.connection.execute(
                "SELECT COUNT(*) FROM raw_observation"
            ).fetchone()[0],
            2,
        )
        statuses = repository.connection.execute(
            "SELECT status FROM ingestion_run ORDER BY rowid"
        ).fetchall()
        self.assertEqual([row["status"] for row in statuses], ["SUCCESS"] * 3)
        repository.close()

    def test_concurrent_creation_inserts_entity_once(self):
        repository = RawRepository(self.database_url)
        repository.close()
        record = self.record("A", "2023-04-01T10:00:00Z")

        results = self.concurrent_ingest(
            ((record, "2023-04-01"), (record, "2023-04-01"))
        )

        repository = RawRepository(self.database_url)
        counts = sorted((result.new, result.unchanged) for result in results)
        self.assertEqual(counts, [(0, 1), (1, 0)])
        row = repository.connection.execute(
            "SELECT previous_version_id FROM raw_asset"
        ).fetchone()
        self.assertIsNone(row["previous_version_id"])
        self.assertEqual(
            repository.connection.execute(
                "SELECT COUNT(*) FROM raw_asset"
            ).fetchone()[0],
            1,
        )
        repository.close()

    def test_historical_and_incremental_workers_converge_on_same_update(self):
        self.seed()
        historical = self.record(
            "B",
            "2023-04-02T10:00:00Z",
            source="pncp_historico",
        )
        incremental = self.record(
            "B",
            "2023-04-02T10:00:00Z",
            source="pncp_incremental",
        )

        results = self.concurrent_ingest(
            (
                (historical, "2023-04-02"),
                (incremental, "2023-04-02"),
            )
        )

        repository = RawRepository(self.database_url)
        counts = sorted(
            (result.updated, result.unchanged) for result in results
        )
        self.assertEqual(counts, [(0, 1), (1, 0)])
        self.assertEqual(
            repository.connection.execute(
                "SELECT COUNT(*) FROM raw_asset"
            ).fetchone()[0],
            2,
        )
        repository.close()

    def test_legacy_schema_also_prevents_concurrent_duplicate(self):
        self.seed()
        repository = RawRepository(self.database_url)
        repository.connection.execute("DROP TABLE raw_observation")
        repository.connection.commit()
        repository.close()
        record = self.record("B", "2023-04-02T10:00:00Z")

        results = self.concurrent_ingest(
            ((record, "2023-04-02"), (record, "2023-04-02"))
        )

        repository = RawRepository(self.database_url)
        counts = sorted(
            (result.updated, result.unchanged) for result in results
        )
        self.assertEqual(counts, [(0, 1), (1, 0)])
        self.assertEqual(
            repository.connection.execute(
                "SELECT COUNT(*) FROM raw_asset"
            ).fetchone()[0],
            2,
        )
        repository.close()

    def test_observation_failure_rolls_back_raw_before_run_failure_commit(self):
        self.seed()
        repository = RawRepository(self.database_url)
        repository.connection.executescript(
            "CREATE TRIGGER reject_observation "
            "BEFORE INSERT ON raw_observation "
            "BEGIN SELECT RAISE(ABORT, 'falha simulada'); END;"
        )

        with self.assertRaises(sqlite3.IntegrityError):
            self.ingest(
                repository,
                self.record("B", "2023-04-02T10:00:00Z"),
                "2023-04-02",
            )

        self.assertFalse(repository.connection.in_transaction)
        self.assertEqual(
            repository.connection.execute(
                "SELECT COUNT(*) FROM raw_asset"
            ).fetchone()[0],
            1,
        )
        self.assertEqual(
            repository.connection.execute(
                "SELECT COUNT(*) FROM raw_observation"
            ).fetchone()[0],
            1,
        )
        orphan_count = repository.connection.execute(
            "SELECT COUNT(*) FROM raw_asset AS asset "
            "LEFT JOIN raw_observation AS observation "
            "ON observation.raw_asset_id = asset.id "
            "WHERE observation.raw_asset_id IS NULL"
        ).fetchone()[0]
        self.assertEqual(orphan_count, 0)
        failed = repository.connection.execute(
            "SELECT records_failed FROM ingestion_run "
            "WHERE status = 'FAILED'"
        ).fetchone()
        self.assertEqual(failed["records_failed"], 1)

        repository.connection.execute("DROP TRIGGER reject_observation")
        repository.connection.commit()
        recovered = self.ingest(
            repository,
            self.record("B", "2023-04-02T10:00:00Z"),
            "2023-04-02",
        )
        self.assertEqual(recovered.updated, 1)
        repository.close()


if __name__ == "__main__":
    unittest.main()
