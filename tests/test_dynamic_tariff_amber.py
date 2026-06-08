import asyncio

import pandas as pd
import pytest

from emhass.dynamic_tariffs.models import DynamicTariffError
from emhass.dynamic_tariffs.providers.amberelectric import (
    HomeAssistantAmberSensorsProvider,
    HomeAssistantAmberServiceProvider,
)


class FakeClient:
    def __init__(self, states=None, service_response=None):
        self.states = states or {}
        self.service_response = service_response or {}
        self.calls = []

    async def get_state(self, entity_id):
        return self.states[entity_id]

    async def call_service_response(self, domain, service, payload):
        self.calls.append((domain, service, payload))
        return self.service_response[payload["channel_type"]]


def _dates(periods=2):
    return pd.date_range("2026-06-08 08:30:00", periods=periods, freq="5min", tz="UTC")


def _amber_row(start, end, duration, per_kwh=0.2, advanced_price_predicted=None):
    row = {
        "start_time": start,
        "end_time": end,
        "duration": duration,
        "per_kwh": per_kwh,
    }
    if advanced_price_predicted is not None:
        row["advanced_price_predicted"] = advanced_price_predicted
    return row


def _state(rows):
    return {"attributes": {"forecasts": rows}}


def _optim_conf(**overrides):
    conf = {
        "dynamic_tariff_import_forecast_entity": "sensor.general",
        "dynamic_tariff_export_forecast_entity": "sensor.feed_in",
        "dynamic_tariff_forecast_attribute": "forecasts",
        "dynamic_tariff_start_key": "start_time",
        "dynamic_tariff_end_key": "end_time",
        "dynamic_tariff_duration_key": "duration",
        "dynamic_tariff_import_price_key": "per_kwh",
        "dynamic_tariff_export_price_key": "per_kwh",
        "dynamic_tariff_time_boundary_strategy": "explicit_start_end",
        "dynamic_tariff_export_sign": "invert",
        "dynamic_tariff_amber_config_entry_id": "abc123",
        "dynamic_tariff_import_channel_type": "general",
        "dynamic_tariff_export_channel_type": "feed_in",
    }
    conf.update(overrides)
    return conf


def test_amber_sensor_provider_uses_end_minus_duration():
    client = FakeClient(
        {
            "sensor.general": _state(
                [_amber_row("2026-06-08T08:30:01+00:00", "2026-06-08T08:35:00+00:00", 5, 0.4)]
            ),
            "sensor.feed_in": _state(
                [_amber_row("2026-06-08T08:30:01+00:00", "2026-06-08T08:35:00+00:00", 5, 0.1)]
            ),
        }
    )

    pair = asyncio.run(
        HomeAssistantAmberSensorsProvider().fetch_pair(
            client=client,
            optim_conf=_optim_conf(),
            retrieve_hass_conf={"time_zone": "UTC"},
            forecast_dates=_dates(1),
            logger=None,
        )
    )

    assert pair.load_cost_forecast == [0.4]


def test_amber_sensor_provider_keeps_feed_in_sign_from_home_assistant():
    client = FakeClient(
        {
            "sensor.general": _state(
                [_amber_row("2026-06-08T08:30:00+00:00", "2026-06-08T08:35:00+00:00", 5, 0.4)]
            ),
            "sensor.feed_in": _state(
                [_amber_row("2026-06-08T08:30:00+00:00", "2026-06-08T08:35:00+00:00", 5, 0.1)]
            ),
        }
    )

    pair = asyncio.run(
        HomeAssistantAmberSensorsProvider().fetch_pair(
            client=client,
            optim_conf=_optim_conf(dynamic_tariff_export_sign="invert"),
            retrieve_hass_conf={"time_zone": "UTC"},
            forecast_dates=_dates(1),
            logger=None,
        )
    )

    assert pair.prod_price_forecast == [0.1]


def test_amber_service_provider_calls_general_and_feed_in_channels():
    client = FakeClient(
        service_response={
            "general": {"forecasts": [_amber_row("2026-06-08T08:30:00+00:00", "2026-06-08T08:35:00+00:00", 5, 0.2)]},
            "feed_in": {"forecasts": [_amber_row("2026-06-08T08:30:00+00:00", "2026-06-08T08:35:00+00:00", 5, 0.05)]},
        }
    )

    pair = asyncio.run(
        HomeAssistantAmberServiceProvider().fetch_pair(
            client=client,
            optim_conf=_optim_conf(),
            retrieve_hass_conf={"time_zone": "UTC"},
            forecast_dates=_dates(1),
            logger=None,
        )
    )

    assert pair.load_cost_forecast == [0.2]
    assert pair.prod_price_forecast == [0.05]
    assert client.calls == [
        ("amberelectric", "get_forecasts", {"config_entry_id": "abc123", "channel_type": "general"}),
        ("amberelectric", "get_forecasts", {"config_entry_id": "abc123", "channel_type": "feed_in"}),
    ]


def test_amber_service_provider_can_use_advanced_price_predicted():
    client = FakeClient(
        service_response={
            "general": {
                "forecasts": [
                    _amber_row(
                        "2026-06-08T08:30:00+00:00",
                        "2026-06-08T08:35:00+00:00",
                        5,
                        0.2,
                        advanced_price_predicted=0.33,
                    )
                ]
            },
            "feed_in": {
                "forecasts": [
                    _amber_row(
                        "2026-06-08T08:30:00+00:00",
                        "2026-06-08T08:35:00+00:00",
                        5,
                        0.05,
                        advanced_price_predicted=0.07,
                    )
                ]
            },
        }
    )

    pair = asyncio.run(
        HomeAssistantAmberServiceProvider().fetch_pair(
            client=client,
            optim_conf=_optim_conf(
                dynamic_tariff_import_price_key="advanced_price_predicted",
                dynamic_tariff_export_price_key="advanced_price_predicted",
            ),
            retrieve_hass_conf={"time_zone": "UTC"},
            forecast_dates=_dates(1),
            logger=None,
        )
    )

    assert pair.load_cost_forecast == [0.33]
    assert pair.prod_price_forecast == [0.07]


def test_amber_provider_rejects_short_12_hour_forecast_for_24_hour_horizon():
    rows = [
        _amber_row(
            (pd.Timestamp("2026-06-08T00:00:00+00:00") + pd.Timedelta(minutes=30 * i)).isoformat(),
            (pd.Timestamp("2026-06-08T00:30:00+00:00") + pd.Timedelta(minutes=30 * i)).isoformat(),
            30,
            0.2,
        )
        for i in range(24)
    ]
    client = FakeClient({"sensor.general": _state(rows), "sensor.feed_in": _state(rows)})

    with pytest.raises(DynamicTariffError):
        asyncio.run(
            HomeAssistantAmberSensorsProvider().fetch_pair(
                client=client,
                optim_conf=_optim_conf(),
                retrieve_hass_conf={"time_zone": "UTC"},
                forecast_dates=pd.date_range("2026-06-08", periods=48, freq="30min", tz="UTC"),
                logger=None,
            )
        )
