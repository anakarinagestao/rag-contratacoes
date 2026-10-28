from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3
import tempfile
import uuid
from typing import Mapping

from src.obs import utc_now

from .runner import DEFAULT_SCHEMA, normalize_core


ROOT = Path(__file__).parents[2]
DEFAULT_RECONCILIATION_SCHEMA = (
    ROOT / "migrations" / "101_normalization_reconciliation.sql"
)

ENTITY_KEYS: Mapping[str, tuple[str, ...]] = {
    "orgao": ("cnpj",),
    "unidade": ("orgao_cnpj", "codigo_unidade"),
    "fornecedor": ("tipo_pessoa", "ni_fornecedor"),
    "contratacao": ("numero_controle_pncp",),
    "contratacao_item": ("numero_controle_pncp", "numero_item"),
    "resultado_item": (
        "numero_controle_pncp",
        "numero_item",
        "sequencial_resultado",
    ),
    "contrato": ("numero_controle_pncp",),
    "empenho": ("numero_controle_contrato", "sequencial_empenho"),
    "ata_registro_preco": ("numero_controle_pncp_ata",),
    "pca": ("id_pca_pncp",),
    "pca_item": ("id_pca_pncp", "numero_item"),
}

COVERAGE_TABLE = {
    "contratacao": "contratacao",
    "contratacao_item": "contratacao_item",
    "resultado_item": "resultado_item",
    "contrato": "contrato",
    "empenho": "empenho",
    "ata_registro_preco": "ata_registro_preco",
    "pca": "pca",
}


@dataclass(frozen=True)
class IncrementalNormalizationResult:
    run_id: str
    source_snapshot_sha256: str
    raw_records_seen: int
    inserted: int
    updated: int
    unchanged: int
    skipped: int
    rejected: int
    target_counts: Mapping[str, int]


def _table_columns(connection: sqlite3.Connection, table: str) -> tuple[str, ...]:
    return tuple(row[1] for row in connection.execute(f"PRAGMA table_info({table})"))


def _where(keys: tuple[str, ...]) -> str:
    return " AND ".join(f"{key} = ?" for key in keys)


def _merge_table(
    target: sqlite3.Connection,
    stage: sqlite3.Connection,
    table: str,
    keys: tuple[str, ...],
) -> tuple[int, int, int]:
    columns = _table_columns(stage, table)
    if not columns or columns != _table_columns(target, table):
        raise RuntimeError(f"Esquema incompatível para a tabela {table}")
    key_set = set(keys)
    non_keys = tuple(column for column in columns if column not in key_set)
    insert_sql = (
        f"INSERT INTO {table} ({', '.join(columns)}) "
        f"VALUES ({', '.join('?' for _ in columns)})"
    )
    update_sql = (
        f"UPDATE {table} SET "
        + ", ".join(f"{column} = ?" for column in non_keys)
        + f" WHERE {_where(keys)}"
    )
    select_sql = (
        f"SELECT current_source_payload_hash FROM {table} WHERE {_where(keys)}"
    )
    inserted = updated = unchanged = 0
    for row in stage.execute(f"SELECT {', '.join(columns)} FROM {table}"):
        key_values = tuple(row[key] for key in keys)
        existing = target.execute(select_sql, key_values).fetchone()
        if existing is None:
            target.execute(insert_sql, tuple(row[column] for column in columns))
            inserted += 1
        elif existing["current_source_payload_hash"] == row["current_source_payload_hash"]:
            unchanged += 1
        else:
            target.execute(
                update_sql,
                tuple(row[column] for column in non_keys) + key_values,
            )
            updated += 1
    return inserted, updated, unchanged


def _merge_lineage(
    target: sqlite3.Connection,
    stage: sqlite3.Connection,
    run_id: str,
) -> None:
    entity_keys = list(
        stage.execute(
            "SELECT DISTINCT normalized_table, entity_key "
            "FROM normalization_lineage"
        )
    )
    target.executemany(
        "UPDATE normalization_lineage SET is_current = 0 "
        "WHERE normalized_table = ? AND entity_key = ?",
        ((row["normalized_table"], row["entity_key"]) for row in entity_keys),
    )
    for row in stage.execute(
        "SELECT normalized_table, entity_key, raw_asset_id, payload_hash, "
        "is_current, observed_at FROM normalization_lineage"
    ):
        target.execute(
            "INSERT INTO normalization_lineage "
            "(normalized_table, entity_key, raw_asset_id, payload_hash, "
            "normalization_run_id, is_current, observed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (normalized_table, entity_key, raw_asset_id) "
            "DO UPDATE SET is_current = excluded.is_current",
            (
                row["normalized_table"],
                row["entity_key"],
                row["raw_asset_id"],
                row["payload_hash"],
                run_id,
                row["is_current"],
                row["observed_at"],
            ),
        )


