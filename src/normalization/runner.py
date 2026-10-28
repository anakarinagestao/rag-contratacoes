from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import hashlib
import json
from pathlib import Path
import sqlite3
import uuid
from typing import Any, Iterable, Mapping

from src.obs import utc_now

from .current import iter_current_raw_rows
from .transformers import (
    normalize_commitment,
    normalize_contract,
    normalize_item,
    normalize_pca,
    normalize_price_registry,
    normalize_procurement,
    normalize_result,
)


ROOT = Path(__file__).parents[2]
DEFAULT_SCHEMA = ROOT / "migrations" / "100_normalized_schema.sql"


@dataclass(frozen=True)
class NormalizationResult:
    run_id: str
    raw_records_seen: int
    normalized_rows_written: int
    skipped: int
    rejected: int
    issues: int
    counts: Mapping[str, int]
    source_snapshot_sha256: str


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _rows(connection: sqlite3.Connection, resource_type: str) -> Iterable[sqlite3.Row]:
    return iter_current_raw_rows(connection, resource_type)


def _upsert(
    connection: sqlite3.Connection,
    table: str,
    row: Mapping[str, Any],
    conflict: str,
    *,
    preserve_existing_on_null: bool = False,
) -> None:
    columns = tuple(row)
    names = ", ".join(columns)
    placeholders = ", ".join("?" for _ in columns)
    updates = ", ".join(
        (
            f"{column}=COALESCE(excluded.{column}, {table}.{column})"
            if preserve_existing_on_null
            else f"{column}=excluded.{column}"
        )
        for column in columns
        if column not in {part.strip() for part in conflict.split(",")}
    )
    connection.execute(
        f"INSERT INTO {table} ({names}) VALUES ({placeholders}) "
        f"ON CONFLICT ({conflict}) DO UPDATE SET {updates}",
        tuple(row[column] for column in columns),
    )


def _insert_lineage(
    connection: sqlite3.Connection,
    *,
    table: str,
    entity_key: str,
    raw_asset: Mapping[str, Any],
    run_id: str,
    observed_at: str,
) -> None:
    connection.execute(
        "UPDATE normalization_lineage SET is_current = 0 "
        "WHERE normalized_table = ? AND entity_key = ? AND is_current = 1",
        (table, entity_key),
    )
    connection.execute(
        "INSERT INTO normalization_lineage "
        "(normalized_table, entity_key, raw_asset_id, payload_hash, "
        "normalization_run_id, is_current, observed_at) "
        "VALUES (?, ?, ?, ?, ?, 1, ?) "
        "ON CONFLICT (normalized_table, entity_key, raw_asset_id) "
        "DO UPDATE SET is_current = 1",
        (
            table,
            entity_key,
            raw_asset["id"],
            raw_asset["payload_hash"],
            run_id,
            observed_at,
        ),
    )


def _raw_mapping(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "external_id": row["external_id"],
        "payload_hash": row["payload_hash"],
    }


def _insert_issue(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    raw_asset_id: str,
    resource_type: str,
    issue_code: str,
    severity: str,
    details: str,
    created_at: str,
    field_name: str | None = None,
    observed_value: Any = None,
) -> None:
    connection.execute(
        "INSERT INTO normalization_issue "
        "(id, normalization_run_id, raw_asset_id, resource_type, "
        "issue_code, severity, field_name, observed_value, details, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            str(uuid.uuid4()),
            run_id,
            raw_asset_id,
            resource_type,
            issue_code,
            severity,
            field_name,
            None if observed_value is None else str(observed_value),
            details,
            created_at,
        ),
    )


