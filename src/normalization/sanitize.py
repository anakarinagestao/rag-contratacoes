from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
from typing import Iterable

from src.obs import utc_now


POLICY_VERSION = "pncp-public-v1"
TOKEN_PREFIX = "anon-v1-"
MASKED_NAME = "PESSOA PROTEGIDA"
MINIMUM_KEY_BYTES = 32

REQUIRED_TABLES = {
    "fornecedor",
    "resultado_item",
    "contrato",
    "empenho",
    "normalization_lineage",
    "normalization_issue",
}

SENSITIVE_ISSUE_FIELDS = (
    "cpf",
    "ni_fornecedor",
    "ni_credor_fornecedor",
    "nome_razao_social",
    "nome_fornecedor_informado",
    "nome_credor_fornecedor",
)


@dataclass(frozen=True)
class SanitizationResult:
    output_database: str
    policy_version: str
    protected_types: tuple[str, ...]
    suppliers_pseudonymized: int
    results_pseudonymized: int
    contracts_pseudonymized: int
    commitments_pseudonymized: int
    lineage_keys_pseudonymized: int
    issues_redacted: int
    source_sha256: str
    output_sha256: str
    key_fingerprint: str


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _pseudonym(key: bytes, person_type: str, identifier: str) -> str:
    message = f"{person_type}\x00{identifier}".encode("utf-8")
    return TOKEN_PREFIX + hmac.new(key, message, hashlib.sha256).hexdigest()


def _normalized_types(values: Iterable[str]) -> tuple[str, ...]:
    protected = tuple(sorted({str(value).strip().upper() for value in values}))
    if not protected or any(not value for value in protected):
        raise ValueError("Informe ao menos um tipo de pessoa para proteger")
    return protected


def _count(
    connection: sqlite3.Connection,
    table: str,
    type_column: str,
    protected: tuple[str, ...],
) -> int:
    placeholders = ", ".join("?" for _ in protected)
    return int(
        connection.execute(
            f"SELECT COUNT(*) FROM {table} "
            f"WHERE {type_column} IN ({placeholders})",
            protected,
        ).fetchone()[0]
    )


