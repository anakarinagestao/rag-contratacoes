"""Avaliação somente leitura de cobertura e apresentação determinística."""

import sqlite3

from .schemas import DataCoverage, QueryAnalysis, StructuredQueryResult


def avaliar_cobertura(connection: sqlite3.Connection, analysis: QueryAnalysis) -> DataCoverage:
    resource = analysis.entity.value
    resources = [resource]
    if resource == "pca_item":
        resources += ["pca"]
    elif resource in {"contratacao_item", "resultado_item"}:
        resources += ["contratacao"]
        if resource == "resultado_item":
            resources += ["contratacao_item"]
    elif resource == "contrato":
        # Modalidade e vínculos de compras dependem das contratações-pai.
        resources += ["contratacao"]
    tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    details = []
    states = []
    for name in resources:
        count = connection.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0] if name in tables else 0
        metadata = None
        if "normalization_coverage" in tables:
            cursor = connection.execute("SELECT * FROM normalization_coverage WHERE resource_type=?", (name,))
            row = cursor.fetchone()
            if row is not None:
                metadata = dict(zip([c[0] for c in cursor.description], row))
        state = "unknown"
        if metadata:
            expected = metadata.get("expected_parent_count")
            processed = metadata.get("processed_parent_count")
            start, end = metadata.get("period_start"), metadata.get("period_end")
            f = analysis.filters
            query_start = str(f.period_start) if f.period_start else (f"{f.ano}-01-01" if f.ano else None)
            query_end = str(f.period_end) if f.period_end else (f"{f.ano}-12-31" if f.ano else None)
            bounded = bool(start and end and query_start and query_end)
            # Não inferir que period_start/end se referem a publicação,
            # assinatura ou ano da entidade. A prova de universo abaixo
            # vale somente para o inventário declarado, nunca para todo PNCP.
            inventory_complete = (
                metadata.get("coverage_status") == "COMPLETE"
                and expected is not None and expected > 0 and expected == processed
                and bool(metadata.get("inventory_id"))
                and not (query_start or query_end)
            )
            if metadata.get("record_count") != count:
                state = "unknown"
            elif metadata.get("coverage_status") in {"PARTIAL", "KNOWN_GAPS"} or metadata.get("known_gap_count", 0):
                state = "partial"
            elif inventory_complete:
                state = "covered"
            elif count:
                state = "partial"
            else:
                state = "unavailable"
            details.append({"resource": name, "available_records": count, "state": state,
                            "declared": metadata, "bounded_period": bounded,
                            "parent_counts_match": expected is not None and expected == processed})
        else:
            details.append({"resource": name, "available_records": count, "state": state})
        states.append(state)
    if resource == "contrato" and "contrato" in tables:
        missing = connection.execute("SELECT COUNT(*) FROM contrato WHERE parent_resolution_status != 'RESOLVED'").fetchone()[0]
        if missing:
            details.append({"dependency": "contratacao", "unresolved_contracts": missing})
            states.append("partial")
    state = next((s for s in ["unavailable", "unknown", "partial"] if s in states), "covered")
    messages = ["Consulta coberta somente no inventário declarado; resultados não representam todo o PNCP."] if state == "covered" else [
        ("Recurso sem dados disponíveis neste snapshot. " if state == "unavailable" else
         "Cobertura parcial do snapshot local. " if state == "partial" else "Cobertura não comprovada. ")
        + "Valores e contagens se limitam aos dados disponíveis; zero ou ausência de resultados não comprova inexistência no período ou universo consultado."
    ]
    return DataCoverage(state=state, resources=details, messages=messages)


def acrescentar_avisos_cobertura(answer: str, result: StructuredQueryResult | None) -> str:
    if result is None:
        return answer
    messages = [m for m in result.coverage.messages if m not in answer]
    if not messages:
        return answer
    return answer + "\n\nCobertura dos dados:\n" + "\n".join(f"- {m}" for m in messages)
