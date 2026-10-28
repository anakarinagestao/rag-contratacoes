"""Testes offline para a política de artefatos versionados."""

import unittest

from scripts.check_repository_governance import (
    MAX_TRACKED_FILE_BYTES,
    policy_violations,
)


class RepositoryGovernanceTests(unittest.TestCase):
    def test_allows_reproducible_project_files(self):
        allowed = (
            ".env.example",
            "migrations/001_schema.sql",
            "tests/fixtures/synthetic.json",
            "corpus_manifest.yaml",
            "src/index.py",
        )
        for path in allowed:
            with self.subTest(path=path):
                self.assertEqual(policy_violations(path, 1024), [])

    def test_blocks_operational_sqlite_and_sidecar_files(self):
        blocked = (
            "app.db",
            "data/runtime.sqlite",
            "data/runtime.sqlite3-wal",
            "data/runtime.db-shm",
            "data/runtime.db-journal",
        )
        for path in blocked:
            with self.subTest(path=path):
                self.assertIn(
                    "banco SQLite operacional",
                    policy_violations(path, 1024),
                )

    def test_blocks_credentials_indexes_logs_caches_and_temporaries(self):
        blocked = (
            ".env.local",
            "config/private.key",
            "data/index/faiss/current.faiss",
            "data/embeddings/vectors.npy",
            "logs/runtime.log.1",
            ".pytest_cache/state.json",
            "data/export.tmp",
        )
        for path in blocked:
            with self.subTest(path=path):
                self.assertTrue(policy_violations(path, 1024))

    def test_blocks_large_new_artifact(self):
        violations = policy_violations(
            "data/generated/artifact.json",
            MAX_TRACKED_FILE_BYTES + 1,
        )
        self.assertIn("arquivo maior que 50 MiB", violations)

    def test_blocks_generated_chunks_even_below_size_limit(self):
        violations = policy_violations(
            "data/chunks/v2/2026/chunks_2026_10_lote_001.jsonl",
            1024,
        )
        self.assertIn("artefato de chunks gerado", violations)

    def test_accepts_file_at_size_limit(self):
        self.assertEqual(
            policy_violations("tests/fixtures/authorized.json", MAX_TRACKED_FILE_BYTES),
            [],
        )


if __name__ == "__main__":
    unittest.main()