def normalize_incremental(
    raw_database: str | Path,
    normalized_database: str | Path,
    *,
    schema_path: str | Path = DEFAULT_SCHEMA,
    reconciliation_schema_path: str | Path = DEFAULT_RECONCILIATION_SCHEMA,
    scope_inventory_path: str | Path = ROOT / "config" / "pncp_rj_scope_inventory.json",
    transform_version: str = "normalized-v2-incremental",
    known_commitment_gap_count: int = 0,
) -> IncrementalNormalizationResult:
    """Reconcilia uma RAW cumulativa com um banco normalizado existente.

    Toda transformação ocorre primeiro em staging. A base de destino é
    alterada somente dentro de uma transação validada por chaves estrangeiras
    e quick_check. Ausência no snapshot não é interpretada como exclusão.
    """

    raw_path = Path(raw_database).expanduser().resolve()
    target_path = Path(normalized_database).expanduser().resolve()
    if not target_path.is_file():
        raise FileNotFoundError(
            "Banco normalizado não encontrado; execute primeiro o modo build: "
            f"{target_path}"
        )
    if raw_path == target_path:
        raise ValueError("RAW e normalizada devem ser bancos distintos")

    with tempfile.TemporaryDirectory(prefix="pncp-normalization-") as directory:
        stage_path = Path(directory) / "stage.db"
        stage_result = normalize_core(
            raw_path,
            stage_path,
            schema_path=schema_path,
            scope_inventory_path=scope_inventory_path,
            transform_version=transform_version,
            known_commitment_gap_count=known_commitment_gap_count,
        )
        stage = sqlite3.connect(f"file:{stage_path}?mode=ro", uri=True)
        stage.row_factory = sqlite3.Row
        stage.execute("PRAGMA query_only = ON")
        target = sqlite3.connect(target_path)
        target.row_factory = sqlite3.Row
        target.execute("PRAGMA foreign_keys = ON")
        try:
            required = set(ENTITY_KEYS) | {
                "normalization_run",
                "normalization_lineage",
                "normalization_issue",
                "normalization_coverage",
            }
            available = {
                row[0]
                for row in target.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            missing = required - available
            if missing:
                raise RuntimeError(
                    "Banco de destino não possui o esquema normalizado: "
                    + ", ".join(sorted(missing))
                )
            target.executescript(
                Path(reconciliation_schema_path)
                .expanduser()
                .resolve()
                .read_text(encoding="utf-8")
            )

            run_id = str(uuid.uuid4())
            started_at = utc_now()
            target.execute("BEGIN IMMEDIATE")
            target.execute(
                "INSERT INTO normalization_run "
                "(id, source_snapshot_sha256, transform_version, started_at, status) "
                "VALUES (?, ?, ?, ?, 'RUNNING')",
                (
                    run_id,
                    stage_result.source_snapshot_sha256,
                    transform_version,
                    started_at,
                ),
            )
            total_inserted = total_updated = total_unchanged = 0
            target_counts: dict[str, int] = {}
            reconciliation: list[tuple[str, int, int, int, int, int]] = []
            for table, keys in ENTITY_KEYS.items():
                inserted, updated, unchanged = _merge_table(
                    target, stage, table, keys
                )
                staged_count = stage.execute(
                    f"SELECT COUNT(*) FROM {table}"
                ).fetchone()[0]
                target_count = target.execute(
                    f"SELECT COUNT(*) FROM {table}"
                ).fetchone()[0]
                target_counts[table] = target_count
                reconciliation.append(
                    (
                        table,
                        staged_count,
                        inserted,
                        updated,
                        unchanged,
                        target_count,
                    )
                )
                total_inserted += inserted
                total_updated += updated
                total_unchanged += unchanged

            _merge_lineage(target, stage, run_id)
            for issue in stage.execute(
                "SELECT raw_asset_id, resource_type, issue_code, severity, "
                "field_name, observed_value, details, created_at "
                "FROM normalization_issue"
            ):
                target.execute(
                    "INSERT INTO normalization_issue "
                    "(id, normalization_run_id, raw_asset_id, resource_type, "
                    "issue_code, severity, field_name, observed_value, details, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(uuid.uuid4()),
                        run_id,
                        issue["raw_asset_id"],
                        issue["resource_type"],
                        issue["issue_code"],
                        issue["severity"],
                        issue["field_name"],
                        issue["observed_value"],
                        issue["details"],
                        issue["created_at"],
                    ),
                )

            for coverage in stage.execute("SELECT * FROM normalization_coverage"):
                resource = coverage["resource_type"]
                count = target.execute(
                    f"SELECT COUNT(*) FROM {COVERAGE_TABLE[resource]}"
                ).fetchone()[0]
                columns = tuple(coverage.keys())
                values = [coverage[column] for column in columns]
                values[columns.index("record_count")] = count
                values[columns.index("updated_at")] = utc_now()
                target.execute(
                    "INSERT INTO normalization_coverage "
                    f"({', '.join(columns)}) VALUES "
                    f"({', '.join('?' for _ in columns)}) "
                    "ON CONFLICT (resource_type) DO UPDATE SET "
                    + ", ".join(
                        f"{column} = excluded.{column}"
                        for column in columns
                        if column != "resource_type"
                    ),
                    values,
                )

            target.executemany(
                "INSERT INTO normalization_reconciliation "
                "(normalization_run_id, table_name, staged_count, "
                "inserted_count, updated_count, unchanged_count, target_count) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                ((run_id, *row) for row in reconciliation),
            )
            target.execute(
                "UPDATE normalization_run SET status = 'SUCCESS', finished_at = ?, "
                "records_seen = ?, records_written = ?, records_skipped = ?, "
                "records_rejected = ? WHERE id = ?",
                (
                    utc_now(),
                    stage_result.raw_records_seen,
                    total_inserted + total_updated,
                    stage_result.skipped,
                    stage_result.rejected,
                    run_id,
                ),
            )
            violations = target.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise RuntimeError(
                    f"Reconciliação gerou {len(violations)} violações de FK"
                )
            if target.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise RuntimeError("quick_check falhou apó a reconciliação")
            target.commit()
        except BaseException:
            target.rollback()
            raise
        finally:
            stage.close()
            target.close()

    return IncrementalNormalizationResult(
        run_id=run_id,
        source_snapshot_sha256=stage_result.source_snapshot_sha256,
        raw_records_seen=stage_result.raw_records_seen,
        inserted=total_inserted,
        updated=total_updated,
        unchanged=total_unchanged,
        skipped=stage_result.skipped,
        rejected=stage_result.rejected,
        target_counts=target_counts,
    )
