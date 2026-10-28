from __future__ import annotations

import json
import sqlite3
import unittest

from src.normalization.current import iter_current_raw_rows


class CurrentRawSelectionTests(unittest.TestCase):
    def test_selects_current_by_canonical_identity_and_source_timestamp(self) -> None:
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.execute(
            "CREATE TABLE raw_asset ("
            "id TEXT PRIMARY KEY, base_id TEXT NOT NULL, "
            "resource_type TEXT NOT NULL, external_id TEXT NOT NULL, "
            "payload_hash TEXT NOT NULL, payload TEXT NOT NULL, "
            "query_params TEXT NOT NULL, source_updated_at TEXT, "
            "fetched_at TEXT NOT NULL)"
        )
        payload = {"numeroControlePNCP": "entity-1"}
        rows = (
            ("a", "hash-a", "2026-01-02T00:00:00", "2026-01-02T01:00:00"),
            ("b", "hash-b", "2026-01-03T00:00:00", "2026-01-03T01:00:00"),
            # Chegou depois, mas representa um estado mais antigo.
            ("old", "hash-old", "2026-01-01T00:00:00", "2026-01-04T01:00:00"),
            # Timestamp ausente não supera uma versão com timestamp válido.
            ("missing", "hash-missing", None, "2026-01-05T01:00:00"),
        )
        for raw_id, digest, updated, fetched in rows:
            connection.execute(
                "INSERT INTO raw_asset VALUES (?, 'pncp', 'contratacao', "
                "'entity-1', ?, ?, '{}', ?, ?)",
                (raw_id, digest, json.dumps(payload), updated, fetched),
            )

        current = list(iter_current_raw_rows(connection, "contratacao"))

        self.assertEqual(len(current), 1)
        self.assertEqual(current[0]["id"], "b")
        self.assertEqual(current[0]["payload_hash"], "hash-b")

    def test_legacy_snapshot_falls_back_to_last_arrival(self) -> None:
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.execute(
            "CREATE TABLE raw_asset ("
            "id TEXT PRIMARY KEY, resource_type TEXT NOT NULL, "
            "external_id TEXT NOT NULL, payload_hash TEXT NOT NULL, "
            "payload TEXT NOT NULL)"
        )
        for raw_id in ("first", "last"):
            connection.execute(
                "INSERT INTO raw_asset VALUES (?, 'contratacao', 'entity-1', ?, ?)",
                (raw_id, f"hash-{raw_id}", json.dumps({"id": raw_id})),
            )

        current = list(iter_current_raw_rows(connection, "contratacao"))

        self.assertEqual(current[0]["id"], "last")


if __name__ == "__main__":
    unittest.main()
