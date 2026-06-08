import asyncio
import logging
import pathlib

import numpy as np
import orjson
import pandas as pd
import pytest

from emhass import utils
from emhass.command_line import (
    OptimizationCache,
    dayahead_forecast_optim,
    naive_mpc_optim,
    set_input_data_dict,
)
from emhass.dynamic_tariffs.ha_client import HomeAssistantTariffClient


root = pathlib.Path(utils.get_root(__file__, num_parent=2))
emhass_conf = {
    "data_path": root / "data/",
    "root_path": root / "src/emhass/",
    "defaults_path": root / "src/emhass/data/config_defaults.json",
    "associations_path": root / "src/emhass/data/associations.csv",
}
logger = logging.getLogger(__name__)


class FakeDynamicClient(HomeAssistantTariffClient):
    states = {}

    def __init__(self, *args, **kwargs):
        pass

    async def get_state(self, entity_id):
        return self.states[entity_id]


def _rows(values, start):
    return [
        {
            "start_time": (start + pd.Timedelta(minutes=5 * i)).isoformat(),
            "end_time": (start + pd.Timedelta(minutes=5 * (i + 1))).isoformat(),
            "duration": 5,
            "per_kwh": value,
        }
        for i, value in enumerate(values)
    ]


@pytest.fixture
def base_params():
    async def _build():
        config = await utils.build_config(emhass_conf, logger, emhass_conf["defaults_path"])
        _, secrets = await utils.build_secrets(emhass_conf, logger, no_response=True)
        return await utils.build_params(emhass_conf, secrets, config, logger)

    params = asyncio.run(_build())
    params["retrieve_hass_conf"]["optimization_time_step"] = 5
    params["retrieve_hass_conf"]["time_zone"] = "UTC"
    params["retrieve_hass_conf"]["method_ts_round"] = "first"
    params["optim_conf"].update(
        {
            "set_use_pv": False,
            "set_use_battery": False,
            "weather_forecast_method": "list",
            "load_forecast_method": "list",
            "load_cost_forecast_method": "amber",
            "production_price_forecast_method": "amber",
            "dynamic_tariff_import_forecast_entity": "sensor.import",
            "dynamic_tariff_export_forecast_entity": "sensor.export",
        }
    )
    params["plant_conf"]["maximum_power_from_grid"] = 9000
    params["plant_conf"]["maximum_power_to_grid"] = 9000
    return params


def _runtime(horizon):
    full_day_steps = 288
    return {
        "pv_power_forecast": [0] * full_day_steps,
        "load_power_forecast": [500] * full_day_steps,
        "prediction_horizon": horizon,
        "soc_init": 0.5,
        "soc_final": 0.5,
    }


def _full_day_prices(prices):
    full_day_steps = 288
    return list(prices) + [prices[-1]] * (full_day_steps - len(prices))


async def _setup_async(monkeypatch, params, import_prices, export_prices, action="dayahead-optim"):
    start = pd.Timestamp.now(tz="UTC").replace(microsecond=0).floor("5min")
    FakeDynamicClient.states = {
        "sensor.import": {"attributes": {"forecasts": _rows(_full_day_prices(import_prices), start)}},
        "sensor.export": {"attributes": {"forecasts": _rows(_full_day_prices(export_prices), start)}},
    }
    monkeypatch.setattr("emhass.dynamic_tariffs.service.HomeAssistantTariffClient", FakeDynamicClient)
    monkeypatch.setattr("emhass.dynamic_tariffs.ha_client.HomeAssistantTariffClient", FakeDynamicClient)
    OptimizationCache.clear()
    return await set_input_data_dict(
        emhass_conf,
        "profit",
        orjson.dumps(params, default=str).decode(),
        orjson.dumps(_runtime(len(import_prices))).decode(),
        action,
        logger,
        get_data_from_file=True,
    )


