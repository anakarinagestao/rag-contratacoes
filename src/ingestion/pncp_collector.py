from __future__ import annotations

from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date, timedelta
import re
import threading
import time
from typing import Any, Callable, Iterable, Mapping, TypeVar

from ..obs import JsonlLogger
from ..schema import (
    CollectionCheckpoint,
    CollectedRecord,
    SourceConfig,
    records_from_response,
    value_at_path,
)
from .checkpoint import stable_target_metadata
from .pca import pca_plan_context, validate_complete_pca_plan
from .pncp_client import PNCPClient, PNCPClientError


class PNCPPaginationError(RuntimeError):
    """Envelope paginado inconsistente, sem cobertura segura comprovável."""


class _PaginationGuard:
    def __init__(
        self,
        source_id: str,
        logger: JsonlLogger,
        **log_context: Any,
    ) -> None:
        self.source_id = source_id
        self.logger = logger
        self.log_context = log_context
        self.expected_total_pages: int | None = None

    def inspect(
        self,
        response: Any,
        page: int,
        record_count: int,
    ) -> str:
        total_pages = self._envelope_integer(
            response, "totalPaginas", page=page, minimum=0
        )
        response_page = self._envelope_integer(
            response, "numeroPagina", page=page, minimum=0
        )
        if response_page is not None and response_page != page:
            self._fail(
                "unexpected_page_number",
                page,
                reported_page=response_page,
                total_pages=total_pages,
            )
        if total_pages is not None:
            if self.expected_total_pages is None:
                self.expected_total_pages = total_pages
            elif total_pages != self.expected_total_pages:
                self._fail(
                    "total_pages_changed",
                    page,
                    expected_total_pages=self.expected_total_pages,
                    reported_total_pages=total_pages,
                )
            if total_pages == 0:
                if page != 1 or record_count:
                    self._fail(
                        "invalid_zero_total",
                        page,
                        record_count=record_count,
                    )
                return "last"
            if page > total_pages:
                self._fail(
                    "page_exceeds_total",
                    page,
                    total_pages=total_pages,
                )
            if not record_count and page < total_pages:
                return "empty_intermediate"
            if page >= total_pages:
                return "last"
        return "last" if not record_count else "continue"

    def _envelope_integer(
        self,
        response: Any,
        field: str,
        *,
        page: int,
        minimum: int,
    ) -> int | None:
        if not isinstance(response, Mapping) or field not in response:
            return None
        value = response[field]
        if isinstance(value, bool):
            self._fail(
                "invalid_envelope_field", page, field=field, value=value
            )
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            self._fail(
                "invalid_envelope_field", page, field=field, value=value
            )
        if parsed < minimum:
            self._fail(
                "invalid_envelope_field", page, field=field, value=value
            )
        return parsed

    def _fail(self, reason: str, page: int, **fields: Any) -> None:
        self.logger.event(
            "pncp_pagination_anomaly",
            source_id=self.source_id,
            page=page,
            reason=reason,
            **self.log_context,
            **fields,
        )
        raise PNCPPaginationError(
            f"Paginação PNCP inconsistente em {self.source_id}: "
            f"{reason} (página {page})"
        )


def _fetch_validated_page(
    request: Callable[[], tuple[Any, Any]],
    source: SourceConfig,
    page: int,
    guard: _PaginationGuard,
) -> tuple[Any, list[Mapping[str, Any]], Any, int, str]:
    """Busca uma página e nunca aceita vazio intermediário silencioso."""

    attempts = 0
    empty_retries = 0
    while True:
        response, context = request()
        attempts += 1
        records = (
            []
            if response == []
            else records_from_response(response, source.item_path)
        )
        outcome = guard.inspect(response, page, len(records))
        if outcome != "empty_intermediate":
            return response, records, context, attempts, outcome

        retrying = empty_retries < source.max_retries
        guard.logger.event(
            "pncp_pagination_empty_intermediate",
            source_id=source.id,
            page=page,
            total_pages=guard.expected_total_pages,
            attempt=empty_retries + 1,
            retrying=retrying,
            **guard.log_context,
        )
        if not retrying:
            raise PNCPPaginationError(
                "Página intermediária vazia persistente no PNCP: "
                f"fonte={source.id}, página={page}, "
                f"total={guard.expected_total_pages}"
            )
        delay = source.retry_backoff_seconds * (2**empty_retries)
        if delay:
            time.sleep(delay)
        empty_retries += 1


def _target_checkpoint(
    cursor: int | str,
    target: Mapping[str, Any],
    *,
    qualifier: str | None = None,
    target_key: str | None = None,
) -> CollectionCheckpoint:
    """Anexa ao cursor legado a identidade estável usada pelo checkpoint v2."""

    derived_key, fingerprint = stable_target_metadata(target, qualifier)
    return CollectionCheckpoint(
        str(cursor), target_key or derived_key, fingerprint
    )


class PNCPCollector:
    record_empty_pages = False

    def __init__(
        self, client: PNCPClient, logger: JsonlLogger | None = None
    ) -> None:
        self.client = client
        self.logger = logger or JsonlLogger()

    def collect(self, source: SourceConfig, start_page: int = 1):
        ordinal = 0
        page = start_page
        guard = _PaginationGuard(source.id, self.logger)
        while source.max_pages is None or page < start_page + source.max_pages:
            response, records, url, _, outcome = _fetch_validated_page(
                lambda: self.client.fetch_page(source, page),
                source,
                page,
                guard,
            )
            query_params = {
                **source.audit_context,
                **source.params,
                source.page_param: page,
                source.page_size_param: source.page_size,
            }
            self.logger.event(
                "pncp_page",
                source_id=source.id,
                resource_type=source.resource_type,
                page=page,
                count=len(records),
                **_response_envelope_summary(response),
            )
            if not records:
                if self.record_empty_pages:
                    yield CollectionCheckpoint(str(page))
                break
            for payload in records:
                ordinal += 1
                yield CollectedRecord(
                    source.id,
                    payload,
                    url,
                    str(page),
                    ordinal,
                    query_params=query_params,
                )
            if outcome == "last":
                break
            page += 1
            if source.rate_limit_seconds:
                time.sleep(source.rate_limit_seconds)