def normalize_core(
    raw_database: str | Path,
    normalized_database: str | Path,
    *,
    schema_path: str | Path = DEFAULT_SCHEMA,
    scope_inventory_path: str | Path = ROOT / "config" / "pncp_rj_scope_inventory.json",
    transform_version: str = "normalized-v1",
    reference_date: date | None = None,
    known_commitment_gap_count: int = 0,
) -> NormalizationResult:
    raw_path = Path(raw_database).expanduser().resolve()
    output_path = Path(normalized_database).expanduser().resolve()
    schema = Path(schema_path).expanduser().resolve()
    inventory_path = Path(scope_inventory_path).expanduser().resolve()
    if known_commitment_gap_count < 0:
        raise ValueError("known_commitment_gap_count não pode ser negativo")
    if not raw_path.is_file():
        raise FileNotFoundError(f"Banco RAW não encontrado: {raw_path}")
    if output_path.exists():
        raise FileExistsError(
            f"Destino já existe; recusei sobrescrever: {output_path}"
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not inventory_path.is_file():
        raise FileNotFoundError(f"Inventário de escopo não encontrado: {inventory_path}")

    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    inventory_id = inventory["scope"]["inventory_id"]
    inventory_members = {
        (str(member["cnpj"]), str(member["codigo_unidade"]))
        for member in inventory["scope"]["members"]
    }

    snapshot_hash = _sha256(raw_path)
    normalized_at = utc_now()
    run_id = str(uuid.uuid4())
    effective_reference_date = reference_date or date.today()
    counts: dict[str, int] = {
        "orgao": 0,
        "unidade": 0,
        "fornecedor": 0,
        "contratacao": 0,
        "contratacao_item": 0,
        "resultado_item": 0,
        "contrato": 0,
        "empenho": 0,
        "ata_registro_preco": 0,
        "pca": 0,
        "pca_item": 0,
    }
    raw_seen = 0
    issue_count = 0
    skipped = 0

    raw = sqlite3.connect(f"file:{raw_path}?mode=ro", uri=True)
    raw.row_factory = sqlite3.Row
    raw.execute("PRAGMA query_only = ON")
    normalized = sqlite3.connect(output_path)
    normalized.row_factory = sqlite3.Row
    normalized.execute("PRAGMA foreign_keys = ON")
    try:
        normalized.executescript(schema.read_text(encoding="utf-8"))
        normalized.execute("BEGIN IMMEDIATE")
        normalized.execute(
            "INSERT INTO normalization_run "
            "(id, source_snapshot_sha256, transform_version, started_at, status) "
            "VALUES (?, ?, ?, ?, 'RUNNING')",
            (run_id, snapshot_hash, transform_version, normalized_at),
        )

        for source_row in _rows(raw, "contratacao"):
            raw_seen += 1
            raw_asset = _raw_mapping(source_row)
            payload = json.loads(source_row["payload"])
            organization, unit, procurement = normalize_procurement(
                raw_asset, payload, normalized_at=normalized_at
            )
            _upsert(
                normalized,
                "orgao",
                organization,
                "cnpj",
                preserve_existing_on_null=True,
            )
            _upsert(
                normalized,
                "unidade",
                unit,
                "orgao_cnpj, codigo_unidade",
                preserve_existing_on_null=True,
            )
            _upsert(
                normalized,
                "contratacao",
                procurement,
                "numero_controle_pncp",
            )
            for table, key in (
                ("orgao", organization["cnpj"]),
                (
                    "unidade",
                    f"{unit['orgao_cnpj']}|{unit['codigo_unidade']}",
                ),
                ("contratacao", procurement["numero_controle_pncp"]),
            ):
                _insert_lineage(
                    normalized,
                    table=table,
                    entity_key=key,
                    raw_asset=raw_asset,
                    run_id=run_id,
                    observed_at=normalized_at,
                )

        for source_row in _rows(raw, "contratacao_item"):
            raw_seen += 1
            raw_asset = _raw_mapping(source_row)
            item = normalize_item(
                raw_asset,
                json.loads(source_row["payload"]),
                normalized_at=normalized_at,
            )
            _upsert(
                normalized,
                "contratacao_item",
                item,
                "numero_controle_pncp, numero_item",
            )
            _insert_lineage(
                normalized,
                table="contratacao_item",
                entity_key=f"{item['numero_controle_pncp']}|{item['numero_item']}",
                raw_asset=raw_asset,
                run_id=run_id,
                observed_at=normalized_at,
            )

        for source_row in _rows(raw, "resultado_item"):
            raw_seen += 1
            raw_asset = _raw_mapping(source_row)
            supplier, result = normalize_result(
                raw_asset,
                json.loads(source_row["payload"]),
                normalized_at=normalized_at,
                reference_date=effective_reference_date,
            )
            _upsert(
                normalized,
                "fornecedor",
                supplier,
                "tipo_pessoa, ni_fornecedor",
                preserve_existing_on_null=True,
            )
            _upsert(
                normalized,
                "resultado_item",
                result,
                "numero_controle_pncp, numero_item, sequencial_resultado",
            )
            supplier_key = f"{supplier['tipo_pessoa']}|{supplier['ni_fornecedor']}"
            result_key = (
                f"{result['numero_controle_pncp']}|{result['numero_item']}|"
                f"{result['sequencial_resultado']}"
            )
            _insert_lineage(
                normalized,
                table="fornecedor",
                entity_key=supplier_key,
                raw_asset=raw_asset,
                run_id=run_id,
                observed_at=normalized_at,
            )
            _insert_lineage(
                normalized,
                table="resultado_item",
                entity_key=result_key,
                raw_asset=raw_asset,
                run_id=run_id,
                observed_at=normalized_at,
            )
            if result["data_quality_status"] != "VALID":
                issue_count += 1
                _insert_issue(
                    normalized,
                    run_id=run_id,
                    raw_asset_id=raw_asset["id"],
                    resource_type="resultado_item",
                    issue_code=result["data_quality_status"],
                    severity="WARNING",
                    field_name="dataResultado",
                    observed_value=result["data_resultado"],
                    details=(
                        "Data preservada, mas excluída de indicadores "
                        "temporais confiáveis."
                    ),
                    created_at=normalized_at,
                )

        known_procurements = {
            row[0]
            for row in normalized.execute(
                "SELECT numero_controle_pncp FROM contratacao"
            )
        }
        for source_row in _rows(raw, "contrato"):
            raw_seen += 1
            raw_asset = _raw_mapping(source_row)
            payload = json.loads(source_row["payload"])
            purchase_id = str(payload.get("numeroControlePncpCompra") or "")
            organization, unit, supplier, contract = normalize_contract(
                raw_asset,
                payload,
                normalized_at=normalized_at,
                parent_is_present=purchase_id in known_procurements,
            )
            _upsert(
                normalized,
                "orgao",
                organization,
                "cnpj",
                preserve_existing_on_null=True,
            )
            _upsert(
                normalized,
                "unidade",
                unit,
                "orgao_cnpj, codigo_unidade",
                preserve_existing_on_null=True,
            )
            if supplier is not None:
                _upsert(
                    normalized,
                    "fornecedor",
                    supplier,
                    "tipo_pessoa, ni_fornecedor",
                    preserve_existing_on_null=True,
                )
            _upsert(normalized, "contrato", contract, "numero_controle_pncp")
            lineage_entities = [
                ("orgao", organization["cnpj"]),
                ("unidade", f"{unit['orgao_cnpj']}|{unit['codigo_unidade']}"),
                ("contrato", contract["numero_controle_pncp"]),
            ]
            if supplier is not None:
                lineage_entities.append(
                    (
                        "fornecedor",
                        f"{supplier['tipo_pessoa']}|{supplier['ni_fornecedor']}",
                    )
                )
            for table, key in lineage_entities:
                _insert_lineage(
                    normalized,
                    table=table,
                    entity_key=key,
                    raw_asset=raw_asset,
                    run_id=run_id,
                    observed_at=normalized_at,
                )

        known_contracts = {
            row[0]
            for row in normalized.execute(
                "SELECT numero_controle_pncp FROM contrato"
            )
        }
        for source_row in _rows(raw, "empenho"):
            raw_seen += 1
            raw_asset = _raw_mapping(source_row)
            commitment = normalize_commitment(
                raw_asset,
                json.loads(source_row["payload"]),
                normalized_at=normalized_at,
            )
            if commitment["numero_controle_contrato"] not in known_contracts:
                raise ValueError("Empenho sem contrato-pai no snapshot")
            _upsert(
                normalized,
                "empenho",
                commitment,
                "numero_controle_contrato, sequencial_empenho",
            )
            _insert_lineage(
                normalized,
                table="empenho",
                entity_key=(
                    f"{commitment['numero_controle_contrato']}|"
                    f"{commitment['sequencial_empenho']}"
                ),
                raw_asset=raw_asset,
                run_id=run_id,
                observed_at=normalized_at,
            )

        for source_row in _rows(raw, "ata_registro_preco"):
            raw_seen += 1
            raw_asset = _raw_mapping(source_row)
            payload = json.loads(source_row["payload"])
            query_params = json.loads(source_row["query_params"] or "{}")
            purchase_id = payload.get("numeroControlePNCPCompra") or query_params.get(
                "numeroControlePNCPCompra"
            )
            organization, unit, ata = normalize_price_registry(
                raw_asset,
                payload,
                query_params,
                normalized_at=normalized_at,
                parent_is_present=str(purchase_id) in known_procurements,
            )
            _upsert(
                normalized,
                "orgao",
                organization,
                "cnpj",
                preserve_existing_on_null=True,
            )
            _upsert(
                normalized,
                "unidade",
                unit,
                "orgao_cnpj, codigo_unidade",
                preserve_existing_on_null=True,
            )
            _upsert(
                normalized,
                "ata_registro_preco",
                ata,
                "numero_controle_pncp_ata",
            )
            _insert_lineage(
                normalized,
                table="ata_registro_preco",
                entity_key=ata["numero_controle_pncp_ata"],
                raw_asset=raw_asset,
                run_id=run_id,
                observed_at=normalized_at,
            )

        known_organizations = {
            row[0] for row in normalized.execute("SELECT cnpj FROM orgao")
        }
        for source_row in _rows(raw, "pca"):
            raw_seen += 1
            raw_asset = _raw_mapping(source_row)
            pca, unit, pca_items = normalize_pca(
                raw_asset,
                json.loads(source_row["payload"]),
                normalized_at=normalized_at,
            )
            pair = (pca["orgao_cnpj"], pca["codigo_unidade"])
            if pair not in inventory_members or pca["orgao_cnpj"] not in known_organizations:
                skipped += 1
                issue_count += 1
                _insert_issue(
                    normalized,
                    run_id=run_id,
                    raw_asset_id=raw_asset["id"],
                    resource_type="pca",
                    issue_code="OUT_OF_SCOPE",
                    severity="INFO",
                    field_name="orgao_unidade",
                    observed_value=f"{pair[0]}|{pair[1]}",
                    details="PCA fora do inventário comprovado pela RAW do RJ.",
                    created_at=normalized_at,
                )
                continue
            _upsert(
                normalized,
                "unidade",
                unit,
                "orgao_cnpj, codigo_unidade",
                preserve_existing_on_null=True,
            )
            _upsert(normalized, "pca", pca, "id_pca_pncp")
            _insert_lineage(
                normalized,
                table="pca",
                entity_key=pca["id_pca_pncp"],
                raw_asset=raw_asset,
                run_id=run_id,
                observed_at=normalized_at,
            )
            for pca_item in pca_items:
                _upsert(
                    normalized,
                    "pca_item",
                    pca_item,
                    "id_pca_pncp, numero_item",
                )
                _insert_lineage(
                    normalized,
                    table="pca_item",
                    entity_key=f"{pca_item['id_pca_pncp']}|{pca_item['numero_item']}",
                    raw_asset=raw_asset,
                    run_id=run_id,
                    observed_at=normalized_at,
                )

        for table in counts:
            counts[table] = normalized.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0]

        coverage = {
            "contratacao": ("COMPLETE", 0, "Completo em relação ao snapshot RAW informado."),
            "contratacao_item": ("COMPLETE", 0, "Completo em relação ao snapshot RAW informado."),
            "resultado_item": ("COMPLETE", 0, "Completo em relação ao snapshot RAW informado."),
            "contrato": ("COMPLETE", 0, "Completo em relação ao snapshot RAW informado."),
            "empenho": (
                "PARTIAL",
                known_commitment_gap_count,
                "Cobertura de empenhos não representa todos os contratos; "
                "ausência de linha não prova inexistência.",
            ),
            "ata_registro_preco": ("PARTIAL", 0, "Cobertura híbrida e direcionada."),
            "pca": (
                "COMPLETE",
                0,
                "Completo em relação ao snapshot e ao inventário RJ; planos externos foram filtrados.",
            ),
        }
        for resource, (status, gaps, notes) in coverage.items():
            count = counts[resource]
            normalized.execute(
                "INSERT INTO normalization_coverage "
                "(resource_type, coverage_status, record_count, known_gap_count, "
                "inventory_id, notes, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    resource,
                    status,
                    count,
                    gaps,
                    inventory_id if resource in {"pca", "contrato", "ata_registro_preco"} else None,
                    notes,
                    normalized_at,
                ),
            )

        written = sum(counts.values())
        normalized.execute(
            "UPDATE normalization_run SET status='SUCCESS', finished_at=?, "
            "records_seen=?, records_written=?, records_skipped=?, "
            "records_rejected=0 WHERE id=?",
            (utc_now(), raw_seen, written, skipped, run_id),
        )
        normalized.commit()
    except Exception:
        normalized.rollback()
        normalized.close()
        output_path.unlink(missing_ok=True)
        raise
    finally:
        raw.close()
        if normalized:
            try:
                normalized.close()
            except sqlite3.Error:
                pass

    return NormalizationResult(
        run_id=run_id,
        raw_records_seen=raw_seen,
        normalized_rows_written=sum(counts.values()),
        skipped=skipped,
        rejected=0,
        issues=issue_count,
        counts=counts,
        source_snapshot_sha256=snapshot_hash,
    )
