from __future__ import annotations

import argparse
import json
from dataclasses import replace

from .ingest import RawRepository, ingest_records
from .ingestion.pncp_client import PNCPClient
from .ingestion.pncp_collector import PCAFullCollector
from .obs import JsonlLogger
from .schema import Manifest


PCA_SOURCE_ID = "pncp_pca_rj_2023"
RUN_SOURCE_ID = "pncp_pca_rj_2023_q2_novos_orgaos"
Q2_CONTRACTING_SOURCE_ID = "pncp_contratacoes_rj_2023_t2_ate_hoje"
T1_SOURCE_PATTERN = "pncp_contratacoes_rj_2023_t1_m%"
Q2_START = "2023-04-01"
Q2_END = "2023-06-30"


def _valid_cnpjs(rows) -> set[str]:
    return {
        str(row[0])
        for row in rows
        if row[0] is not None
        and str(row[0]).isdigit()
        and len(str(row[0])) == 14
    }


def new_q2_organization_cnpjs(repository: RawRepository) -> tuple[str, ...]:
    """Retorna, em ordem estável, os CNPJs do T2 ausentes no recorte T1."""
    q2_rows = repository.connection.execute(
        """
        SELECT DISTINCT json_extract(payload, '$.orgaoEntidade.cnpj')
        FROM raw_asset
        WHERE source = ?
          AND resource_type = 'contratacao'
          AND substr(json_extract(payload, '$.dataPublicacaoPncp'), 1, 10)
              BETWEEN ? AND ?
        """,
        (Q2_CONTRACTING_SOURCE_ID, Q2_START, Q2_END),
    )
    t1_rows = repository.connection.execute(
        """
        SELECT DISTINCT json_extract(payload, '$.orgaoEntidade.cnpj')
        FROM raw_asset
        WHERE source LIKE ?
          AND resource_type = 'contratacao'
        """,
        (T1_SOURCE_PATTERN,),
    )
    return tuple(sorted(_valid_cnpjs(q2_rows) - _valid_cnpjs(t1_rows)))


def run(manifest_path: str, database_url: str, mode: str) -> None:
    repository = RawRepository(database_url)
    try:
        cnpjs = new_q2_organization_cnpjs(repository)
        if not cnpjs:
            raise ValueError(
                "Nenhum novo CNPJ do T2 foi encontrado na camada RAW"
            )

        source = Manifest.load(manifest_path).source(PCA_SOURCE_ID)
        source = replace(
            source,
            targets=tuple({"cnpj": cnpj} for cnpj in cnpjs),
            target_source_id=None,
            target_source_ids=(),
            audit_context={
                **source.audit_context,
                "recorte_orgaos": "novos_orgaos_contratacoes_rj_2023_t2",
                "periodo_publicacao_inicial": Q2_START,
                "periodo_publicacao_final": Q2_END,
            },
        )
        checkpoint = repository.get_checkpoint(RUN_SOURCE_ID, "pca")
        start_page = checkpoint + 1 if mode == "update" else 1
        print(
            json.dumps(
                {
                    "source": RUN_SOURCE_ID,
                    "cnpjs_novos": len(cnpjs),
                    "checkpoint_anterior": checkpoint,
                    "proximo_alvo": start_page,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

        logger = JsonlLogger(
            "logs/pncp-pca-rj-2023-q2-novos-orgaos.jsonl"
        )
        result = ingest_records(
            repository,
            RUN_SOURCE_ID,
            source.key_fields,
            PCAFullCollector(PNCPClient(), logger=logger).collect(
                source, start_page=start_page
            ),
            mode=mode,
            logger=logger,
            resource_type=source.resource_type,
            base_id=source.base_id,
            natureza=source.natureza,
            query_params=dict(source.params),
            sensitivity=source.sensitivity,
            initial_checkpoint=checkpoint if mode == "update" else 0,
            window_start=Q2_START,
            window_end=Q2_END,
        )
        print(json.dumps(result.__dict__, ensure_ascii=False, indent=2))
    finally:
        repository.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Coleta PCA 2023 dos novos órgãos encontrados no T2"
    )
    parser.add_argument("command", choices=("fetch", "update"))
    parser.add_argument("--manifest", default="corpus_manifest.yaml")
    parser.add_argument(
        "--database-url",
        default="sqlite:///./data/debug/pncp_contracts_2023_t1_all.db",
    )
    args = parser.parse_args()
    run(args.manifest, args.database_url, args.command)


if __name__ == "__main__":
    main()
