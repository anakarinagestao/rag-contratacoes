from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from src.ingestion.checkpoint import normalized_date


@dataclass(frozen=True)
class PlannedWindow:
    start: str
    end: str
    overlaps_confirmed_range: bool = False


@dataclass(frozen=True)
class IncrementalWindowPolicy:
    """Política determinística de janelas fechadas e sobrepostas."""

    overlap_days: int = 2
    max_window_days: int = 1

    def __post_init__(self) -> None:
        if self.overlap_days < 0:
            raise ValueError("Sobreposição não pode ser negativa")
        if self.max_window_days <= 0:
            raise ValueError("Tamanho máximo da janela deve ser positivo")


def _as_date(value: date | datetime | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if "T" in text:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    return date.fromisoformat(normalized_date(text))


def plan_closed_windows(
    initial_start: date | str,
    through: date | str,
    *,
    confirmed_watermark: datetime | date | str | None = None,
    policy: IncrementalWindowPolicy | None = None,
) -> tuple[PlannedWindow, ...]:
    """Planeja cobertura inclusiva sem interpretar timestamp como cursor.

    `overlap_days` informa quantos dias já confirmados serão revisitados. O
    watermark continua sendo o fim de uma janela integralmente concluída; a
    sobreposição apenas cria novas observações idempotentes.
    """

    selected = policy or IncrementalWindowPolicy()
    floor = _as_date(initial_start)
    limit = _as_date(through)
    if confirmed_watermark is None:
        confirmed = None
        cursor = floor
    else:
        confirmed = _as_date(confirmed_watermark)
        cursor = confirmed + timedelta(days=1 - selected.overlap_days)
        cursor = max(cursor, floor)
    if cursor > limit:
        return ()

    windows: list[PlannedWindow] = []
    while cursor <= limit:
        end = min(
            cursor + timedelta(days=selected.max_window_days - 1),
            limit,
        )
        windows.append(
            PlannedWindow(
                cursor.isoformat(),
                end.isoformat(),
                overlaps_confirmed_range=(
                    confirmed is not None and cursor <= confirmed
                ),
            )
        )
        cursor = end + timedelta(days=1)
    return tuple(windows)


__all__ = [
    "IncrementalWindowPolicy",
    "PlannedWindow",
    "plan_closed_windows",
]
