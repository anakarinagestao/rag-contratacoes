from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from urllib.parse import quote

from app.core.database.connection import database_path


REQUIRED_TABLES = {"ingestion_run", "raw_asset", "sync_checkpoint"}


def _rows(connection: sqlite3.Connection, sql: str, params=()):
    return [dict(row) for row in connection.execute(sql, params).fetchall()]


def inspect_database(
    database_url: str,
    source: str | None = None,
    limit: int = 10,
) -> dict[str, object]:
    if limit < 1:
        raise ValueError("limit deve ser maior que zero")
    path = Path(database_path(database_url))
    if not path.is_file():
        raise FileNotFoundError(f"Banco de debug ainda não existe: {path}")

    uri = f"file:{quote(str(path))}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        missing = REQUIRED_TABLES.difference(tables)
        if missing:
            raise ValueError(
                "Banco sem as tabelas RAW: " + ", ".join(sorted(missing))
            )

        where = " WHERE source = ?" if source else ""
        params = (source,) if source else ()
        assets = _rows(
            connection,
            "SELECT source, natureza, resource_type, sensitivity, "
            "processing_status, COUNT(*) AS versions, "
            "COUNT(DISTINCT external_id) AS distinct_assets "
            f"FROM raw_asset{where} "
            "GROUP BY source, natureza, resource_type, sensitivity, "
            "processing_status ORDER BY source, resource_type",
            params,
        )
        runs = _rows(
            connection,
            "SELECT id, source, resource_type, status, pages_processed, "
            "records_received, records_inserted, records_updated, "
            "records_unchanged, records_failed, started_at, finished_at "
            f"FROM ingestion_run{where} ORDER BY rowid DESC LIMIT ?",
            (*params, limit),
        )
        checkpoints = _rows(
            connection,
            "SELECT source, resource_type, last_page, last_successful_sync, "
            "status FROM sync_checkpoint"
            + (" WHERE source = ?" if source else "")
            + " ORDER BY source, resource_type",
            params,
        )
        return {
            "database": str(path.resolve()),
            "size_bytes": path.stat().st_size,
            "read_only": True,
            "source_filter": source,
            "payload_omitted": True,
            "asset_summary": assets,
            "latest_runs": runs,
            "checkpoints": checkpoints,
        }
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Inspeciona contagens e execuções do SQLite RAW"
    )
    parser.add_argument(
        "--database-url",
        default="sqlite:///./data/debug/pncp_debug.db",
    )
    parser.add_argument("--source", default=None)
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()

    report = inspect_database(args.database_url, args.source, args.limit)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
