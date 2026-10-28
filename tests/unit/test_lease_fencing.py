from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from app.core.database.repositories import SyncRepository
from app.core.database.repositories.sync_repository import (
    LeaseConflictError,
    LeaseExpiredError,
)
from src.ingest import RawRepository, ingest_records, ingest_window_records
from src.ingestion.checkpoint import (
    INCREMENTAL_MODE,
    SyncJobSpec,
    SyncWindowSpec,
)
from src.schema import CollectedRecord


class SilentLogger:
    def event(self, event, **fields):
        pass


class LeaseFencingTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "lease-fencing.db"
        self.database_url = f"sqlite:///{self.database_path}"
        self.repository = RawRepository(self.database_url)
        self.sync = SyncRepository(self.repository.connection)
        self.logger = SilentLogger()
        self.job = SyncJobSpec(
            source="pncp_incremental_contratacao",
            mode=INCREMENTAL_MODE,
            base_id="pncp",
            resource_type="contratacao",
            endpoint="offline://lease-fencing",
            uf="RJ",
            modality=6,
            filters={"offline": True},
        )
        self.sync.register_job(self.job)
        self.window = SyncWindowSpec(
            self.job.job_id, "2023-01-01", "2023-01-01"
        )
        self.sync.create_window(self.window)

    def tearDown(self):
        self.repository.close()
        self.temp_dir.cleanup()

    @staticmethod
    def record(source="pncp_incremental_contratacao"):
        return CollectedRecord(
            source,
            {
                "numeroControlePNCP": "compra-1",
                "estado": "A",
                "dataAtualizacaoGlobal": "2023-01-01T10:00:00Z",
            },
            "offline://lease-fencing",
            "1",
            1,
            external_id="compra-1",
        )

    def ingest_window(self, lease, records):
        return ingest_window_records(
            self.repository,
            self.sync,
            self.window.window_id,
            lease.token,
            "2023-01-01T23:59:59Z",
            "pncp_incremental_contratacao",
            ("numeroControlePNCP",),
            records,
            logger=self.logger,
            resource_type="contratacao",
        )

    def counts(self):
        return {
            "raw": self.repository.connection.execute(
                "SELECT COUNT(*) FROM raw_asset"
            ).fetchone()[0],
            "observations": self.repository.connection.execute(
                "SELECT COUNT(*) FROM raw_observation"
            ).fetchone()[0],
            "watermarks": self.repository.connection.execute(
                "SELECT COUNT(*) FROM sync_watermark"
            ).fetchone()[0],
        }

    def test_recovered_lease_fences_stale_worker_before_raw_write(self):
        base = datetime.now(timezone.utc)
        stale = self.sync.acquire_window(
            self.window.window_id,
            "worker-a",
            lease_seconds=10,
            now=base,
        )

        def events_after_takeover():
            worker_b = RawRepository(self.database_url)
            try:
                SyncRepository(worker_b.connection).acquire_window(
                    self.window.window_id,
                    "worker-b",
                    lease_seconds=300,
                    now=base + timedelta(seconds=11),
                )
            finally:
                worker_b.close()
            yield self.record()

        with self.assertRaises(LeaseConflictError):
            self.ingest_window(stale, events_after_takeover())

        self.assertEqual(
            self.counts(), {"raw": 0, "observations": 0, "watermarks": 0}
        )
        run = self.repository.connection.execute(
            "SELECT status, records_failed FROM ingestion_run"
        ).fetchone()
        window = self.sync.get_window(self.window.window_id)
        self.assertEqual((run["status"], run["records_failed"]), ("FAILED", 1))
        self.assertEqual(window["lease_owner"], "worker-b")
        self.assertEqual(window["status"], "RUNNING")
        self.assertEqual(
            self.repository.connection.execute(
                "PRAGMA integrity_check"
            ).fetchone()[0],
            "ok",
        )

    def test_expired_lease_fences_worker_without_recovery(self):
        lease = self.sync.acquire_window(
            self.window.window_id, "worker-a", lease_seconds=300
        )

        def events_after_expiry():
            connection = sqlite3.connect(self.database_path)
            try:
                expired = (
                    datetime.now(timezone.utc) - timedelta(seconds=1)
                ).isoformat(timespec="microseconds")
                connection.execute(
                    "UPDATE sync_window SET lease_expires_at=? WHERE id=?",
                    (expired, self.window.window_id),
                )
                connection.commit()
            finally:
                connection.close()
            yield self.record()

        with self.assertRaises(LeaseExpiredError):
            self.ingest_window(lease, events_after_expiry())

        self.assertEqual(
            self.counts(), {"raw": 0, "observations": 0, "watermarks": 0}
        )
        run = self.repository.connection.execute(
            "SELECT status FROM ingestion_run"
        ).fetchone()
        self.assertEqual(run["status"], "FAILED")

    def test_valid_lease_persists_and_completes_window(self):
        lease = self.sync.acquire_window(
            self.window.window_id, "worker-a", lease_seconds=300
        )

        result = self.ingest_window(lease, [self.record()])

        self.assertEqual(result.status, "SUCCEEDED")
        self.assertEqual(
            self.counts(), {"raw": 1, "observations": 1, "watermarks": 1}
        )
        self.assertEqual(
            self.sync.get_window(self.window.window_id)["status"],
            "COMPLETED",
        )

    def test_historical_ingestion_does_not_require_lease(self):
        result = ingest_records(
            self.repository,
            "pncp_historico_contratacao",
            ("numeroControlePNCP",),
            [self.record("pncp_historico_contratacao")],
            logger=self.logger,
            resource_type="contratacao",
        )

        self.assertEqual(result.status, "SUCCEEDED")
        self.assertEqual(result.new, 1)
        self.assertEqual(self.counts()["raw"], 1)

    def test_lease_token_cannot_authorize_an_inactive_run(self):
        lease = self.sync.acquire_window(
            self.window.window_id, "worker-a", lease_seconds=300
        )
        stale_run = self.repository.start_run(
            "pncp_incremental_contratacao", "contratacao"
        )
        self.sync.attach_run(self.window.window_id, lease.token, stale_run)
        active_run = self.repository.start_run(
            "pncp_incremental_contratacao", "contratacao"
        )
        self.sync.attach_run(self.window.window_id, lease.token, active_run)

        with self.assertRaises(LeaseConflictError):
            self.repository.persist_record(
                stale_run,
                self.record(),
                ("numeroControlePNCP",),
                resource_type="contratacao",
                lease_window_id=self.window.window_id,
                lease_token=lease.token,
            )

        self.assertEqual(self.counts()["raw"], 0)

    def test_write_transaction_fences_concurrent_takeover(self):
        base = datetime.now(timezone.utc)
        lease = self.sync.acquire_window(
            self.window.window_id,
            "worker-a",
            lease_seconds=10,
            now=base,
        )
        run_id = self.repository.start_run(
            "pncp_incremental_contratacao",
            "contratacao",
            window_start=self.window.start,
            window_end=self.window.end,
        )
        self.sync.attach_run(self.window.window_id, lease.token, run_id)
        validated = threading.Event()
        takeover_started = threading.Event()
        takeover_finished = threading.Event()
        failures = []
        original = SyncRepository.require_active_lease
        first_validation = True

        def paused_validation(instance, *args, **kwargs):
            nonlocal first_validation
            window = original(instance, *args, **kwargs)
            if first_validation:
                first_validation = False
                validated.set()
                self.assertTrue(takeover_started.wait(timeout=5))
                self.assertFalse(takeover_finished.is_set())
            return window

        def recover_window():
            try:
                if not validated.wait(timeout=5):
                    raise AssertionError("validação do lease não ocorreu")
                worker_b = RawRepository(self.database_url)
                try:
                    takeover_started.set()
                    SyncRepository(worker_b.connection).acquire_window(
                        self.window.window_id,
                        "worker-b",
                        lease_seconds=300,
                        now=base + timedelta(seconds=11),
                    )
                finally:
                    worker_b.close()
            except BaseException as error:
                failures.append(error)
            finally:
                takeover_finished.set()

        thread = threading.Thread(target=recover_window)
        thread.start()
        with patch.object(
            SyncRepository,
            "require_active_lease",
            new=paused_validation,
        ):
            status = self.repository.persist_record(
                run_id,
                self.record(),
                ("numeroControlePNCP",),
                resource_type="contratacao",
                window_start=self.window.start,
                window_end=self.window.end,
                lease_window_id=self.window.window_id,
                lease_token=lease.token,
            )
        thread.join(timeout=5)

        self.assertFalse(thread.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(status, "NEW")
        self.assertEqual(self.counts()["raw"], 1)
        self.assertEqual(
            self.sync.get_window(self.window.window_id)["lease_owner"],
            "worker-b",
        )


if __name__ == "__main__":
    unittest.main()
