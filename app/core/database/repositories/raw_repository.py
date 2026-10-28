from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any, Mapping

from src.ingestion.identity import (
    CanonicalIdentity,
    ParsedTimestamp,
    TimestampStatus,
    parse_timestamp,
    source_timestamps,
)
from src.obs import utc_now
from src.schema import CollectedRecord, value_at_path


class RawRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def find_latest(self, source: str, resource_type: str, external_id: str):
        return self.connection.execute(
            "SELECT * FROM raw_asset WHERE source = ? AND resource_type = ? "
            "AND external_id = ? ORDER BY rowid DESC LIMIT 1",
            (source, resource_type, external_id),
        ).fetchone()

    def latest(self, source: str, resource_type: str, external_id: str):
        """Alias de compatibilidade para find_latest."""
        return self.find_latest(source, resource_type, external_id)

    def latest_hash(
        self, source: str, resource_type: str, external_id: str
    ) -> str | None:
        row = self.find_latest(source, resource_type, external_id)
        return row["payload_hash"] if row else None

    def _canonical_rows(self, identity: CanonicalIdentity):
        return self.connection.execute(
            "SELECT rowid AS _rowid, * FROM raw_asset "
            "WHERE base_id = ? AND resource_type = ? AND external_id = ?",
            identity.values,
        ).fetchall()

    def _table_exists(self, table_name: str) -> bool:
        return self.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table_name,),
        ).fetchone() is not None

    @staticmethod
    def _row_source_updated_at(row: sqlite3.Row) -> str | None:
        stored = row["source_updated_at"]
        if stored not in (None, ""):
            return str(stored)
        payload = json.loads(row["payload"])
        _, updated_at = source_timestamps(payload, row["resource_type"])
        return updated_at

    @classmethod
    def _row_timestamp(cls, row: sqlite3.Row) -> ParsedTimestamp:
        return parse_timestamp(cls._row_source_updated_at(row))

    @staticmethod
    def _arrival_key(row: sqlite3.Row):
        return row["fetched_at"], row["_rowid"]

    @classmethod
    def _is_more_current(
        cls, candidate: sqlite3.Row, current: sqlite3.Row
    ) -> bool:
        candidate_timestamp = cls._row_timestamp(candidate)
        current_timestamp = cls._row_timestamp(current)
        candidate_valid = candidate_timestamp.status == TimestampStatus.VALID
        current_valid = current_timestamp.status == TimestampStatus.VALID
        if candidate_valid != current_valid:
            return candidate_valid
        if (
            candidate_valid
            and candidate_timestamp.kind == current_timestamp.kind
        ):
            candidate_key = (
                candidate_timestamp.normalized,
                *cls._arrival_key(candidate),
            )
            current_key = (
                current_timestamp.normalized,
                *cls._arrival_key(current),
            )
            return candidate_key > current_key
        # Timestamps inválidos/ausentes e formatos com semânticas de fuso
        # diferentes não são comparados lexicograficamente.
        return cls._arrival_key(candidate) > cls._arrival_key(current)

    @classmethod
    def _select_current(cls, rows):
        current = None
        for row in rows:
            if current is None or cls._is_more_current(row, current):
                current = row
        return current

    def find_current(self, identity: CanonicalIdentity):
        """Retorna o estado atual pela identidade, não pelo job de coleta."""

        rows = self._canonical_rows(identity)
        return self._select_current(rows)

    def contains_observation(
        self,
        identity: CanonicalIdentity,
        digest: str,
        source_updated_at: str | None,
        observation_key: str | None = None,
    ) -> bool:
        """Detecta replay da mesma versão sem bloquear reversões posteriores."""

        if observation_key and self._table_exists("raw_observation"):
            return self.connection.execute(
                "SELECT 1 FROM raw_observation "
                "WHERE observation_key = ? AND base_id = ? "
                "AND resource_type = ? AND external_id = ? "
                "AND payload_hash = ? LIMIT 1",
                (observation_key, *identity.values, digest),
            ).fetchone() is not None

        expected_timestamp = parse_timestamp(source_updated_at).normalized
        if expected_timestamp is None:
            return self.connection.execute(
                "SELECT 1 FROM raw_asset "
                "WHERE base_id = ? AND resource_type = ? AND external_id = ? "
                "AND payload_hash = ? LIMIT 1",
                (*identity.values, digest),
            ).fetchone() is not None
        rows = self.connection.execute(
            "SELECT rowid AS _rowid, * FROM raw_asset "
            "WHERE base_id = ? AND resource_type = ? AND external_id = ? "
            "AND payload_hash = ?",
            (*identity.values, digest),
        ).fetchall()
        return any(
            parse_timestamp(self._row_source_updated_at(row)).normalized
            == expected_timestamp
            for row in rows
        )

    def contains_version(
        self,
        source: str,
        resource_type: str,
        external_id: str,
        digest: str,
    ) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM raw_asset WHERE source = ? "
            "AND resource_type = ? AND external_id = ? "
            "AND payload_hash = ? LIMIT 1",
            (source, resource_type, external_id, digest),
        ).fetchone()
        return row is not None

    def list_latest(self, source: str, resource_type: str):
        return list(self._iter_current_source_rows(source, resource_type))

    def _iter_current_source_rows(self, source: str, resource_type: str):
        """Seleciona o estado atual sem confundir ordem de chegada com versão."""

        cursor = self.connection.execute(
            "SELECT rowid AS _rowid, * FROM raw_asset "
            "WHERE source = ? AND resource_type = ? "
            "ORDER BY external_id, rowid",
            (source, resource_type),
        )
        current_external_id = None
        current_row = None
        for row in cursor:
            if row["external_id"] != current_external_id:
                if current_row is not None:
                    yield current_row
                current_external_id = row["external_id"]
                current_row = row
            elif self._is_more_current(row, current_row):
                current_row = row
        if current_row is not None:
            yield current_row

    def list_latest_with_payload(self, source: str, resource_type: str):
        """Retorna metadados e JSON dos ativos mais recentes para novos alvos."""
        rows = self.list_latest(source, resource_type)
        return [
            {
                "external_id": row["external_id"],
                "payload": json.loads(row["payload"]),
            }
            for row in rows
        ]

    def iter_latest_target_values(
        self,
        source: str,
        resource_type: str,
        field_paths: Mapping[str, str],
    ):
        """Extrai alvos do estado atual, inclusive com chegada fora de ordem."""

        for row in self._iter_current_source_rows(source, resource_type):
            target = {}
            external_id = row["external_id"]
            payload = None
            for name, path in field_paths.items():
                if path == "@external_id":
                    target[name] = external_id
                elif path == "@parent_external_id":
                    parent, separator, _ = external_id.rpartition("|")
                    target[name] = parent if separator else None
                else:
                    if payload is None:
                        payload = json.loads(row["payload"])
                    target[name] = value_at_path(payload, path)
            yield target

    def insert_raw(
        self,
        run_id: str,
        record: CollectedRecord,
        external_id: str,
        digest: str,
        resource_type: str = "records",
        base_id: str = "pncp",
        natureza: str = "estruturada",
        query_params: Mapping[str, Any] | None = None,
        corpus_version: str = "v1",
        sensitivity: str = "public",
        source_published_at: str | None = None,
        source_updated_at: str | None = None,
        identity: CanonicalIdentity | None = None,
        previous_version_id: str | None = None,
        source_timestamp: ParsedTimestamp | None = None,
        observation_key: str | None = None,
        window_start: str | None = None,
        window_end: str | None = None,
        commit: bool = True,
    ) -> str:
        identity = identity or CanonicalIdentity(
            base_id, resource_type, external_id
        )
        if previous_version_id is None:
            previous = self.find_current(identity)
            previous_version_id = previous["id"] if previous else None
        asset_id = str(uuid.uuid4())
        payload_json = json.dumps(
            record.payload, ensure_ascii=False, separators=(",", ":")
        )
        observed_at = utc_now()
        self.connection.execute(
            "INSERT INTO raw_asset "
            "(id, source, base_id, natureza, resource_type, endpoint, "
            "external_id, query_params, payload, payload_hash, "
            "source_published_at, source_updated_at, fetched_at, "
            "corpus_version, sensitivity, ingestion_run_id, "
            "previous_version_id, processing_status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                asset_id,
                record.source_id,
                base_id,
                natureza,
                resource_type,
                record.source_url,
                external_id,
                json.dumps(query_params or {}, ensure_ascii=False),
                payload_json,
                digest,
                source_published_at,
                source_updated_at,
                observed_at,
                corpus_version,
                sensitivity,
                run_id,
                previous_version_id,
                "RAW",
                observed_at,
            ),
        )
        if observation_key:
            self.insert_observation(
                observation_key,
                asset_id,
                run_id,
                record,
                identity,
                digest,
                source_timestamp
                or parse_timestamp(source_updated_at),
                window_start,
                window_end,
                observed_at=observed_at,
                commit=False,
            )
        if commit:
            self.connection.commit()
        return asset_id

    def insert_observation(
        self,
        observation_key: str,
        raw_asset_id: str,
        run_id: str,
        record: CollectedRecord,
        identity: CanonicalIdentity,
        digest: str,
        source_timestamp: ParsedTimestamp,
        window_start: str | None,
        window_end: str | None,
        *,
        observed_at: str | None = None,
        commit: bool = True,
    ) -> None:
        """Registra a observação sem criar outra versão RAW."""

        if not self._table_exists("raw_observation"):
            return
        self.connection.execute(
            "INSERT INTO raw_observation "
            "(observation_key, raw_asset_id, base_id, resource_type, "
            "external_id, payload_hash, source, window_start, window_end, "
            "cursor, ordinal, source_timestamp_status, "
            "source_timestamp_kind, source_updated_at_normalized, "
            "ingestion_run_id, observed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                observation_key,
                raw_asset_id,
                *identity.values,
                digest,
                record.source_id,
                window_start,
                window_end,
                str(record.cursor),
                int(record.ordinal),
                source_timestamp.status.value,
                (
                    source_timestamp.kind.value
                    if source_timestamp.kind is not None
                    else None
                ),
                source_timestamp.normalized,
                run_id,
                observed_at or utc_now(),
            ),
        )
        if commit:
            self.connection.commit()

    def save_asset(
        self,
        run_id: str,
        record: CollectedRecord,
        external_id: str,
        digest: str,
        **metadata: Any,
    ) -> str:
        """Alias de compatibilidade para a inserção append-only."""
        return self.insert_raw(
            run_id, record, external_id, digest, **metadata
        )
