import math

import pandas as pd
import pytest

from emhass.dynamic_tariffs.intervals import (
    canonicalise_interval_rows,
    resolve_intervals_to_forecast,
)
from emhass.dynamic_tariffs.models import (
    DynamicTariffError,
    ExportSignPolicy,
    TimeBoundaryStrategy,
)


TZ = "Australia/Sydney"


def _forecast_dates(periods=3):
    return pd.date_range("2026-06-08 08:30:00", periods=periods, freq="5min", tz=TZ)


def _row(start, end, value=0.2, duration="5"):
    return {
        "start_time": start,
        "end_time": end,
        "duration": duration,
        "per_kwh": value,
    }


def _resolve(rows, dates=None, **kwargs):
    intervals = canonicalise_interval_rows(
        rows,
        time_zone=TZ,
        start_key="start_time",
        end_key="end_time",
        duration_key="duration",
        price_key="per_kwh",
        boundary_strategy=kwargs.pop(
            "boundary_strategy", TimeBoundaryStrategy.EXPLICIT_START_END
        ),
        export_sign_policy=kwargs.pop("export_sign_policy", ExportSignPolicy.SOURCE_SIGNED),
    )
    return resolve_intervals_to_forecast(
        intervals,
        dates if dates is not None else _forecast_dates(len(rows)),
        source_name="test",
    )


def test_end_minus_duration_ignores_amber_start_second_offset():
    rows = [_row("2026-06-08T08:35:01+10:00", "2026-06-08T08:40:00+10:00", 0.31)]

    values = _resolve(
        rows,
        pd.DatetimeIndex([pd.Timestamp("2026-06-08T08:35:00+10:00")]),
        boundary_strategy=TimeBoundaryStrategy.END_MINUS_DURATION,
    )

    assert values == [0.31]


def test_explicit_start_end_resolves_generic_rows():
    rows = [
        _row("2026-06-08T08:30:00+10:00", "2026-06-08T08:35:00+10:00", 0.11),
        _row("2026-06-08T08:35:00+10:00", "2026-06-08T08:40:00+10:00", 0.12),
    ]

    assert _resolve(rows, _forecast_dates(2)) == [0.11, 0.12]


def test_mixed_5_and_30_minute_intervals_expand_to_5_minute_steps():
    rows = [
        _row("2026-06-08T08:30:00+10:00", "2026-06-08T08:35:00+10:00", 0.1),
        _row("2026-06-08T08:35:00+10:00", "2026-06-08T09:05:00+10:00", 0.2, "30"),
    ]

    assert _resolve(rows, _forecast_dates(7)) == [0.1] + [0.2] * 6


@pytest.mark.parametrize(
    "rows,dates",
    [
        (
            [_row("2026-06-08T08:35:00+10:00", "2026-06-08T08:40:00+10:00", 0.2)],
            _forecast_dates(2),
        ),
        (
            [
                _row("2026-06-08T08:30:00+10:00", "2026-06-08T08:35:00+10:00", 0.2),
                _row("2026-06-08T08:40:00+10:00", "2026-06-08T08:45:00+10:00", 0.3),
            ],
            _forecast_dates(3),
        ),
        (
            [_row("2026-06-08T08:30:00+10:00", "2026-06-08T08:35:00+10:00", 0.2)],
            _forecast_dates(2),
        ),
    ],
)
def test_rejects_gap_at_first_middle_or_tail_timestep(rows, dates):
    with pytest.raises(DynamicTariffError):
        _resolve(rows, dates)


def test_rejects_overlapping_intervals():
    rows = [
        _row("2026-06-08T08:30:00+10:00", "2026-06-08T08:40:00+10:00", 0.2, "10"),
        _row("2026-06-08T08:35:00+10:00", "2026-06-08T08:45:00+10:00", 0.3, "10"),
    ]

    with pytest.raises(DynamicTariffError):
        canonicalise_interval_rows(
            rows,
            time_zone=TZ,
            start_key="start_time",
            end_key="end_time",
            duration_key="duration",
            price_key="per_kwh",
            boundary_strategy=TimeBoundaryStrategy.EXPLICIT_START_END,
        )


@pytest.mark.parametrize("bad_value", [None, "text", math.nan, math.inf])
def test_rejects_missing_or_non_numeric_price(bad_value):
    row = _row("2026-06-08T08:30:00+10:00", "2026-06-08T08:35:00+10:00", bad_value)

    with pytest.raises(DynamicTariffError):
        canonicalise_interval_rows(
            [row],
            time_zone=TZ,
            start_key="start_time",
            end_key="end_time",
            duration_key="duration",
            price_key="per_kwh",
            boundary_strategy=TimeBoundaryStrategy.EXPLICIT_START_END,
        )

    row.pop("per_kwh", None)
    with pytest.raises(DynamicTariffError):
        canonicalise_interval_rows(
            [row],
            time_zone=TZ,
            start_key="start_time",
            end_key="end_time",
            duration_key="duration",
            price_key="per_kwh",
            boundary_strategy=TimeBoundaryStrategy.EXPLICIT_START_END,
        )


def test_rejects_naive_timestamp():
    rows = [_row("2026-06-08 08:30:00", "2026-06-08T08:35:00+10:00", 0.2)]

    with pytest.raises(DynamicTariffError):
        _resolve(rows, _forecast_dates(1))


def test_export_sign_source_signed_keeps_value():
    rows = [_row("2026-06-08T08:30:00+10:00", "2026-06-08T08:35:00+10:00", 0.05)]

    assert _resolve(rows, _forecast_dates(1), export_sign_policy=ExportSignPolicy.SOURCE_SIGNED) == [
        0.05
    ]


def test_export_sign_invert_multiplies_by_minus_one():
    rows = [_row("2026-06-08T08:30:00+10:00", "2026-06-08T08:35:00+10:00", 0.05)]

    assert _resolve(rows, _forecast_dates(1), export_sign_policy=ExportSignPolicy.INVERT) == [
        -0.05
    ]
