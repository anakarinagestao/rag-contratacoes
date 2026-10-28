from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import Enum
import hashlib
import json
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class CanonicalIdentity:
    """Identidade PNCP independente do coletor que observou a entidade."""

    base_id: str
    resource_type: str
    external_id: str

    def __post_init__(self) -> None:
        for field_name, value in (
            ("base_id", self.base_id),
            ("resource_type", self.resource_type),
            ("external_id", self.external_id),
        ):
            if not str(value).strip():
                raise ValueError(
                    f"Identidade canônica requer {field_name} não vazio"
                )

    @property
    def values(self) -> tuple[str, str, str]:
        return self.base_id, self.resource_type, self.external_id


class TimestampStatus(str, Enum):
    VALID = "VALID"
    MISSING = "MISSING"
    INVALID = "INVALID"


class TimestampKind(str, Enum):
    AWARE_DATETIME = "AWARE_DATETIME"
    NAIVE_DATETIME = "NAIVE_DATETIME"
    DATE = "DATE"


@dataclass(frozen=True)
class ParsedTimestamp:
    raw: str | None
    normalized: str | None
    status: TimestampStatus
    kind: TimestampKind | None = None
    field: str | None = None
    invalid_candidates: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class SourceTimestamps:
    published: ParsedTimestamp
    updated: ParsedTimestamp


_UPDATED_FIELDS: dict[str, tuple[str, ...]] = {
    "pca": ("dataAtualizacaoGlobalPCA", "dataAtualizacao"),
    "contratacao": ("dataAtualizacaoGlobal", "dataAtualizacao"),
    "contratacao_item": ("dataAtualizacao",),
    "resultado_item": ("dataAtualizacao",),
    "contrato": ("dataAtualizacaoGlobal", "dataAtualizacao"),
    "empenho": ("dataAtualizacao",),
    "ata_registro_preco": ("dataAtualizacaoGlobal", "dataAtualizacao"),
}

_PUBLISHED_FIELDS: dict[str, tuple[str, ...]] = {
    "pca": ("dataPublicacaoPNCP", "dataPublicacaoPncp"),
    "contratacao": ("dataPublicacaoPncp", "dataInclusao"),
    "contratacao_item": ("dataInclusao",),
    "resultado_item": ("dataResultado", "dataInclusao"),
    "contrato": ("dataPublicacaoPncp", "dataInclusao"),
    "empenho": ("dataInclusao",),
    "ata_registro_preco": ("dataPublicacaoPncp", "dataInclusao"),
}


def parse_timestamp(value: object) -> ParsedTimestamp:
    """Classifica e normaliza um timestamp sem inventar fuso horário."""

    if value in (None, ""):
        return ParsedTimestamp(None, None, TimestampStatus.MISSING)
    text = str(value).strip()
    if not text:
        return ParsedTimestamp(None, None, TimestampStatus.MISSING)
    if len(text) == 10:
        try:
            parsed_date = date.fromisoformat(text)
        except ValueError:
            pass
        else:
            return ParsedTimestamp(
                text,
                parsed_date.isoformat(),
                TimestampStatus.VALID,
                TimestampKind.DATE,
            )
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return ParsedTimestamp(text, None, TimestampStatus.INVALID)
    if parsed.tzinfo is not None:
        normalized = (
            parsed.astimezone(timezone.utc)
            .replace(tzinfo=None)
            .isoformat(timespec="microseconds")
        )
        kind = TimestampKind.AWARE_DATETIME
    else:
        normalized = parsed.isoformat(timespec="microseconds")
        kind = TimestampKind.NAIVE_DATETIME
    return ParsedTimestamp(
        text,
        normalized,
        TimestampStatus.VALID,
        kind,
    )


def _select_timestamp(
    payload: Mapping[str, Any], fields: Sequence[str]
) -> ParsedTimestamp:
    invalid_candidates: list[tuple[str, str]] = []
    for field in fields:
        value = payload.get(field)
        parsed = parse_timestamp(value)
        if parsed.status == TimestampStatus.MISSING:
            continue
        if parsed.status == TimestampStatus.VALID:
            return ParsedTimestamp(
                parsed.raw,
                parsed.normalized,
                parsed.status,
                parsed.kind,
                field,
                tuple(invalid_candidates),
            )
        invalid_candidates.append((field, parsed.raw or ""))
    if invalid_candidates:
        field, raw = invalid_candidates[0]
        return ParsedTimestamp(
            raw,
            None,
            TimestampStatus.INVALID,
            field=field,
            invalid_candidates=tuple(invalid_candidates),
        )
    return ParsedTimestamp(None, None, TimestampStatus.MISSING)


def extract_source_timestamps(
    payload: Mapping[str, Any], resource_type: str
) -> SourceTimestamps:
    """Extrai timestamps tipados, preservando candidatos inválidos."""

    published_fields = _PUBLISHED_FIELDS.get(
        resource_type,
        ("dataPublicacaoPncp", "dataPublicacaoPNCP", "dataInclusao"),
    )
    updated_fields = _UPDATED_FIELDS.get(
        resource_type,
        ("dataAtualizacaoGlobal", "dataAtualizacao"),
    )
    return SourceTimestamps(
        published=_select_timestamp(payload, published_fields),
        updated=_select_timestamp(payload, updated_fields),
    )


def source_timestamps(
    payload: Mapping[str, Any], resource_type: str
) -> tuple[str | None, str | None]:
    """Interface legada: retorna os valores brutos selecionados."""

    timestamps = extract_source_timestamps(payload, resource_type)
    return timestamps.published.raw, timestamps.updated.raw


def normalized_timestamp(value: object) -> str | None:
    """Normaliza ISO-8601 para comparação e igualdade entre observações."""

    return parse_timestamp(value).normalized


def build_observation_key(
    identity: CanonicalIdentity,
    digest: str,
    timestamp: ParsedTimestamp,
    *,
    source_id: str,
    window_start: str | None,
    window_end: str | None,
) -> str:
    """Identifica uma observação de modo estável entre reexecuções.

    A janela distingue uma reversão posterior de um replay da mesma
    observação. Cursor e ordinal não participam da chave porque a API pode
    repaginar o mesmo conjunto. O hash do conteúdo identifica a versão RAW.
    """

    document = {
        "identity": identity.values,
        "digest": digest,
        "timestamp_status": timestamp.status.value,
        "timestamp_kind": timestamp.kind.value if timestamp.kind else None,
        "timestamp": timestamp.normalized,
        "source": source_id,
        "window_start": window_start,
        "window_end": window_end,
    }
    encoded = json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