def _setup(monkeypatch, params, import_prices, export_prices, action="dayahead-optim"):
    return asyncio.run(_setup_async(monkeypatch, params, import_prices, export_prices, action))


def test_dayahead_price_prep_uses_dynamic_tariff_lists_in_unit_columns(
    monkeypatch, base_params
):
    import_prices = [0.2, 0.3, 0.4, 0.5]
    export_prices = [0.05, 0.06, 0.07, 0.08]
    input_data = _setup(monkeypatch, base_params, import_prices, export_prices)
    df = input_data["fcst"].get_load_cost_forecast(
        input_data["df_input_data_dayahead"],
        method=input_data["optim_conf"]["load_cost_forecast_method"],
    )
    df = input_data["fcst"].get_prod_price_forecast(
        df,
        method=input_data["optim_conf"]["production_price_forecast_method"],
    )

    assert df["unit_load_cost"].iloc[:4].tolist() == import_prices
    assert df["unit_prod_price"].iloc[:4].tolist() == export_prices


def test_naive_mpc_price_prep_uses_dynamic_tariff_lists_in_unit_columns(
    monkeypatch, base_params
):
    import_prices = [0.21, 0.22, 0.23, 0.24]
    export_prices = [0.01, 0.02, 0.03, 0.04]
    input_data = _setup(
        monkeypatch, base_params, import_prices, export_prices, action="naive-mpc-optim"
    )
    df = input_data["fcst"].get_load_cost_forecast(
        input_data["df_input_data_dayahead"],
        method=input_data["optim_conf"]["load_cost_forecast_method"],
    )
    df = input_data["fcst"].get_prod_price_forecast(
        df,
        method=input_data["optim_conf"]["production_price_forecast_method"],
    )

    assert df["unit_load_cost"].tolist() == import_prices
    assert df["unit_prod_price"].tolist() == export_prices


def test_dayahead_optimization_result_changes_when_dynamic_prices_change(
    monkeypatch, base_params
):
    input_a = _setup(monkeypatch, base_params, [0.1] * 4, [0.01] * 4)
    result_a = asyncio.run(dayahead_forecast_optim(input_a, logger, debug=True))
    input_b = _setup(monkeypatch, base_params, [0.5] * 4, [0.01] * 4)
    result_b = asyncio.run(dayahead_forecast_optim(input_b, logger, debug=True))

    assert result_a["unit_load_cost"].tolist() != result_b["unit_load_cost"].tolist()


def test_short_provider_horizon_blocks_optimization_before_solver(monkeypatch, base_params):
    params = base_params
    start = pd.Timestamp.now(tz="UTC").replace(microsecond=0).floor("5min")
    FakeDynamicClient.states = {
        "sensor.import": {"attributes": {"forecasts": _rows([0.2, 0.3], start)}},
        "sensor.export": {"attributes": {"forecasts": _rows([0.05, 0.06], start)}},
    }
    monkeypatch.setattr("emhass.dynamic_tariffs.service.HomeAssistantTariffClient", FakeDynamicClient)

    result = asyncio.run(
        set_input_data_dict(
            emhass_conf,
            "profit",
            orjson.dumps(params, default=str).decode(),
            orjson.dumps(_runtime(4)).decode(),
            "dayahead-optim",
            logger,
            get_data_from_file=True,
        )
    )

    assert result is False


def test_negative_export_prices_are_preserved_as_export_penalties(monkeypatch, base_params):
    export_prices = [-0.01, -0.02, -0.03, -0.04]
    input_data = _setup(monkeypatch, base_params, [0.2] * 4, export_prices)
    df = input_data["fcst"].get_load_cost_forecast(
        input_data["df_input_data_dayahead"],
        method=input_data["optim_conf"]["load_cost_forecast_method"],
    )
    df = input_data["fcst"].get_prod_price_forecast(
        df,
        method=input_data["optim_conf"]["production_price_forecast_method"],
    )

    assert np.allclose(df["unit_prod_price"].iloc[:4].to_numpy(), export_prices)
