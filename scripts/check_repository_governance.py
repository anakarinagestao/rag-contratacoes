"""Bloqueia novos artefatos operacionais e sensíveis no Git.

O verificador não abre bancos nem inspeciona o conteúdo dos arquivos. Ele
analisa somente nomes, extensões e tamanhos registrados no índice/commit.
"""

from __future__ import annotations

import argparse
from pathlib import PurePosixPath
import subprocess
import sys
from typing import Iterable, Literal


MAX_TRACKED_FILE_BYTES = 50 * 1024 * 1024

_SQLITE_SUFFIXES = (
    ".db",
    ".sqlite",
    ".sqlite3",
    ".db-wal",
    ".db-shm",
    ".db-journal",
    ".sqlite-wal",
    ".sqlite-shm",
    ".sqlite-journal",
    ".sqlite3-wal",
    ".sqlite3-shm",
    ".sqlite3-journal",
)
_GENERATED_INDEX_SUFFIXES = (
    ".faiss",
    ".ann",
    ".hnsw",
    ".npy",
    ".npz",
    ".safetensors",
    ".pt",
    ".pth",
)
_CREDENTIAL_SUFFIXES = (".pem", ".key", ".p12", ".pfx")
_CREDENTIAL_NAMES = {
    "credentials.json",
    "service-account.json",
    "service_account.json",
    "secrets.json",
}
_CACHE_PARTS = {
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".cache",
}
_GENERATED_PATH_PREFIXES = (
    "data/chunks/",
)


def policy_violations(path: str, size: int) -> list[str]:
    """Retorna violações sem ler o conteúdo do arquivo."""

    normalized = path.replace("\\", "/")
    candidate = PurePosixPath(normalized)
    name = candidate.name.lower()
    parts = {part.lower() for part in candidate.parts}
    violations: list[str] = []

    if name.endswith(_SQLITE_SUFFIXES):
        violations.append("banco SQLite operacional")

    if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
        violations.append("arquivo de ambiente/credencial")
    if name in _CREDENTIAL_NAMES or name.endswith(_CREDENTIAL_SUFFIXES):
        violations.append("arquivo de credencial")

    if name.endswith(_GENERATED_INDEX_SUFFIXES):
        violations.append("índice ou embedding gerado")

    lowered_path = normalized.lower().lstrip("./")
    if lowered_path.startswith(_GENERATED_PATH_PREFIXES):
        violations.append("artefato de chunks gerado")

    if name.endswith(".log") or ".log." in name:
        violations.append("log operacional")
    if name.endswith((".pid", ".tmp", ".temp", ".swp", "~")):
        violations.append("arquivo temporário")
    if parts & _CACHE_PARTS:
        violations.append("cache gerado")

    if size > MAX_TRACKED_FILE_BYTES:
        violations.append(
            f"arquivo maior que {MAX_TRACKED_FILE_BYTES // (1024 * 1024)} MiB"
        )

    return violations


def _git_output(*args: str) -> bytes:
    result = subprocess.run(
        ["git", *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout


def _paths_from_nul(output: bytes) -> list[str]:
    return [
        value.decode("utf-8", errors="surrogateescape")
        for value in output.split(b"\0")
        if value
    ]


def candidate_paths(
    mode: Literal["staged", "tracked", "changed"],
    changed_from: str | None = None,
) -> list[str]:
    """Lista somente arquivos presentes no estado a ser validado."""

    if mode == "staged":
        output = _git_output(
            "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"
        )
    elif mode == "tracked":
        output = _git_output("ls-files", "-z")
    else:
        if not changed_from:
            raise ValueError("changed_from é obrigatório no modo changed")
        output = _git_output(
            "diff",
            "--name-only",
            "--diff-filter=ACMR",
            "-z",
            f"{changed_from}...HEAD",
        )
    return _paths_from_nul(output)


def indexed_size(path: str, mode: Literal["staged", "tracked", "changed"]) -> int:
    """Obtém o tamanho do blob sem depender do arquivo no working tree."""

    object_name = f":{path}" if mode == "staged" else f"HEAD:{path}"
    return int(_git_output("cat-file", "-s", object_name).strip())


def inspect_candidates(
    paths: Iterable[str], mode: Literal["staged", "tracked", "changed"]
) -> list[tuple[str, list[str]]]:
    failures: list[tuple[str, list[str]]] = []
    for path in paths:
        violations = policy_violations(path, indexed_size(path, mode))
        if violations:
            failures.append((path, violations))
    return failures


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verifica artefatos proibidos antes de entrarem no Git."
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--staged",
        action="store_true",
        help="verifica arquivos adicionados/modificados no índice (padrão)",
    )
    group.add_argument(
        "--tracked",
        action="store_true",
        help="verifica todos os arquivos do HEAD",
    )
    group.add_argument(
        "--changed-from",
        metavar="REF",
        help="verifica arquivos adicionados/modificados desde REF",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.tracked:
        mode: Literal["staged", "tracked", "changed"] = "tracked"
        changed_from = None
    elif args.changed_from:
        mode = "changed"
        changed_from = args.changed_from
    else:
        mode = "staged"
        changed_from = None

    try:
        paths = candidate_paths(mode, changed_from)
        failures = inspect_candidates(paths, mode)
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        print(f"Falha ao executar a verificação de governança: {exc}", file=sys.stderr)
        return 2

    if failures:
        print("Artefatos proibidos encontrados:", file=sys.stderr)
        for path, reasons in failures:
            print(f"- {path}: {', '.join(reasons)}", file=sys.stderr)
        return 1

    print(f"Governança do repositório: OK ({len(paths)} arquivo(s) verificado(s)).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
