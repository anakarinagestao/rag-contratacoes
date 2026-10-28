from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from src.normalization.sanitize import MASKED_NAME, sanitize_normalized_database


MIGRATION = Path(__file__).parents[2] / "migrations" / "100_normalized_schema.sql"


class SanitizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.source = self.root / "source.db"
        self.output = self.root / "sanitized.db"
        self.key = b"test-key-with-at-least-thirty-two-bytes!!"
        self._create_source()

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_pseudonymizes_protected_people_and_preserves_relationships(self) -> None:
        result = sanitize_normalized_database(
            self.source,
            self.output,
            hmac_key=self.key,
        )

        self.assertEqual(result.suppliers_pseudonymized, 2)
        self.assertEqual(result.results_pseudonymized, 2)
        self.assertEqual(result.contracts_pseudonymized, 2)
        self.assertEqual(result.commitments_pseudonymized, 2)
        self.assertEqual(result.lineage_keys_pseudonymized, 2)

        with sqlite3.connect(self.output) as db:
            pf = db.execute(
                "SELECT ni_fornecedor, nome_razao_social FROM fornecedor "
                "WHERE tipo_pessoa='PF'"
            ).fetchone()
            pe = db.execute(
                "SELECT ni_fornecedor, nome_razao_social FROM fornecedor "
                "WHERE tipo_pessoa='PE'"
            ).fetchone()
            pj = db.execute(
                "SELECT ni_fornecedor, nome_razao_social FROM fornecedor "
                "WHERE tipo_pessoa='PJ'"
            ).fetchone()
            self.assertTrue(pf[0].startswith("anon-v1-"))
            self.assertTrue(pe[0].startswith("anon-v1-"))
            self.assertNotEqual(pf[0], pe[0])
            self.assertEqual(pf[1], MASKED_NAME)
            self.assertEqual(pe[1], MASKED_NAME)
            self.assertEqual(pj, ("PJ-PUBLIC-ID", "Empresa Publica"))

            result_ids = dict(
                db.execute(
                    "SELECT tipo_pessoa_fornecedor, ni_fornecedor "
                    "FROM resultado_item"
                )
            )
            self.assertEqual(result_ids["PF"], pf[0])
            self.assertEqual(result_ids["PE"], pe[0])
            self.assertEqual(
                db.execute(
                    "SELECT ni_fornecedor FROM contrato "
                    "WHERE numero_controle_pncp='contract-pf'"
                ).fetchone()[0],
                pf[0],
            )
            self.assertEqual(
                db.execute(
                    "SELECT nome_fornecedor_informado FROM contrato "
                    "WHERE numero_controle_pncp='contract-pe-missing'"
                ).fetchone()[0],
                MASKED_NAME,
            )
            self.assertEqual(
                db.execute(
                    "SELECT ni_credor_fornecedor FROM empenho "
                    "WHERE tipo_pessoa_credor='PF'"
                ).fetchone()[0],
                pf[0],
            )
            lineage = {
                row[0]
                for row in db.execute(
                    "SELECT entity_key FROM normalization_lineage "
                    "WHERE normalized_table='fornecedor'"
                )
            }
            self.assertIn(f"PF|{pf[0]}", lineage)
            self.assertIn(f"PE|{pe[0]}", lineage)
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            sensitive_issue = db.execute(
                "SELECT observed_value,details FROM normalization_issue "
                "WHERE issue_code='PERSONAL_IDENTIFIER'"
            ).fetchone()
            date_issue = db.execute(
                "SELECT observed_value,details FROM normalization_issue "
                "WHERE issue_code='SUSPECT_DATE'"
            ).fetchone()
            self.assertEqual(
                sensitive_issue,
                (None, "Detalhe removido pela política de sanitização."),
            )
            self.assertEqual(
                date_issue,
                ("2099-01-01", "Data futura preservada para auditoria."),
            )

        content = self.output.read_bytes()
        for secret in (
            b"PF-SECRET-ID",
            b"PE-SECRET-ID",
            b"Pessoa Fisica Secreta",
            b"Pessoa Estrangeira Secreta",
            b"PF-SECRET-IN-ISSUE",
        ):
            self.assertNotIn(secret, content)

    def test_is_deterministic_for_the_same_key(self) -> None:
        second = self.root / "sanitized-second.db"
        sanitize_normalized_database(self.source, self.output, hmac_key=self.key)
        sanitize_normalized_database(self.source, second, hmac_key=self.key)
        with sqlite3.connect(self.output) as first_db, sqlite3.connect(second) as second_db:
            first = first_db.execute(
                "SELECT ni_fornecedor FROM fornecedor WHERE tipo_pessoa='PF'"
            ).fetchone()[0]
            other = second_db.execute(
                "SELECT ni_fornecedor FROM fornecedor WHERE tipo_pessoa='PF'"
            ).fetchone()[0]
        self.assertEqual(first, other)

    def test_refuses_unsafe_paths_and_short_keys(self) -> None:
        with self.assertRaises(ValueError):
            sanitize_normalized_database(
                self.source,
                self.output,
                hmac_key=b"short",
            )
        with self.assertRaises(ValueError):
            sanitize_normalized_database(
                self.source,
                self.source,
                hmac_key=self.key,
            )
        self.output.touch()
        with self.assertRaises(FileExistsError):
            sanitize_normalized_database(
                self.source,
                self.output,
                hmac_key=self.key,
            )

    def test_refuses_database_without_normalized_schema(self) -> None:
        invalid = self.root / "invalid.db"
        with sqlite3.connect(invalid) as db:
            db.execute("CREATE TABLE anything (id INTEGER)")
        with self.assertRaises(ValueError):
            sanitize_normalized_database(
                invalid,
                self.output,
                hmac_key=self.key,
            )
        self.assertFalse(self.output.exists())

    def _create_source(self) -> None:
        with sqlite3.connect(self.source) as db:
            db.execute("PRAGMA foreign_keys = ON")
            db.executescript(MIGRATION.read_text(encoding="utf-8"))
            db.execute(
                "INSERT INTO normalization_run "
                "(id,source_snapshot_sha256,transform_version,started_at,status) "
                "VALUES ('run-1','source-hash','v1','2026-10-20','SUCCESS')"
            )
            db.execute(
                "INSERT INTO orgao "
                "(cnpj,razao_social,current_source_raw_asset_id,"
                "current_source_payload_hash,normalized_at) "
                "VALUES ('12345678000199','Orgao','raw-org','hash','2026-10-20')"
            )
            db.execute(
                "INSERT INTO unidade "
                "(orgao_cnpj,codigo_unidade,uf_sigla,current_source_raw_asset_id,"
                "current_source_payload_hash,normalized_at) "
                "VALUES ('12345678000199','U1','RJ','raw-unit','hash','2026-10-20')"
            )
            db.execute(
                "INSERT INTO contratacao "
                "(numero_controle_pncp,orgao_cnpj,codigo_unidade,ano_compra,"
                "sequencial_compra,modalidade_id,srp,current_source_raw_asset_id,"
                "current_source_payload_hash,normalized_at) "
                "VALUES ('purchase-1','12345678000199','U1',2026,1,6,0,"
                "'raw-purchase','hash','2026-10-20')"
            )
            db.execute(
                "INSERT INTO contratacao_item "
                "(numero_controle_pncp,numero_item,tem_resultado,"
                "current_source_raw_asset_id,current_source_payload_hash,normalized_at) "
                "VALUES ('purchase-1',1,1,'raw-item','hash','2026-10-20')"
            )
            suppliers = (
                ("PF", "PF-SECRET-ID", "Pessoa Fisica Secreta", 1),
                ("PE", "PE-SECRET-ID", "Pessoa Estrangeira Secreta", 0),
                ("PJ", "PJ-PUBLIC-ID", "Empresa Publica", 0),
            )
            db.executemany(
                "INSERT INTO fornecedor "
                "(tipo_pessoa,ni_fornecedor,nome_razao_social,identificador_pessoal,"
                "current_source_raw_asset_id,current_source_payload_hash,normalized_at) "
                "VALUES (?,?,?,?,?,?,?)",
                [
                    (*supplier, f"raw-{supplier[0]}", "hash", "2026-10-20")
                    for supplier in suppliers
                ],
            )
            db.executemany(
                "INSERT INTO resultado_item "
                "(numero_controle_pncp,numero_item,sequencial_resultado,"
                "tipo_pessoa_fornecedor,ni_fornecedor,current_source_raw_asset_id,"
                "current_source_payload_hash,normalized_at) VALUES (?,?,?,?,?,?,?,?)",
                (
                    ("purchase-1", 1, 1, "PF", "PF-SECRET-ID", "raw-r1", "hash", "2026-10-20"),
                    ("purchase-1", 1, 2, "PE", "PE-SECRET-ID", "raw-r2", "hash", "2026-10-20"),
                ),
            )
            contracts = (
                ("contract-pf", "PF", "PF-SECRET-ID", "Pessoa Fisica Secreta", "RESOLVED"),
                ("contract-pj", "PJ", "PJ-PUBLIC-ID", "Empresa Publica", "RESOLVED"),
                ("contract-pe-missing", "PE", None, "Pessoa Estrangeira Sem NI", "MISSING_IDENTIFIER"),
            )
            db.executemany(
                "INSERT INTO contrato "
                "(numero_controle_pncp,numero_controle_compra_externo,"
                "contratacao_id,parent_resolution_status,orgao_cnpj,codigo_unidade,"
                "tipo_pessoa_fornecedor,ni_fornecedor,nome_fornecedor_informado,"
                "supplier_resolution_status,current_source_raw_asset_id,"
                "current_source_payload_hash,normalized_at) "
                "VALUES (?, 'outside', NULL, 'OUT_OF_SCOPE', '12345678000199',"
                "'U1',?,?,?,?,?,?,?)",
                [
                    (
                        number,
                        person_type,
                        identifier,
                        name,
                        status,
                        f"raw-{number}",
                        "hash",
                        "2026-10-20",
                    )
                    for number, person_type, identifier, name, status in contracts
                ],
            )
            db.executemany(
                "INSERT INTO empenho "
                "(numero_controle_contrato,sequencial_empenho,tipo_pessoa_credor,"
                "ni_credor_fornecedor,nome_credor_fornecedor,"
                "current_source_raw_asset_id,current_source_payload_hash,normalized_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    ("contract-pf", 1, "PF", "PF-SECRET-ID", "Pessoa Fisica Secreta", "raw-e1", "hash", "2026-10-20"),
                    ("contract-pj", 1, "PE", "PE-SECRET-ID", "Pessoa Estrangeira Secreta", "raw-e2", "hash", "2026-10-20"),
                ),
            )
            db.executemany(
                "INSERT INTO normalization_lineage "
                "(normalized_table,entity_key,raw_asset_id,payload_hash,"
                "normalization_run_id,is_current,observed_at) "
                "VALUES ('fornecedor',?,?,?,'run-1',1,'2026-10-20')",
                (
                    ("PF|PF-SECRET-ID", "raw-PF", "hash"),
                    ("PE|PE-SECRET-ID", "raw-PE", "hash"),
                ),
            )
            db.executemany(
                "INSERT INTO normalization_issue "
                "(id,normalization_run_id,raw_asset_id,resource_type,issue_code,"
                "severity,field_name,observed_value,details,created_at) "
                "VALUES (?,'run-1',NULL,?,?,?,?,?,?, '2026-10-20')",
                (
                    (
                        "issue-sensitive",
                        "fornecedor",
                        "PERSONAL_IDENTIFIER",
                        "WARNING",
                        "ni_fornecedor",
                        "PF-SECRET-IN-ISSUE",
                        "Identificador PF-SECRET-IN-ISSUE deve ser removido.",
                    ),
                    (
                        "issue-date",
                        "resultado_item",
                        "SUSPECT_DATE",
                        "WARNING",
                        "data_resultado",
                        "2099-01-01",
                        "Data futura preservada para auditoria.",
                    ),
                ),
            )


if __name__ == "__main__":
    unittest.main()
