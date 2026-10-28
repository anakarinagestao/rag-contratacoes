from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Iterable, Mapping

from src.schema import value_at_path


class ScopeInventoryError(ValueError):
    pass


@dataclass(frozen=True, order=True)
class OrganizationUnit:
    cnpj: str
    unit_code: str
    uf: str

    def __post_init__(self) -> None:
        cnpj = str(self.cnpj).strip()
        unit_code = str(self.unit_code).strip()
        uf = str(self.uf).strip().upper()
        if len(cnpj) != 14 or not cnpj.isdigit():
            raise ScopeInventoryError("Inventário possui CNPJ inválido")
        if not unit_code:
            raise ScopeInventoryError("Inventário possui unidade vazia")
        if len(uf) != 2 or not uf.isalpha():
            raise ScopeInventoryError("Inventário possui UF inválida")
        object.__setattr__(self, "cnpj", cnpj)
        object.__setattr__(self, "unit_code", unit_code)
        object.__setattr__(self, "uf", uf)

    @property
    def member_key(self) -> str:
        return f"orgao-unidade:{self.cnpj}:{self.unit_code}"

    @property
    def target(self) -> dict[str, str]:
        return {
            "cnpj": self.cnpj,
            "codigo_unidade": self.unit_code,
            "uf": self.uf,
        }


@dataclass(frozen=True)
class ScopeInventory:
    uf: str
    members: tuple[OrganizationUnit, ...]

    def __post_init__(self) -> None:
        normalized_uf = self.uf.strip().upper()
        if not self.members:
            raise ScopeInventoryError("Inventário de cobertura está vazio")
        if any(member.uf != normalized_uf for member in self.members):
            raise ScopeInventoryError("Inventário mistura UFs")
        if tuple(sorted(set(self.members))) != self.members:
            raise ScopeInventoryError(
                "Inventário deve estar ordenado e sem duplicatas"
            )
        object.__setattr__(self, "uf", normalized_uf)

    @property
    def inventory_id(self) -> str:
        document = {
            "uf": self.uf,
            "members": [member.target for member in self.members],
        }
        serialized = json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return "inventory-" + hashlib.sha256(
            serialized.encode("utf-8")
        ).hexdigest()

    @property
    def coverage_members(self) -> tuple[str, ...]:
        return tuple(member.member_key for member in self.members)

    @property
    def targets(self) -> tuple[dict[str, str], ...]:
        return tuple(member.target for member in self.members)


def build_scope_inventory(
    payloads: Iterable[Mapping[str, Any]],
    *,
    expected_uf: str,
) -> ScopeInventory:
    uf = expected_uf.strip().upper()
    if len(uf) != 2 or not uf.isalpha():
        raise ScopeInventoryError("UF esperada é inválida")
    members: set[OrganizationUnit] = set()
    for index, payload in enumerate(payloads, start=1):
        cnpj = value_at_path(payload, "orgaoEntidade.cnpj")
        unit = value_at_path(payload, "unidadeOrgao.codigoUnidade")
        actual_uf = value_at_path(payload, "unidadeOrgao.ufSigla")
        missing = [
            name
            for name, value in (
                ("orgaoEntidade.cnpj", cnpj),
                ("unidadeOrgao.codigoUnidade", unit),
                ("unidadeOrgao.ufSigla", actual_uf),
            )
            if value in (None, "")
        ]
        if missing:
            raise ScopeInventoryError(
                f"Payload {index} sem campos do inventário: "
                + ", ".join(missing)
            )
        actual_uf = str(actual_uf).upper()
        if actual_uf != uf:
            raise ScopeInventoryError(
                f"Payload {index} fora da UF esperada: {actual_uf}"
            )
        members.add(OrganizationUnit(str(cnpj), str(unit), actual_uf))
    return ScopeInventory(uf, tuple(sorted(members)))


__all__ = [
    "OrganizationUnit",
    "ScopeInventory",
    "ScopeInventoryError",
    "build_scope_inventory",
]
