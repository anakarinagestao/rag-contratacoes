import tempfile
import unittest
from pathlib import Path

from src.ingest import RawRepository, ingest_records
from src.inspection.raw_database import inspect_database
from src.schema import CollectedRecord


class RawDatabaseInspectionTests(unittest.TestCase):
    def test_reports_counts_without_returning_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "debug.db"
            database_url = f"sqlite:///{path}"
            repository = RawRepository(database_url)
            ingest_records(
                repository,
                "pncp_pca_rj",
                ("idPcaPncp",),
                [
                    CollectedRecord(
                        "pncp_pca_rj",
                        {"idPcaPncp": "pca-1", "segredo": "omitido"},
                        "https://pncp.example",
                        "1",
                        1,
                    )
                ],
                resource_type="pca",
                natureza="agregada",
            )
            repository.close()

            report = inspect_database(database_url)

            self.assertTrue(report["payload_omitted"])
            self.assertEqual(report["asset_summary"][0]["versions"], 1)
            self.assertNotIn("segredo", str(report))


if __name__ == "__main__":
    unittest.main()