class PCACollector(PNCPCollector):
    """Usa o feed fragmentado para descobrir e buscar cada PCA completo."""

    external_id_field = "idPcaPncp"

    def collect(self, source: SourceConfig, start_page: int = 1):
        if not source.detail_endpoint_template:
            raise ValueError(
                "Fonte PCA por atualização sem detail_endpoint_template"
            )
        discovered: dict[str, dict[str, Any]] = {}
        for record in super().collect(source, start_page):
            if isinstance(record, CollectionCheckpoint):
                continue
            external_id = record.payload.get(self.external_id_field)
            if not external_id:
                raise ValueError(
                    "Registro PCA sem idPcaPncp; coleta interrompida"
                )
            external_id = str(external_id)
            context = pca_plan_context(external_id)
            entry = discovered.setdefault(
                external_id,
                {
                    "context": context,
                    "pages": [],
                    "fragment_count": 0,
                },
            )
            if record.cursor not in entry["pages"]:
                entry["pages"].append(record.cursor)
            entry["fragment_count"] += 1

        # Busca e valida todos os planos antes de emitir o primeiro registro.
        # Assim, uma falha no detalhe não deixa um checkpoint parcial.
        plans = []
        for external_id, entry in discovered.items():
            context = entry["context"]
            endpoint = source.detail_endpoint_template.format(**context)
            url = self.client.build_url(endpoint, base_url=source.base)
            payload = self.client.get(
                endpoint,
                base_url=source.base,
                timeout_seconds=source.timeout_seconds,
                headers=source.headers,
                max_retries=source.max_retries,
                retry_backoff_seconds=source.retry_backoff_seconds,
            )
            validate_complete_pca_plan(payload, context, url)
            plans.append((external_id, entry, url, payload))

        for ordinal, (external_id, entry, url, payload) in enumerate(
            plans, start=1
        ):
            pages = entry["pages"]
            yield CollectedRecord(
                source.id,
                payload,
                url,
                pages[-1],
                ordinal,
                external_id=external_id,
                query_params={
                    **source.audit_context,
                    **source.params,
                    "numeroControlePNCP": external_id,
                    "discovery_pages": list(pages),
                    "discovery_fragment_count": entry["fragment_count"],
                    "representation": "complete_pca_plan",
                },
            )
        self.logger.event(
            "pncp_pca_update_discovery",
            source_id=source.id,
            fragments_count=sum(
                entry["fragment_count"] for entry in discovered.values()
            ),
            plans_count=len(plans),
        )


