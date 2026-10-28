import json
import tempfile
import unittest
from pathlib import Path

from app.core.database import initialize_database
from app.core.database.repositories.raw_repository import (
    RawRepository as AssetRepository,
)
from app.core.database.repositories.run_repository import RunRepository
from src.ingest import RawRepository, ingest_records
from src.ingestion.hashing import payload_hash
from src.ingestion.identity import (
    CanonicalIdentity,
    TimestampKind,
    TimestampStatus,
    extract_source_timestamps,
    parse_timestamp,
)
from src.schema import CollectedRecord


class SilentLogger:
    def event(self, event, **fields):
        pass


class IncrementalIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "incremental.db"
        self.database_url = f"sqlite:///{self.database_path}"
        self.logger = SilentLogger()

    def tearDown(self):
        self.temp_dir.cleanup()

    @staticmethod
    def record(
        source,
        state,
        updated_at="2023-04-01T10:00:00Z",
        external_id="compra-1",
    ):
        return CollectedRecord(
            source,
            {
                "numeroControlePNCP": external_id,
                "estado": state,
                "dataAtualizacaoGlobal": updated_at,
            },
            "https://pncp.example/contratacoes",
            "1",
            1,
            external_id=external_id,
        )

    def ingest(self, repository, source, records, resource_type="contratacao"):
        return ingest_records(
            repository,
            source,
            ("numeroControlePNCP",),
            records,
            resource_type=resource_type,
            logger=self.logger,
        )

    def ingest_window(
        self,
        repository,
        source,
        records,
        window_start,
        window_end,
        resource_type="contratacao",
    ):
        return ingest_records(
            repository,
            source,
            ("numeroControlePNCP",),
            records,
            resource_type=resource_type,
            logger=self.logger,
            window_start=window_start,
            window_end=window_end,
        )

    def test_timestamp_parser_distinguishes_valid_missing_and_invalid(self):
        valid = parse_timestamp("2023-04-01T10:00:00Z")
        missing = parse_timestamp(None)
        invalid = parse_timestamp("nao-e-uma-data")

        self.assertEqual(valid.status, TimestampStatus.VALID)
        self.assertEqual(valid.kind, TimestampKind.AWARE_DATETIME)
        self.assertEqual(valid.normalized, "2023-04-01T10:00:00.000000")
        self.assertEqual(missing.status, TimestampStatus.MISSING)
        self.assertIsNone(missing.normalized)
        self.assertEqual(invalid.status, TimestampStatus.INVALID)
        self.assertIsNone(invalid.normalized)

    def test_equivalent_timezone_offsets_have_same_normalized_timestamp(self):
        utc = parse_timestamp("2023-04-01T13:00:00Z")
        brazil = parse_timestamp("2023-04-01T10:00:00-03:00")

        self.assertEqual(utc.normalized, brazil.normalized)
        self.assertEqual(utc.kind, brazil.kind)

    def test_date_and_naive_datetime_keep_distinct_semantics(self):
        date_only = parse_timestamp("2023-04-01")
        naive = parse_timestamp("2023-04-01T00:00:00")

        self.assertEqual(date_only.kind, TimestampKind.DATE)
        self.assertEqual(naive.kind, TimestampKind.NAIVE_DATETIME)
        self.assertNotEqual(date_only.kind, naive.kind)

    def test_invalid_preferred_field_uses_valid_fallback(self):
        timestamps = extract_source_timestamps(
            {
                "dataAtualizacaoGlobal": "invalida",
                "dataAtualizacao": "2023-04-01T10:00:00Z",
            },
            "contratacao",
        )

        self.assertEqual(timestamps.updated.status, TimestampStatus.VALID)
        self.assertEqual(timestamps.updated.field, "dataAtualizacao")
        self.assertEqual(
            timestamps.updated.normalized, "2023-04-01T10:00:00.000000"
        )
        self.assertEqual(
            timestamps.updated.invalid_candidates,
            (("dataAtualizacaoGlobal", "invalida"),),
        )

    def test_historical_and_incremental_collectors_share_canonical_identity(self):
        repository = RawRepository(self.database_url)
        historical = self.record(
            "pncp_historico", "PUBLICADA", "2023-04-01T10:00:00Z"
        )
        incremental = self.record(
            "pncp_incremental", "PUBLICADA", "2023-04-01T10:00:00Z"
        )

        first = self.ingest(repository, "pncp_historico", [historical])
        second = self.ingest(repository, "pncp_incremental", [incremental])

        self.assertEqual((first.new, second.unchanged), (1, 1))
        self.assertEqual(
            repository.connection.execute(
                "SELECT COUNT(*) FROM raw_asset"
            ).fetchone()[0],
            1,
        )
        self.assertEqual(
            repository.connection.execute(
                "SELECT COUNT(*) FROM ingestion_run"
            ).fetchone()[0],
            2,
        )
        repository.close()

    def test_new_entity_is_classified_as_new(self):
        repository = RawRepository(self.database_url)
        result = self.ingest(
            repository,
            "pncp_incremental",
            [self.record("pncp_incremental", "A", "2023-04-01T10:00:00Z")],
        )

        self.assertEqual((result.new, result.updated, result.unchanged), (1, 0, 0))
        repository.close()

    def test_changed_content_is_classified_as_updated(self):
        repository = RawRepository(self.database_url)
        self.ingest(
            repository,
            "pncp_incremental",
            [self.record("pncp_incremental", "A", "2023-04-01T10:00:00Z")],
        )
        result = self.ingest(
            repository,
            "pncp_incremental",
            [self.record("pncp_incremental", "B", "2023-04-02T10:00:00Z")],
        )

        self.assertEqual((result.new, result.updated, result.unchanged), (0, 1, 0))
        repository.close()

    def test_same_observation_is_classified_as_unchanged(self):
        repository = RawRepository(self.database_url)
        record = self.record(
            "pncp_incremental", "A", "2023-04-01T10:00:00Z"
        )
        self.ingest(repository, "pncp_incremental", [record])
        result = self.ingest(repository, "pncp_incremental", [record])

        self.assertEqual((result.new, result.updated, result.unchanged), (0, 0, 1))
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
        repository.close()

    def test_unchanged_in_new_window_records_observation_without_new_version(self):
        repository = RawRepository(self.database_url)
        first = self.record(
            "pncp_incremental", "A", "2023-04-01T10:00:00Z"
        )
        second = self.record(
            "pncp_incremental", "A", "2023-04-01T10:00:00Z"
        )

        self.ingest_window(
            repository,
            "pncp_incremental",
            [first],
            "2023-04-01",
            "2023-04-01",
        )
        result = self.ingest_window(
            repository,
            "pncp_incremental",
            [second],
            "2023-04-02",
            "2023-04-02",
        )

        self.assertEqual(result.unchanged, 1)
        self.assertEqual(
            repository.connection.execute(
                "SELECT COUNT(*) FROM raw_asset"
            ).fetchone()[0],
            1,
        )
        observations = repository.connection.execute(
            "SELECT raw_asset_id, window_start FROM raw_observation "
            "ORDER BY window_start"
        ).fetchall()
        self.assertEqual(
            [row["window_start"] for row in observations],
            ["2023-04-01", "2023-04-02"],
        )
        self.assertEqual(
            len({row["raw_asset_id"] for row in observations}), 1
        )
        repository.close()

    def test_reversion_a_b_a_preserves_three_observations(self):
        repository = RawRepository(self.database_url)
        observations = (
            self.record("pncp_incremental", "A", "2023-04-01T10:00:00Z"),
            self.record("pncp_incremental", "B", "2023-04-02T10:00:00Z"),
            self.record("pncp_incremental", "A", "2023-04-03T10:00:00Z"),
        )

        results = [
            self.ingest(repository, "pncp_incremental", [record])
            for record in observations
        ]
        states = [
            json.loads(row["payload"])["estado"]
            for row in repository.connection.execute(
                "SELECT payload FROM raw_asset ORDER BY rowid"
            )
        ]

        self.assertEqual(
            [(result.new, result.updated) for result in results],
            [(1, 0), (0, 1), (0, 1)],
        )
        self.assertEqual(states, ["A", "B", "A"])
        repository.close()

    def test_same_timestamp_reversion_in_new_window_is_preserved(self):
        repository = RawRepository(self.database_url)
        timestamp = "2023-04-01T10:00:00Z"
        observations = (
            ("A", "2023-04-01", "2023-04-01"),
            ("B", "2023-04-02", "2023-04-02"),
            ("A", "2023-04-03", "2023-04-03"),
        )

        results = [
            self.ingest_window(
                repository,
                "pncp_incremental",
                [self.record("pncp_incremental", state, timestamp)],
                start,
                end,
            )
            for state, start, end in observations
        ]
        replay = self.ingest_window(
            repository,
            "pncp_incremental",
            [self.record("pncp_incremental", "A", timestamp)],
            "2023-04-03",
            "2023-04-03",
        )

        self.assertEqual(
            [(result.new, result.updated) for result in results],
            [(1, 0), (0, 1), (0, 1)],
        )
        self.assertEqual(replay.unchanged, 1)
        self.assertEqual(
            repository.connection.execute(
                "SELECT COUNT(*) FROM raw_asset"
            ).fetchone()[0],
            3,
        )
        self.assertEqual(
            repository.connection.execute(
                "SELECT COUNT(*) FROM raw_observation"
            ).fetchone()[0],
            3,
        )
        links = repository.connection.execute(
            "SELECT raw_asset_id FROM raw_observation ORDER BY observed_at"
        ).fetchall()
        self.assertEqual(len({row["raw_asset_id"] for row in links}), 3)
        repository.close()

    def test_missing_timestamp_reversion_in_new_window_is_preserved(self):
        repository = RawRepository(self.database_url)
        observations = (
            ("A", "2023-04-01"),
            ("B", "2023-04-02"),
            ("A", "2023-04-03"),
        )

        results = [
            self.ingest_window(
                repository,
                "pncp_incremental",
                [self.record("pncp_incremental", state, None)],
                window,
                window,
            )
            for state, window in observations
        ]
        replay = self.ingest_window(
            repository,
            "pncp_incremental",
            [self.record("pncp_incremental", "A", None)],
            "2023-04-03",
            "2023-04-03",
        )

        self.assertEqual(
            [(result.new, result.updated) for result in results],
            [(1, 0), (0, 1), (0, 1)],
        )
        self.assertEqual(replay.unchanged, 1)
        self.assertEqual(
            repository.connection.execute(
                "SELECT COUNT(*) FROM raw_asset"
            ).fetchone()[0],
            3,
        )
        repository.close()

    def test_invalid_timestamp_never_replaces_valid_current_state(self):
        repository = RawRepository(self.database_url)
        self.ingest_window(
            repository,
            "pncp_incremental",
            [self.record("pncp_incremental", "VALIDO", "2023-04-01T10:00:00Z")],
            "2023-04-01",
            "2023-04-01",
        )
        result = self.ingest_window(
            repository,
            "pncp_incremental",
            [self.record("pncp_incremental", "INVALIDO", "zzzz")],
            "2023-04-02",
            "2023-04-02",
        )

        current = repository.assets.find_current(
            CanonicalIdentity("pncp", "contratacao", "compra-1")
        )
        self.assertEqual(result.updated, 1)
        self.assertEqual(json.loads(current["payload"])["estado"], "VALIDO")
        repository.close()

    def test_missing_timestamp_never_replaces_valid_current_state(self):
        repository = RawRepository(self.database_url)
        self.ingest_window(
            repository,
            "pncp_incremental",
            [self.record("pncp_incremental", "VALIDO", "2023-04-01T10:00:00Z")],
            "2023-04-01",
            "2023-04-01",
        )
        result = self.ingest_window(
            repository,
            "pncp_incremental",
            [self.record("pncp_incremental", "SEM_DATA", None)],
            "2023-04-02",
            "2023-04-02",
        )

        current = repository.assets.find_current(
            CanonicalIdentity("pncp", "contratacao", "compra-1")
        )
        self.assertEqual(result.updated, 1)
        self.assertEqual(json.loads(current["payload"])["estado"], "VALIDO")
        repository.close()

    def test_previous_version_chain_is_preserved(self):
        repository = RawRepository(self.database_url)
        for state, timestamp in (
            ("A", "2023-04-01T10:00:00Z"),
            ("B", "2023-04-02T10:00:00Z"),
            ("A", "2023-04-03T10:00:00Z"),
        ):
            self.ingest(
                repository,
                "pncp_incremental",
                [self.record("pncp_incremental", state, timestamp)],
            )

        rows = repository.connection.execute(
            "SELECT id, previous_version_id FROM raw_asset ORDER BY rowid"
        ).fetchall()
        self.assertIsNone(rows[0]["previous_version_id"])
        self.assertEqual(rows[1]["previous_version_id"], rows[0]["id"])
        self.assertEqual(rows[2]["previous_version_id"], rows[1]["id"])
        repository.close()

    def test_reexecution_is_idempotent_even_without_source_timestamp(self):
        repository = RawRepository(self.database_url)
        first = CollectedRecord(
            "pncp_historico",
            {"idPcaPncp": "pca-1", "itens": [{"numeroItem": 1}]},
            "https://pncp.example/pca",
            "1",
            1,
            external_id="pca-1",
        )
        second = CollectedRecord(
            "pncp_historico",
            {"idPcaPncp": "pca-1", "itens": [{"numeroItem": 2}]},
            "https://pncp.example/pca",
            "2",
            2,
            external_id="pca-1",
        )

        initial = self.ingest(
            repository, "pncp_historico", [first, second], resource_type="pca"
        )
        replay = self.ingest(
            repository, "pncp_historico", [first, second], resource_type="pca"
        )

        self.assertEqual((initial.new, initial.updated), (1, 1))
        self.assertEqual(
            (replay.new, replay.updated, replay.unchanged), (0, 0, 2)
        )
        self.assertEqual(
            repository.connection.execute(
                "SELECT COUNT(*) FROM raw_asset"
            ).fetchone()[0],
            2,
        )
        repository.close()

    def test_replay_remains_idempotent_when_pagination_changes(self):
        repository = RawRepository(self.database_url)
        first = CollectedRecord(
            "pncp_incremental",
            {
                "numeroControlePNCP": "compra-1",
                "estado": "A",
                "dataAtualizacaoGlobal": "2023-04-01T10:00:00Z",
            },
            "https://pncp.example/contratacoes",
            "1",
            1,
            external_id="compra-1",
        )
        second = self.record(
            "pncp_incremental", "B", "2023-04-02T10:00:00Z"
        )
        replay_with_new_page = CollectedRecord(
            first.source_id,
            first.payload,
            first.source_url,
            "9",
            25,
            external_id=first.external_id,
        )

        self.ingest_window(
            repository,
            "pncp_incremental",
            [first],
            "2023-04-01",
            "2023-04-01",
        )
        self.ingest_window(
            repository,
            "pncp_incremental",
            [second],
            "2023-04-01",
            "2023-04-01",
        )
        replay = self.ingest_window(
            repository,
            "pncp_incremental",
            [replay_with_new_page],
            "2023-04-01",
            "2023-04-01",
        )

        self.assertEqual(replay.unchanged, 1)
        self.assertEqual(
            repository.connection.execute(
                "SELECT COUNT(*) FROM raw_asset"
            ).fetchone()[0],
            2,
        )
        repository.close()

    def test_same_apparent_key_in_distinct_resources_is_not_a_collision(self):
        repository = RawRepository(self.database_url)
        external_id = "12345678000199-1-000001/2023"
        contratacao = self.record(
            "pncp_incremental",
            "PUBLICADA",
            "2023-04-01T10:00:00Z",
            external_id,
        )
        contrato = CollectedRecord(
            "pncp_incremental",
            {
                "numeroControlePNCP": external_id,
                "dataAtualizacaoGlobal": "2023-04-01T10:00:00Z",
            },
            "https://pncp.example/contratos",
            "1",
            1,
            external_id=external_id,
        )

        purchase_result = self.ingest(
            repository, "pncp_incremental", [contratacao], "contratacao"
        )
        contract_result = self.ingest(
            repository, "pncp_incremental", [contrato], "contrato"
        )

        self.assertEqual((purchase_result.new, contract_result.new), (1, 1))
        self.assertEqual(
            repository.connection.execute(
                "SELECT COUNT(*) FROM raw_asset"
            ).fetchone()[0],
            2,
        )
        repository.close()

    def test_out_of_order_version_is_preserved_but_does_not_become_current(self):
        repository = RawRepository(self.database_url)
        for state, timestamp in (
            ("A", "2023-04-02T10:00:00Z"),
            ("B", "2023-04-03T10:00:00Z"),
            ("C", "2023-04-01T10:00:00Z"),
        ):
            self.ingest(
                repository,
                "pncp_incremental",
                [self.record("pncp_incremental", state, timestamp)],
            )

        current = repository.assets.find_current(
            CanonicalIdentity("pncp", "contratacao", "compra-1")
        )
        latest_payloads = repository.latest_payloads(
            "pncp_incremental", "contratacao"
        )
        replay = self.ingest(
            repository,
            "pncp_incremental",
            [self.record("pncp_incremental", "C", "2023-04-01T10:00:00Z")],
        )

        self.assertEqual(json.loads(current["payload"])["estado"], "B")
        self.assertEqual(latest_payloads[0]["estado"], "B")
        self.assertEqual(replay.unchanged, 1)
        self.assertEqual(
            repository.connection.execute(
                "SELECT COUNT(*) FROM raw_asset"
            ).fetchone()[0],
            3,
        )
        repository.close()

    def test_existing_historical_schema_and_null_metadata_remain_compatible(self):
        connection = initialize_database(self.database_url)
        connection.execute("DROP TABLE raw_observation")
        connection.execute("DROP TABLE sync_coverage_watermark")
        connection.execute("DROP TABLE sync_coverage_member")
        connection.execute("DROP TABLE sync_coverage_window")
        connection.execute("DROP TABLE sync_coverage_plan")
        connection.execute("DROP TABLE sync_reconciliation_target")
        connection.execute("DROP TABLE sync_watermark")
        connection.execute("DROP TABLE sync_target_progress")
        connection.execute("DROP TABLE sync_window")
        connection.execute("DROP TABLE sync_job")
        connection.execute("DROP INDEX idx_raw_asset_canonical_identity")
        runs = RunRepository(connection)
        assets = AssetRepository(connection)
        run_id = runs.start_run("pncp_historico", "contratacao")
        legacy = self.record(
            "pncp_historico", "A", "2023-04-01T10:00:00Z"
        )
        legacy_id = assets.insert_raw(
            run_id,
            legacy,
            "compra-1",
            payload_hash(legacy.payload),
            resource_type="contratacao",
            source_updated_at=None,
        )
        connection.close()

        repository = RawRepository(self.database_url)
        indexes = {
            row[0]
            for row in repository.connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            )
        }
        tables = {
            row[0]
            for row in repository.connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        result = self.ingest(
            repository,
            "pncp_incremental",
            [self.record("pncp_incremental", "B", "2023-04-02T10:00:00Z")],
        )
        new_row = repository.connection.execute(
            "SELECT previous_version_id FROM raw_asset ORDER BY rowid DESC LIMIT 1"
        ).fetchone()

        self.assertNotIn("idx_raw_asset_canonical_identity", indexes)
        self.assertNotIn("sync_job", tables)
        self.assertEqual(result.updated, 1)
        self.assertEqual(new_row["previous_version_id"], legacy_id)
        repository.close()


class CanonicalIdentityMigrationTests(unittest.TestCase):
    def test_migration_is_additive_and_preserves_raw_and_checkpoint(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database_url = f"sqlite:///{Path(temp_dir) / 'migration.db'}"
            repository = RawRepository(database_url)
            record = IncrementalIdentityTests.record(
                "pncp_historico", "A", "2023-04-01T10:00:00Z"
            )
            ingest_records(
                repository,
                "pncp_historico",
                ("numeroControlePNCP",),
                [record],
                resource_type="contratacao",
                logger=SilentLogger(),
            )
            repository.connection.execute(
                "DROP INDEX idx_raw_asset_canonical_identity"
            )
            before_asset = repository.connection.execute(
                "SELECT id, payload, payload_hash FROM raw_asset"
            ).fetchone()
            before_checkpoint = repository.connection.execute(
                "SELECT source, resource_type, last_page, last_run_id, status "
                "FROM sync_checkpoint"
            ).fetchone()
            migration = (
                Path(__file__).parents[2]
                / "migrations"
                / "001_raw_canonical_identity_index.sql"
            ).read_text(encoding="utf-8")

            repository.connection.executescript(migration)

            after_asset = repository.connection.execute(
                "SELECT id, payload, payload_hash FROM raw_asset"
            ).fetchone()
            after_checkpoint = repository.connection.execute(
                "SELECT source, resource_type, last_page, last_run_id, status "
                "FROM sync_checkpoint"
            ).fetchone()
            index = repository.connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' "
                "AND name = 'idx_raw_asset_canonical_identity'"
            ).fetchone()
            integrity = repository.connection.execute(
                "PRAGMA integrity_check"
            ).fetchone()[0]

            self.assertEqual(tuple(after_asset), tuple(before_asset))
            self.assertEqual(tuple(after_checkpoint), tuple(before_checkpoint))
            self.assertIsNotNone(index)
            self.assertEqual(integrity, "ok")
            repository.close()

    def test_observation_migration_is_idempotent_and_preserves_raw(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database_url = f"sqlite:///{Path(temp_dir) / 'migration.db'}"
            repository = RawRepository(database_url)
            record = IncrementalIdentityTests.record(
                "pncp_historico", "A", "2023-04-01T10:00:00Z"
            )
            ingest_records(
                repository,
                "pncp_historico",
                ("numeroControlePNCP",),
                [record],
                resource_type="contratacao",
                logger=SilentLogger(),
            )
            repository.connection.execute("DROP TABLE raw_observation")
            before = repository.connection.execute(
                "SELECT id, payload, payload_hash, previous_version_id "
                "FROM raw_asset ORDER BY rowid"
            ).fetchall()
            migration = (
                Path(__file__).parents[2]
                / "migrations"
                / "003_raw_observation.sql"
            ).read_text(encoding="utf-8")

            repository.connection.executescript(migration)
            repository.connection.executescript(migration)

            after = repository.connection.execute(
                "SELECT id, payload, payload_hash, previous_version_id "
                "FROM raw_asset ORDER BY rowid"
            ).fetchall()
            table = repository.connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name = 'raw_observation'"
            ).fetchone()
            integrity = repository.connection.execute(
                "PRAGMA integrity_check"
            ).fetchone()[0]
            foreign_keys = repository.connection.execute(
                "PRAGMA foreign_key_check"
            ).fetchall()

            self.assertEqual(
                [tuple(row) for row in after],
                [tuple(row) for row in before],
            )
            self.assertIsNotNone(table)
            self.assertEqual(integrity, "ok")
            self.assertEqual(foreign_keys, [])
            repository.close()


if __name__ == "__main__":
    unittest.main()
