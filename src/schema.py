from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import yaml


@dataclass(frozen=True)
class SourceConfig:
	id: str
	base: str
	endpoint: str
	collector: str = "paged"
	base_id: str = "pncp"
	natureza: str = "estruturada"
	resource_type: str = "records"
	sensitivity: str = "public"
	params: Mapping[str, Any] = field(default_factory=dict)
	audit_context: Mapping[str, Any] = field(default_factory=dict)
	targets: tuple[Mapping[str, Any], ...] = ()
	target_source_id: str | None = None
	target_source_ids: tuple[str, ...] = ()
	target_resource_type: str | None = None
	target_field_paths: Mapping[str, str] = field(default_factory=dict)
	discovery_endpoint_template: str | None = None
	detail_endpoint_template: str | None = None
	items_endpoint_template: str | None = None
	items_page_size: int = 50
	max_items_per_purchase: int | None = None
	item_path: str | None = None
	key_fields: tuple[str, ...] = ()
	page_param: str = "pagina"
	page_size_param: str = "tamanhoPagina"
	page_size: int = 50
	max_pages: int | None = None
	timeout_seconds: float = 30.0
	rate_limit_seconds: float = 0.0
	max_retries: int = 2
	retry_backoff_seconds: float = 0.5
	max_concurrency: int = 1
	headers: Mapping[str, str] = field(default_factory=dict)

	@classmethod
	def from_mapping(cls, value: Mapping[str, Any]) -> "SourceConfig":
		pagination = value.get("pagination", {})
		request = value.get("request", {})
		return cls(
			id=str(value["id"]),
			base=str(value.get("base", "")),
			endpoint=str(value["endpoint"]),
			collector=str(value.get("collector", "paged")),
			base_id=str(value.get("base_id", "pncp")),
			natureza=str(value.get("natureza", "estruturada")),
			resource_type=str(value.get("resource_type", "records")),
			sensitivity=str(value.get("sensitivity", "public")),
			params=dict(value.get("params", {})),
			audit_context=dict(value.get("audit_context", {})),
			targets=tuple(dict(target) for target in value.get("targets", ())),
			target_source_id=value.get("target_source_id"),
			target_source_ids=tuple(
				str(source_id)
				for source_id in value.get("target_source_ids", ())
			),
			target_resource_type=value.get("target_resource_type"),
			target_field_paths=dict(value.get("target_field_paths", {})),
			discovery_endpoint_template=value.get(
				"discovery_endpoint_template"
			),
			detail_endpoint_template=value.get("detail_endpoint_template"),
			items_endpoint_template=value.get("items_endpoint_template"),
			items_page_size=int(value.get("items_page_size", 50)),
			max_items_per_purchase=(
				int(value["max_items_per_purchase"])
				if value.get("max_items_per_purchase") is not None
				else None
			),
			item_path=value.get("item_path"),
			key_fields=tuple(value.get("key_fields", ())),
			page_param=str(pagination.get("page_param", "pagina")),
			page_size_param=str(pagination.get("page_size_param", "tamanhoPagina")),
			page_size=int(pagination.get("page_size", 50)),
			max_pages=(int(pagination["max_pages"]) if pagination.get("max_pages") is not None else None),
			timeout_seconds=float(request.get("timeout_seconds", 30.0)),
			rate_limit_seconds=float(request.get("rate_limit_seconds", 0.0)),
			max_retries=int(request.get("max_retries", 2)),
			retry_backoff_seconds=float(
				request.get("retry_backoff_seconds", 0.5)
			),
			max_concurrency=int(request.get("max_concurrency", 1)),
			headers=dict(request.get("headers", {})),
		)

	@property
	def url(self) -> str:
		return f"{self.base.rstrip('/')}/{self.endpoint.lstrip('/')}"


@dataclass(frozen=True)
class Manifest:
	sources: tuple[SourceConfig, ...]

	@classmethod
	def load(cls, path: str | Path) -> "Manifest":
		with Path(path).open(encoding="utf-8") as file:
			document = yaml.safe_load(file) or {}
		return cls(tuple(SourceConfig.from_mapping(item) for item in document.get("sources", [])))

	def source(self, source_id: str) -> SourceConfig:
		for source in self.sources:
			if source.id == source_id:
				return source
		raise KeyError(f"Fonte não encontrada no manifesto: {source_id}")


@dataclass(frozen=True)
class CollectedRecord:
	source_id: str
	payload: Mapping[str, Any]
	source_url: str
	cursor: str
	ordinal: int
	external_id: str | None = None
	query_params: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class CollectionCheckpoint:
	"""Marca um alvo concluído, mesmo quando ele não retorna registros."""

	cursor: str
	target_key: str | None = field(default=None, compare=False)
	target_fingerprint: str | None = field(default=None, compare=False)
	reconciliation_outcome: str | None = field(default=None, compare=False)


@dataclass(frozen=True)
class IngestionResult:
	run_id: str
	source_id: str
	status: str
	records_seen: int
	new: int
	updated: int
	unchanged: int
	failed: int = 0


def value_at_path(value: Any, path: str) -> Any:
	current = value
	for part in path.split(".") if path else ():
		if isinstance(current, Mapping):
			current = current.get(part)
		elif isinstance(current, list) and part.isdigit():
			current = current[int(part)]
		else:
			return None
	return current


def records_from_response(response: Any, item_path: str | None = None) -> list[Mapping[str, Any]]:
	values = value_at_path(response, item_path) if item_path else response
	if isinstance(values, list):
		return [item for item in values if isinstance(item, Mapping)]
	if isinstance(values, Mapping):
		for key in ("data", "items", "content", "resultado", "results"):
			candidate = values.get(key)
			if isinstance(candidate, list):
				return [item for item in candidate if isinstance(item, Mapping)]
	raise ValueError("Resposta PNCP não contém uma lista de registros")
