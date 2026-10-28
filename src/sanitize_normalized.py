from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path

from src.normalization.sanitize import sanitize_normalized_database


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Cria uma cópia sanitizada da base normalizada sem alterar a origem."
        )
    )
    parser.add_argument("--source-database", required=True)
    parser.add_argument("--output-database", required=True)
    parser.add_argument(
        "--key-file",
        required=True,
        help="Arquivo privado com ao menos 32 bytes; a chave não é persistida",
    )
    parser.add_argument(
        "--protect-type",
        action="append",
        dest="protected_types",
        help="Tipo de pessoa protegido; o padrão conservador é PF e PE",
    )
    args = parser.parse_args()

    key_path = Path(args.key_file).expanduser().resolve()
    if not key_path.is_file():
        raise FileNotFoundError(f"Arquivo de chave não encontrado: {key_path}")
    result = sanitize_normalized_database(
        args.source_database,
        args.output_database,
        hmac_key=key_path.read_bytes(),
        protected_types=args.protected_types or ("PF", "PE"),
    )
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