class PCAFullCollector:
    """Descobre PCAs por órgão e preserva cada plano completo com seus itens."""

    required_target_fields = ("cnpj",)

    def __init__(
        self, client: PNCPClient, logger: JsonlLogger | None = None
    ) -> None:
        self.client = client
        self.logger = logger or JsonlLogger()

    def collect(self, source: SourceConfig, start_page: int = 1):
        if not source.targets:
            raise ValueError("Fonte PCA sem órgãos RAW configurados como targets")
        if not source.discovery_endpoint_template:
            raise ValueError("Fonte PCA sem endpoint de descoberta")
        try:
            ano = int(source.params["anoPca"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("Fonte PCA sem anoPca válido") from error

        targets = list(
            enumerate(source.targets[start_page - 1 :], start=start_page)
        )
        if source.max_pages is not None:
            targets = targets[: source.max_pages]
        pacer = _RequestPacer(source.rate_limit_seconds)

        def request(endpoint: str, not_found_as_empty: bool = False):
            url = self.client.build_url(endpoint, base_url=source.base)
            pacer.wait()
            payload = self.client.get(
                endpoint,
                base_url=source.base,
                timeout_seconds=source.timeout_seconds,
                headers=source.headers,
                max_retries=source.max_retries,
                retry_backoff_seconds=source.retry_backoff_seconds,
                not_found_as_empty=not_found_as_empty,
            )
            return payload, url

        def fetch(indexed_target):
            target_index, target = indexed_target
            cnpj = self._target_cnpj(target)
            discovery_endpoint = source.discovery_endpoint_template.format(
                cnpj=cnpj, ano=ano
            )
            discovery, _ = request(
                discovery_endpoint, not_found_as_empty=True
            )
            summaries = records_from_response(discovery)
            plans = []
            seen = set()
            for summary in summaries:
                sequencial = summary.get("sequencialPca")
                numero_controle = summary.get("numeroControlePNCP")
                context = self._plan_context(
                    cnpj, ano, sequencial, numero_controle
                )
                if numero_controle in seen:
                    continue
                seen.add(numero_controle)
                endpoint = source.endpoint.format(**context)
                payload, url = request(endpoint)
                validate_complete_pca_plan(payload, context, url)
                plans.append((context, url, payload))
            return target_index, cnpj, plans

        ordinal = 0
        for target_index, cnpj, plans in _ordered_parallel_map(
            fetch, targets, source.max_concurrency
        ):
            for context, source_url, payload in plans:
                ordinal += 1
                yield CollectedRecord(
                    source.id,
                    payload,
                    source_url,
                    str(target_index),
                    ordinal,
                    external_id=context["numero_controle"],
                    query_params={
                        **source.audit_context,
                        "anoPca": ano,
                        "cnpj": cnpj,
                        "sequencialPca": context["sequencial"],
                        "numeroControlePNCP": context["numero_controle"],
                        "target_index": target_index,
                    },
                )
            self.logger.event(
                "pncp_pca_plans",
                source_id=source.id,
                target_index=target_index,
                cnpj=cnpj,
                ano_pca=ano,
                plans_count=len(plans),
            )
            yield _target_checkpoint(target_index, {"cnpj": cnpj})

    def _target_cnpj(self, target) -> str:
        missing = [
            field
            for field in self.required_target_fields
            if target.get(field) in (None, "")
        ]
        if missing:
            raise ValueError("Target PCA incompleto: " + ", ".join(missing))
        cnpj = str(target["cnpj"])
        if not cnpj.isdigit() or len(cnpj) != 14:
            raise ValueError("Target PCA com CNPJ inválido")
        return cnpj

    def _plan_context(self, cnpj, ano, sequencial, numero_controle):
        if sequencial is None or not numero_controle:
            raise ValueError("Resumo de PCA sem identificador obrigatório")
        return pca_plan_context(
            numero_controle,
            expected_cnpj=cnpj,
            expected_year=ano,
            expected_sequence=sequencial,
        )


class PriceRegistryCollector:
    """Coleta atas vigentes por unidade do RJ em janelas anuais."""

    required_target_fields = ("cnpj", "codigo_unidade", "uf")

    def __init__(
        self, client: PNCPClient, logger: JsonlLogger | None = None
    ) -> None:
        self.client = client
        self.logger = logger or JsonlLogger()

    def collect(self, source: SourceConfig, start_page: int = 1):
        if not source.targets:
            raise ValueError("Fonte de atas sem unidades RAW configuradas")
        expected_uf = str(source.audit_context.get("uf") or "")
        if not expected_uf:
            raise ValueError("Fonte de atas sem UF de auditoria")
        windows = _annual_date_windows(
            source.params.get("dataInicial"),
            source.params.get("dataFinal"),
        )
        work_items = [
            (target, window_index, window_start, window_end)
            for target in source.targets
            for window_index, (window_start, window_end) in enumerate(
                windows, start=1
            )
        ]
        indexed_items = list(
            enumerate(work_items[start_page - 1 :], start=start_page)
        )
        if source.max_pages is not None:
            indexed_items = indexed_items[: source.max_pages]
        pacer = _RequestPacer(source.rate_limit_seconds)

        def fetch(indexed_item):
            work_index, item = indexed_item
            target, window_index, window_start, window_end = item
            context = self._target_context(target, expected_uf)
            page = 1
            found = []
            pages_requested = 0
            guard = _PaginationGuard(
                source.id,
                self.logger,
                work_index=work_index,
                cnpj=context["cnpj"],
                codigo_unidade=context["codigo_unidade"],
            )
            while True:
                params = {
                    "dataInicial": window_start,
                    "dataFinal": window_end,
                    "cnpj": context["cnpj"],
                    "codigoUnidadeAdministrativa": context[
                        "codigo_unidade"
                    ],
                    source.page_param: page,
                    source.page_size_param: source.page_size,
                }
                url = self.client.build_url(
                    source.endpoint, params, base_url=source.base
                )

                def request():
                    pacer.wait()
                    return (
                        self.client.get(
                            source.endpoint,
                            params,
                            base_url=source.base,
                            timeout_seconds=source.timeout_seconds,
                            headers=source.headers,
                            max_retries=source.max_retries,
                            retry_backoff_seconds=source.retry_backoff_seconds,
                        ),
                        None,
                    )

                response, records, _, attempts, outcome = (
                    _fetch_validated_page(
                        request, source, page, guard
                    )
                )
                pages_requested += attempts
                for payload in records:
                    external_id = self._validate_ata(payload, context, url)
                    if external_id is not None:
                        found.append((payload, external_id, url, params))
                if outcome == "last":
                    break
                page += 1
            return (
                work_index,
                context,
                window_index,
                window_start,
                window_end,
                found,
                pages_requested,
            )

        ordinal = 0
        for (
            work_index,
            context,
            window_index,
            window_start,
            window_end,
            records,
            pages_requested,
        ) in _ordered_parallel_map(
            fetch, indexed_items, source.max_concurrency
        ):
            for payload, external_id, url, params in records:
                ordinal += 1
                yield CollectedRecord(
                    source.id,
                    payload,
                    url,
                    str(work_index),
                    ordinal,
                    external_id=external_id,
                    query_params={
                        **source.audit_context,
                        **params,
                        "window_index": window_index,
                        "target_index": (work_index - 1) // len(windows) + 1,
                    },
                )
            self.logger.event(
                "pncp_price_registry",
                source_id=source.id,
                work_index=work_index,
                cnpj=context["cnpj"],
                codigo_unidade=context["codigo_unidade"],
                uf=context["uf"],
                window_index=window_index,
                window_start=window_start,
                window_end=window_end,
                pages_requested=pages_requested,
                records_count=len(records),
            )
            yield _target_checkpoint(
                work_index,
                context,
                qualifier=f"{window_start}:{window_end}",
            )

    def _target_context(self, target, expected_uf):
        missing = [
            field
            for field in self.required_target_fields
            if target.get(field) in (None, "")
        ]
        if missing:
            raise ValueError("Target de ata incompleto: " + ", ".join(missing))
        cnpj = str(target["cnpj"])
        if not cnpj.isdigit() or len(cnpj) != 14:
            raise ValueError("Target de ata com CNPJ inválido")
        actual_uf = str(target["uf"])
        if actual_uf != expected_uf:
            raise ValueError(
                f"Unidade fora do recorte: esperado={expected_uf}, "
                f"recebido={actual_uf}"
            )
        return {
            "cnpj": cnpj,
            "codigo_unidade": str(target["codigo_unidade"]),
            "uf": actual_uf,
        }

    def _validate_ata(self, payload, context, source_url):
        external_id = payload.get("numeroControlePNCPAta")
        actual_cnpj = payload.get("cnpjOrgao")
        actual_unit = payload.get("codigoUnidadeOrgao")
        if not external_id:
            raise ValueError(f"Ata sem número de controle em {source_url}")
        if (
            str(actual_cnpj) != context["cnpj"]
            or not str(external_id).startswith(context["cnpj"] + "-")
        ):
            raise ValueError(f"Ata fora do órgão alvo em {source_url}")
        if str(actual_unit) != context["codigo_unidade"]:
            self.logger.event(
                "pncp_ata_scope_mismatch",
                external_id=str(external_id),
                expected_unit=context["codigo_unidade"],
                actual_unit=str(actual_unit),
                action="skipped_for_current_target",
            )
            return None
        return str(external_id)


class PublishedContractCollector:
    """Coleta contratos publicados por órgão/unidade do recorte estadual."""

    required_target_fields = ("cnpj", "codigo_unidade", "uf")
    control_number_pattern = re.compile(
        r"^(?P<cnpj>\d{14})-2-(?P<sequencial>\d+)/(?P<ano>\d{4})$"
    )

    def __init__(
        self, client: PNCPClient, logger: JsonlLogger | None = None
    ) -> None:
        self.client = client
        self.logger = logger or JsonlLogger()

    def collect(self, source: SourceConfig, start_page: int = 1):
        if not source.targets:
            raise ValueError("Fonte contrato sem unidades RAW configuradas")
        expected_uf = source.audit_context.get("uf")
        if not expected_uf:
            raise ValueError("Fonte contrato sem UF de auditoria")

        targets = list(
            enumerate(source.targets[start_page - 1 :], start=start_page)
        )
        if source.max_pages is not None:
            targets = targets[: source.max_pages]
        pacer = _RequestPacer(source.rate_limit_seconds)

        def fetch(indexed_target):
            target_index, target = indexed_target
            context = self._target_context(target, str(expected_uf))
            page = 1
            found = []
            pages_requested = 0
            guard = _PaginationGuard(
                source.id,
                self.logger,
                target_index=target_index,
                cnpj=context["cnpj"],
                codigo_unidade=context["codigo_unidade"],
            )
            while True:
                query_unit = self._query_unit_code(
                    context["codigo_unidade"]
                )
                params = {
                    **source.params,
                    "cnpjOrgao": context["cnpj"],
                    "codigoUnidadeAdministrativa": query_unit,
                    source.page_param: page,
                    source.page_size_param: source.page_size,
                }
                url = self.client.build_url(
                    source.endpoint, params, base_url=source.base
                )

                def request():
                    pacer.wait()
                    try:
                        response = self._request(source, params)
                        request_url = url
                        record_params = {
                            **params,
                            "codigoUnidadeAdministrativaOriginal": context[
                                "codigo_unidade"
                            ],
                            "consultaSemFiltroUnidade": False,
                        }
                    except PNCPClientError as error:
                        if error.status_code != 422:
                            raise
                        fallback_params = dict(params)
                        fallback_params.pop("codigoUnidadeAdministrativa")
                        request_url = self.client.build_url(
                            source.endpoint,
                            fallback_params,
                            base_url=source.base,
                        )
                        self.logger.event(
                            "pncp_contract_unit_fallback",
                            source_id=source.id,
                            target_index=target_index,
                            cnpj=context["cnpj"],
                            codigo_unidade=context["codigo_unidade"],
                            codigo_unidade_consulta=query_unit,
                            page=page,
                            status_original=422,
                            action="retry_without_unit_filter",
                        )
                        pacer.wait()
                        response = self._request(source, fallback_params)
                        record_params = {
                            **fallback_params,
                            "codigoUnidadeAdministrativaOriginal": context[
                                "codigo_unidade"
                            ],
                            "consultaSemFiltroUnidade": True,
                        }
                    return response, (request_url, record_params)

                response, records, request_context, attempts, outcome = (
                    _fetch_validated_page(
                        request, source, page, guard
                    )
                )
                pages_requested += attempts
                url, record_params = request_context
                for payload in records:
                    external_id = self._validate_contract(
                        payload, context, url
                    )
                    if external_id is not None:
                        found.append(
                            (payload, external_id, url, record_params)
                        )
                if outcome == "last":
                    break
                page += 1
            return target_index, context, found, pages_requested

        ordinal = 0
        for target_index, context, contracts, pages_requested in (
            _ordered_parallel_map(fetch, targets, source.max_concurrency)
        ):
            for payload, external_id, url, params in contracts:
                ordinal += 1
                yield CollectedRecord(
                    source.id,
                    payload,
                    url,
                    str(target_index),
                    ordinal,
                    external_id=external_id,
                    query_params={
                        **source.audit_context,
                        **params,
                        "target_index": target_index,
                    },
                )
            self.logger.event(
                "pncp_published_contracts",
                source_id=source.id,
                target_index=target_index,
                cnpj=context["cnpj"],
                codigo_unidade=context["codigo_unidade"],
                uf=context["uf"],
                pages_requested=pages_requested,
                contracts_count=len(contracts),
            )
            yield _target_checkpoint(target_index, context)

    def _target_context(self, target, expected_uf):
        missing = [
            field
            for field in self.required_target_fields
            if target.get(field) in (None, "")
        ]
        if missing:
            raise ValueError("Target de contrato incompleto: " + ", ".join(missing))
        cnpj = str(target["cnpj"])
        if not cnpj.isdigit() or len(cnpj) != 14:
            raise ValueError("Target de contrato com CNPJ inválido")
        actual_uf = str(target["uf"])
        if actual_uf != expected_uf:
            raise ValueError(
                f"Unidade fora do recorte: esperado={expected_uf}, recebido={actual_uf}"
            )
        return {
            "cnpj": cnpj,
            "codigo_unidade": str(target["codigo_unidade"]),
            "uf": actual_uf,
        }

    def _request(self, source, params):
        return self.client.get(
            source.endpoint,
            params,
            base_url=source.base,
            timeout_seconds=source.timeout_seconds,
            headers=source.headers,
            max_retries=source.max_retries,
            retry_backoff_seconds=source.retry_backoff_seconds,
        )

    @staticmethod
    def _query_unit_code(unit_code: str) -> str:
        if len(unit_code) > 6 and unit_code.isdigit():
            return str(int(unit_code))
        return unit_code

    @staticmethod
    def _same_unit_code(actual: object, expected: str) -> bool:
        actual_text = str(actual)
        if actual_text == expected:
            return True
        if actual_text.isdigit() and expected.isdigit():
            return int(actual_text) == int(expected)
        return False

    def _validate_contract(self, payload, context, source_url):
        external_id = payload.get("numeroControlePNCP")
        match = self.control_number_pattern.fullmatch(str(external_id or ""))
        if not match:
            raise ValueError(f"Contrato sem número de controle válido em {source_url}")
        actual_cnpj = value_at_path(payload, "orgaoEntidade.cnpj")
        actual_unit = value_at_path(payload, "unidadeOrgao.codigoUnidade")
        actual_uf = value_at_path(payload, "unidadeOrgao.ufSigla")
        if (
            match.group("cnpj") != context["cnpj"]
            or str(actual_cnpj) != context["cnpj"]
            or str(actual_uf) != context["uf"]
        ):
            raise ValueError(f"Contrato fora do órgão/UF alvo em {source_url}")
        if not self._same_unit_code(actual_unit, context["codigo_unidade"]):
            self.logger.event(
                "pncp_contract_scope_mismatch",
                external_id=str(external_id),
                expected_unit=context["codigo_unidade"],
                actual_unit=str(actual_unit),
                action="skipped_for_current_target",
            )
            return None
        return str(external_id)


class CommitmentCollector:
    """Coleta empenhos paginados para os contratos preservados na RAW."""

    control_number_pattern = re.compile(
        r"^(?P<cnpj>\d{14})-2-(?P<sequencial>\d+)/(?P<ano>\d{4})$"
    )
    required_fields = ("cnpj", "ano", "sequencialContrato", "sequencialEmpenho")

    def __init__(
        self, client: PNCPClient, logger: JsonlLogger | None = None
    ) -> None:
        self.client = client
        self.logger = logger or JsonlLogger()

    def collect(self, source: SourceConfig, start_page: int = 1):
        if not source.targets:
            raise ValueError("Fonte empenho sem contratos RAW configurados")
        targets = list(
            enumerate(source.targets[start_page - 1 :], start=start_page)
        )
        if source.max_pages is not None:
            targets = targets[: source.max_pages]
        pacer = _RequestPacer(source.rate_limit_seconds)

        def fetch(indexed_target):
            target_index, target = indexed_target
            context = self._target_context(target)
            endpoint = source.endpoint.format(**context)
            page = 1
            found = []
            pages_requested = 0
            guard = _PaginationGuard(
                source.id,
                self.logger,
                target_index=target_index,
                numero_controle=context["numero_controle"],
            )
            while True:
                params = {
                    source.page_param: page,
                    source.page_size_param: source.page_size,
                }
                url = self.client.build_url(
                    endpoint, params, base_url=source.base
                )

                def request():
                    pacer.wait()
                    return (
                        self.client.get(
                            endpoint,
                            params,
                            base_url=source.base,
                            timeout_seconds=source.timeout_seconds,
                            headers=source.headers,
                            max_retries=source.max_retries,
                            retry_backoff_seconds=source.retry_backoff_seconds,
                            not_found_as_empty=True,
                        ),
                        None,
                    )

                response, records, _, attempts, outcome = (
                    _fetch_validated_page(
                        request, source, page, guard
                    )
                )
                pages_requested += attempts
                for payload in records:
                    external_id = self._validate_commitment(
                        payload, context, url
                    )
                    found.append((payload, external_id, url, params))
                if outcome == "last":
                    break
                page += 1
            return target_index, context, found, pages_requested

        ordinal = 0
        for target_index, context, commitments, pages_requested in (
            _ordered_parallel_map(fetch, targets, source.max_concurrency)
        ):
            for payload, external_id, url, params in commitments:
                ordinal += 1
                yield CollectedRecord(
                    source.id,
                    payload,
                    url,
                    str(target_index),
                    ordinal,
                    external_id=external_id,
                    query_params={
                        **source.audit_context,
                        **params,
                        "numeroControlePNCPContrato": context["numero_controle"],
                        "target_index": target_index,
                    },
                )
            self.logger.event(
                "pncp_commitments",
                source_id=source.id,
                target_index=target_index,
                numero_controle=context["numero_controle"],
                pages_requested=pages_requested,
                commitments_count=len(commitments),
            )
            yield _target_checkpoint(target_index, context)

    def _target_context(self, target):
        numero_controle = str(target.get("numero_controle") or "")
        match = self.control_number_pattern.fullmatch(numero_controle)
        if not match:
            raise ValueError("Target de empenho com contrato pai inválido")
        return {
            "cnpj": match.group("cnpj"),
            "ano": int(match.group("ano")),
            "sequencial": int(match.group("sequencial")),
            "numero_controle": numero_controle,
        }

    def _validate_commitment(self, payload, context, source_url):
        missing = [field for field in self.required_fields if payload.get(field) is None]
        if missing:
            raise ValueError(
                "Empenho sem identificador obrigatório: " + ", ".join(missing)
            )
        if (
            str(payload["cnpj"]) != context["cnpj"]
            or int(payload["ano"]) != context["ano"]
            or int(payload["sequencialContrato"]) != context["sequencial"]
        ):
            raise ValueError(f"Empenho fora do contrato alvo em {source_url}")
        return (
            f"{context['numero_controle']}|{int(payload['sequencialEmpenho'])}"
        )


class ContractTimelineCollector:
    """Coleta contratações por trimestre e modalidade com checkpoint composto."""

    external_id_field = "numeroControlePNCP"
    uf_field_path = "unidadeOrgao.ufSigla"

    def __init__(
        self, client: PNCPClient, logger: JsonlLogger | None = None
    ) -> None:
        self.client = client
        self.logger = logger or JsonlLogger()

    def collect(self, source: SourceConfig, start_page: int = 1):
        expected_uf = str(source.params.get("uf") or "")
        if not expected_uf:
            raise ValueError("Fonte histórica sem parâmetro uf")
        try:
            modalities = tuple(
                int(value) for value in source.params["modalidades"]
            )
        except (KeyError, TypeError, ValueError):
            raise ValueError(
                "Fonte histórica requer lista de modalidades"
            ) from None
        if not modalities or len(set(modalities)) != len(modalities):
            raise ValueError("Fonte histórica possui modalidades inválidas")
        windows = _quarter_date_windows(
            source.params.get("dataInicial"),
            source.params.get("dataFinal"),
        )
        work_items = [
            (window_index, window_start, window_end, modality)
            for window_index, (window_start, window_end) in enumerate(
                windows, start=1
            )
            for modality in modalities
        ]
        indexed_items = list(
            enumerate(work_items[start_page - 1 :], start=start_page)
        )
        if source.max_pages is not None:
            indexed_items = indexed_items[: source.max_pages]
        pacer = _RequestPacer(source.rate_limit_seconds)
        base_params = {
            key: value
            for key, value in source.params.items()
            if key not in {"dataInicial", "dataFinal", "modalidades"}
        }
        ordinal = 0

        for work_index, work_item in indexed_items:
            window_index, window_start, window_end, modality = work_item
            page = 1
            pages_requested = 0
            records_count = 0
            guard = _PaginationGuard(
                source.id,
                self.logger,
                work_index=work_index,
                window_start=window_start,
                window_end=window_end,
                modality=modality,
            )
            while True:
                params = {
                    **base_params,
                    "dataInicial": window_start,
                    "dataFinal": window_end,
                    "codigoModalidadeContratacao": modality,
                    source.page_param: page,
                    source.page_size_param: source.page_size,
                }
                url = self.client.build_url(
                    source.endpoint, params, base_url=source.base
                )

                def request():
                    pacer.wait()
                    return (
                        self.client.get(
                            source.endpoint,
                            params,
                            base_url=source.base,
                            timeout_seconds=source.timeout_seconds,
                            headers=source.headers,
                            max_retries=source.max_retries,
                            retry_backoff_seconds=source.retry_backoff_seconds,
                        ),
                        None,
                    )

                response, records, _, attempts, outcome = (
                    _fetch_validated_page(
                        request, source, page, guard
                    )
                )
                pages_requested += attempts
                for payload in records:
                    external_id = payload.get(self.external_id_field)
                    actual_uf = value_at_path(payload, self.uf_field_path)
                    if external_id in (None, ""):
                        raise ValueError(
                            f"Contratação sem numeroControlePNCP em {url}"
                        )
                    if actual_uf != expected_uf:
                        raise ValueError(
                            "Contratação fora do recorte estadual: "
                            f"esperado={expected_uf}, recebido={actual_uf}"
                        )
                    ordinal += 1
                    records_count += 1
                    yield CollectedRecord(
                        source.id,
                        payload,
                        url,
                        str(work_index),
                        ordinal,
                        external_id=str(external_id),
                        query_params={
                            **source.audit_context,
                            **params,
                            "ano": int(window_start[:4]),
                            "trimestre": (int(window_start[4:6]) - 1) // 3 + 1,
                            "window_index": window_index,
                            "work_index": work_index,
                        },
                    )
                if outcome == "last":
                    break
                page += 1
            self.logger.event(
                "pncp_contract_timeline",
                source_id=source.id,
                work_index=work_index,
                window_index=window_index,
                window_start=window_start,
                window_end=window_end,
                modality=modality,
                pages_requested=pages_requested,
                records_count=records_count,
            )
            yield _target_checkpoint(
                work_index,
                {
                    "window_start": window_start,
                    "window_end": window_end,
                    "modality": modality,
                },
                target_key=(
                    f"{window_start}:{window_end}|modalidade={modality}"
                ),
            )


class ContractCollector(PNCPCollector):
    """Coleta contratações RAW e valida identidade e recorte estadual."""

    external_id_field = "numeroControlePNCP"
    uf_field_path = "unidadeOrgao.ufSigla"
    record_empty_pages = True

    def collect(self, source: SourceConfig, start_page: int = 1):
        expected_uf = source.params.get("uf")
        if not expected_uf:
            raise ValueError("Fonte de contratações sem parâmetro uf")

        for record in super().collect(source, start_page):
            if isinstance(record, CollectionCheckpoint):
                yield record
                continue

            external_id = record.payload.get(self.external_id_field)
            actual_uf = value_at_path(record.payload, self.uf_field_path)
            if external_id in (None, ""):
                self._inconsistency(
                    record, "missing_external_id", expected_uf, actual_uf
                )
                raise ValueError(
                    "Contratação sem numeroControlePNCP; coleta interrompida"
                )
            if actual_uf != expected_uf:
                self._inconsistency(
                    record, "unexpected_uf", expected_uf, actual_uf
                )
                raise ValueError(
                    "Contratação fora do recorte estadual: "
                    f"esperado={expected_uf}, recebido={actual_uf}"
                )
            yield replace(record, external_id=str(external_id))

    def _inconsistency(
        self,
        record: CollectedRecord,
        reason: str,
        expected_uf: Any,
        actual_uf: Any,
    ) -> None:
        self.logger.event(
            "pncp_contract_inconsistency",
            source_id=record.source_id,
            resource_type="contratacao",
            page=int(record.cursor),
            ordinal=record.ordinal,
            reason=reason,
            expected_uf=expected_uf,
            actual_uf=actual_uf,
        )


def _response_envelope_summary(response: Any) -> dict[str, Any]:
    if not isinstance(response, Mapping):
        return {"response_type": type(response).__name__}
    summary = {"response_keys": sorted(str(key) for key in response)}
    for key in (
        "totalRegistros",
        "totalPaginas",
        "numeroPagina",
        "paginasRestantes",
    ):
        if key in response:
            summary[key] = response[key]
    return summary


def _quarter_date_windows(
    initial_value: Any, final_value: Any
) -> tuple[tuple[str, str], ...]:
    """Divide um intervalo inclusivo em trimestres civis."""
    try:
        initial_text = str(initial_value)
        final_text = str(final_value)
        if len(initial_text) != 8 or len(final_text) != 8:
            raise ValueError
        initial = date.fromisoformat(
            f"{initial_text[:4]}-{initial_text[4:6]}-{initial_text[6:8]}"
        )
        final = date.fromisoformat(
            f"{final_text[:4]}-{final_text[4:6]}-{final_text[6:8]}"
        )
    except (TypeError, ValueError):
        raise ValueError(
            "Fonte histórica requer dataInicial e dataFinal em yyyyMMdd"
        ) from None
    if final < initial:
        raise ValueError("Fonte histórica possui intervalo de datas invertido")
    windows = []
    cursor = initial
    while cursor <= final:
        quarter_last_month = ((cursor.month - 1) // 3 + 1) * 3
        if quarter_last_month == 12:
            quarter_end = date(cursor.year, 12, 31)
        else:
            quarter_end = date(cursor.year, quarter_last_month + 1, 1) - timedelta(days=1)
        end = min(quarter_end, final)
        windows.append((cursor.strftime("%Y%m%d"), end.strftime("%Y%m%d")))
        cursor = end + timedelta(days=1)
    return tuple(windows)


def _annual_date_windows(
    initial_value: Any, final_value: Any
) -> tuple[tuple[str, str], ...]:
    """Divide um intervalo inclusivo por ano civil (máximo 365 dias)."""
    try:
        initial = date.fromisoformat(
            f"{str(initial_value)[:4]}-{str(initial_value)[4:6]}-"
            f"{str(initial_value)[6:8]}"
        )
        final = date.fromisoformat(
            f"{str(final_value)[:4]}-{str(final_value)[4:6]}-"
            f"{str(final_value)[6:8]}"
        )
    except (TypeError, ValueError):
        raise ValueError(
            "Fonte de atas requer dataInicial e dataFinal em yyyyMMdd"
        ) from None
    if final < initial:
        raise ValueError("Fonte de atas possui intervalo de datas invertido")
    windows = []
    cursor = initial
    while cursor <= final:
        end = min(date(cursor.year, 12, 31), final)
        windows.append((cursor.strftime("%Y%m%d"), end.strftime("%Y%m%d")))
        cursor = end + timedelta(days=1)
    return tuple(windows)


ResultT = TypeVar("ResultT")


class _RequestPacer:
    """Limita globalmente o início das chamadas feitas por várias threads."""

    def __init__(self, interval_seconds: float) -> None:
        self.interval_seconds = interval_seconds
        self._next_request_at = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        if not self.interval_seconds:
            return
        with self._lock:
            now = time.monotonic()
            delay = max(0.0, self._next_request_at - now)
            self._next_request_at = (
                max(now, self._next_request_at) + self.interval_seconds
            )
        if delay:
            time.sleep(delay)


def _ordered_parallel_map(
    function: Callable[[Any], ResultT],
    values: Iterable[Any],
    max_workers: int,
) -> Iterable[ResultT]:
    """Executa HTTP em paralelo e entrega resultados na ordem dos alvos."""
    if max_workers == 1:
        for value in values:
            yield function(value)
        return

    iterator = iter(values)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        pending = deque()
        for _ in range(max_workers):
            try:
                value = next(iterator)
            except StopIteration:
                break
            pending.append(executor.submit(function, value))

        while pending:
            yield pending.popleft().result()
            try:
                value = next(iterator)
            except StopIteration:
                continue
            pending.append(executor.submit(function, value))


class PurchaseItemCollector:
    """Coleta e preserva os itens pertencentes a contratações RAW."""

    required_target_fields = ("cnpj", "ano", "sequencial", "numero_controle")

    def __init__(
        self, client: PNCPClient, logger: JsonlLogger | None = None
    ) -> None:
        self.client = client
        self.logger = logger or JsonlLogger()

    def collect(self, source: SourceConfig, start_page: int = 1):
        if not source.targets:
            raise ValueError(
                "Fonte contratacao_item sem contratações-alvo configuradas"
            )

        targets = list(
            enumerate(source.targets[start_page - 1 :], start=start_page)
        )
        if source.max_pages is not None:
            targets = targets[: source.max_pages]
        pacer = _RequestPacer(source.rate_limit_seconds)

        def fetch(indexed_target):
            target_index, target = indexed_target
            context = self._target_context(target)
            endpoint = source.endpoint.format(**context)
            source_url = self.client.build_url(endpoint, base_url=source.base)
            pacer.wait()
            response = self.client.get(
                endpoint,
                base_url=source.base,
                timeout_seconds=source.timeout_seconds,
                headers=source.headers,
                max_retries=source.max_retries,
                retry_backoff_seconds=source.retry_backoff_seconds,
                not_found_as_empty=True,
            )
            return (
                target_index,
                context,
                source_url,
                records_from_response(response),
            )

        ordinal = 0
        for target_index, context, source_url, items in _ordered_parallel_map(
            fetch, targets, source.max_concurrency
        ):
            query_params = {
                **source.audit_context,
                "numeroControlePNCP": context["numero_controle"],
                "target_index": target_index,
            }
            for payload in items:
                numero_item = payload.get("numeroItem")
                if numero_item is None:
                    raise ValueError(
                        f"Item PNCP sem numeroItem em {source_url}"
                    )
                ordinal += 1
                yield CollectedRecord(
                    source.id,
                    payload,
                    source_url,
                    str(target_index),
                    ordinal,
                    external_id=(
                        f"{context['numero_controle']}|{numero_item}"
                    ),
                    query_params=query_params,
                )

            self.logger.event(
                "pncp_purchase_items",
                source_id=source.id,
                target_index=target_index,
                numero_controle=context["numero_controle"],
                items_count=len(items),
            )
            yield _target_checkpoint(target_index, context)

    def _target_context(self, target):
        missing = [
            field
            for field in self.required_target_fields
            if target.get(field) in (None, "")
        ]
        if missing:
            raise ValueError(
                "Target PNCP incompleto para itens: " + ", ".join(missing)
            )

        cnpj = str(target["cnpj"])
        if not cnpj.isdigit() or len(cnpj) != 14:
            raise ValueError("Target PNCP com CNPJ inválido")
        return {
            "cnpj": cnpj,
            "ano": int(target["ano"]),
            "sequencial": int(target["sequencial"]),
            "numero_controle": str(target["numero_controle"]),
        }


class ItemResultCollector:
    """Coleta resultados usando os itens já preservados na camada RAW."""

    required_target_fields = ("numero_controle", "numero_item")
    required_result_fields = (
        "numeroControlePNCPCompra",
        "numeroItem",
        "sequencialResultado",
    )
    control_number_pattern = re.compile(
        r"^(?P<cnpj>\d{14})-\d+-(?P<sequencial>\d+)/(?P<ano>\d{4})$"
    )

    def __init__(
        self, client: PNCPClient, logger: JsonlLogger | None = None
    ) -> None:
        self.client = client
        self.logger = logger or JsonlLogger()

    def collect(self, source: SourceConfig, start_page: int = 1):
        if not source.targets:
            raise ValueError(
                "Fonte resultado_item sem itens RAW configurados como targets"
            )

        targets = list(
            enumerate(source.targets[start_page - 1 :], start=start_page)
        )
        if source.max_pages is not None:
            targets = targets[: source.max_pages]
        pacer = _RequestPacer(source.rate_limit_seconds)

        def fetch(indexed_target):
            target_index, target = indexed_target
            context = self._target_context(target)
            requested = target.get("tem_resultado") is not False
            if not requested:
                return target_index, context, False, None, []
            endpoint = source.endpoint.format(**context)
            source_url = self.client.build_url(endpoint, base_url=source.base)
            pacer.wait()
            response = self.client.get(
                endpoint,
                base_url=source.base,
                timeout_seconds=source.timeout_seconds,
                headers=source.headers,
                max_retries=source.max_retries,
                retry_backoff_seconds=source.retry_backoff_seconds,
                not_found_as_empty=True,
            )
            return (
                target_index,
                context,
                True,
                source_url,
                records_from_response(response),
            )

        ordinal = 0
        for (
            target_index,
            context,
            requested,
            result_url,
            results,
        ) in _ordered_parallel_map(fetch, targets, source.max_concurrency):
            query_params = {
                **source.audit_context,
                "numeroControlePNCP": context["numero_controle"],
                "numeroItem": context["numeroItem"],
                "target_index": target_index,
            }
            for payload in results:
                missing = [
                    field
                    for field in self.required_result_fields
                    if payload.get(field) is None
                ]
                if missing:
                    raise ValueError(
                        "Resultado de item sem identificador obrigatório: "
                        + ", ".join(missing)
                    )
                ordinal += 1
                yield CollectedRecord(
                    source.id,
                    payload,
                    result_url,
                    str(target_index),
                    ordinal,
                    external_id=(
                        f"{context['numero_controle']}|"
                        f"{context['numeroItem']}|"
                        f"{payload['sequencialResultado']}"
                    ),
                    query_params=query_params,
                )

            self.logger.event(
                "pncp_item_results",
                source_id=source.id,
                target_index=target_index,
                numero_controle=context["numero_controle"],
                numero_item=context["numeroItem"],
                requested=requested,
                results_count=len(results),
            )
            yield _target_checkpoint(
                target_index,
                {
                    "numero_controle": context["numero_controle"],
                    "numero_item": context["numeroItem"],
                    "tem_resultado": requested,
                },
            )

    def _target_context(self, target):
        missing = [
            field
            for field in self.required_target_fields
            if target.get(field) in (None, "")
        ]
        if missing:
            raise ValueError(
                "Target PNCP incompleto: " + ", ".join(missing)
            )

        numero_controle = str(target["numero_controle"])
        match = self.control_number_pattern.fullmatch(numero_controle)
        if not match:
            raise ValueError(
                "Item RAW com numeroControlePNCP do pai inválido"
            )
        return {
            "cnpj": match.group("cnpj"),
            "ano": int(match.group("ano")),
            "sequencial": int(match.group("sequencial")),
            "numeroItem": int(target["numero_item"]),
            "numero_controle": numero_controle,
        }
