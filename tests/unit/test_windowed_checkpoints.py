from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import tempfile
import unittest

from app.core.database.repositories import SyncRepository
from src.fetch_corpus import prepare_window_targets
from src.ingest import RawRepository, ingest_records, ingest_window_records
from src.ingestion.checkpoint import (
    CoveragePlanSpec,
    CoverageWindowSpec,
    HISTORICAL_MODE,
    INCREMENTAL_MODE,
    SyncJobSpec,
    SyncWindowSpec,
    stable_target_metadata,
    windows_overlap,
)
from src.schema import CollectionCheckpoint, CollectedRecord, SourceConfig


class SilentLogger:
    def event(self, event, **fields):
        pass


class WindowedCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        path = Path(self.temp_dir.name) / "checkpoint-v2.db"
        self.repository = RawRepository(f"sqlite:///{path}")
        self.sync = SyncRepository(self.repository.connection)
        self.logger = SilentLogger()

    def tearDown(self):
        self.repository.close()
        self.temp_dir.cleanup()

    def job(
        self,
        mode=HISTORICAL_MODE,
        *,
        source="pncp_contratacoes",
        modality=6,
    ):
        spec = SyncJobSpec(
            source=source,
            mode=mode,
            base_id="pncp",
            resource_type="contratacao",
            endpoint="/v1/contratacoes/publicacao",
            uf="RJ",
            modality=modality,
            filters={"tipoData": "publicacao"},
        )
        self.sync.register_job(spec)
        return spec

    def window(self, job, start="2023-01-01", end="2023-01-31"):
        spec = SyncWindowSpec(job.job_id, start, end)
        self.sync.create_window(spec)
        return spec

    def successful_run(self, window, lease, source="pncp_contratacoes"):
        run_id = self.running_run(window, lease, source)
        self.repository.finish_run(
            run_id,
            {
                "records_seen": 0,
                "NEW": 0,
                "UPDATED": 0,
                "UNCHANGED": 0,
                "FAILED": 0,
            },
        )
        return run_id

    def running_run(self, window, lease, source="pncp_contratacoes"):
        run_id = self.repository.start_run(
            source,
            "contratacao",
            window_start=window.start,
            window_end=window.end,
        )
        self.sync.attach_run(window.window_id, lease.token, run_id)
        return run_id

    @staticmethod
    def record(source="pncp_contratacoes", value="A"):
        return CollectedRecord(
            source,
            {
                "numeroControlePNCP": "compra-1",
                "estado": value,
                "dataAtualizacaoGlobal": "2023-01-20T10:00:00Z",
            },
            "https://pncp.example/contratacoes",
            "1",
            1,
            external_id="compra-1",
        )

    def test_historical_and_incremental_jobs_are_separate(self):
        historical = self.job(HISTORICAL_MODE)
        incremental = self.job(INCREMENTAL_MODE)

        self.assertNotEqual(historical.job_id, incremental.job_id)
        self.assertEqual(
            self.sync.get_job(historical.job_id)["mode"], "historical"
        )
        self.assertEqual(
            self.sync.get_job(incremental.job_id)["mode"], "incremental"
        )

    def test_modalities_have_separate_checkpoint_identities(self):
        six = self.job(modality=6)
        eight = self.job(modality=8)

        self.assertNotEqual(six.job_id, eight.job_id)
        self.assertEqual(self.sync.get_job(six.job_id)["modality"], 6)
        self.assertEqual(self.sync.get_job(eight.job_id)["modality"], 8)

    def test_windows_are_deterministic_and_not_duplicated(self):
        job = self.job()
        january = self.window(job)
        february = self.window(job, "2023-02-01", "2023-02-28")
        duplicate = self.window(job)

        self.assertEqual(january.window_id, duplicate.window_id)
        self.assertNotEqual(january.window_id, february.window_id)
        count = self.repository.connection.execute(
            "SELECT COUNT(*) FROM sync_window"
        ).fetchone()[0]
        self.assertEqual(count, 2)

    def test_failure_during_pagination_does_not_advance_watermark(self):
        job = self.job()
        window = self.window(job)
        lease = self.sync.acquire_window(window.window_id, "worker-a")
        key_one, fp_one = stable_target_metadata({"cnpj": "1"})
        key_two, fp_two = stable_target_metadata({"cnpj": "2"})
        self.sync.register_target(window.window_id, key_one, fp_one)
        self.sync.register_target(window.window_id, key_two, fp_two)

        def interrupted():
            yield CollectionCheckpoint("1", key_one, fp_one)
            raise RuntimeError("timeout na página seguinte")

        with self.assertRaisesRegex(RuntimeError, "timeout"):
            ingest_window_records(
                self.repository,
                self.sync,
                window.window_id,
                lease.token,
                "2023-01-31T23:59:59Z",
                "pncp_contratacoes",
                ("numeroControlePNCP",),
                interrupted(),
                logger=self.logger,
                resource_type="contratacao",
            )

        self.assertEqual(
            self.sync.get_window(window.window_id)["status"], "FAILED"
        )
        self.assertEqual(
            self.sync.get_target(window.window_id, key_one)["status"],
            "COMPLETED",
        )
        self.assertEqual(
            self.sync.get_target(window.window_id, key_two)["status"],
            "PENDING",
        )
        self.assertIsNone(self.sync.get_watermark(job.job_id))

    def test_failure_after_raw_persistence_before_confirmation_is_resumable(self):
        job = self.job()
        window = self.window(job)
        lease = self.sync.acquire_window(window.window_id, "worker-a")
        key, fingerprint = stable_target_metadata(
            {"numero_controle": "compra-1"}
        )
        self.sync.register_target(window.window_id, key, fingerprint)

        def interrupted():
            yield self.record()
            raise RuntimeError("falha antes do checkpoint do alvo")

        with self.assertRaises(RuntimeError):
            ingest_window_records(
                self.repository,
                self.sync,
                window.window_id,
                lease.token,
                "2023-01-31T23:59:59Z",
                "pncp_contratacoes",
                ("numeroControlePNCP",),
                interrupted(),
                logger=self.logger,
                resource_type="contratacao",
            )

        raw_count = self.repository.connection.execute(
            "SELECT COUNT(*) FROM raw_asset"
        ).fetchone()[0]
        target = self.sync.get_target(window.window_id, key)
        self.assertEqual(raw_count, 1)
        self.assertEqual(target["status"], "PENDING")
        self.assertIsNone(target["processed_fingerprint"])
        self.assertIsNone(self.sync.get_watermark(job.job_id))

    def test_target_confirmation_and_reconciliation_update_are_atomic(self):
        job = self.job(INCREMENTAL_MODE)
        window = self.window(job, "2023-01-01", "2023-01-01")
        target = {"numero_controle": "compra-1"}
        key, fingerprint = stable_target_metadata(target)
        self.sync.register_reconciliation_target(
            job.job_id,
            key,
            target,
            fingerprint,
            "2023-01-01",
            cadence_days=1,
            horizon_days=30,
        )
        self.sync.register_target(window.window_id, key, fingerprint)
        lease = self.sync.acquire_window(window.window_id, "worker-a")
        self.running_run(window, lease)
        self.repository.connection.executescript(
            "CREATE TRIGGER fail_reconciliation_update "
            "BEFORE UPDATE ON sync_reconciliation_target "
            "BEGIN SELECT RAISE(ABORT, 'queue failure'); END;"
        )

        with self.assertRaisesRegex(sqlite3.IntegrityError, "queue failure"):
            self.sync.confirm_reconciled_target(
                window.window_id,
                key,
                fingerprint,
                "EMPTY",
                lease.token,
            )

        progress = self.sync.get_target(window.window_id, key)
        queued = self.sync.get_reconciliation_target(job.job_id, key)
        self.assertEqual(progress["status"], "PENDING")
        self.assertIsNone(progress["processed_fingerprint"])
        self.assertIsNone(queued["last_checked_at"])
        self.assertIsNone(queued["last_outcome"])

    def test_composite_coverage_failure_rolls_back_individual_completion(self):
        job = self.job(INCREMENTAL_MODE)
        plan = CoveragePlanSpec(
            "contratacoes-rj",
            "pncp",
            "contratacao",
            "RJ",
            ("modalidade:6",),
        )
        self.sync.register_coverage_plan(plan)
        earlier = CoverageWindowSpec(plan.plan_id, "2023-01-01", "2023-01-01")
        later = CoverageWindowSpec(plan.plan_id, "2023-01-02", "2023-01-02")
        self.sync.create_coverage_window(earlier)
        self.sync.create_coverage_window(later)
        shard = self.window(job, "2023-01-02", "2023-01-02")
        self.sync.attach_coverage_member(
            later.window_id,
            "modalidade:6",
            job.job_id,
            shard.window_id,
        )
        lease = self.sync.acquire_window(shard.window_id, "worker-a")
        self.successful_run(shard, lease)

        from app.core.database.repositories.sync_repository import (
            WindowContinuityError,
        )

        with self.assertRaisesRegex(WindowContinuityError, "composta anterior"):
            self.sync.complete_window(
                shard.window_id,
                lease.token,
                "2023-01-02T23:59:59Z",
                coverage_window_id=later.window_id,
                coverage_member_key="modalidade:6",
            )

        member = self.repository.connection.execute(
            "SELECT status FROM sync_coverage_member "
            "WHERE coverage_window_id=? AND member_key='modalidade:6'",
            (later.window_id,),
        ).fetchone()
        self.assertEqual(self.sync.get_window(shard.window_id)["status"], "RUNNING")
        self.assertEqual(member["status"], "PENDING")
        self.assertIsNone(self.sync.get_watermark(job.job_id))
        self.assertIsNone(self.sync.get_coverage_watermark(plan.plan_id))

    def test_coverage_plan_identity_canonicalizes_configuration(self):
        first = CoveragePlanSpec(
            " contratacoes-rj ",
            " pncp ",
            " contratacao ",
            " rj ",
            (" modalidade:8 ", "modalidade:6"),
        )
        second = CoveragePlanSpec(
            "contratacoes-rj",
            "pncp",
            "contratacao",
            "RJ",
            ("modalidade:6", "modalidade:8"),
        )

        self.assertEqual(first, second)
        self.assertEqual(first.plan_id, second.plan_id)

    def test_restart_after_failure_reuses_window_and_preserves_progress(self):
        job = self.job()
        window = self.window(job)
        first = self.sync.acquire_window(window.window_id, "worker-a")
        key, fingerprint = stable_target_metadata({"cnpj": "1"})
        self.sync.register_target(window.window_id, key, fingerprint)
        first_run = self.running_run(window, first)
        self.sync.confirm_target(
            window.window_id, key, fingerprint, first.token, cursor="1"
        )
        self.repository.runs.fail_run(
            first_run,
            {
                "records_seen": 0,
                "NEW": 0,
                "UPDATED": 0,
                "UNCHANGED": 0,
                "FAILED": 1,
            },
            error="interrompido",
        )
        self.sync.fail_window(window.window_id, first.token, "interrompido")

        second = self.sync.acquire_window(window.window_id, "worker-b")
        target = self.sync.get_target(window.window_id, key)

        self.assertNotEqual(first.token, second.token)
        self.assertEqual(target["status"], "COMPLETED")
        self.assertEqual(
            self.sync.get_window(window.window_id)["attempt_count"], 2
        )

    def test_completed_window_requires_explicit_reexecution(self):
        job = self.job()
        window = self.window(job)
        lease = self.sync.acquire_window(window.window_id, "worker-a")
        self.successful_run(window, lease)
        self.sync.complete_window(
            window.window_id, lease.token, "2023-01-31T23:59:59Z"
        )

        from app.core.database.repositories.sync_repository import (
            WindowCompletedError,
        )

        with self.assertRaises(WindowCompletedError):
            self.sync.acquire_window(window.window_id, "worker-b")
        replay = self.sync.acquire_window(
            window.window_id, "worker-b", allow_completed=True
        )
        self.assertEqual(replay.owner, "worker-b")

    def test_overlapping_windows_remain_explicit_and_independent(self):
        job = self.job()
        first = self.window(job, "2023-01-01", "2023-01-31")
        second = self.window(job, "2023-01-25", "2023-02-07")

        self.assertTrue(windows_overlap(first, second))
        self.assertNotEqual(first.window_id, second.window_id)
        self.assertEqual(
            self.sync.get_window(first.window_id)["status"], "PENDING"
        )
        self.assertEqual(
            self.sync.get_window(second.window_id)["status"], "PENDING"
        )

    def test_equal_watermark_timestamp_uses_tie_breaker_without_regression(self):
        job = self.job(INCREMENTAL_MODE)
        windows = [
            self.window(job, "2023-01-01", "2023-01-31"),
            self.window(job, "2023-01-11", "2023-01-31"),
            self.window(job, "2023-01-21", "2023-01-31"),
        ]
        for window, tie_breaker in zip(windows, ("b", "a", "c")):
            lease = self.sync.acquire_window(window.window_id, tie_breaker)
            self.successful_run(window, lease)
            self.sync.complete_window(
                window.window_id,
                lease.token,
                "2023-01-31T23:59:59Z",
                tie_breaker,
            )

        watermark = self.sync.get_watermark(job.job_id)
        self.assertEqual(watermark["tie_breaker"], "c")
        self.assertEqual(watermark["last_window_id"], windows[2].window_id)

    def test_new_target_inserted_before_old_position_is_not_skipped(self):
        job = self.job()
        window = self.window(job)
        old_source = SourceConfig(
            id="targets",
            base="https://pncp.example",
            endpoint="/itens",
            targets=(
                {"numero_controle": "B"},
                {"numero_controle": "C"},
            ),
        )
        prepared = prepare_window_targets(
            self.sync, old_source, window.window_id
        )
        self.assertEqual(prepared.targets, old_source.targets)
        key_b, fp_b = stable_target_metadata(old_source.targets[0])
        lease = self.sync.acquire_window(window.window_id, "worker-a")
        self.running_run(window, lease)
        self.sync.confirm_target(
            window.window_id, key_b, fp_b, lease.token, cursor="1"
        )
        changed_order = SourceConfig(
            id="targets",
            base="https://pncp.example",
            endpoint="/itens",
            targets=(
                {"numero_controle": "A"},
                {"numero_controle": "B"},
                {"numero_controle": "C"},
            ),
        )

        resumed = prepare_window_targets(
            self.sync, changed_order, window.window_id
        )

        self.assertEqual(
            [target["numero_controle"] for target in resumed.targets],
            ["A", "C"],
        )

    def test_changed_target_fingerprint_returns_to_pending(self):
        job = self.job()
        window = self.window(job)
        original = {"numero_controle": "compra-1", "tem_resultado": False}
        changed = {"numero_controle": "compra-1", "tem_resultado": True}
        key, original_fp = stable_target_metadata(original)
        _, changed_fp = stable_target_metadata(changed)
        self.sync.register_target(window.window_id, key, original_fp)
        lease = self.sync.acquire_window(window.window_id, "worker-a")
        self.running_run(window, lease)
        self.sync.confirm_target(
            window.window_id, key, original_fp, lease.token
        )

        row = self.sync.register_target(window.window_id, key, changed_fp)

        self.assertEqual(row["status"], "PENDING")
        self.assertEqual(row["processed_fingerprint"], original_fp)
        self.assertNotEqual(
            row["processed_fingerprint"], row["target_fingerprint"]
        )
        with self.assertRaisesRegex(ValueError, "Fingerprint"):
            self.sync.confirm_target(
                window.window_id, key, original_fp, lease.token
            )

    def test_two_workers_cannot_acquire_the_same_live_window(self):
        from app.core.database.repositories.sync_repository import (
            LeaseConflictError,
        )

        job = self.job()
        window = self.window(job)
        self.sync.acquire_window(window.window_id, "worker-a")

        with self.assertRaises(LeaseConflictError):
            self.sync.acquire_window(window.window_id, "worker-b")

    def test_expired_lease_can_be_recovered_and_old_token_is_rejected(self):
        from app.core.database.repositories.sync_repository import (
            LeaseConflictError,
        )

        job = self.job()
        window = self.window(job)
        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        old = self.sync.acquire_window(
            window.window_id, "worker-a", lease_seconds=10, now=base
        )
        new = self.sync.acquire_window(
            window.window_id,
            "worker-b",
            lease_seconds=10,
            now=base + timedelta(seconds=11),
        )
        run_id = self.repository.start_run(
            "pncp_contratacoes", "contratacao"
        )
        self.sync.attach_run(
            window.window_id,
            new.token,
            run_id,
            now=base + timedelta(seconds=11),
        )

        with self.assertRaises(LeaseConflictError):
            self.sync.update_cursor(
                window.window_id,
                "1",
                old.token,
                now=base + timedelta(seconds=12),
            )
        self.sync.update_cursor(
            window.window_id,
            "2",
            new.token,
            now=base + timedelta(seconds=12),
        )

    def test_v2_operations_do_not_reinterpret_legacy_checkpoint(self):
        ingest_records(
            self.repository,
            "pncp_contratacoes",
            ("numeroControlePNCP",),
            [self.record()],
            logger=self.logger,
            resource_type="contratacao",
        )
        before = self.repository.connection.execute(
            "SELECT * FROM sync_checkpoint"
        ).fetchone()
        job = self.job(INCREMENTAL_MODE)
        self.window(job)
        after = self.repository.connection.execute(
            "SELECT * FROM sync_checkpoint"
        ).fetchone()

        self.assertEqual(tuple(before), tuple(after))

    def test_historical_and_incremental_windows_share_canonical_raw(self):
        results = []
        for mode, source in (
            (HISTORICAL_MODE, "pncp_historico"),
            (INCREMENTAL_MODE, "pncp_incremental"),
        ):
            job = self.job(mode, source=source)
            window = self.window(job)
            lease = self.sync.acquire_window(window.window_id, source)
            target = {"numero_controle": "compra-1"}
            key, fingerprint = stable_target_metadata(target)
            self.sync.register_target(window.window_id, key, fingerprint)
            results.append(
                ingest_window_records(
                    self.repository,
                    self.sync,
                    window.window_id,
                    lease.token,
                    "2023-01-31T23:59:59Z",
                    source,
                    ("numeroControlePNCP",),
                    [
                        self.record(source),
                        CollectionCheckpoint("1", key, fingerprint),
                    ],
                    logger=self.logger,
                    resource_type="contratacao",
                )
            )

        self.assertEqual((results[0].new, results[1].unchanged), (1, 1))
        self.assertEqual(
            self.repository.connection.execute(
                "SELECT COUNT(*) FROM raw_asset"
            ).fetchone()[0],
            1,
        )
        self.assertEqual(
            self.repository.connection.execute(
                "SELECT COUNT(*) FROM sync_watermark"
            ).fetchone()[0],
            2,
        )

    def test_manual_v2_migration_is_additive_on_legacy_schema(self):
        ingest_records(
            self.repository,
            "pncp_legado",
            ("numeroControlePNCP",),
            [self.record("pncp_legado")],
            logger=self.logger,
            resource_type="contratacao",
        )
        connection = self.repository.connection
        connection.execute("DROP TABLE sync_coverage_watermark")
        connection.execute("DROP TABLE sync_coverage_member")
        connection.execute("DROP TABLE sync_coverage_window")
        connection.execute("DROP TABLE sync_coverage_plan")
        connection.execute("DROP TABLE sync_reconciliation_target")
        connection.execute("DROP TABLE sync_watermark")
        connection.execute("DROP TABLE sync_target_progress")
        connection.execute("DROP TABLE sync_window")
        connection.execute("DROP TABLE sync_job")
        connection.commit()
        before_raw = connection.execute(
            "SELECT id, payload, payload_hash FROM raw_asset"
        ).fetchall()
        before_checkpoint = connection.execute(
            "SELECT * FROM sync_checkpoint"
        ).fetchall()
        migration = (
            Path(__file__).parents[2]
            / "migrations"
            / "002_sync_window_checkpoint.sql"
        ).read_text(encoding="utf-8")

        connection.executescript(migration)

        after_raw = connection.execute(
            "SELECT id, payload, payload_hash FROM raw_asset"
        ).fetchall()
        after_checkpoint = connection.execute(
            "SELECT * FROM sync_checkpoint"
        ).fetchall()
        self.assertEqual(
            [tuple(row) for row in before_raw],
            [tuple(row) for row in after_raw],
        )
        self.assertEqual(
            [tuple(row) for row in before_checkpoint],
            [tuple(row) for row in after_checkpoint],
        )
        self.assertEqual(
            connection.execute("PRAGMA integrity_check").fetchone()[0],
            "ok",
        )
        self.assertEqual(
            connection.execute("PRAGMA foreign_key_check").fetchall(), []
        )

    def test_reconciliation_migration_is_additive_and_idempotent(self):
        ingest_records(
            self.repository,
            "pncp_legado",
            ("numeroControlePNCP",),
            [self.record("pncp_legado")],
            logger=self.logger,
            resource_type="contratacao",
        )
        connection = self.repository.connection
        connection.execute("DROP TABLE sync_reconciliation_target")
        connection.commit()
        before_raw = connection.execute(
            "SELECT id, payload, payload_hash FROM raw_asset"
        ).fetchall()
        before_checkpoint = connection.execute(
            "SELECT * FROM sync_checkpoint"
        ).fetchall()
        migration = (
            Path(__file__).parents[2]
            / "migrations"
            / "004_sync_reconciliation_target.sql"
        ).read_text(encoding="utf-8")

        connection.executescript(migration)
        connection.executescript(migration)

        after_raw = connection.execute(
            "SELECT id, payload, payload_hash FROM raw_asset"
        ).fetchall()
        after_checkpoint = connection.execute(
            "SELECT * FROM sync_checkpoint"
        ).fetchall()
        table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='sync_reconciliation_target'"
        ).fetchone()
        index = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND name='idx_sync_reconciliation_due'"
        ).fetchone()
        self.assertEqual(
            [tuple(row) for row in before_raw],
            [tuple(row) for row in after_raw],
        )
        self.assertEqual(
            [tuple(row) for row in before_checkpoint],
            [tuple(row) for row in after_checkpoint],
        )
        self.assertIsNotNone(table)
        self.assertIsNotNone(index)
        self.assertEqual(
            connection.execute("PRAGMA integrity_check").fetchone()[0],
            "ok",
        )
        self.assertEqual(
            connection.execute("PRAGMA foreign_key_check").fetchall(), []
        )

    def test_coverage_manifest_migration_is_additive_and_idempotent(self):
        ingest_records(
            self.repository,
            "pncp_legado",
            ("numeroControlePNCP",),
            [self.record("pncp_legado")],
            logger=self.logger,
            resource_type="contratacao",
        )
        connection = self.repository.connection
        for table in (
            "sync_coverage_watermark",
            "sync_coverage_member",
            "sync_coverage_window",
            "sync_coverage_plan",
        ):
            connection.execute(f"DROP TABLE {table}")
        connection.commit()
        before_raw = connection.execute(
            "SELECT id, payload, payload_hash FROM raw_asset"
        ).fetchall()
        migration = (
            Path(__file__).parents[2]
            / "migrations"
            / "005_sync_coverage_manifest.sql"
        ).read_text(encoding="utf-8")

        connection.executescript(migration)
        connection.executescript(migration)

        after_raw = connection.execute(
            "SELECT id, payload, payload_hash FROM raw_asset"
        ).fetchall()
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name LIKE 'sync_coverage_%'"
            )
        }
        self.assertEqual(
            [tuple(row) for row in before_raw],
            [tuple(row) for row in after_raw],
        )
        self.assertEqual(
            tables,
            {
                "sync_coverage_plan",
                "sync_coverage_window",
                "sync_coverage_member",
                "sync_coverage_watermark",
            },
        )
        self.assertEqual(
            connection.execute("PRAGMA integrity_check").fetchone()[0],
            "ok",
        )
        self.assertEqual(
            connection.execute("PRAGMA foreign_key_check").fetchall(), []
        )


if __name__ == "__main__":
    unittest.main()
