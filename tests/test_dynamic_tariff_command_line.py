import asyncio
import copy
import logging
import pathlib

import orjson
import pytest

from emhass import utils
from emhass.command_line import OptimizationCache, set_input_data_dict


root = pathlib.Path(utils.get_root(__file__, num_parent=2))
emhass_conf = {
    "data_path": root / "data/",
    "root_path": root / "src/emhass/",
    "defaults_path": root / "src/emhass/data/config_defaults.json",
    "associations_path": root / "src/emhass/data/associations.csv",
}
logger = logging.getLogger(__name__)


@pytest.fixture
def base_params():
    async def _build():
        config = await utils.build_config(emhass_conf, logger, emhass_conf["defaults_path"])
        _, secrets = await utils.build_secrets(emhass_conf, logger, no_response=True)
        return await utils.build_params(emhass_conf, secrets, config, logger)

    params = asyncio.run(_build())
    params["optim_conf"]["set_use_pv"] = False
    params["optim_conf"]["weather_forecast_method"] = "list"
    params["optim_conf"]["load_forecast_method"] = "list"
    params["optim_conf"]["load_cost_forecast_method"] = "amber"
    params["optim_conf"]["production_price_forecast_method"] = "amber"
    return params


def _runtime(include_tariffs=False):
    runtime = {
        "pv_power_forecast": [0] * 4,
        "load_power_forecast": [1000] * 4,
        "prediction_horizon": 4,
    }
    if include_tariffs:
        runtime["load_cost_forecast"] = [0.1] * 4
        runtime["prod_price_forecast"] = [0.02] * 4
    return runtime


def test_set_input_data_dict_prepares_dynamic_tariffs_before_cache_key(
    monkeypatch, base_params
):
    captured = {}

    async def fake_prepare(**kwargs):
        kwargs["params"]["passed_data"]["load_cost_forecast"] = [0.2] * len(
            kwargs["forecast_dates"]
        )
        kwargs["params"]["passed_data"]["prod_price_forecast"] = [0.05] * len(
            kwargs["forecast_dates"]
        )
        kwargs["optim_conf"]["load_cost_forecast_method"] = "list"
        kwargs["optim_conf"]["production_price_forecast_method"] = "list"
        return True

    original_get = OptimizationCache.get

    def capture_get(optim_conf, *args, **kwargs):
        captured["load_method"] = optim_conf["load_cost_forecast_method"]
        captured["prod_method"] = optim_conf["production_price_forecast_method"]
        return original_get(optim_conf, *args, **kwargs)

    monkeypatch.setattr("emhass.command_line.prepare_dynamic_tariffs", fake_prepare)
    monkeypatch.setattr(OptimizationCache, "get", capture_get)
    OptimizationCache.clear()

    result = asyncio.run(
        set_input_data_dict(
            emhass_conf,
            "profit",
            orjson.dumps(base_params).decode(),
            orjson.dumps(_runtime()).decode(),
            "naive-mpc-optim",
            logger,
            get_data_from_file=True,
        )
    )

    assert result
    assert captured == {"load_method": "list", "prod_method": "list"}


def test_set_input_data_dict_returns_false_when_dynamic_tariff_coverage_missing(
    monkeypatch, base_params
):
    async def fake_prepare(**kwargs):
        return False

    def fail_get(*args, **kwargs):
        raise AssertionError("OptimizationCache.get should not run")

    monkeypatch.setattr("emhass.command_line.prepare_dynamic_tariffs", fake_prepare)
    monkeypatch.setattr(OptimizationCache, "get", fail_get)

    result = asyncio.run(
        set_input_data_dict(
            emhass_conf,
            "profit",
            orjson.dumps(base_params).decode(),
            orjson.dumps(_runtime()).decode(),
            "naive-mpc-optim",
            logger,
            get_data_from_file=True,
        )
    )

    assert result is False


def test_set_input_data_dict_does_not_call_provider_when_runtime_tariffs_present(
    monkeypatch, base_params
):
    calls = 0

    async def fake_prepare(**kwargs):
        nonlocal calls
        calls += 1
        return True

    monkeypatch.setattr("emhass.command_line.prepare_dynamic_tariffs", fake_prepare)
    params = copy.deepcopy(base_params)
    runtime = _runtime(include_tariffs=True)
    result = asyncio.run(
        set_input_data_dict(
            emhass_conf,
            "profit",
            orjson.dumps(params).decode(),
            orjson.dumps(runtime).decode(),
            "naive-mpc-optim",
            logger,
            get_data_from_file=True,
        )
    )

    assert result
    assert calls == 1
    assert result["params"]["passed_data"]["load_cost_forecast"] == [0.1] * 4


def test_publish_data_action_does_not_require_dynamic_tariffs(monkeypatch, base_params):
    async def fail_prepare(**kwargs):
        raise AssertionError("dynamic tariff preparation should not run")

    monkeypatch.setattr("emhass.command_line.prepare_dynamic_tariffs", fail_prepare)

    result = asyncio.run(
        set_input_data_dict(
            emhass_conf,
            "profit",
            orjson.dumps(base_params).decode(),
            orjson.dumps({}).decode(),
            "publish-data",
            logger,
            get_data_from_file=True,
        )
    )

    assert result
    assert result["fcst"] is None
    assert result["opt"] is None
