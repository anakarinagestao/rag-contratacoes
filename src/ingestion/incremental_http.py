from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from src.ingestion.pncp_client import PNCPClient, PNCPHTTPResponse
from src.schema import value_at_path


class IncrementalAdapterError(RuntimeError):
    """Resposta HTTP válida no transporte, mas inválida para o contrato incremental."""


@dataclass(frozen=True)
class IncrementalHTTPPage:
    status_code: int
    outcome: str
    records: tuple[Mapping[str, Any], ...]
    page: int
    total_pages: int | None
    source_url: str
    attempts: int


@dataclass(frozen=True)
class IncrementalHTTPDocument:
    status_code: int
    outcome: str
    payload: Mapping[str, Any] | None
    source_url: str
    attempts: int


class PNCPIncrementalHTTPAdapter:
    """Converte o transporte PNCP em páginas incrementais auditáveis."""

    TOTAL_PAGE_FIELDS = ("totalPaginas", "total_pages", "page.totalPages")
    CURRENT_PAGE_FIELDS = ("numeroPagina", "pagina", "page.number")
    DEFAULT_ITEM_PATHS = ("data", "content", "items")

    def __init__(self, client: PNCPClient) -> None:
        self.client = client

    def fetch_page(
        self,
        endpoint: str,
        params: Mapping[str, Any],
        *,
        page: int,
        page_param: str | None = "pagina",
        item_path: str | None = None,
        base_url: str | None = None,
        timeout_seconds: float | None = None,
        headers: Mapping[str, str] | None = None,
        max_retries: int | None = None,
        retry_backoff_seconds: float | None = None,
        not_found_as_empty: bool = False,
    ) -> IncrementalHTTPPage:
        if page <= 0:
            raise ValueError("Página incremental deve ser positiva")
        request_params = dict(params)
        if page_param is not None:
            request_params[page_param] = page
        response = self.client.get_response(
            endpoint,
            request_params,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            headers=headers,
            max_retries=max_retries,
            retry_backoff_seconds=retry_backoff_seconds,
            not_found_as_empty=not_found_as_empty,
        )
        records = self._records(response, item_path)
        total_pages = self._optional_int(
            response.payload, self.TOTAL_PAGE_FIELDS
        )
        reported_page = self._optional_int(
            response.payload, self.CURRENT_PAGE_FIELDS
        )
        if reported_page is not None and reported_page != page:
            raise IncrementalAdapterError(
                f"PNCP respondeu página {reported_page}; esperada {page}"
            )
        if total_pages is not None and total_pages < page and records:
            raise IncrementalAdapterError(
                "PNCP retornou registros além do total de páginas declarado"
            )
        outcome = response.outcome
        if outcome == "RESPONSE":
            outcome = "FOUND" if records else "EMPTY"
        return IncrementalHTTPPage(
            response.status_code,
            outcome,
            records,
            page,
            total_pages,
            response.url,
            response.attempts,
        )

    def fetch_document(
        self,
        endpoint: str,
        params: Mapping[str, Any] | None = None,
        *,
        base_url: str | None = None,
        timeout_seconds: float | None = None,
        headers: Mapping[str, str] | None = None,
        max_retries: int | None = None,
        retry_backoff_seconds: float | None = None,
        not_found_as_empty: bool = False,
    ) -> IncrementalHTTPDocument:
        response = self.client.get_response(
            endpoint,
            params,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            headers=headers,
            max_retries=max_retries,
            retry_backoff_seconds=retry_backoff_seconds,
            not_found_as_empty=not_found_as_empty,
        )
        if response.outcome in {"EMPTY", "NOT_FOUND"}:
            payload = None
            outcome = response.outcome
        elif isinstance(response.payload, Mapping):
            payload = response.payload
            outcome = "FOUND"
        else:
            raise IncrementalAdapterError(
                "Resposta incremental de detalhe não contém um objeto"
            )
        return IncrementalHTTPDocument(
            response.status_code,
            outcome,
            payload,
            response.url,
            response.attempts,
        )

    @classmethod
    def _records(
        cls, response: PNCPHTTPResponse, item_path: str | None
    ) -> tuple[Mapping[str, Any], ...]:
        if response.outcome in {"EMPTY", "NOT_FOUND"}:
            return ()
        payload = response.payload
        values: Any = payload
        if item_path:
            values = value_at_path(payload, item_path)
        elif isinstance(payload, Mapping):
            for path in cls.DEFAULT_ITEM_PATHS:
                candidate = value_at_path(payload, path)
                if isinstance(candidate, list):
                    values = candidate
                    break
        if not isinstance(values, list):
            raise IncrementalAdapterError(
                "Resposta incremental não contém uma lista de registros"
            )
        if any(not isinstance(value, Mapping) for value in values):
            raise IncrementalAdapterError(
                "Resposta incremental contém registro que não é objeto"
            )
        return tuple(values)

    @staticmethod
    def _optional_int(
        payload: Any, paths: Sequence[str]
    ) -> int | None:
        if not isinstance(payload, Mapping):
            return None
        for path in paths:
            value = value_at_path(payload, path)
            if value is None:
                continue
            try:
                parsed = int(value)
            except (TypeError, ValueError) as error:
                raise IncrementalAdapterError(
                    f"Metadado de paginação inválido em {path}"
                ) from error
            if parsed < 0:
                raise IncrementalAdapterError(
                    f"Metadado de paginação negativo em {path}"
                )
            return parsed
        return None


__all__ = [
    "IncrementalAdapterError",
    "IncrementalHTTPDocument",
    "IncrementalHTTPPage",
    "PNCPIncrementalHTTPAdapter",
]
