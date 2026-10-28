from __future__ import annotations

from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from app.core.database.repositories import SyncRepository
from app.core.database.repositories.sync_repository import (
    WindowContinuityError,
)
from src.ingest import RawRepository
from src.ingestion.checkpoint import (
    INCREMENTAL_MODE,
    SyncJobSpec,
    SyncWindowSpec,
)


COUNTS = {
    "records_seen": 0,
    "NEW": 0,
    "UPDATED": 0,
    "UNCHANGED": 0,
    "FAILED": 0,
}


class WatermarkContinuityTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "watermark.db"
        self.database_url = f"sqlite:///{self.database_path}"
        self.repository = RawRepository(self.database_url)
        self.sync = SyncRepository(self.repository.connection)

    def tearDown(self):
        self.repository.close()
        self.temp_dir.cleanup()

    def job(self, suffix, *, modality=6):
        spec = SyncJobSpec(
            source=f"pncp_incremental_{suffix}",
            mode=INCREMENTAL_MODE,
            base_id="pncp",
            resource_type="contratacao",
            endpoint="offline://watermark-continuity",
            uf="RJ",
            modality=modality,
            filters={"offline": True},
        )
        self.sync.register_job(spec)
        return spec

    def window(self, job, start, end=None):
        spec = SyncWindowSpec(job.job_id, start, end or start)
        self.sync.create_window(spec)
        return spec

    def successful_run(self, window, owner):
        lease = self.sync.acquire_window(window.window_id, owner)
        run_id = self.repository.start_run(
            "pncp_incremental_contratacao",
            "contratacao",
            window_start=window.start,
            window_end=window.end,
        )
        self.sync.attach_run(window.window_id, lease.token, run_id)
        self.repository.finish_run(run_id, COUNTS)
        return lease, run_id

    def complete(self, window, owner):
        lease, run_id = self.successful_run(window, owner)
        self.sync.complete_window(
            window.window_id,
            lease.token,
            f"{window.end}T23:59:59.999999Z",
        )
        return run_id

    def watermark(self, job):
        return self.sync.get_watermark(job.job_id)

    def test_incomplete_earlier_window_blocks_watermark_for_all_states(self):
        for status in ("PENDING", "RUNNING", "FAILED"):
            with self.subTest(status=status):
                job = self.job(status.lower())
                earlier = self.window(job, "2023-01-01")
                later = self.window(job, "2023-01-02")
                if status in ("RUNNING", "FAILED"):
                    old_lease = self.sync.acquire_window(
                        earlier.window_id, f"worker-{status.lower()}"
                    )
                    if status == "FAILED":
                        self.sync.fail_window(
                            earlier.window_id,
                            old_lease.token,
                            "falha simulada",
                        )
                later_lease, _ = self.successful_run(
                    later, f"later-{status.lower()}"
                )

                with self.assertRaisesRegex(
                    WindowContinuityError, "Janela anterior"
                ):
                    self.sync.complete_window(
                        later.window_id,
                        later_lease.token,
                        "2023-01-02T23:59:59Z",
                    )

                self.assertIsNone(self.watermark(job))
                self.assertEqual(
                    self.sync.get_window(later.window_id)["status"],
                    "RUNNING",
                )

    def test_completed_earlier_window_allows_contiguous_advance(self):
        job = self.job("contiguous")
        earlier = self.window(job, "2023-01-01")
        later = self.window(job, "2023-01-02")

        self.complete(earlier, "worker-one")
        later_run = self.complete(later, "worker-two")

        watermark = self.watermark(job)
        self.assertEqual(
            watermark["watermark_value"], "2023-01-02T23:59:59.999999"
        )
        self.assertEqual(watermark["last_window_id"], later.window_id)
        self.assertEqual(watermark["confirmed_run_id"], later_run)

    def test_future_pending_window_does_not_block_current_window(self):
        job = self.job("future")
        current = self.window(job, "2023-01-01")
        future = self.window(job, "2023-01-02")

        self.complete(current, "worker-current")

        self.assertEqual(
            self.watermark(job)["last_window_id"], current.window_id
        )
        self.assertEqual(
            self.sync.get_window(future.window_id)["status"], "PENDING"
        )

    def test_pending_window_from_another_job_does_not_block(self):
        blocked_job = self.job("other-job", modality=6)
        independent_job = self.job("independent-job", modality=8)
        self.window(blocked_job, "2023-01-01")
        independent = self.window(independent_job, "2023-01-02")

        self.complete(independent, "independent-worker")

        self.assertEqual(
            self.watermark(independent_job)["last_window_id"],
            independent.window_id,
        )
        self.assertIsNone(self.watermark(blocked_job))

    def test_missing_calendar_interval_after_watermark_is_rejected(self):
        job = self.job("missing-date")
        first = self.window(job, "2023-01-01")
        after_gap = self.window(job, "2023-01-03")
        self.complete(first, "worker-first")
        lease, _ = self.successful_run(after_gap, "worker-after-gap")

        with self.assertRaisesRegex(
            WindowContinuityError, "deixa lacuna"
        ):
            self.sync.complete_window(
                after_gap.window_id,
                lease.token,
                "2023-01-03T23:59:59Z",
            )

        watermark = self.watermark(job)
        self.assertEqual(
            watermark["watermark_value"], "2023-01-01T23:59:59.999999"
        )
        self.assertEqual(watermark["last_window_id"], first.window_id)

    def test_overlapping_window_can_extend_confirmed_coverage(self):
        job = self.job("overlap")
        first = self.window(job, "2023-01-01", "2023-01-10")
        overlap = self.window(job, "2023-01-05", "2023-01-15")

        self.complete(first, "worker-first")
        self.complete(overlap, "worker-overlap")

        self.assertEqual(
            self.watermark(job)["watermark_value"],
            "2023-01-15T23:59:59.999999",
        )

    def test_incomplete_overlapping_earlier_window_blocks_advance(self):
        job = self.job("overlap-blocked")
        earlier = self.window(job, "2023-01-01", "2023-01-10")
        later = self.window(job, "2023-01-05", "2023-01-15")
        lease, _ = self.successful_run(later, "worker-later")

        with self.assertRaises(WindowContinuityError):
            self.sync.complete_window(
                later.window_id,
                lease.token,
                "2023-01-15T23:59:59Z",
            )

        self.assertEqual(
            self.sync.get_window(earlier.window_id)["status"], "PENDING"
        )
        self.assertIsNone(self.watermark(job))

    def test_reprocessing_already_covered_window_does_not_block_advance(self):
        job = self.job("reprocess")
        covered = self.window(job, "2023-01-01")
        next_window = self.window(job, "2023-01-02")
        self.complete(covered, "worker-original")
        self.sync.acquire_window(
            covered.window_id,
            "worker-reprocess",
            allow_completed=True,
        )

        self.complete(next_window, "worker-next")

        self.assertEqual(
            self.watermark(job)["last_window_id"], next_window.window_id
        )

    def test_watermark_must_match_window_end_date(self):
        job = self.job("bounds")
        window = self.window(job, "2023-01-01", "2023-01-03")
        lease, _ = self.successful_run(window, "worker-bounds")

        for invalid in (
            "2023-01-02T23:59:59Z",
            "2023-01-04T00:00:00Z",
            "2099-12-31T23:59:59Z",
        ):
            with self.subTest(watermark=invalid), self.assertRaisesRegex(
                WindowContinuityError, "data final"
            ):
                self.sync.complete_window(
                    window.window_id, lease.token, invalid
                )

        self.assertIsNone(self.watermark(job))
        self.assertEqual(
            self.sync.get_window(window.window_id)["status"], "RUNNING"
        )
        self.sync.complete_window(
            window.window_id,
            lease.token,
            "2023-01-03T23:59:59Z",
        )
        self.assertIsNotNone(self.watermark(job))

    def test_rollback_preserves_integrity_after_continuity_failure(self):
        job = self.job("rollback")
        self.window(job, "2023-01-01")
        later = self.window(job, "2023-01-02")
        lease, _ = self.successful_run(later, "worker-later")

        with self.assertRaises(WindowContinuityError):
            self.sync.complete_window(
                later.window_id,
                lease.token,
                "2023-01-02T23:59:59Z",
            )

        window = self.sync.get_window(later.window_id)
        self.assertEqual(window["status"], "RUNNING")
        self.assertIsNone(window["completed_at"])
        self.assertIsNone(self.watermark(job))
        self.assertEqual(
            self.repository.connection.execute(
                "PRAGMA integrity_check"
            ).fetchone()[0],
            "ok",
        )

    def test_concurrent_completion_never_advances_over_earlier_window(self):
        job = self.job("concurrent")
        earlier = self.window(job, "2023-01-01")
        later = self.window(job, "2023-01-02")
        earlier_lease, _ = self.successful_run(earlier, "worker-earlier")
        later_lease, _ = self.successful_run(later, "worker-later")
        later_in_transaction = threading.Event()
        earlier_attempted = threading.Event()
        errors = []
        errors_lock = threading.Lock()
        original = SyncRepository._require_contiguous_coverage

        def coordinated_check(instance, window, current):
            if window["id"] == later.window_id:
                later_in_transaction.set()
                if not earlier_attempted.wait(timeout=5):
                    raise AssertionError(
                        "conclusão da janela anterior não foi iniciada"
                    )
            return original(instance, window, current)

        def complete_on_separate_connection(window, lease, name):
            repository = RawRepository(self.database_url)
            try:
                if name == "earlier":
                    if not later_in_transaction.wait(timeout=5):
                        raise AssertionError(
                            "janela posterior não iniciou a transação"
                        )
                    earlier_attempted.set()
                SyncRepository(repository.connection).complete_window(
                    window.window_id,
                    lease.token,
                    f"{window.end}T23:59:59Z",
                )
            except BaseException as error:
                with errors_lock:
                    errors.append((name, error))
            finally:
                repository.close()

        with patch.object(
            SyncRepository,
            "_require_contiguous_coverage",
            new=coordinated_check,
        ):
            later_thread = threading.Thread(
                target=complete_on_separate_connection,
                args=(later, later_lease, "later"),
            )
            earlier_thread = threading.Thread(
                target=complete_on_separate_connection,
                args=(earlier, earlier_lease, "earlier"),
            )
            later_thread.start()
            earlier_thread.start()
            later_thread.join(timeout=10)
            earlier_thread.join(timeout=10)

        self.assertFalse(later_thread.is_alive())
        self.assertFalse(earlier_thread.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0][0], "later")
        self.assertIsInstance(errors[0][1], WindowContinuityError)
        self.assertEqual(
            self.sync.get_window(earlier.window_id)["status"], "COMPLETED"
        )
        self.assertEqual(
            self.sync.get_window(later.window_id)["status"], "RUNNING"
        )
        self.assertEqual(
            self.watermark(job)["last_window_id"], earlier.window_id
        )


if __name__ == "__main__":
    unittest.main()
