from __future__ import annotations

import json
import sqlite3
from typing import Iterable

from src.ingestion.identity import TimestampStatus, parse_timestamp, source_timestamps


def _columns(connection: sqlite3.Connection) -> set[str]:
    return {row[1] for row in connection.execute("PRAGMA table_info(raw_asset)")}


def _value(row: sqlite3.Row, column: str, default=None):
    return row[column] if column in row.keys() else default


def _updated_at(row: sqlite3.Row) -> str | None:
    stored = _value(row, "source_updated_at")
    if stored not in (None, ""):
        return str(stored)
    payload = json.loads(row["payload"])
    _, updated = source_timestamps(payload, row["resource_type"])
    return updated


def _arrival_key(row: sqlite3.Row) -> tuple[str, int]:
    arrival = (
        _value(row, "fetched_at")
        or _value(row, "created_at")
        or ""
    )
    return str(arrival), int(row["_rowid"])


def _is_more_current(candidate: sqlite3.Row, current: sqlite3.Row) -> bool:
    candidate_timestamp = parse_timestamp(_updated_at(candidate))
    current_timestamp = parse_timestamp(_updated_at(current))
    candidate_valid = candidate_timestamp.status == TimestampStatus.VALID
    current_valid = current_timestamp.status == TimestampStatus.VALID
    if candidate_valid != current_valid:
        return candidate_valid
    if candidate_valid and candidate_timestamp.kind == current_timestamp.kind:
        return (
            candidate_timestamp.normalized,
            *_arrival_key(candidate),
        ) > (
            current_timestamp.normalized,
            *_arrival_key(current),
        )
    return _arrival_key(candidate) > _arrival_key(current)


def iter_current_raw_rows(
    connection: sqlite3.Connection, resource_type: str
) -> Iterable[sqlite3.Row]:
    """Seleciona uma versão vigente por identidade canônica da RAW.

    A fonte/job não participa da identidade. Timestamps válidos da fonte têm
    precedência; ordem de chegada é usada apenas como desempate ou fallback.
    O seletor também aceita os dumps legados que não possuem todas as colunas
    operacionais da RAW incremental.
    """

    columns = _columns(connection)
    base_expression = "base_id" if "base_id" in columns else "'pncp'"
    query_expression = "query_params" if "query_params" in columns else "'{}'"
    optional = [
        name
        for name in ("source_updated_at", "fetched_at", "created_at")
        if name in columns
    ]
    selected = ", ".join(optional)
    if selected:
        selected = ", " + selected
    cursor = connection.execute(
        "SELECT rowid AS _rowid, id, resource_type, external_id, "
        "payload_hash, payload, "
        f"{base_expression} AS _base_id, {query_expression} AS query_params"
        f"{selected} FROM raw_asset WHERE resource_type = ? "
        "ORDER BY _base_id, external_id, _rowid",
        (resource_type,),
    )
    identity = None
    current = None
    for row in cursor:
        row_identity = (row["_base_id"], resource_type, row["external_id"])
        if row_identity != identity:
            if current is not None:
                yield current
            identity = row_identity
            current = row
        elif _is_more_current(row, current):
            current = row
    if current is not None:
        yield current