def sanitize_normalized_database(
    source_database: str | Path,
    output_database: str | Path,
    *,
    hmac_key: bytes,
    protected_types: Iterable[str] = ("PF", "PE"),
    policy_version: str = POLICY_VERSION,
) -> SanitizationResult:
    """Cria uma cópia publicável sem identificadores diretos de PF/PE.

    A origem é aberta somente para leitura. A saída nunca é sobrescrita e,
    em caso de falha, o arquivo incompleto é removido. A chave HMAC não é
    persistida; somente seu fingerprint é registrado para auditoria.
    """

    source_path = Path(source_database).expanduser().resolve()
    output_path = Path(output_database).expanduser().resolve()
    protected = _normalized_types(protected_types)
    key = bytes(hmac_key)

    if len(key) < MINIMUM_KEY_BYTES:
        raise ValueError(
            f"A chave HMAC deve possuir ao menos {MINIMUM_KEY_BYTES} bytes"
        )
    if not source_path.is_file():
        raise FileNotFoundError(f"Banco normalizado não encontrado: {source_path}")
    if source_path == output_path:
        raise ValueError("Origem e saída sanitizada devem ser bancos distintos")
    if output_path.exists():
        raise FileExistsError(f"Destino já existe; recusei sobrescrever: {output_path}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    source_sha = _sha256(source_path)
    key_fingerprint = hashlib.sha256(b"pncp-key-id\x00" + key).hexdigest()
    created_at = utc_now()

    source: sqlite3.Connection | None = None
    target: sqlite3.Connection | None = None
    try:
        source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
        source.execute("PRAGMA query_only = ON")
        tables = {
            row[0]
            for row in source.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        missing = sorted(REQUIRED_TABLES - tables)
        if missing:
            raise ValueError(
                "Banco não possui o esquema normalizado esperado: "
                + ", ".join(missing)
            )

        target = sqlite3.connect(output_path)
        source.backup(target)
        source.close()
        source = None

        target.execute("PRAGMA journal_mode = DELETE")
        target.execute("PRAGMA secure_delete = ON")
        target.execute("PRAGMA foreign_keys = OFF")
        target.create_function(
            "pncp_pseudonym",
            2,
            lambda person_type, identifier: (
                None
                if identifier is None
                else _pseudonym(key, str(person_type), str(identifier))
            ),
            deterministic=True,
        )

        suppliers = _count(target, "fornecedor", "tipo_pessoa", protected)
        results = _count(
            target, "resultado_item", "tipo_pessoa_fornecedor", protected
        )
        contracts = _count(
            target, "contrato", "tipo_pessoa_fornecedor", protected
        )
        commitments = _count(
            target, "empenho", "tipo_pessoa_credor", protected
        )
        placeholders = ", ".join("?" for _ in protected)
        supplier_keys = list(
            target.execute(
                "SELECT tipo_pessoa, ni_fornecedor FROM fornecedor "
                f"WHERE tipo_pessoa IN ({placeholders})",
                protected,
            )
        )
        token_pairs = [
            (
                f"{person_type}|{_pseudonym(key, person_type, identifier)}",
                f"{person_type}|{identifier}",
            )
            for person_type, identifier in supplier_keys
        ]
        if len({new for new, _ in token_pairs}) != len(token_pairs):
            raise ValueError("Colisão inesperada entre pseudônimos")

        issue_placeholders = ", ".join("?" for _ in SENSITIVE_ISSUE_FIELDS)
        issues_redacted = int(
            target.execute(
                "SELECT COUNT(*) FROM normalization_issue "
                f"WHERE lower(COALESCE(field_name, '')) IN ({issue_placeholders})",
                SENSITIVE_ISSUE_FIELDS,
            ).fetchone()[0]
        )

        target.execute("BEGIN IMMEDIATE")
        target.execute(
            "UPDATE resultado_item "
            "SET ni_fornecedor = pncp_pseudonym("
            "tipo_pessoa_fornecedor, ni_fornecedor) "
            f"WHERE tipo_pessoa_fornecedor IN ({placeholders})",
            protected,
        )
        target.execute(
            "UPDATE contrato SET "
            "ni_fornecedor = CASE WHEN ni_fornecedor IS NULL THEN NULL "
            "ELSE pncp_pseudonym(tipo_pessoa_fornecedor, ni_fornecedor) END, "
            "nome_fornecedor_informado = CASE "
            "WHEN nome_fornecedor_informado IS NULL THEN NULL ELSE ? END "
            f"WHERE tipo_pessoa_fornecedor IN ({placeholders})",
            (MASKED_NAME, *protected),
        )
        target.execute(
            "UPDATE empenho SET "
            "ni_credor_fornecedor = pncp_pseudonym("
            "tipo_pessoa_credor, ni_credor_fornecedor), "
            "nome_credor_fornecedor = CASE "
            "WHEN nome_credor_fornecedor IS NULL THEN NULL ELSE ? END "
            f"WHERE tipo_pessoa_credor IN ({placeholders})",
            (MASKED_NAME, *protected),
        )
        target.executemany(
            "UPDATE normalization_lineage SET entity_key = ? "
            "WHERE normalized_table = 'fornecedor' AND entity_key = ?",
            token_pairs,
        )
        target.execute(
            "UPDATE fornecedor SET "
            "ni_fornecedor = pncp_pseudonym(tipo_pessoa, ni_fornecedor), "
            "nome_razao_social = CASE "
            "WHEN nome_razao_social IS NULL THEN NULL ELSE ? END "
            f"WHERE tipo_pessoa IN ({placeholders})",
            (MASKED_NAME, *protected),
        )
        target.execute(
            "UPDATE normalization_issue SET observed_value = NULL, "
            "details = 'Detalhe removido pela política de sanitização.' "
            f"WHERE lower(COALESCE(field_name, '')) IN ({issue_placeholders})",
            SENSITIVE_ISSUE_FIELDS,
        )
        target.execute(
            "CREATE TABLE sanitization_metadata ("
            "policy_version TEXT PRIMARY KEY, "
            "source_sha256 TEXT NOT NULL, "
            "key_fingerprint TEXT NOT NULL, "
            "protected_types_json TEXT NOT NULL, "
            "suppliers_pseudonymized INTEGER NOT NULL, "
            "results_pseudonymized INTEGER NOT NULL, "
            "contracts_pseudonymized INTEGER NOT NULL, "
            "commitments_pseudonymized INTEGER NOT NULL, "
            "lineage_keys_pseudonymized INTEGER NOT NULL, "
            "issues_redacted INTEGER NOT NULL, "
            "masked_name TEXT NOT NULL, "
            "vacuumed INTEGER NOT NULL CHECK (vacuumed = 1), "
            "created_at TEXT NOT NULL)"
        )
        target.execute(
            "INSERT INTO sanitization_metadata VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
            (
                policy_version,
                source_sha,
                key_fingerprint,
                json.dumps(protected, ensure_ascii=False),
                suppliers,
                results,
                contracts,
                commitments,
                len(token_pairs),
                issues_redacted,
                MASKED_NAME,
                created_at,
            ),
        )
        target.commit()

        target.execute("PRAGMA foreign_keys = ON")
        violations = target.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise ValueError(
                f"Sanitização gerou {len(violations)} violações de FK"
            )
        target.execute("VACUUM")
        target.execute("PRAGMA optimize")
        integrity = target.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise ValueError(f"Banco sanitizado inválido: {integrity}")
        target.close()
        target = None

        output_sha = _sha256(output_path)
        return SanitizationResult(
            output_database=str(output_path),
            policy_version=policy_version,
            protected_types=protected,
            suppliers_pseudonymized=suppliers,
            results_pseudonymized=results,
            contracts_pseudonymized=contracts,
            commitments_pseudonymized=commitments,
            lineage_keys_pseudonymized=len(token_pairs),
            issues_redacted=issues_redacted,
            source_sha256=source_sha,
            output_sha256=output_sha,
            key_fingerprint=key_fingerprint,
        )
    except Exception:
        if source is not None:
            source.close()
        if target is not None:
            target.close()
        output_path.unlink(missing_ok=True)
        raise
