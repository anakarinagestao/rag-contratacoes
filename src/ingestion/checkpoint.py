from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
import hashlib
import json
from typing import Any, Mapping


HISTORICAL_MODE = "historical"
INCREMENTAL_MODE = "incremental"
SYNC_MODES = (HISTORICAL_MODE, INCREMENTAL_MODE)


def canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def stable_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def normalized_date(value: date | str) -> str:
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if len(text) == 8 and text.isdigit():
        text = f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return date.fromisoformat(text).isoformat()


def normalized_watermark(value: datetime | date | str) -> str:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime.min.time())
    else:
        text = str(value).strip()
        if len(text) in (8, 10) and (
            text.isdigit() or text.count("-") == 2
        ):
            parsed = datetime.fromisoformat(normalized_date(text))
        else:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed.isoformat(timespec="microseconds")


@dataclass(frozen=True)
class SyncJobSpec:
    source: str
    mode: str
    base_id: str
    resource_type: str
    endpoint: str
    uf: str | None = None
    modality: int | None = None
    filters: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.mode not in SYNC_MODES:
            raise ValueError(f"Modo de sincronização inválido: {self.mode}")
        for name, value in (
            ("source", self.source),
            ("base_id", self.base_id),
            ("resource_type", self.resource_type),
            ("endpoint", self.endpoint),
        ):
            if not str(value).strip():
                raise ValueError(f"Job de sincronização requer {name}")

    @property
    def configuration(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "mode": self.mode,
            "base_id": self.base_id,
            "resource_type": self.resource_type,
            "endpoint": self.endpoint,
            "uf": self.uf,
            "modality": self.modality,
            "filters": dict(self.filters),
        }

    @property
    def configuration_json(self) -> str:
        return canonical_json(self.configuration)

    @property
    def configuration_hash(self) -> str:
        return stable_hash(self.configuration)

    @property
    def job_id(self) -> str:
        return f"job-{self.configuration_hash}"


@dataclass(frozen=True)
class SyncWindowSpec:
    job_id: str
    window_start: date | str
    window_end: date | str

    def __post_init__(self) -> None:
        start = normalized_date(self.window_start)
        end = normalized_date(self.window_end)
        if end < start:
            raise ValueError("Janela possui limite temporal invertido")

    @property
    def start(self) -> str:
        return normalized_date(self.window_start)

    @property
    def end(self) -> str:
        return normalized_date(self.window_end)

    @property
    def window_id(self) -> str:
        digest = stable_hash(
            {"job_id": self.job_id, "start": self.start, "end": self.end}
        )
        return f"window-{digest}"


@dataclass(frozen=True)
class CoveragePlanSpec:
    name: str
    base_id: str
    resource_type: str
    uf: str | None
    members: tuple[str, ...]

    def __post_init__(self) -> None:
        name = self.name.strip()
        base_id = self.base_id.strip()
        resource_type = self.resource_type.strip()
        if not name or not base_id:
            raise ValueError("Plano de cobertura requer nome e base_id")
        if not resource_type:
            raise ValueError("Plano de cobertura requer resource_type")
        normalized = tuple(str(member).strip() for member in self.members)
        if not normalized or any(not member for member in normalized):
            raise ValueError("Plano de cobertura requer membros não vazios")
        if len(set(normalized)) != len(normalized):
            raise ValueError("Plano de cobertura possui membros duplicados")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "base_id", base_id)
        object.__setattr__(self, "resource_type", resource_type)
        object.__setattr__(self, "members", tuple(sorted(normalized)))
        object.__setattr__(
            self,
            "uf",
            str(self.uf).strip().upper() if self.uf is not None else None,
        )

    @property
    def configuration(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "base_id": self.base_id,
            "resource_type": self.resource_type,
            "uf": self.uf,
            "members": sorted(self.members),
        }

    @property
    def configuration_json(self) -> str:
        return canonical_json(self.configuration)

    @property
    def plan_id(self) -> str:
        return f"coverage-{stable_hash(self.configuration)}"


@dataclass(frozen=True)
class CoverageWindowSpec:
    plan_id: str
    window_start: date | str
    window_end: date | str

    def __post_init__(self) -> None:
        if not self.plan_id.strip():
            raise ValueError("Janela de cobertura requer plan_id")
        if self.end < self.start:
            raise ValueError("Janela de cobertura possui limites invertidos")

    @property
    def start(self) -> str:
        return normalized_date(self.window_start)

    @property
    def end(self) -> str:
        return normalized_date(self.window_end)

    @property
    def window_id(self) -> str:
        return "coverage-window-" + stable_hash(
            {"plan_id": self.plan_id, "start": self.start, "end": self.end}
        )


def stable_target_metadata(
    target: Mapping[str, Any], qualifier: str | None = None
) -> tuple[str, str]:
    """Retorna identidade estável e fingerprint mutável de um alvo."""

    visible = {
        str(key): value
        for key, value in target.items()
        if not str(key).startswith("__sync_")
    }
    numero_controle = visible.get("numero_controle")
    numero_item = visible.get("numero_item")
    if numero_controle not in (None, "") and numero_item not in (None, ""):
        target_key = f"{numero_controle}|{numero_item}"
    elif numero_controle not in (None, ""):
        target_key = str(numero_controle)
    elif visible.get("cnpj") not in (None, "") and visible.get(
        "codigo_unidade"
    ) not in (None, ""):
        target_key = f"{visible['cnpj']}|{visible['codigo_unidade']}"
    elif visible.get("cnpj") not in (None, ""):
        target_key = str(visible["cnpj"])
    else:
        target_key = f"target-{stable_hash(visible)}"
    fingerprint_value: dict[str, Any] = {"target": visible}
    if qualifier is not None:
        target_key = f"{target_key}|{qualifier}"
        fingerprint_value["qualifier"] = qualifier
    return target_key, stable_hash(fingerprint_value)


def windows_overlap(first: SyncWindowSpec, second: SyncWindowSpec) -> bool:
    return first.start <= second.end and second.start <= first.end
