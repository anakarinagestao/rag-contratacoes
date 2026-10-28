from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Iterator, Mapping

from src.ingestion.checkpoint import normalized_date, stable_target_metadata
from src.ingestion.incremental_http import (
    IncrementalAdapterError,
    PNCPIncrementalHTTPAdapter,
)
from src.ingestion.pca import pca_plan_context, validate_complete_pca_plan
from src.schema import CollectionCheckpoint, CollectedRecord, value_at_path


@dataclass(frozen=True)
class EndpointUpdateSpec:
    resource: str
    endpoint: str
    identity_field: str
    start_param: str = "dataInicial"
    end_param: str = "dataFinal"
    item_path: str | None = "data"
    page_param: str = "pagina"
    page_size_param: str = "tamanhoPagina"
    page_size: int = 50


UPDATE_ENDPOINTS = {
    "contratacao": EndpointUpdateSpec(
        "contratacao",
        "/api/consulta/v1/contratacoes/atualizacao",
        "numeroControlePNCP",
        page_size=50,
    ),
    "contrato": EndpointUpdateSpec(
        "contrato",
        "/api/consulta/v1/contratos/atualizacao",
        "numeroControlePNCP",
        page_size=500,
    ),
    "ata_registro_preco": EndpointUpdateSpec(
        "ata_registro_preco",
        "/api/consulta/v1/atas/atualizacao",
        "numeroControlePNCPAta",
        page_size=500,
    ),
}


@dataclass(frozen=True)
class RelationshipSpec:
    resource: str
    endpoint: str
    identity_field: str
    page_param: str | None = "pagina"
    page_size: int | None = 50
    not_found_as_empty: bool = False


RELATIONSHIP_ENDPOINTS = {
    "contratacao_item": RelationshipSpec(
        "contratacao_item",
        "/api/pncp/v1/orgaos/{cnpj}/compras/{ano}/{sequencial}/itens",
        "numeroItem",
    ),
    "resultado_item": RelationshipSpec(
        "resultado_item",
        (
            "/api/pncp/v1/orgaos/{cnpj}/compras/{ano}/{sequencial}"
            "/itens/{numero_item}/resultados"
        ),
        "sequencialResultado",
        page_param=None,
        page_size=None,
    ),
    "empenho": RelationshipSpec(
        "empenho",
        "/api/pncp/v1/orgaos/{cnpj}/contratos/{ano}/{sequencial}/empenhos",
        "sequencialEmpenho",
        not_found_as_empty=True,
    ),
}


CONTROL_NUMBER_PATTERN = re.compile(
    r"^(?P<cnpj>\d{14})-\d+-(?P<sequencial>\d+)/(?P<ano>\d{4})$"
)


