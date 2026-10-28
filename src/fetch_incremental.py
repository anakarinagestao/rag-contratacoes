from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys
import tempfile

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.ingestion.incremental_runner import (  # noqa: E402
    INCREMENTAL_RESOURCES,
    IncrementalConfigurationError,
    IncrementalRunConfig,
    OfflineFixtureError,
    run_offline_incremental,
)
from src.ingestion.online_pilot import (  # noqa: E402
    run_online_operational,
    run_online_pilot,
)


def safe_sqlite_database_path(value: str) -> Path:
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        raise argparse.ArgumentTypeError(
            "--db-path deve usar um caminho absoluto"
        )
    resolved = candidate.resolve()
    if resolved.is_dir():
        raise argparse.ArgumentTypeError("--db-path não pode ser diretório")
    if resolved.suffix.lower() not in {".db", ".sqlite"}:
        raise argparse.ArgumentTypeError(
            "Use um arquivo SQLite com extensão .db ou .sqlite"
        )
    return resolved


def _is_temporary_path(path: Path) -> bool:
    try:
        path.resolve().relative_to(Path(tempfile.gettempdir()).resolve())
    except ValueError:
        return False
    return True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Ingestão incremental PNCP protegida por checkpoint v2"
        )
    )
    resources = sorted((*INCREMENTAL_RESOURCES.keys(), "ata"))
    parser.add_argument("--resource", required=True, choices=resources)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--uf", required=True)
    parser.add_argument("--modality", type=int, default=None)
    parser.add_argument(
        "--db-path",
        required=True,
        type=safe_sqlite_database_path,
        help="Caminho absoluto do banco SQLite",
    )
    parser.add_argument(
        "--fixture",
        type=Path,
        help="Arquivo JSON contendo páginas ou alvos simulados (offline)",
    )
    parser.add_argument(
        "--job-name",
        default=None,
        help="Nome lógico; o job_id final continua sendo determinístico",
    )
    parser.add_argument("--worker-id", default="offline-worker")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--resume", action="store_true")
    action.add_argument("--reprocess", action="store_true")
    parser.add_argument("--lease-seconds", type=float, default=300.0)
    parser.add_argument(
        "--lease-renewal-seconds", type=float, default=120.0
    )
    parser.add_argument(
        "--reconciliation-cadence-days",
        type=int,
        default=None,
        help="Sobrescreve a cadência padrão do recurso dependente",
    )
    parser.add_argument(
        "--reconciliation-horizon-days",
        type=int,
        default=None,
        help="Sobrescreve o horizonte padrão do recurso dependente",
    )
    parser.add_argument(
        "--coverage-name",
        default=None,
        help="Nome estável do manifesto de cobertura composta",
    )
    parser.add_argument(
        "--coverage-member",
        default=None,
        help="Membro/shard processado por esta execução",
    )
    parser.add_argument(
        "--coverage-members",
        nargs="+",
        default=(),
        help="Conjunto completo e congelado de membros esperados",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Executa somente a fixture local, sem acesso à rede",
    )
    parser.add_argument(
        "--online-pilot",
        action="store_true",
        help=(
            "Habilita explicitamente o piloto real de contratações: RJ, "
            "uma modalidade, um dia e banco em /tmp"
        ),
    )
    parser.add_argument(
        "--online-operational",
        action="store_true",
        help=(
            "Habilita uma janela real controlada no banco operacional; "
            "exige confirmação redundante do caminho e log explícito"
        ),
    )
    parser.add_argument(
        "--confirm-operational-database",
        type=safe_sqlite_database_path,
        default=None,
        help="Repita exatamente o caminho absoluto de --db-path",
    )
    parser.add_argument(
        "--log-path",
        type=Path,
        default=None,
        help="Log JSONL absoluto e obrigatório no modo operacional",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    selected_modes = sum(
        (args.offline, args.online_pilot, args.online_operational)
    )
    if selected_modes != 1:
        print(
            "ERRO: escolha exatamente um modo: --offline, --online-pilot "
            "ou --online-operational.",
            file=sys.stderr,
        )
        return 2
    if not args.online_operational and not _is_temporary_path(args.db_path):
        print(
            "ERRO: --offline e --online-pilot exigem banco dentro de /tmp.",
            file=sys.stderr,
        )
        return 2
    if args.offline and (args.fixture is None or not args.fixture.is_file()):
        print(
            f"ERRO: fixture offline não encontrada: {args.fixture}",
            file=sys.stderr,
        )
        return 2
    if (
        args.online_pilot or args.online_operational
    ) and args.fixture is not None:
        print(
            "ERRO: modos online não aceitam --fixture.",
            file=sys.stderr,
        )
        return 2
    if args.online_operational:
        if args.confirm_operational_database != args.db_path:
            print(
                "ERRO: --confirm-operational-database deve repetir "
                "exatamente --db-path.",
                file=sys.stderr,
            )
            return 2
        if not args.db_path.is_file():
            print("ERRO: banco operacional não existe.", file=sys.stderr)
            return 2
        if (
            args.log_path is None
            or not args.log_path.is_absolute()
            or args.log_path.suffix.lower() != ".jsonl"
        ):
            print(
                "ERRO: --log-path operacional deve ser absoluto e terminar "
                "em .jsonl.",
                file=sys.stderr,
            )
            return 2
    database_url = f"sqlite:///{args.db_path}"
    try:
        config = IncrementalRunConfig(
            resource=args.resource,
            start_date=args.start_date,
            end_date=args.end_date,
            uf=args.uf,
            modality=args.modality,
            database_url=database_url,
            fixture_path=args.fixture,
            job_name=args.job_name,
            worker_id=args.worker_id,
            resume=args.resume,
            reprocess=args.reprocess,
            lease_seconds=args.lease_seconds,
            lease_renewal_seconds=args.lease_renewal_seconds,
            reconciliation_cadence_days=(
                args.reconciliation_cadence_days
            ),
            reconciliation_horizon_days=(
                args.reconciliation_horizon_days
            ),
            coverage_name=args.coverage_name,
            coverage_member=args.coverage_member,
            coverage_members=tuple(args.coverage_members),
            log_path=args.log_path,
        )
        if args.offline:
            report = run_offline_incremental(config)
        elif args.online_pilot:
            report = run_online_pilot(config)
        else:
            report = run_online_operational(
                config, args.confirm_operational_database
            )
    except (
        IncrementalConfigurationError,
        OfflineFixtureError,
        RuntimeError,
    ) as error:
        print(
            json.dumps(
                {"status": "FAILED", "error": str(error)},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(asdict(report), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
