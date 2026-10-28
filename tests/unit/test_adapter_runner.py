from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from app.core.database.init_db import SCHEMA, initialize_database
from app.core.database.repositories.sync_repository import WindowContinuityError
from src.ingestion.adapter_runner import (
    IncrementalAdapterPlan,
    run_adapter_incremental,
)
from src.ingestion.incremental_http import IncrementalHTTPPage
from src.ingestion.incremental_runner import (
    IncrementalConfigurationError,
    IncrementalRunConfig,
)
from src.ingestion.pncp_incremental_adapters import (
    PNCPEndpointIncrementalAdapter,
    PNCPRelationshipIncrementalAdapter,
)
from src.schema import CollectionCheckpoint, CollectedRecord


class FakeTransport:
    def __init__(self, pages):
        self.pages = list(pages)

    def fetch_page(self, endpoint, params, **options):
        return self.pages.pop(0)


def page(number, records=(), *, total=1, outcome=None, status=200):
    return IncrementalHTTPPage(
        status,
        outcome or ("FOUND" if records else "EMPTY"),
        tuple(records),
        number,
        total,
        f"https://pncp.example/api?pagina={number}",
        1,
    )


class AdapterRunnerTests(unittest.TestCase):
    PURCHASE = "12345678000199-1-000007/2023"

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = Path(self.temp_dir.name) / "adapter.db"

    def tearDown(self):
        self.temp_dir.cleanup()

    def config(self, resource, start, end, **changes):
        values = {
            "resource": resource,
            "start_date": start,
            "end_date": end,
            "uf": "RJ",
            "database_url": f"sqlite:///{self.database}",
            "job_name": f"adapter-{resource}",
            "worker_id": "adapter-test-worker",
        }
        values.update(changes)
        return IncrementalRunConfig(**values)

    def test_endpoint_adapter_runs_through_raw_and_checkpoint_v2(self):
        adapter = PNCPEndpointIncrementalAdapter(
            FakeTransport(
                [
                    page(
                        1,
                        (
                            {
                                "numeroControlePNCP": self.PURCHASE,
                                "unidadeOrgao": {"ufSigla": "RJ"},
                                "dataAtualizacaoGlobal": "2023-04-01T10:00:00",
                            },
                        ),
                    )
                ]
            )
        )
        plan = IncrementalAdapterPlan(
            endpoint="/api/consulta/v1/contratacoes/atualizacao",
            filters={"codigoModalidadeContratacao": 6, "uf": "RJ"},
            event_provider=lambda _: adapter.collect(
                "contratacao",
                "adapter-contratacao",
                "2023-04-01",
                "2023-04-01",
                filters={"codigoModalidadeContratacao": 6, "uf": "RJ"},
                base_url="https://pncp.example",
            ),
        )

        with patch(
            "urllib.request.urlopen",
            side_effect=AssertionError("rede real foi acessada"),
        ) as network:
            report = run_adapter_incremental(
                self.config(
                    "contratacao",
                    "2023-04-01",
                    "2023-04-01",
                    modality=6,
                ),
                plan,
            )

        network.assert_not_called()
        self.assertEqual((report.status, report.new), ("SUCCEEDED", 1))
        connection = sqlite3.connect(self.database)
        self.assertEqual(
            connection.execute("SELECT COUNT(*) FROM raw_asset").fetchone()[0],
            1,
        )
        self.assertEqual(
            connection.execute(
                "SELECT status FROM sync_window"
            ).fetchone()[0],
            "COMPLETED",
        )
        self.assertEqual(
            connection.execute(
                "SELECT watermark_value FROM sync_watermark"
            ).fetchone()[0],
            "2023-04-01T23:59:59.999999",
        )
        connection.close()

    def test_due_relationship_target_is_reconciled_without_parent_change(self):
        target = {"numero_controle": self.PURCHASE}
        empty_adapter = PNCPRelationshipIncrementalAdapter(
            FakeTransport([page(1, (), total=None, outcome="EMPTY")])
        )
        first_plan = IncrementalAdapterPlan(
            endpoint="/api/pncp/v1/orgaos/{cnpj}/compras/{ano}/{sequencial}/itens",
            filters={"scope": "purchase-items"},
            discoveries=(target,),
            event_provider=lambda targets: self._relationship_events(
                empty_adapter, "contratacao_item", targets
            ),
        )
        first = run_adapter_incremental(
            self.config(
                "contratacao_item", "2023-04-01", "2023-04-01"
            ),
            first_plan,
        )
        self.assertEqual(first.records_seen, 0)

        late_adapter = PNCPRelationshipIncrementalAdapter(
            FakeTransport([page(1, ({"numeroItem": 1},))])
        )
        second_plan = IncrementalAdapterPlan(
            endpoint=first_plan.endpoint,
            filters=first_plan.filters,
            discoveries=(),
            event_provider=lambda targets: self._relationship_events(
                late_adapter, "contratacao_item", targets
            ),
        )
        second = run_adapter_incremental(
            self.config(
                "contratacao_item", "2023-04-02", "2023-04-02"
            ),
            second_plan,
        )

        self.assertEqual((second.records_seen, second.new), (1, 1))
        connection = sqlite3.connect(self.database)
        queue = connection.execute(
            "SELECT last_outcome, last_checked_at, next_due_at "
            "FROM sync_reconciliation_target"
        ).fetchone()
        self.assertEqual(queue, ("FOUND", "2023-04-02", "2023-04-03"))
        connection.close()

    def test_missing_target_checkpoint_fails_without_watermark(self):
        target = {"numero_controle": self.PURCHASE}
        plan = IncrementalAdapterPlan(
            endpoint="/api/pncp/v1/items",
            filters={"scope": "purchase-items"},
            discoveries=(target,),
            event_provider=lambda _: (),
        )
        with self.assertRaisesRegex(ValueError, "alvos incompletos"):
            run_adapter_incremental(
                self.config(
                    "contratacao_item", "2023-04-01", "2023-04-01"
                ),
                plan,
            )
        connection = sqlite3.connect(self.database)
        self.assertEqual(
            connection.execute("SELECT COUNT(*) FROM sync_watermark").fetchone()[0],
            0,
        )
        self.assertEqual(
            connection.execute("SELECT status FROM sync_window").fetchone()[0],
            "FAILED",
        )
        connection.close()

    def test_failed_endpoint_window_restarts_from_first_page_on_resume(self):
        first_payload = {
            "numeroControlePNCP": self.PURCHASE,
            "unidadeOrgao": {"ufSigla": "RJ"},
        }
        second_control = "12345678000199-1-000008/2023"
        second_payload = {
            "numeroControlePNCP": second_control,
            "unidadeOrgao": {"ufSigla": "RJ"},
        }

        failing_adapter = PNCPEndpointIncrementalAdapter(
            FakeTransport(
                [
                    page(1, (first_payload,), total=2),
                    page(2, (second_payload,), total=3),
                ]
            )
        )
        failing_plan = self._contracting_plan(failing_adapter)
        config = self.config(
            "contratacao",
            "2023-04-01",
            "2023-04-01",
            modality=6,
        )
        with self.assertRaisesRegex(RuntimeError, "mudou"):
            run_adapter_incremental(config, failing_plan)

        connection = sqlite3.connect(self.database)
        self.assertEqual(
            connection.execute("SELECT status FROM sync_window").fetchone()[0],
            "FAILED",
        )
        self.assertEqual(
            connection.execute("SELECT COUNT(*) FROM sync_watermark").fetchone()[0],
            0,
        )
        connection.close()

        resumed_adapter = PNCPEndpointIncrementalAdapter(
            FakeTransport(
                [
                    page(1, (first_payload,), total=2),
                    page(2, (second_payload,), total=2),
                ]
            )
        )
        report = run_adapter_incremental(
            self.config(
                "contratacao",
                "2023-04-01",
                "2023-04-01",
                modality=6,
                resume=True,
                worker_id="resume-worker",
            ),
            self._contracting_plan(resumed_adapter),
        )
        self.assertEqual((report.new, report.unchanged), (1, 1))
        connection = sqlite3.connect(self.database)
        self.assertEqual(
            connection.execute("SELECT COUNT(*) FROM raw_asset").fetchone()[0],
            2,
        )
        self.assertEqual(
            connection.execute("SELECT COUNT(*) FROM sync_watermark").fetchone()[0],
            1,
        )
        connection.close()

    def test_gap_is_rejected_before_transport_is_consumed(self):
        first_adapter = PNCPEndpointIncrementalAdapter(
            FakeTransport([page(1, ())])
        )
        run_adapter_incremental(
            self.config(
                "contratacao",
                "2023-04-01",
                "2023-04-01",
                modality=6,
            ),
            self._contracting_plan(first_adapter),
        )

        def transport_must_not_be_consumed(_):
            raise AssertionError("transporte foi consumido antes do preflight")
            yield

        gap_plan = IncrementalAdapterPlan(
            endpoint="/api/consulta/v1/contratacoes/atualizacao",
            filters={"codigoModalidadeContratacao": 6, "uf": "RJ"},
            event_provider=transport_must_not_be_consumed,
        )
        with self.assertRaisesRegex(
            WindowContinuityError, "deixa lacuna"
        ):
            run_adapter_incremental(
                self.config(
                    "contratacao",
                    "2023-04-03",
                    "2023-04-03",
                    modality=6,
                ),
                gap_plan,
            )

        connection = sqlite3.connect(self.database)
        self.assertEqual(
            connection.execute("SELECT COUNT(*) FROM sync_window").fetchone()[0],
            1,
        )
        self.assertEqual(
            connection.execute("SELECT COUNT(*) FROM ingestion_run").fetchone()[0],
            1,
        )
        connection.close()

    def test_rejects_database_outside_temporary_directory(self):
        config = self.config("contratacao", "2023-04-01", "2023-04-01")
        config = IncrementalRunConfig(
            **{
                **config.__dict__,
                "database_url": "sqlite:///./data/operational.db",
            }
        )
        plan = IncrementalAdapterPlan(
            endpoint="/api",
            filters={},
            event_provider=lambda _: (),
        )
        with self.assertRaisesRegex(
            IncrementalConfigurationError, "dentro de /tmp"
        ):
            run_adapter_incremental(config, plan)

    def test_operational_database_requires_matching_confirmation(self):
        database = Path.cwd() / "data" / "operational.db"
        config = self.config("contratacao", "2023-04-01", "2023-04-01")
        config = IncrementalRunConfig(
            **{
                **config.__dict__,
                "database_url": f"sqlite:///{database}",
            }
        )
        plan = IncrementalAdapterPlan(
            endpoint="/api",
            filters={},
            event_provider=lambda _: (),
        )
        with self.assertRaisesRegex(
            IncrementalConfigurationError, "Confirmação operacional"
        ):
            run_adapter_incremental(
                config,
                plan,
                confirmed_operational_database=database.with_name(
                    "outro.db"
                ),
            )

    def test_rejects_existing_database_with_partial_migrations(self):
        with sqlite3.connect(self.database) as connection:
            connection.executescript(SCHEMA)
        plan = IncrementalAdapterPlan(
            endpoint="/api",
            filters={},
            event_provider=lambda _: (),
        )

        with self.assertRaisesRegex(
            IncrementalConfigurationError, "migrações 001-005"
        ):
            run_adapter_incremental(
                self.config(
                    "contratacao", "2023-04-01", "2023-04-01"
                ),
                plan,
            )

    def test_rejects_experimental_observation_unique_constraint(self):
        # A abertura inicializa o esquema atual completo; o índice abaixo
        # simula uma cópia que recebeu a primeira versão experimental da 003.
        initialized = initialize_database(f"sqlite:///{self.database}")
        initialized.execute(
            "CREATE UNIQUE INDEX legacy_raw_observation_asset_unique "
            "ON raw_observation(raw_asset_id)"
        )
        initialized.commit()
        initialized.close()

        plan = IncrementalAdapterPlan(
            endpoint="/api",
            filters={},
            event_provider=lambda _: (),
        )
        with self.assertRaisesRegex(
            IncrementalConfigurationError, "experimental incompatível"
        ):
            run_adapter_incremental(
                self.config(
                    "contratacao", "2023-04-01", "2023-04-01"
                ),
                plan,
            )

    def test_rejects_log_outside_temporary_directory(self):
        plan = IncrementalAdapterPlan(
            endpoint="/api",
            filters={},
            event_provider=lambda _: (),
        )
        with self.assertRaisesRegex(
            IncrementalConfigurationError, "permanecer em /tmp"
        ):
            run_adapter_incremental(
                self.config(
                    "contratacao",
                    "2023-04-01",
                    "2023-04-01",
                    log_path=Path("./logs/incremental.jsonl"),
                ),
                plan,
            )

    def test_rejects_record_from_source_different_from_job(self):
        events = (
            CollectedRecord(
                "unexpected-source",
                {
                    "numeroControlePNCP": self.PURCHASE,
                    "unidadeOrgao": {"ufSigla": "RJ"},
                },
                "https://pncp.example/api",
                "1",
                1,
                external_id=self.PURCHASE,
            ),
            CollectionCheckpoint("1"),
        )
        plan = IncrementalAdapterPlan(
            endpoint="/api",
            filters={},
            event_provider=lambda _: events,
        )

        with self.assertRaisesRegex(
            IncrementalConfigurationError, "source_id diferente"
        ):
            run_adapter_incremental(
                self.config(
                    "contratacao", "2023-04-01", "2023-04-01"
                ),
                plan,
            )

    @staticmethod
    def _relationship_events(adapter, resource, targets):
        for target in targets:
            yield from adapter.collect_target(
                resource,
                f"adapter-{resource}",
                target,
                base_url="https://pncp.example",
            )

    @staticmethod
    def _contracting_plan(adapter):
        filters = {"codigoModalidadeContratacao": 6, "uf": "RJ"}
        return IncrementalAdapterPlan(
            endpoint="/api/consulta/v1/contratacoes/atualizacao",
            filters=filters,
            event_provider=lambda _: adapter.collect(
                "contratacao",
                "adapter-contratacao",
                "2023-04-01",
                "2023-04-01",
                filters=filters,
                base_url="https://pncp.example",
            ),
        )


if __name__ == "__main__":
    unittest.main()
