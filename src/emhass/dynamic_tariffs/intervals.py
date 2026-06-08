from __future__ import annotations

import math
from typing import Any

import pandas as pd

from emhass.dynamic_tariffs.models import (
    DynamicTariffError,
    DynamicTariffInterval,
    ExportSignPolicy,
    TimeBoundaryStrategy,
)


def _coerce_enum(enum_type, value, field_name: str):
    try:
        return enum_type(value)
    except ValueError as exc:
        valid = ", ".join(item.value for item in enum_type)
        raise DynamicTariffError(f"Invalid {field_name} {value!r}; expected one of {valid}") from exc


def _parse_timestamp(value: Any, *, row_index: int, key: str, time_zone: str) -> pd.Timestamp:
    if value is None:
        raise DynamicTariffError(f"Dynamic tariff row {row_index} is missing timestamp {key!r}")
    try:
        ts = pd.Timestamp(value)
    except Exception as exc:
        raise DynamicTariffError(
            f"Dynamic tariff row {row_index} has invalid timestamp {key!r}: {value!r}"
        ) from exc
    if ts.tz is None:
        raise DynamicTariffError(
            f"Dynamic tariff row {row_index} timestamp {key!r} must be timezone-aware"
        )
    return ts.tz_convert(time_zone)


def _parse_duration(value: Any, *, row_index: int, start: pd.Timestamp, end: pd.Timestamp):
    if value is None:
        duration = end - start
    elif isinstance(value, int | float) and not isinstance(value, bool):
        duration = pd.to_timedelta(float(value), unit="minutes")
    elif isinstance(value, str):
        stripped = value.strip()
        if stripped == "":
            duration = end - start
        else:
            try:
                duration = pd.to_timedelta(float(stripped), unit="minutes")
            except ValueError:
                try:
                    duration = pd.to_timedelta(stripped)
                except Exception as exc:
                    raise DynamicTariffError(
                        f"Dynamic tariff row {row_index} has invalid duration {value!r}"
                    ) from exc
    else:
        try:
            duration = pd.to_timedelta(value)
        except Exception as exc:
            raise DynamicTariffError(
                f"Dynamic tariff row {row_index} has invalid duration {value!r}"
            ) from exc
    if duration <= pd.Timedelta(0):
        raise DynamicTariffError(f"Dynamic tariff row {row_index} has non-positive duration")
    return duration


def _parse_price(row: dict[str, Any], *, row_index: int, price_key: str) -> float:
    if price_key not in row:
        raise DynamicTariffError(f"Dynamic tariff row {row_index} is missing price {price_key!r}")
    try:
        value = float(row[price_key])
    except (TypeError, ValueError) as exc:
        raise DynamicTariffError(
            f"Dynamic tariff row {row_index} has non-numeric price {price_key!r}"
        ) from exc
    if not math.isfinite(value):
        raise DynamicTariffError(
            f"Dynamic tariff row {row_index} has non-finite price {price_key!r}"
        )
    return value


def canonicalise_interval_rows(
    rows: list[dict[str, Any]],
    *,
    time_zone: str,
    start_key: str,
    end_key: str,
    duration_key: str,
    price_key: str,
    boundary_strategy: TimeBoundaryStrategy,
    export_sign_policy: ExportSignPolicy = ExportSignPolicy.SOURCE_SIGNED,
) -> list[DynamicTariffInterval]:
    """Convert provider rows to validated, timezone-normalized intervals."""
    boundary_strategy = _coerce_enum(
        TimeBoundaryStrategy, boundary_strategy, "dynamic_tariff_time_boundary_strategy"
    )
    export_sign_policy = _coerce_enum(
        ExportSignPolicy, export_sign_policy, "dynamic_tariff_export_sign"
    )
    if not rows:
        raise DynamicTariffError("Dynamic tariff provider returned no forecast rows")

    intervals: list[DynamicTariffInterval] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise DynamicTariffError(f"Dynamic tariff row {index} is not an object")
        if end_key not in row:
            raise DynamicTariffError(f"Dynamic tariff row {index} is missing {end_key!r}")
        end = _parse_timestamp(row[end_key], row_index=index, key=end_key, time_zone=str(time_zone))
        if start_key not in row:
            raise DynamicTariffError(f"Dynamic tariff row {index} is missing {start_key!r}")
        explicit_start = _parse_timestamp(
            row[start_key], row_index=index, key=start_key, time_zone=str(time_zone)
        )
        duration = _parse_duration(
            row.get(duration_key), row_index=index, start=explicit_start, end=end
        )
        start = end - duration if boundary_strategy == TimeBoundaryStrategy.END_MINUS_DURATION else explicit_start
        if end <= start:
            raise DynamicTariffError(f"Dynamic tariff row {index} has end before start")
        value = _parse_price(row, row_index=index, price_key=price_key)
        if export_sign_policy == ExportSignPolicy.INVERT:
            value *= -1
        intervals.append(
            DynamicTariffInterval(start=start, end=end, value=value, source_index=index)
        )

    intervals.sort(key=lambda interval: (interval.start, interval.end, interval.source_index))
    previous: DynamicTariffInterval | None = None
    for interval in intervals:
        if previous is not None and interval.start < previous.end:
            raise DynamicTariffError(
                "Dynamic tariff intervals overlap at "
                f"{interval.start.isoformat()} between rows "
                f"{previous.source_index} and {interval.source_index}"
            )
        previous = interval
    return intervals


def resolve_intervals_to_forecast(
    intervals: list[DynamicTariffInterval],
    forecast_dates: pd.DatetimeIndex,
    *,
    source_name: str,
) -> list[float]:
    """Resolve validated intervals to exactly one price per EMHASS forecast timestamp."""
    if not intervals:
        raise DynamicTariffError(f"{source_name} returned no dynamic tariff intervals")
    dates = pd.DatetimeIndex(forecast_dates)
    if dates.empty:
        return []
    if dates.tz is None:
        raise DynamicTariffError("EMHASS forecast_dates must be timezone-aware")

    target_tz = intervals[0].start.tz
    values: list[float] = []
    interval_index = 0
    for ts in dates.tz_convert(target_tz):
        while interval_index < len(intervals) and ts >= intervals[interval_index].end:
            interval_index += 1
        if interval_index >= len(intervals):
            raise DynamicTariffError(
                f"{source_name} dynamic tariff has no coverage for {ts.isoformat()}"
            )
        interval = intervals[interval_index]
        if not (interval.start <= ts < interval.end):
            raise DynamicTariffError(
                f"{source_name} dynamic tariff has no coverage for {ts.isoformat()}"
            )
        values.append(interval.value)
    return values
