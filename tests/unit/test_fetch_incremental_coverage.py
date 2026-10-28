from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.core.database.init_db import initialize_database
from src.fetch_incremental_coverage import main


class FetchIncrementalCoverageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.database = Path(self.temporary.name) / "coverage.db"
        connection = initialize_database(f"sqlite:///{self.database}")
        connection.close()

    def tearDown(self):
        self.temporary.cleanup()

    def arguments(self):
        return [
            "--date",
            "2026-10-16",
            "--db-path",
            str(self.database),
        ]

    def test_default_is_read_only_dry_run(self):
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            status = main(self.arguments())

        result = json.loads(stdout.getvalue())
        self.assertEqual(status, 0)
        self.assertTrue(result["plan"]["ready"])
        self.assertEqual(len(result["plan"]["members"]), 14)
        self.assertEqual(result["reports"], [])
        connection = initialize_database(f"sqlite:///{self.database}")
        self.assertEqual(
            connection.execute(
                "SELECT COUNT(*) FROM sync_coverage_plan"
            ).fetchone()[0],
            0,
        )
        connection.close()

    def test_execute_requires_repeated_database_confirmation(self):
        stderr = io.StringIO()

        with redirect_stderr(stderr):
            status = main(
                self.arguments()
                + [
                    "--execute",
                    "--log-path",
                    str(self.database.with_suffix(".jsonl")),
                ]
            )

        self.assertEqual(status, 1)
        self.assertIn("--confirm-operational-database", stderr.getvalue())

    @patch("src.fetch_incremental_coverage.execute_contracting_coverage")
    def test_execute_routes_validated_plan_to_orchestrator(self, execute):
        execute.return_value = ()
        stdout = io.StringIO()
        log_path = self.database.with_suffix(".jsonl")

        with redirect_stdout(stdout):
            status = main(
                self.arguments()
                + [
                    "--execute",
                    "--confirm-operational-database",
                    str(self.database),
                    "--log-path",
                    str(log_path),
                ]
            )

        self.assertEqual(status, 0)
        execute.assert_called_once()
        self.assertEqual(
            execute.call_args.kwargs["confirmed_database"],
            self.database.resolve(),
        )
        self.assertEqual(execute.call_args.kwargs["log_path"], log_path)
        self.assertEqual(json.loads(stdout.getvalue())["reports"], [])


if __name__ == "__main__":
    unittest.main()