class PNCPEndpointIncrementalAdapter:
    """Adapta feeds globais de atualização sem persistir ou avançar estado."""

    def __init__(self, transport: PNCPIncrementalHTTPAdapter) -> None:
        self.transport = transport

    def collect(
        self,
        resource: str,
        source_id: str,
        window_start: str,
        window_end: str,
        *,
        filters: Mapping[str, Any],
        base_url: str = "https://pncp.gov.br",
        timeout_seconds: float = 30.0,
        max_retries: int = 2,
        retry_backoff_seconds: float = 0.5,
    ) -> Iterator[CollectedRecord | CollectionCheckpoint]:
        try:
            spec = UPDATE_ENDPOINTS[resource]
        except KeyError as error:
            raise ValueError(f"Feed incremental não suportado: {resource}") from error
        start = normalized_date(window_start).replace("-", "")
        end = normalized_date(window_end).replace("-", "")
        if end < start:
            raise ValueError("Janela incremental possui limites invertidos")
        params = {
            **dict(filters),
            spec.start_param: start,
            spec.end_param: end,
            spec.page_size_param: spec.page_size,
        }
        self._validate_filters(resource, params)
        page_number = 1
        ordinal = 0
        expected_total: int | None = None
        seen_page_hashes: set[str] = set()
        while True:
            page = self.transport.fetch_page(
                spec.endpoint,
                params,
                page=page_number,
                page_param=spec.page_param,
                item_path=spec.item_path,
                base_url=base_url,
                timeout_seconds=timeout_seconds,
                max_retries=max_retries,
                retry_backoff_seconds=retry_backoff_seconds,
            )
            if page.outcome == "NOT_FOUND":
                raise IncrementalAdapterError(
                    "HTTP 404 não representa vazio em feed global de atualização"
                )
            if page.total_pages is not None:
                if expected_total is None:
                    expected_total = page.total_pages
                elif page.total_pages != expected_total:
                    raise IncrementalAdapterError(
                        "Total de páginas mudou durante a janela incremental"
                    )
            if not page.records and expected_total is not None and page_number < expected_total:
                raise IncrementalAdapterError(
                    "Página intermediária vazia no feed incremental"
                )
            page_hash = self._page_hash(page.records)
            if page.records and page_hash in seen_page_hashes:
                raise IncrementalAdapterError(
                    "Feed incremental repetiu uma página inteira"
                )
            seen_page_hashes.add(page_hash)
            for payload in page.records:
                external_id = value_at_path(payload, spec.identity_field)
                if external_id in (None, ""):
                    raise IncrementalAdapterError(
                        f"{resource} sem identidade {spec.identity_field}"
                    )
                self._validate_scope(resource, payload, params)
                ordinal += 1
                yield CollectedRecord(
                    source_id,
                    payload,
                    page.source_url,
                    str(page_number),
                    ordinal,
                    external_id=str(external_id),
                    query_params={**params, spec.page_param: page_number},
                )
            yield CollectionCheckpoint(str(page_number))
            if self._is_last(page_number, page.total_pages, page.records, spec.page_size):
                return
            page_number += 1

    @staticmethod
    def _validate_filters(resource: str, params: Mapping[str, Any]) -> None:
        if resource == "contratacao":
            if params.get("codigoModalidadeContratacao") is None:
                raise ValueError(
                    "Feed de contratações requer codigoModalidadeContratacao"
                )
            if not str(params.get("uf") or "").strip():
                raise ValueError("Feed de contratações requer filtro uf")
        elif resource in {"contrato", "ata_registro_preco"}:
            if not str(params.get("cnpjOrgao") or params.get("cnpj") or "").strip():
                raise ValueError(f"Feed de {resource} requer CNPJ do inventário")

    @staticmethod
    def _validate_scope(
        resource: str,
        payload: Mapping[str, Any],
        params: Mapping[str, Any],
    ) -> None:
        expected_uf = str(params.get("uf") or "").upper()
        actual_uf = value_at_path(payload, "unidadeOrgao.ufSigla")
        if expected_uf and str(actual_uf or "").upper() != expected_uf:
            raise IncrementalAdapterError(
                f"{resource} fora da UF esperada: {actual_uf!r}"
            )
        expected_cnpj = str(
            params.get("cnpjOrgao") or params.get("cnpj") or ""
        )
        actual_cnpj = (
            payload.get("cnpjOrgao")
            if resource == "ata_registro_preco"
            else value_at_path(payload, "orgaoEntidade.cnpj")
        )
        if expected_cnpj and str(actual_cnpj or "") != expected_cnpj:
            raise IncrementalAdapterError(
                f"{resource} fora do CNPJ esperado"
            )
        expected_unit = params.get("codigoUnidadeAdministrativa")
        actual_unit = (
            payload.get("codigoUnidadeOrgao")
            if resource == "ata_registro_preco"
            else value_at_path(payload, "unidadeOrgao.codigoUnidade")
        )
        if (
            expected_unit is not None
            and not PNCPEndpointIncrementalAdapter._same_code(
                actual_unit, expected_unit
            )
        ):
            raise IncrementalAdapterError(
                f"{resource} fora da unidade esperada"
            )

    @staticmethod
    def _same_code(actual: Any, expected: Any) -> bool:
        actual_text = str(actual or "").strip()
        expected_text = str(expected or "").strip()
        if actual_text == expected_text:
            return True
        if actual_text.isdigit() and expected_text.isdigit():
            return int(actual_text) == int(expected_text)
        return False

    @staticmethod
    def _is_last(
        page: int,
        total_pages: int | None,
        records: tuple[Mapping[str, Any], ...],
        page_size: int,
    ) -> bool:
        if total_pages is not None:
            return page >= total_pages
        return len(records) < page_size

    @staticmethod
    def _page_hash(records: tuple[Mapping[str, Any], ...]) -> str:
        serialized = json.dumps(
            records,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


class PNCPPCAIncrementalAdapter:
    """Descobre PCA alterado e emite somente o plano integral validado."""

    DISCOVERY_ENDPOINT = "/api/consulta/v1/pca/atualizacao"
    DETAIL_ENDPOINT = (
        "/api/pncp/v1/orgaos/{cnpj}/pca/{ano}/{sequencial}/itens/plano"
    )
    PAGE_SIZE = 500

    def __init__(self, transport: PNCPIncrementalHTTPAdapter) -> None:
        self.transport = transport

    def collect(
        self,
        source_id: str,
        window_start: str,
        window_end: str,
        *,
        filters: Mapping[str, Any],
        base_url: str = "https://pncp.gov.br",
        timeout_seconds: float = 30.0,
        max_retries: int = 2,
        retry_backoff_seconds: float = 0.5,
    ) -> Iterator[CollectedRecord | CollectionCheckpoint]:
        start = normalized_date(window_start).replace("-", "")
        end = normalized_date(window_end).replace("-", "")
        if end < start:
            raise ValueError("Janela incremental possui limites invertidos")
        params = {
            **dict(filters),
            "dataInicio": start,
            "dataFim": end,
            "tamanhoPagina": self.PAGE_SIZE,
        }
        expected_cnpj = str(params.get("cnpj") or "").strip()
        if not expected_cnpj:
            raise ValueError("Feed incremental de PCA requer CNPJ do inventário")
        expected_unit = str(params.get("codigoUnidade") or "").strip()
        if not expected_unit:
            raise ValueError(
                "Feed incremental de PCA requer unidade do inventário"
            )

        discovered: dict[str, dict[str, Any]] = {}
        page_number = 1
        expected_total: int | None = None
        seen_page_hashes: set[str] = set()
        while True:
            page = self.transport.fetch_page(
                self.DISCOVERY_ENDPOINT,
                params,
                page=page_number,
                item_path="data",
                base_url=base_url,
                timeout_seconds=timeout_seconds,
                max_retries=max_retries,
                retry_backoff_seconds=retry_backoff_seconds,
            )
            if page.outcome == "NOT_FOUND":
                raise IncrementalAdapterError(
                    "HTTP 404 não representa vazio no feed global de PCA"
                )
            if page.total_pages is not None:
                if expected_total is None:
                    expected_total = page.total_pages
                elif page.total_pages != expected_total:
                    raise IncrementalAdapterError(
                        "Total de páginas do PCA mudou durante a janela"
                    )
            if not page.records and expected_total is not None and page_number < expected_total:
                raise IncrementalAdapterError(
                    "Página intermediária vazia no feed de PCA"
                )
            page_hash = PNCPEndpointIncrementalAdapter._page_hash(page.records)
            if page.records and page_hash in seen_page_hashes:
                raise IncrementalAdapterError(
                    "Feed incremental de PCA repetiu uma página inteira"
                )
            seen_page_hashes.add(page_hash)
            for fragment in page.records:
                external_id = fragment.get("idPcaPncp")
                if not external_id:
                    raise IncrementalAdapterError(
                        "Fragmento de PCA sem idPcaPncp"
                    )
                try:
                    context = pca_plan_context(
                        external_id, expected_cnpj=expected_cnpj
                    )
                except ValueError as error:
                    raise IncrementalAdapterError(str(error)) from error
                entry = discovered.setdefault(
                    str(external_id),
                    {"context": context, "pages": [], "fragments": 0},
                )
                if page_number not in entry["pages"]:
                    entry["pages"].append(page_number)
                entry["fragments"] += 1
            if PNCPEndpointIncrementalAdapter._is_last(
                page_number,
                page.total_pages,
                page.records,
                self.PAGE_SIZE,
            ):
                break
            page_number += 1

        # Todos os detalhes são buscados e validados antes da primeira emissão.
        # Uma falha não deixa a janela com um subconjunto de planos observados.
        plans: list[tuple[str, dict[str, Any], str, Mapping[str, Any]]] = []
        for external_id, entry in discovered.items():
            endpoint = self.DETAIL_ENDPOINT.format(**entry["context"])
            document = self.transport.fetch_document(
                endpoint,
                base_url=base_url,
                timeout_seconds=timeout_seconds,
                max_retries=max_retries,
                retry_backoff_seconds=retry_backoff_seconds,
            )
            if document.outcome != "FOUND" or document.payload is None:
                raise IncrementalAdapterError(
                    f"PCA descoberto sem plano integral: {external_id}"
                )
            try:
                validate_complete_pca_plan(
                    document.payload,
                    entry["context"],
                    document.source_url,
                    expected_unit=expected_unit,
                )
            except ValueError as error:
                raise IncrementalAdapterError(str(error)) from error
            plans.append(
                (external_id, entry, document.source_url, document.payload)
            )

        for ordinal, (external_id, entry, source_url, payload) in enumerate(
            plans, start=1
        ):
            yield CollectedRecord(
                source_id,
                payload,
                source_url,
                str(entry["pages"][-1]),
                ordinal,
                external_id=external_id,
                query_params={
                    **params,
                    "numeroControlePNCP": external_id,
                    "discovery_pages": list(entry["pages"]),
                    "discovery_fragment_count": entry["fragments"],
                    "representation": "complete_pca_plan",
                },
            )
        yield CollectionCheckpoint(str(page_number))


class PNCPRelationshipIncrementalAdapter:
    """Consulta um alvo pai e emite filhos validados para reconciliação."""

    def __init__(self, transport: PNCPIncrementalHTTPAdapter) -> None:
        self.transport = transport

    def collect_target(
        self,
        resource: str,
        source_id: str,
        target: Mapping[str, Any],
        *,
        base_url: str = "https://pncp.gov.br",
        timeout_seconds: float = 30.0,
        max_retries: int = 2,
        retry_backoff_seconds: float = 0.5,
    ) -> Iterator[CollectedRecord | CollectionCheckpoint]:
        try:
            spec = RELATIONSHIP_ENDPOINTS[resource]
        except KeyError as error:
            raise ValueError(
                f"Relacionamento incremental não suportado: {resource}"
            ) from error
        context = self._target_context(resource, target)
        endpoint = spec.endpoint.format(**context)
        target_key, fingerprint = stable_target_metadata(target)
        records: list[tuple[Mapping[str, Any], str, str, int]] = []
        seen_ids: set[str] = set()
        seen_page_hashes: set[str] = set()
        expected_total: int | None = None
        page_number = 1
        empty_outcome = "EMPTY"
        while True:
            params = (
                {"tamanhoPagina": spec.page_size}
                if spec.page_size is not None
                else {}
            )
            page = self.transport.fetch_page(
                endpoint,
                params,
                page=page_number,
                page_param=spec.page_param,
                item_path=None,
                base_url=base_url,
                timeout_seconds=timeout_seconds,
                max_retries=max_retries,
                retry_backoff_seconds=retry_backoff_seconds,
                not_found_as_empty=spec.not_found_as_empty,
            )
            if page.outcome == "NOT_FOUND":
                if not spec.not_found_as_empty:
                    raise IncrementalAdapterError(
                        f"HTTP 404 inesperado para {resource}"
                    )
                empty_outcome = "NOT_FOUND"
            if page.total_pages is not None:
                if expected_total is None:
                    expected_total = page.total_pages
                elif page.total_pages != expected_total:
                    raise IncrementalAdapterError(
                        f"Total de páginas mudou durante {resource}"
                    )
            if (
                not page.records
                and expected_total is not None
                and page_number < expected_total
            ):
                raise IncrementalAdapterError(
                    f"Página intermediária vazia durante {resource}"
                )
            page_hash = PNCPEndpointIncrementalAdapter._page_hash(page.records)
            if page.records and page_hash in seen_page_hashes:
                raise IncrementalAdapterError(
                    f"{resource} repetiu uma página inteira"
                )
            seen_page_hashes.add(page_hash)
            for payload in page.records:
                external_id = self._external_id(resource, payload, context)
                if external_id in seen_ids:
                    raise IncrementalAdapterError(
                        f"{resource} repetiu a identidade {external_id}"
                    )
                seen_ids.add(external_id)
                records.append(
                    (payload, external_id, page.source_url, page_number)
                )
            if spec.page_param is None or (
                spec.page_size is not None
                and PNCPEndpointIncrementalAdapter._is_last(
                    page_number,
                    page.total_pages,
                    page.records,
                    spec.page_size,
                )
            ):
                break
            page_number += 1

        for ordinal, (payload, external_id, source_url, page) in enumerate(
            records, start=1
        ):
            yield CollectedRecord(
                source_id,
                payload,
                source_url,
                str(page),
                ordinal,
                external_id=external_id,
                query_params={
                    "numeroControlePNCP": context["numero_controle"],
                    "numeroItem": context.get("numero_item"),
                    "target_key": target_key,
                    **(
                        {
                            str(spec.page_param): page,
                            "tamanhoPagina": spec.page_size,
                        }
                        if spec.page_param is not None
                        else {}
                    ),
                },
            )
        yield CollectionCheckpoint(
            target_key,
            target_key,
            fingerprint,
            reconciliation_outcome=("FOUND" if records else empty_outcome),
        )

    @staticmethod
    def _target_context(
        resource: str, target: Mapping[str, Any]
    ) -> dict[str, Any]:
        numero_controle = str(target.get("numero_controle") or "")
        match = CONTROL_NUMBER_PATTERN.fullmatch(numero_controle)
        if not match:
            raise ValueError(f"Alvo de {resource} possui controle PNCP inválido")
        if resource == "empenho" and "-2-" not in numero_controle:
            raise ValueError("Alvo de empenho não representa contrato PNCP")
        context: dict[str, Any] = {
            "cnpj": match.group("cnpj"),
            "ano": int(match.group("ano")),
            "sequencial": int(match.group("sequencial")),
            "numero_controle": numero_controle,
        }
        if resource == "resultado_item":
            try:
                context["numero_item"] = int(target["numero_item"])
            except (KeyError, TypeError, ValueError):
                raise ValueError(
                    "Alvo de resultado_item sem numero_item válido"
                ) from None
        return context

    @staticmethod
    def _external_id(
        resource: str,
        payload: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> str:
        if resource == "contratacao_item":
            try:
                numero_item = int(payload["numeroItem"])
            except (KeyError, TypeError, ValueError):
                raise IncrementalAdapterError(
                    "Item sem numeroItem válido"
                ) from None
            return f"{context['numero_controle']}|{numero_item}"
        if resource == "resultado_item":
            required = (
                "numeroControlePNCPCompra",
                "numeroItem",
                "sequencialResultado",
            )
            if any(payload.get(field) is None for field in required):
                raise IncrementalAdapterError(
                    "Resultado sem identidade completa"
                )
            try:
                numero_item = int(payload["numeroItem"])
                sequencial_resultado = int(payload["sequencialResultado"])
            except (TypeError, ValueError):
                raise IncrementalAdapterError(
                    "Resultado sem identidade numérica válida"
                ) from None
            if (
                str(payload["numeroControlePNCPCompra"])
                != context["numero_controle"]
                or numero_item != context["numero_item"]
            ):
                raise IncrementalAdapterError(
                    "Resultado fora do item alvo"
                )
            return (
                f"{context['numero_controle']}|{context['numero_item']}|"
                f"{sequencial_resultado}"
            )
        required = (
            "cnpj",
            "ano",
            "sequencialContrato",
            "sequencialEmpenho",
        )
        if any(payload.get(field) is None for field in required):
            raise IncrementalAdapterError("Empenho sem identidade completa")
        try:
            ano = int(payload["ano"])
            sequencial_contrato = int(payload["sequencialContrato"])
            sequencial_empenho = int(payload["sequencialEmpenho"])
        except (TypeError, ValueError):
            raise IncrementalAdapterError(
                "Empenho sem identidade numérica válida"
            ) from None
        if (
            str(payload["cnpj"]) != context["cnpj"]
            or ano != context["ano"]
            or sequencial_contrato != context["sequencial"]
        ):
            raise IncrementalAdapterError("Empenho fora do contrato alvo")
        return (
            f"{context['numero_controle']}|"
            f"{sequencial_empenho}"
        )


__all__ = [
    "CONTROL_NUMBER_PATTERN",
    "EndpointUpdateSpec",
    "PNCPEndpointIncrementalAdapter",
    "PNCPPCAIncrementalAdapter",
    "PNCPRelationshipIncrementalAdapter",
    "RELATIONSHIP_ENDPOINTS",
    "RelationshipSpec",
    "UPDATE_ENDPOINTS",
]
