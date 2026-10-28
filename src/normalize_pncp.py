from __future__ import annotations

import argparse
from dataclasses import asdict
import json

from src.normalization.incremental import normalize_incremental
from src.normalization.runner import normalize_core


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Normaliza um snapshot RAW PNCP em outro banco SQLite."
    )
    parser.add_argument("--raw-database", required=True)
    parser.add_argument("--output-database", required=True)
    parser.add_argument(
        "--transform-version",
        default=None,
    )
    parser.add_argument(
        "--scope-inventory",
        default="config/pncp_rj_scope_inventory.json",
    )
    parser.add_argument(
        "--mode",
        choices=("build", "update"),
        default="build",
        help=(
            "build cria um banco novo; update reconcilia uma RAW cumulativa "
            "com um banco normalizado existente"
        ),
    )
    parser.add_argument(
        "--known-empenho-gaps",
        type=int,
        default=0,
        help="Quantidade auditada de alvos de empenho com lacuna conhecida",
    )
    args = parser.parse_args()
    operation = normalize_core if args.mode == "build" else normalize_incremental
    transform_version = args.transform_version or (
        "normalized-v1" if args.mode == "build" else "normalized-v2-incremental"
    )
    result = operation(
        args.raw_database,
        args.output_database,
        transform_version=transform_version,
        scope_inventory_path=args.scope_inventory,
        known_commitment_gap_count=args.known_empenho_gaps,
    )
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
