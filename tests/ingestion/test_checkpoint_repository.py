import tempfile
import unittest
from pathlib import Path

from app.core.database import initialize_database
from app.core.database.repositories.checkpoint_repository import (
    CheckpointRepository,
)
from app.core.database.repositories.run_repository import RunRepository


class CheckpointRepositoryTests(unittest.TestCase):
    def test_checkpoint_changes_only_when_update_is_called_after_success(self):
        with tempfile.TemporaryDirectory() as directory:
            connection = initialize_database(
                f"sqlite:///{Path(directory) / 'checkpoint.db'}"
            )
            runs = RunRepository(connection)
            checkpoints = CheckpointRepository(connection)
            run_id = runs.start_run("pncp", "records")

            self.assertIsNone(checkpoints.get_checkpoint("pncp", "records"))
            with self.assertRaises(ValueError):
                checkpoints.update_checkpoint("pncp", "records", run_id, 3)
            runs.finish_run(
                run_id,
                {
                    "records_seen": 0,
                    "NEW": 0,
                    "UPDATED": 0,
                    "UNCHANGED": 0,
                    "FAILED": 0,
                },
                pages_processed=3,
            )
            checkpoints.update_checkpoint("pncp", "records", run_id, 3)
            checkpoint = checkpoints.get_checkpoint("pncp", "records")

            self.assertEqual(checkpoint["last_page"], 3)
            self.assertEqual(checkpoint["last_run_id"], run_id)
            self.assertEqual(checkpoint["status"], "SUCCESS")
            connection.close()


if __name__ == "__main__":
    unittest.main()
