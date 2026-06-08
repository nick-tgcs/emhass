from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import pandas as pd

try:
    from enum import StrEnum
except ImportError:  # pragma: no cover - Python 3.10 compatibility

    class StrEnum(str, Enum):
        pass


class DynamicTariffError(ValueError):
    """Raised when configured dynamic tariff data cannot safely drive a solve."""


class TimeBoundaryStrategy(StrEnum):
    EXPLICIT_START_END = "explicit_start_end"
    END_MINUS_DURATION = "end_minus_duration"


class ExportSignPolicy(StrEnum):
    SOURCE_SIGNED = "source_signed"
    INVERT = "invert"


@dataclass(frozen=True)
class DynamicTariffInterval:
    start: pd.Timestamp
    end: pd.Timestamp
    value: float
    source_index: int


@dataclass(frozen=True)
class DynamicTariffPair:
    load_cost_forecast: list[float]
    prod_price_forecast: list[float]
    import_source: str
    export_source: str
