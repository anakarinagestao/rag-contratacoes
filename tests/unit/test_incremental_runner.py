from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from app.core.database.repositories import SyncRepository
from src.fetch_incremental import main
from src.ingest import RawRepository, ingest_records
from src.ingestion.incremental_runner import (
    IncrementalRunConfig,
    IncrementalRunReport,
    OfflineFixtureError,
    run_offline_incremental,
)
from src.schema import CollectedRecord


class SilentLogger:
    def event(self, event, **fields):
        pass


class IncrementalRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.database = self.root / "incremental.db"

    def tearDown(self):
        self.temp_dir.cleanup()

    def write_fixture(self, document, name="fixture.json"):
        path = self.root / name
        path.write_text(json.dumps(document), encoding="utf-8")
        return path

    @staticmethod
    def record(external_id="compra-1", state="A", timestamp=None):
        return {
            "external_id": external_id,
            "payload": {
                "numeroControlePNCP": external_id,
                "estado": state,
                "dataAtualizacaoGlobal": timestamp
                or "2023-04-10T10:00:00Z",
            },
        }

    @staticmethod
    def pca_record(items=(1, 2)):
        external_id = "12345678000199-0-000007/2023"
        return {
            "external_id": external_id,
            "payload_kind": "complete_pca_plan",
            "source_url": "offline://pca/7/itens/plano",
            "payload": {
                "idPcaPncp": external_id,
                "itens": [
                    {
                        "cnpj": "12345678000199",
                        "anoPca": 2023,
                        "sequencialPca": 7,
                        "numeroItem": number,
                    }
                    for number in items
                ],
            },
        }

    def endpoint_fixture(
        self, records=None, name="fixture.json", **extra
    ):
        return self.write_fixture(
            {
                "strategy": "endpoint",
                "pages": [{"cursor": "1", "records": records or []}],
                **extra,
            },
            name,
        )

    def config(self, fixture, **changes):
        values = {
            "resource": "contratacao",
            "start_date": "2023-04-01",
            "end_date": "2023-04-30",
            "uf": "RJ",
            "database_url": f"sqlite:///{self.database}",
            "fixture_path": fixture,
            "worker_id": "test-worker",
        }
        values.update(changes)
        return IncrementalRunConfig(**values)

    def test_cli_incremental_offline_executes_and_reports_job(self):
        fixture = self.endpoint_fixture([self.record()])
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            status = main(
                [
                    "--resource",
                    "contratacao",
                    "--start-date",
                    "2023-04-01",
                    "--end-date",
                    "2023-04-30",
                    "--uf",
                    "RJ",
                    "--db-path",
                    str(self.database),
                    "--fixture",
                    str(fixture),
                    "--offline",
                ]
            )

        report = json.loads(stdout.getvalue())
        self.assertEqual(status, 0)
        self.assertEqual(report["status"], "SUCCEEDED")
        self.assertEqual((report["new"], report["records_seen"]), (1, 1))
        self.assertTrue(report["job_id"].startswith("job-"))

    def test_cli_rejects_online_execution(self):
        fixture = self.endpoint_fixture([])
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            status = main(
                [
                    "--resource",
                    "contratacao",
                    "--start-date",
                    "2023-04-01",
                    "--end-date",
                    "2023-04-30",
                    "--uf",
                    "RJ",
                    "--db-path",
                    str(self.database),
                    "--fixture",
                    str(fixture),
                ]
            )

        self.assertEqual(status, 2)
        self.assertIn("--online-operational", stderr.getvalue())
        self.assertFalse(self.database.exists())

    def test_cli_routes_explicit_online_pilot_without_fixture(self):
        report = IncrementalRunReport(
            job_id="job-1",
            window_id="window-1",
            run_id="run-1",
            resource="contratacao",
            strategy="endpoint",
            status="SUCCEEDED",
            records_seen=0,
            new=0,
            updated=0,
            unchanged=0,
            lease_renewals=0,
            watermark_value="2023-04-01T23:59:59.999999",
            resumed=False,
            reprocessed=False,
        )
        stdout = io.StringIO()
        with patch(
            "src.fetch_incremental.run_online_pilot", return_value=report
        ) as online, redirect_stdout(stdout):
            status = main(
                [
                    "--resource",
                    "contratacao",
                    "--start-date",
                    "2023-04-01",
                    "--end-date",
                    "2023-04-01",
                    "--uf",
                    "RJ",
                    "--modality",
                    "6",
                    "--db-path",
                    str(self.database),
                    "--online-pilot",
                ]
            )

        self.assertEqual(status, 0)
        online.assert_called_once()
        config = online.call_args.args[0]
        self.assertIsNone(config.fixture_path)
        self.assertEqual((config.resource, config.modality), ("contratacao", 6))
        self.assertEqual(json.loads(stdout.getvalue())["status"], "SUCCEEDED")

    def test_cli_rejects_operational_database_path(self):
        fixture = self.endpoint_fixture([])
        operational = Path(__file__).parents[2] / "data" / "operational.db"
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            status = main(
                [
                    "--resource",
                    "contratacao",
                    "--start-date",
                    "2023-04-01",
                    "--end-date",
                    "2023-04-30",
                    "--uf",
                    "RJ",
                    "--db-path",
                    str(operational),
                    "--fixture",
                    str(fixture),
                    "--offline",
                ]
            )

        self.assertEqual(status, 2)
        self.assertIn("dentro de /tmp", stderr.getvalue())

    def test_cli_routes_explicit_operational_mode_with_double_confirmation(self):
        report = IncrementalRunReport(
            job_id="job-1",
            window_id="window-1",
            run_id="run-1",
            resource="contratacao",
            strategy="endpoint",
            status="SUCCEEDED",
            records_seen=0,
            new=0,
            updated=0,
            unchanged=0,
            lease_renewals=0,
            watermark_value="2023-04-01T23:59:59.999999",
            resumed=False,
            reprocessed=False,
        )
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            database = Path(directory) / "operational.db"
            database.touch()
            log_path = Path(directory) / "operational.jsonl"
            stdout = io.StringIO()
            with patch(
                "src.fetch_incremental.run_online_operational",
                return_value=report,
            ) as operational, redirect_stdout(stdout):
                status = main(
                    [
                        "--resource",
                        "contratacao",
                        "--start-date",
                        "2023-04-01",
                        "--end-date",
                        "2023-04-01",
                        "--uf",
                        "RJ",
                        "--modality",
                        "6",
                        "--db-path",
                        str(database),
                        "--confirm-operational-database",
                        str(database),
                        "--log-path",
                        str(log_path),
                        "--online-operational",
                    ]
                )

        self.assertEqual(status, 0)
        operational.assert_called_once()
        config, confirmation = operational.call_args.args
        self.assertEqual(confirmation, database.resolve())
        self.assertEqual(config.log_path, log_path)
        self.assertEqual(json.loads(stdout.getvalue())["status"], "SUCCEEDED")

    def test_cli_rejects_operational_path_without_matching_confirmation(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            database = Path(directory) / "operational.db"
            database.touch()
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                status = main(
                    [
                        "--resource",
                        "contratacao",
                        "--start-date",
                        "2023-04-01",
                        "--end-date",
                        "2023-04-01",
                        "--uf",
                        "RJ",
                        "--modality",
                        "6",
                        "--db-path",
                        str(database),
                        "--log-path",
                        str(Path(directory) / "operational.jsonl"),
                        "--online-operational",
                    ]
                )

        self.assertEqual(status, 2)
        self.assertIn("deve repetir exatamente", stderr.getvalue())

    def test_pca_fixture_persists_one_complete_plan(self):
        fixture = self.endpoint_fixture([self.pca_record()])

        report = run_offline_incremental(
            self.config(fixture, resource="pca")
        )

        self.assertEqual((report.records_seen, report.new), (1, 1))
        connection = sqlite3.connect(self.database)
        payload = json.loads(
            connection.execute(
                "SELECT payload FROM raw_asset WHERE resource_type = 'pca'"
            ).fetchone()[0]
        )
        connection.close()
        self.assertEqual(
            [item["numeroItem"] for item in payload["itens"]],
            [1, 2],
        )

    def test_pca_fixture_rejects_fragment_before_creating_database(self):
        fragment = self.pca_record(items=(1,))
        fragment.pop("payload_kind")
        fixture = self.endpoint_fixture([fragment])

        with self.assertRaisesRegex(
            OfflineFixtureError, "complete_pca_plan"
        ):
            run_offline_incremental(
                self.config(fixture, resource="pca")
            )

        self.assertFalse(self.database.exists())

    def test_pca_fixture_rejects_repeated_plan_across_pages(self):
        fixture = self.write_fixture(
            {
                "strategy": "endpoint",
                "pages": [
                    {"cursor": "1", "records": [self.pca_record((1,))]},
                    {"cursor": "2", "records": [self.pca_record((2,))]},
                ],
            },
            "fragmented-pca.json",
        )

        with self.assertRaisesRegex(
            OfflineFixtureError, "repetiu o mesmo plano"
        ):
            run_offline_incremental(
                self.config(fixture, resource="pca")
            )

        self.assertFalse(self.database.exists())

    def test_changed_complete_pca_plan_creates_one_new_version(self):
        initial_fixture = self.endpoint_fixture(
            [self.pca_record((1, 2))], name="pca-initial.json"
        )
        changed_fixture = self.endpoint_fixture(
            [self.pca_record((1, 2, 3))], name="pca-changed.json"
        )

        initial = run_offline_incremental(
            self.config(initial_fixture, resource="pca")
        )
        changed = run_offline_incremental(
            self.config(
                changed_fixture,
                resource="pca",
                start_date="2023-05-01",
                end_date="2023-05-31",
            )
        )

        self.assertEqual((initial.new, changed.updated), (1, 1))
        connection = sqlite3.connect(self.database)
        versions = connection.execute(
            "SELECT COUNT(*) FROM raw_asset WHERE resource_type = 'pca'"
        ).fetchone()[0]
        connection.close()
        self.assertEqual(versions, 2)

    def test_configuration_changes_produce_independent_jobs(self):
        fixture = self.endpoint_fixture([])
        first = run_offline_incremental(
            self.config(fixture, modality=6)
        )
        second = run_offline_incremental(
            self.config(fixture, modality=8)
        )

        self.assertNotEqual(first.job_id, second.job_id)
        connection = sqlite3.connect(self.database)
        self.assertEqual(
            connection.execute("SELECT COUNT(*) FROM sync_job").fetchone()[0],
            2,
        )
        connection.close()

    def test_same_job_has_independent_windows(self):
        fixture = self.endpoint_fixture([])
        april = run_offline_incremental(self.config(fixture))
        may = run_offline_incremental(
            self.config(
                fixture,
                start_date="2023-05-01",
                end_date="2023-05-31",
            )
        )

        self.assertEqual(april.job_id, may.job_id)
        self.assertNotEqual(april.window_id, may.window_id)

    def test_lease_is_renewed_during_operation_longer_than_initial_lease(self):
        fixture = self.write_fixture(
            {
                "strategy": "endpoint",
                "delay_seconds": 0.025,
                "pages": [
                    {"cursor": str(index), "records": [self.record(str(index))]}
                    for index in range(1, 4)
                ],
            }
        )

        report = run_offline_incremental(
            self.config(
                fixture,
                lease_seconds=0.08,
                lease_renewal_seconds=0.02,
            )
        )

        self.assertGreater(report.lease_renewals, 0)
        self.assertEqual(report.status, "SUCCEEDED")

    def test_lost_lease_stops_old_worker_without_watermark(self):
        fixture = self.write_fixture(
            {
                "strategy": "endpoint",
                "delay_seconds": 0.07,
                "pages": [{"cursor": "1", "records": [self.record()]}],
            }
        )

        with self.assertRaisesRegex(Exception, "Lease"):
            run_offline_incremental(
                self.config(
                    fixture,
                    lease_seconds=0.04,
                    lease_renewal_seconds=0.01,
                )
            )

        connection = sqlite3.connect(self.database)
        window = connection.execute(
            "SELECT status, lease_expires_at FROM sync_window"
        ).fetchone()
        watermark_count = connection.execute(
            "SELECT COUNT(*) FROM sync_watermark"
        ).fetchone()[0]
        connection.close()
        self.assertEqual(window[0], "RUNNING")
        self.assertIsNotNone(window[1])
        self.assertEqual(watermark_count, 0)

    def test_failure_after_raw_persistence_does_not_confirm_page(self):
        fixture = self.endpoint_fixture(
            [self.record()], fail_after_records=1
        )

        with self.assertRaisesRegex(RuntimeError, "persistência RAW"):
            run_offline_incremental(self.config(fixture))

        connection = sqlite3.connect(self.database)
        self.assertEqual(
            connection.execute("SELECT COUNT(*) FROM raw_asset").fetchone()[0],
            1,
        )
        window = connection.execute(
            "SELECT status, progress_cursor FROM sync_window"
        ).fetchone()
        self.assertEqual(window, ("FAILED", None))
        self.assertEqual(
            connection.execute("SELECT COUNT(*) FROM sync_watermark").fetchone()[0],
            0,
        )
        connection.close()

    def test_failure_and_resume_are_idempotent(self):
        failing = self.endpoint_fixture(
            [self.record()], fail_after_records=1
        )
        with self.assertRaises(RuntimeError):
            run_offline_incremental(self.config(failing))
        recovered = self.endpoint_fixture([self.record()], name="recovered.json")

        report = run_offline_incremental(
            self.config(recovered, resume=True, worker_id="recovery-worker")
        )

        self.assertEqual((report.new, report.unchanged), (0, 1))
        connection = sqlite3.connect(self.database)
        self.assertEqual(
            connection.execute("SELECT COUNT(*) FROM raw_asset").fetchone()[0],
            1,
        )
        self.assertEqual(
            connection.execute("SELECT status FROM sync_window").fetchone()[0],
            "COMPLETED",
        )
        connection.close()

    def test_endpoint_resume_replays_window_after_repagination(self):
        failing = self.write_fixture(
            {
                "strategy": "endpoint",
                "pages": [
                    {
                        "cursor": "1",
                        "records": [self.record("A"), self.record("B")],
                    },
                    {
                        "cursor": "2",
                        "records": [self.record("C"), self.record("D")],
                    },
                ],
                "fail_after_checkpoints": 1,
            },
            "before-repagination.json",
        )
        with self.assertRaisesRegex(RuntimeError, "paginação"):
            run_offline_incremental(self.config(failing))

        repaginated = self.write_fixture(
            {
                "strategy": "endpoint",
                "pages": [
                    {
                        "cursor": "1",
                        "records": [self.record("X"), self.record("A")],
                    },
                    {
                        "cursor": "2",
                        "records": [self.record("B"), self.record("C")],
                    },
                    {"cursor": "3", "records": [self.record("D")]},
                ],
            },
            "after-repagination.json",
        )

        report = run_offline_incremental(
            self.config(
                repaginated,
                resume=True,
                worker_id="recovery-worker",
            )
        )

        self.assertEqual(
            (report.records_seen, report.new, report.unchanged),
            (5, 3, 2),
        )
        connection = sqlite3.connect(self.database)
        ids = {
            row[0]
            for row in connection.execute(
                "SELECT external_id FROM raw_asset"
            )
        }
        window = connection.execute(
            "SELECT status, progress_cursor FROM sync_window"
        ).fetchone()
        watermark_count = connection.execute(
            "SELECT COUNT(*) FROM sync_watermark"
        ).fetchone()[0]
        connection.close()
        self.assertEqual(ids, {"A", "B", "C", "D", "X"})
        self.assertEqual(window, ("COMPLETED", "3"))
        self.assertEqual(watermark_count, 1)

    def test_endpoint_resume_does_not_depend_on_saved_cursor_existing(self):
        failing = self.write_fixture(
            {
                "strategy": "endpoint",
                "pages": [
                    {"cursor": "old-page", "records": [self.record("A")]},
                ],
                "fail_after_checkpoints": 1,
            },
            "old-cursor.json",
        )
        with self.assertRaises(RuntimeError):
            run_offline_incremental(self.config(failing))

        changed = self.write_fixture(
            {
                "strategy": "endpoint",
                "pages": [
                    {
                        "cursor": "new-page",
                        "records": [self.record("A"), self.record("B")],
                    }
                ],
            },
            "new-cursor.json",
        )

        report = run_offline_incremental(
            self.config(changed, resume=True, worker_id="recovery-worker")
        )

        self.assertEqual(
            (report.status, report.new, report.unchanged),
            ("SUCCEEDED", 1, 1),
        )

    def test_endpoint_continues_after_empty_intermediate_page(self):
        fixture = self.write_fixture(
            {
                "strategy": "endpoint",
                "pages": [
                    {"cursor": "1", "records": [self.record("A")]},
                    {"cursor": "2", "records": []},
                    {"cursor": "3", "records": [self.record("B")]},
                ],
            },
            "empty-intermediate-page.json",
        )

        report = run_offline_incremental(self.config(fixture))

        self.assertEqual((report.records_seen, report.new), (2, 2))
        connection = sqlite3.connect(self.database)
        cursor = connection.execute(
            "SELECT progress_cursor FROM sync_window"
        ).fetchone()[0]
        connection.close()
        self.assertEqual(cursor, "3")

    def test_relationship_resume_still_skips_completed_target(self):
        failing = self.write_fixture(
            {
                "strategy": "relationship",
                "targets": [
                    {
                        "target": {"numero_controle": "A"},
                        "pages": [
                            {"records": [self.record("A|1")]},
                        ],
                    },
                    {
                        "target": {"numero_controle": "B"},
                        "pages": [
                            {"records": [self.record("B|1")]},
                        ],
                    },
                ],
                "fail_after_checkpoints": 1,
            },
            "relationship-failure.json",
        )
        with self.assertRaises(RuntimeError):
            run_offline_incremental(
                self.config(failing, resource="contratacao_item")
            )

        recovered = self.write_fixture(
            {
                "strategy": "relationship",
                "targets": [
                    {
                        "target": {"numero_controle": "A"},
                        "pages": [
                            {"records": [self.record("A|1")]},
                        ],
                    },
                    {
                        "target": {"numero_controle": "B"},
                        "pages": [
                            {"records": [self.record("B|1")]},
                        ],
                    },
                ],
            },
            "relationship-recovery.json",
        )

        report = run_offline_incremental(
            self.config(
                recovered,
                resource="contratacao_item",
                resume=True,
                worker_id="recovery-worker",
            )
        )

        self.assertEqual((report.records_seen, report.new), (1, 1))
        connection = sqlite3.connect(self.database)
        statuses = connection.execute(
            "SELECT target_key, status FROM sync_target_progress "
            "ORDER BY target_key"
        ).fetchall()
        connection.close()
        self.assertEqual(statuses, [("A", "COMPLETED"), ("B", "COMPLETED")])

    def test_existing_entity_is_updated_in_later_window(self):
        first = self.endpoint_fixture([self.record(state="A")], name="a.json")
        second = self.endpoint_fixture(
            [
                self.record(
                    state="B", timestamp="2023-05-10T10:00:00Z"
                )
            ],
            name="b.json",
        )
        initial = run_offline_incremental(self.config(first))
        changed = run_offline_incremental(
            self.config(
                second,
                start_date="2023-05-01",
                end_date="2023-05-31",
            )
        )

        self.assertEqual((initial.new, changed.updated), (1, 1))
        connection = sqlite3.connect(self.database)
        chain = connection.execute(
            "SELECT id, previous_version_id FROM raw_asset ORDER BY rowid"
        ).fetchall()
        self.assertEqual(len(chain), 2)
        self.assertEqual(chain[1][1], chain[0][0])
        connection.close()

    def test_relationship_resource_confirms_stable_target(self):
        fixture = self.write_fixture(
            {
                "strategy": "relationship",
                "targets": [
                    {
                        "target": {"numero_controle": "compra-1"},
                        "pages": [
                            {
                                "cursor": "page-1",
                                "records": [
                                    {
                                        "external_id": "compra-1|1",
                                        "payload": {
                                            "numeroItem": 1,
                                            "descricao": "item",
                                        },
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        )

        report = run_offline_incremental(
            self.config(fixture, resource="contratacao_item")
        )

        self.assertEqual((report.strategy, report.new), ("relationship", 1))
        connection = sqlite3.connect(self.database)
        target = connection.execute(
            "SELECT target_key, status, processed_fingerprint "
            "FROM sync_target_progress"
        ).fetchone()
        self.assertEqual(target[0:2], ("compra-1", "COMPLETED"))
        self.assertIsNotNone(target[2])
        connection.close()

    def test_new_relationship_target_before_completed_one_is_processed(self):
        original = self.write_fixture(
            {
                "strategy": "relationship",
                "targets": [
                    {
                        "target": {"numero_controle": "B"},
                        "pages": [{"records": []}],
                    }
                ],
            },
            "targets-1.json",
        )
        first = run_offline_incremental(
            self.config(original, resource="contratacao_item")
        )
        expanded = self.write_fixture(
            {
                "strategy": "relationship",
                "targets": [
                    {
                        "target": {"numero_controle": "A"},
                        "pages": [{"records": []}],
                    },
                    {
                        "target": {"numero_controle": "B"},
                        "pages": [{"records": []}],
                    },
                ],
            },
            "targets-2.json",
        )

        replay = run_offline_incremental(
            self.config(
                expanded,
                resource="contratacao_item",
                reprocess=True,
            )
        )

        self.assertTrue(replay.reprocessed)
        connection = sqlite3.connect(self.database)
        keys = {
            row[0]
            for row in connection.execute(
                "SELECT target_key FROM sync_target_progress"
            )
        }
        connection.close()
        self.assertEqual(keys, {"A", "B"})
        self.assertEqual(first.status, "SUCCEEDED")

    def test_reconciliation_captures_late_result_without_parent_change(self):
        target = {"numero_controle": "compra-1", "numero_item": 1}
        first = self.write_fixture(
            {
                "strategy": "relationship",
                "discoveries": [target],
                "responses": [
                    {
                        "target": target,
                        "outcome": "NOT_FOUND",
                        "pages": [{"records": []}],
                    }
                ],
            },
            "late-result-404.json",
        )
        late = self.write_fixture(
            {
                "strategy": "relationship",
                "discoveries": [],
                "responses": [
                    {
                        "target": target,
                        "outcome": "FOUND",
                        "pages": [
                            {
                                "records": [
                                    {
                                        "external_id": "compra-1|1|1",
                                        "payload": {"sequencialResultado": 1},
                                    }
                                ]
                            }
                        ],
                    }
                ],
            },
            "late-result-200.json",
        )

        empty_report = run_offline_incremental(
            self.config(
                first,
                resource="resultado_item",
                start_date="2023-04-01",
                end_date="2023-04-01",
            )
        )
        late_report = run_offline_incremental(
            self.config(
                late,
                resource="resultado_item",
                start_date="2023-04-02",
                end_date="2023-04-02",
            )
        )

        self.assertEqual((empty_report.records_seen, late_report.new), (0, 1))
        connection = sqlite3.connect(self.database)
        queue = connection.execute(
            "SELECT last_outcome, last_checked_at, last_found_at, "
            "consecutive_empty, next_due_at FROM sync_reconciliation_target"
        ).fetchone()
        raw_count = connection.execute(
            "SELECT COUNT(*) FROM raw_asset WHERE resource_type='resultado_item'"
        ).fetchone()[0]
        connection.close()
        self.assertEqual(
            queue,
            ("FOUND", "2023-04-02", "2023-04-02", 0, "2023-04-03"),
        )
        self.assertEqual(raw_count, 1)

    def test_legitimate_empty_is_reconciled_and_remains_auditable(self):
        target = {"numero_controle": "compra-1"}
        first = self.write_fixture(
            {
                "strategy": "relationship",
                "discoveries": [target],
                "responses": [
                    {"target": target, "outcome": "EMPTY", "pages": []}
                ],
            },
            "empty-first.json",
        )
        repeated = self.write_fixture(
            {
                "strategy": "relationship",
                "discoveries": [],
                "responses": [
                    {"target": target, "outcome": "EMPTY", "pages": []}
                ],
            },
            "empty-repeated.json",
        )

        run_offline_incremental(
            self.config(
                first,
                resource="contratacao_item",
                start_date="2023-04-01",
                end_date="2023-04-01",
            )
        )
        report = run_offline_incremental(
            self.config(
                repeated,
                resource="contratacao_item",
                start_date="2023-04-02",
                end_date="2023-04-02",
            )
        )

        connection = sqlite3.connect(self.database)
        queue = connection.execute(
            "SELECT last_outcome, consecutive_empty, next_due_at "
            "FROM sync_reconciliation_target"
        ).fetchone()
        raw_count = connection.execute("SELECT COUNT(*) FROM raw_asset").fetchone()[0]
        connection.close()
        self.assertEqual(report.records_seen, 0)
        self.assertEqual(queue, ("EMPTY", 2, "2023-04-03"))
        self.assertEqual(raw_count, 0)

    def test_relationship_failure_does_not_advance_reconciliation(self):
        target = {"numero_controle": "contrato-1"}
        failing = self.write_fixture(
            {
                "strategy": "relationship",
                "discoveries": [target],
                "responses": [
                    {
                        "target": target,
                        "outcome": "FOUND",
                        "pages": [
                            {
                                "records": [
                                    {
                                        "external_id": "contrato-1|1",
                                        "payload": {"sequencialEmpenho": 1},
                                    }
                                ]
                            }
                        ],
                    }
                ],
                "fail_after_records": 1,
            },
            "commitment-failure.json",
        )
        recovered = self.write_fixture(
            {
                "strategy": "relationship",
                "discoveries": [],
                "responses": [
                    {
                        "target": target,
                        "outcome": "FOUND",
                        "pages": [
                            {
                                "records": [
                                    {
                                        "external_id": "contrato-1|1",
                                        "payload": {"sequencialEmpenho": 1},
                                    }
                                ]
                            }
                        ],
                    }
                ],
            },
            "commitment-recovery.json",
        )

        with self.assertRaisesRegex(RuntimeError, "Falha offline simulada"):
            run_offline_incremental(
                self.config(
                    failing,
                    resource="empenho",
                    start_date="2023-04-01",
                    end_date="2023-04-01",
                )
            )
        connection = sqlite3.connect(self.database)
        before = connection.execute(
            "SELECT last_checked_at, last_outcome FROM sync_reconciliation_target"
        ).fetchone()
        connection.close()
        self.assertEqual(before, (None, None))

        report = run_offline_incremental(
            self.config(
                recovered,
                resource="empenho",
                start_date="2023-04-01",
                end_date="2023-04-01",
                resume=True,
                worker_id="recovery-worker",
            )
        )
        connection = sqlite3.connect(self.database)
        after = connection.execute(
            "SELECT last_checked_at, last_outcome, next_due_at "
            "FROM sync_reconciliation_target"
        ).fetchone()
        raw_count = connection.execute("SELECT COUNT(*) FROM raw_asset").fetchone()[0]
        connection.close()
        self.assertEqual(report.unchanged, 1)
        self.assertEqual(after, ("2023-04-01", "FOUND", "2023-04-08"))
        self.assertEqual(raw_count, 1)

    def test_reconciliation_respects_cadence_before_reopening_target(self):
        target = {"numero_controle": "contrato-1"}
        first = self.write_fixture(
            {
                "strategy": "relationship",
                "discoveries": [target],
                "responses": [
                    {"target": target, "outcome": "EMPTY", "pages": []}
                ],
            },
            "commitment-empty.json",
        )
        not_due = self.write_fixture(
            {
                "strategy": "relationship",
                "discoveries": [],
                "responses": [],
            },
            "commitment-not-due.json",
        )
        run_offline_incremental(
            self.config(
                first,
                resource="empenho",
                start_date="2023-04-01",
                end_date="2023-04-01",
            )
        )

        report = run_offline_incremental(
            self.config(
                not_due,
                resource="empenho",
                start_date="2023-04-02",
                end_date="2023-04-02",
            )
        )

        connection = sqlite3.connect(self.database)
        target_rows = connection.execute(
            "SELECT COUNT(*) FROM sync_target_progress"
        ).fetchone()[0]
        queue = connection.execute(
            "SELECT next_due_at, consecutive_empty "
            "FROM sync_reconciliation_target"
        ).fetchone()
        connection.close()
        self.assertEqual(report.records_seen, 0)
        self.assertEqual(target_rows, 1)
        self.assertEqual(queue, ("2023-04-08", 1))

    def test_composite_coverage_waits_for_every_expected_shard(self):
        fixture = self.endpoint_fixture([], name="empty-coverage.json")
        common = {
            "coverage_name": "contratacoes-rj-modalidades",
            "coverage_members": ("modalidade:6", "modalidade:8"),
            "start_date": "2023-04-01",
            "end_date": "2023-04-01",
        }

        first = run_offline_incremental(
            self.config(
                fixture,
                modality=6,
                coverage_member="modalidade:6",
                **common,
            )
        )
        connection = sqlite3.connect(self.database)
        composite_before = connection.execute(
            "SELECT status FROM sync_coverage_window"
        ).fetchone()[0]
        watermark_before = connection.execute(
            "SELECT COUNT(*) FROM sync_coverage_watermark"
        ).fetchone()[0]
        individual_before = connection.execute(
            "SELECT COUNT(*) FROM sync_watermark"
        ).fetchone()[0]
        connection.close()

        second = run_offline_incremental(
            self.config(
                fixture,
                modality=8,
                coverage_member="modalidade:8",
                **common,
            )
        )
        connection = sqlite3.connect(self.database)
        composite_after = connection.execute(
            "SELECT status FROM sync_coverage_window"
        ).fetchone()[0]
        coverage_watermark = connection.execute(
            "SELECT watermark_value FROM sync_coverage_watermark"
        ).fetchone()[0]
        member_statuses = connection.execute(
            "SELECT member_key, status FROM sync_coverage_member "
            "ORDER BY member_key"
        ).fetchall()
        individual_after = connection.execute(
            "SELECT COUNT(*) FROM sync_watermark"
        ).fetchone()[0]
        connection.close()

        self.assertEqual(first.coverage_status, "PENDING")
        self.assertEqual((composite_before, watermark_before), ("PENDING", 0))
        self.assertEqual(individual_before, 1)
        self.assertEqual(second.coverage_status, "COMPLETED")
        self.assertEqual(composite_after, "COMPLETED")
        self.assertEqual(
            coverage_watermark, "2023-04-01T23:59:59.999999"
        )
        self.assertEqual(
            member_statuses,
            [
                ("modalidade:6", "COMPLETED"),
                ("modalidade:8", "COMPLETED"),
            ],
        )
        self.assertEqual(individual_after, 2)

    def test_coverage_manifest_cannot_change_after_it_is_frozen(self):
        fixture = self.endpoint_fixture([], name="frozen-coverage.json")
        run_offline_incremental(
            self.config(
                fixture,
                modality=6,
                start_date="2023-04-01",
                end_date="2023-04-01",
                coverage_name="contratacoes-rj-modalidades",
                coverage_member="modalidade:6",
                coverage_members=("modalidade:6", "modalidade:8"),
            )
        )

        with self.assertRaisesRegex(ValueError, "congelado"):
            run_offline_incremental(
                self.config(
                    fixture,
                    modality=9,
                    start_date="2023-04-02",
                    end_date="2023-04-02",
                    coverage_name="contratacoes-rj-modalidades",
                    coverage_member="modalidade:9",
                    coverage_members=(
                        "modalidade:6",
                        "modalidade:8",
                        "modalidade:9",
                    ),
                )
            )

    def test_historical_fetch_entrypoint_remains_legacy(self):
        import inspect
        from src import fetch_corpus

        signature = inspect.signature(fetch_corpus.run)
        source = inspect.getsource(fetch_corpus.run)

        self.assertEqual(
            list(signature.parameters),
            ["manifest_path", "source_id", "mode", "database_url"],
        )
        self.assertIn("repository.get_checkpoint", source)
        self.assertNotIn("run_offline_incremental", source)

    def test_incremental_run_does_not_change_legacy_checkpoint(self):
        repository = RawRepository(f"sqlite:///{self.database}")
        ingest_records(
            repository,
            "legacy",
            ("numeroControlePNCP",),
            [
                CollectedRecord(
                    "legacy",
                    {
                        "numeroControlePNCP": "legacy-1",
                        "dataAtualizacaoGlobal": "2023-01-01T00:00:00Z",
                    },
                    "offline://legacy",
                    "7",
                    1,
                    external_id="legacy-1",
                )
            ],
            logger=SilentLogger(),
            resource_type="contratacao",
        )
        before = tuple(
            repository.connection.execute(
                "SELECT * FROM sync_checkpoint"
            ).fetchone()
        )
        repository.close()
        fixture = self.endpoint_fixture([self.record()])

        run_offline_incremental(self.config(fixture))

        connection = sqlite3.connect(self.database)
        after = tuple(connection.execute("SELECT * FROM sync_checkpoint").fetchone())
        connection.close()
        self.assertEqual(after, before)

    def test_offline_runner_does_not_call_network(self):
        fixture = self.endpoint_fixture([self.record()])
        with patch(
            "urllib.request.urlopen",
            side_effect=AssertionError("network called"),
        ) as network:
            report = run_offline_incremental(self.config(fixture))

        network.assert_not_called()
        self.assertEqual(report.status, "SUCCEEDED")

    def test_multiple_pages_are_processed_and_checkpointed(self):
        fixture = self.write_fixture(
            {
                "strategy": "endpoint",
                "pages": [
                    {"cursor": "page-a", "records": [self.record("A")]},
                    {"cursor": "page-b", "records": [self.record("B")]},
                ],
            }
        )

        report = run_offline_incremental(self.config(fixture))

        self.assertEqual((report.records_seen, report.new), (2, 2))
        connection = sqlite3.connect(self.database)
        self.assertEqual(
            connection.execute(
                "SELECT progress_cursor FROM sync_window"
            ).fetchone()[0],
            "page-b",
        )
        connection.close()

    def test_explicit_reprocessing_is_idempotent(self):
        fixture = self.endpoint_fixture([self.record()])
        first = run_offline_incremental(self.config(fixture))
        replay = run_offline_incremental(
            self.config(fixture, reprocess=True, worker_id="reprocess-worker")
        )

        self.assertEqual((first.new, replay.unchanged), (1, 1))
        connection = sqlite3.connect(self.database)
        self.assertEqual(
            connection.execute("SELECT COUNT(*) FROM raw_asset").fetchone()[0],
            1,
        )
        self.assertEqual(
            connection.execute("SELECT COUNT(*) FROM ingestion_run").fetchone()[0],
            2,
        )
        connection.close()


if __name__ == "__main__":
    unittest.main()
