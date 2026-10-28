from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.fetch_incremental import safe_sqlite_database_path  # noqa: E402
from src.ingestion.coverage_orchestrator import (  # noqa: E402
    DEFAULT_COVERAGE_NAME,
    MINIMUM_SHARD_DELAY_SECONDS,
    execute_contracting_coverage,
    plan_contracting_coverage,
)
from src.ingestion.incremental_runner import (  # noqa: E402
    IncrementalConfigurationError,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Planeja e executa cobertura incremental PNCP por modalidade"
    )
    parser.add_argument("--date", required=True)
    parser.add_argument(
        "--db-path", required=True, type=safe_sqlite_database_path
    )
    parser.add_argument(
        "--coverage-name", default=DEFAULT_COVERAGE_NAME
    )
    parser.add_argument(
        "--inter-shard-seconds",
        type=int,
        default=MINIMUM_SHARD_DELAY_SECONDS,
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument(
        "--reprocess-completed",
        action="store_true",
        help="Reprocessa membros concluídos de uma janela sobreposta",
    )
    parser.add_argument(
        "--confirm-operational-database",
        type=safe_sqlite_database_path,
        default=None,
    )
    parser.add_argument("--log-path", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        plan = plan_contracting_coverage(
            args.db_path,
            args.date,
            coverage_name=args.coverage_name,
            reprocess_completed=args.reprocess_completed,
        )
        output = {"plan": asdict(plan), "reports": []}
        if args.execute:
            if args.confirm_operational_database is None:
                raise IncrementalConfigurationError(
                    "--execute requer --confirm-operational-database"
                )
            if args.log_path is None:
                raise IncrementalConfigurationError(
                    "--execute requer --log-path"
                )
            reports = execute_contracting_coverage(
                plan,
                confirmed_database=args.confirm_operational_database,
                log_path=args.log_path,
                inter_shard_seconds=args.inter_shard_seconds,
            )
            output["reports"] = [asdict(report) for report in reports]
    except (IncrementalConfigurationError, ValueError, RuntimeError) as error:
        print(
            json.dumps(
                {"status": "FAILED", "error": str(error)},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
