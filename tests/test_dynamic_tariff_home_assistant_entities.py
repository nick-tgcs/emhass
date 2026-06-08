import asyncio

import pandas as pd
import pytest

from emhass.dynamic_tariffs.models import DynamicTariffError
from emhass.dynamic_tariffs.providers.home_assistant_entities import (
    HomeAssistantForecastEntitiesProvider,
)


class FakeClient:
    def __init__(self, states):
        self.states = states
        self.requested = []

    async def get_state(self, entity_id):
        self.requested.append(entity_id)
        return self.states[entity_id]


def _dates(periods=2):
    return pd.date_range("2026-06-08 08:30:00", periods=periods, freq="5min", tz="UTC")


def _state(rows, attr="prices"):
    return {"entity_id": "sensor.x", "attributes": {attr: rows}}


def _row(start, end, price):
    return {"from": start, "to": end, "price": price}


def _optim_conf(**overrides):
    conf = {
        "dynamic_tariff_import_forecast_entity": "sensor.import",
        "dynamic_tariff_export_forecast_entity": "sensor.export",
        "dynamic_tariff_forecast_attribute": "prices",
        "dynamic_tariff_start_key": "from",
        "dynamic_tariff_end_key": "to",
        "dynamic_tariff_duration_key": "duration",
        "dynamic_tariff_import_price_key": "price",
        "dynamic_tariff_export_price_key": "price",
        "dynamic_tariff_time_boundary_strategy": "explicit_start_end",
        "dynamic_tariff_export_sign": "source_signed",
    }
    conf.update(overrides)
    return conf


def test_generic_entity_provider_reads_configured_attribute_and_keys():
    client = FakeClient(
        {
            "sensor.import": _state(
                [
                    _row("2026-06-08T08:30:00+00:00", "2026-06-08T08:35:00+00:00", 0.2),
                    _row("2026-06-08T08:35:00+00:00", "2026-06-08T08:40:00+00:00", 0.3),
                ]
            ),
            "sensor.export": _state(
                [
                    _row("2026-06-08T08:30:00+00:00", "2026-06-08T08:35:00+00:00", 0.05),
                    _row("2026-06-08T08:35:00+00:00", "2026-06-08T08:40:00+00:00", 0.06),
                ]
            ),
        }
    )

    pair = asyncio.run(
        HomeAssistantForecastEntitiesProvider().fetch_pair(
            client=client,
            optim_conf=_optim_conf(),
            retrieve_hass_conf={"time_zone": "UTC"},
            forecast_dates=_dates(2),
            logger=None,
        )
    )

    assert client.requested == ["sensor.import", "sensor.export"]
    assert pair.load_cost_forecast == [0.2, 0.3]
    assert pair.prod_price_forecast == [0.05, 0.06]


def test_generic_entity_provider_rejects_missing_forecasts_attribute():
    client = FakeClient(
        {
            "sensor.import": {"attributes": {}},
            "sensor.export": _state([]),
        }
    )

    with pytest.raises(DynamicTariffError):
        asyncio.run(
            HomeAssistantForecastEntitiesProvider().fetch_pair(
                client=client,
                optim_conf=_optim_conf(),
                retrieve_hass_conf={"time_zone": "UTC"},
                forecast_dates=_dates(1),
                logger=None,
            )
        )


def test_generic_entity_provider_applies_export_sign_policy():
    states = {
        "sensor.import": _state(
            [_row("2026-06-08T08:30:00+00:00", "2026-06-08T08:35:00+00:00", 0.2)]
        ),
        "sensor.export": _state(
            [_row("2026-06-08T08:30:00+00:00", "2026-06-08T08:35:00+00:00", 0.05)]
        ),
    }

    source_signed = asyncio.run(
        HomeAssistantForecastEntitiesProvider().fetch_pair(
            client=FakeClient(states),
            optim_conf=_optim_conf(dynamic_tariff_export_sign="source_signed"),
            retrieve_hass_conf={"time_zone": "UTC"},
            forecast_dates=_dates(1),
            logger=None,
        )
    )
    inverted = asyncio.run(
        HomeAssistantForecastEntitiesProvider().fetch_pair(
            client=FakeClient(states),
            optim_conf=_optim_conf(dynamic_tariff_export_sign="invert"),
            retrieve_hass_conf={"time_zone": "UTC"},
            forecast_dates=_dates(1),
            logger=None,
        )
    )

    assert source_signed.prod_price_forecast == [0.05]
    assert inverted.prod_price_forecast == [-0.05]
