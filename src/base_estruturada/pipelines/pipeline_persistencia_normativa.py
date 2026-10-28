"""Persiste a base normativa já processada no SQLite, sem embeddings/FAISS.

Este comando continua existindo com os mesmos argumentos, mas a gravação em si
é feita por app/modules/normative/persistencia.py, o carregador único da base
normativa. Assim há uma só implementação, uma só chave de documento
("editais_normativo|<arquivo>") e uma só versão de corpus ("normativo-<versao>").
"""

from __future__ import annotations

import argparse
from pathlib import Path

from app.modules.normative.persistencia import persistir

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Persiste JSONL normativo no SQLite, sem FAISS."
    )
    parser.add_argument("--versao", default="v1")
    parser.add_argument("--arquivo-chunks", type=Path)
    parser.add_argument("--inventario", type=Path)
    parser.add_argument(
        "--corpus-version-id",
        type=int,
        default=None,
        help="padrão: a versão normativo-<versao>, criada se ainda não existir",
    )
    args = parser.parse_args()

    resumo = persistir(
        versao=args.versao,
        arquivo=args.arquivo_chunks,
        inventario=args.inventario,
        corpus_version_id=args.corpus_version_id,
    )
    print(f"Base normativa persistida no SQLite | {resumo}")
