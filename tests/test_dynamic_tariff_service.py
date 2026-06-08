import asyncio
import logging

import pandas as pd
import pytest

from emhass.dynamic_tariffs import service as dynamic_service
from emhass.dynamic_tariffs.models import DynamicTariffPair
from emhass.dynamic_tariffs.service import prepare_dynamic_tariffs


class FakeProvider:
    source_name = "fake"
    calls = 0
    pair = DynamicTariffPair([0.2, 0.3], [0.05, 0.06], "import", "export")

    async def fetch_pair(self, **kwargs):
        type(self).calls += 1
        return self.pair


class LengthMismatchProvider(FakeProvider):
    pair = DynamicTariffPair([0.2], [0.05, 0.06], "import", "export")


@pytest.fixture(autouse=True)
def _reset_fake_provider(monkeypatch, caplog):
    FakeProvider.calls = 0
    # Register a fake dynamic method that maps to the fake provider source.
    monkeypatch.setitem(dynamic_service.DYNAMIC_TARIFF_METHODS, "faketariff", "fake")
    # Force the emhass logger to emit records so caplog can capture them. Other
    # test modules (e.g. test_web_server) set the "emhass" logger to CRITICAL at
    # import time, which would otherwise suppress the service's error logs.
    caplog.set_level(logging.DEBUG, logger="emhass")


def _params(load=None, prod=None):
    return {"passed_data": {"load_cost_forecast": load, "prod_price_forecast": prod}}


def _optim_conf(method="faketariff"):
    return {
        "load_cost_forecast_method": method,
        "production_price_forecast_method": method,
    }


def _dates():
    return pd.date_range("2026-06-08", periods=2, freq="5min", tz="UTC")


def test_none_source_leaves_existing_methods_unchanged(monkeypatch):
    monkeypatch.setitem(dynamic_service.PROVIDERS, "fake", FakeProvider)
    optim_conf = {
        "load_cost_forecast_method": "hp_hc_periods",
        "production_price_forecast_method": "constant",
    }

    result = asyncio.run(
        prepare_dynamic_tariffs(
            params=_params(),
            retrieve_hass_conf={"hass_url": "http://ha", "long_lived_token": "token"},
            optim_conf=optim_conf,
            forecast_dates=_dates(),
            logger=None,
        )
    )

    assert result is True
    assert optim_conf["load_cost_forecast_method"] == "hp_hc_periods"
    assert FakeProvider.calls == 0


def test_runtime_lists_win_when_both_tariff_lists_are_supplied(monkeypatch):
    monkeypatch.setitem(dynamic_service.PROVIDERS, "fake", FakeProvider)
    params = _params(load=[1, 2], prod=[3, 4])

    result = asyncio.run(
        prepare_dynamic_tariffs(
            params=params,
            retrieve_hass_conf={"hass_url": "http://ha", "long_lived_token": "token"},
            optim_conf=_optim_conf(),
            forecast_dates=_dates(),
            logger=None,
        )
    )

    assert result is True
    assert params["passed_data"]["load_cost_forecast"] == [1, 2]
    assert FakeProvider.calls == 0


def test_one_sided_runtime_tariff_conflict_fails(caplog):
    result = asyncio.run(
        prepare_dynamic_tariffs(
            params=_params(load=[1, 2], prod=None),
            retrieve_hass_conf={"hass_url": "http://ha", "long_lived_token": "token"},
            optim_conf=_optim_conf(),
            forecast_dates=_dates(),
            logger=None,
        )
    )

    assert result is False
    assert "both load_cost_forecast and prod_price_forecast" in caplog.text


def test_unknown_provider_fails(monkeypatch, caplog):
    # Map the fake method to a source name that is not registered in PROVIDERS.
    monkeypatch.setitem(dynamic_service.DYNAMIC_TARIFF_METHODS, "faketariff", "missing")
    result = asyncio.run(
        prepare_dynamic_tariffs(
            params=_params(),
            retrieve_hass_conf={"hass_url": "http://ha", "long_lived_token": "token"},
            optim_conf=_optim_conf(),
            forecast_dates=_dates(),
            logger=None,
        )
    )

    assert result is False
    assert "Unknown dynamic tariff source" in caplog.text


def test_mismatched_dynamic_methods_fail(monkeypatch, caplog):
    monkeypatch.setitem(dynamic_service.PROVIDERS, "fake", FakeProvider)
    optim_conf = {
        "load_cost_forecast_method": "faketariff",
        "production_price_forecast_method": "constant",
    }
    result = asyncio.run(
        prepare_dynamic_tariffs(
            params=_params(),
            retrieve_hass_conf={"hass_url": "http://ha", "long_lived_token": "token"},
            optim_conf=optim_conf,
            forecast_dates=_dates(),
            logger=None,
        )
    )

    assert result is False
    assert "same dynamic value" in caplog.text
    assert FakeProvider.calls == 0


def test_provider_pair_is_injected_as_list_methods(monkeypatch):
    monkeypatch.setitem(dynamic_service.PROVIDERS, "fake", FakeProvider)
    params = _params()
    optim_conf = _optim_conf()

    result = asyncio.run(
        prepare_dynamic_tariffs(
            params=params,
            retrieve_hass_conf={"hass_url": "http://ha", "long_lived_token": "token"},
            optim_conf=optim_conf,
            forecast_dates=_dates(),
            logger=None,
        )
    )

    assert result is True
    assert params["passed_data"]["load_cost_forecast"] == [0.2, 0.3]
    assert params["passed_data"]["prod_price_forecast"] == [0.05, 0.06]
    assert optim_conf["load_cost_forecast_method"] == "list"
    assert optim_conf["production_price_forecast_method"] == "list"


def test_provider_length_mismatch_fails(monkeypatch):
    monkeypatch.setitem(dynamic_service.PROVIDERS, "fake", LengthMismatchProvider)
    params = _params()

    result = asyncio.run(
        prepare_dynamic_tariffs(
            params=params,
            retrieve_hass_conf={"hass_url": "http://ha", "long_lived_token": "token"},
            optim_conf=_optim_conf(),
            forecast_dates=_dates(),
            logger=None,
        )
    )

    assert result is False
    assert params["passed_data"]["load_cost_forecast"] is None
    assert params["passed_data"]["prod_price_forecast"] is None


def test_every_dynamic_method_maps_to_a_registered_provider():
    """The generic contract: each real user-facing dynamic method must resolve
    to a registered provider, so a method can never dangle without a provider.
    """
    from emhass.dynamic_tariffs.providers import PROVIDERS as REGISTERED

    for method in ("amber", "ha_entity"):
        source_name = dynamic_service.DYNAMIC_TARIFF_METHODS[method]
        assert source_name in REGISTERED, (
            f"dynamic method {method!r} maps to unregistered provider {source_name!r}"
        )


def test_generic_ha_entity_method_is_exposed():
    """A non-Amber user must be able to reach the provider-neutral entity provider."""
    assert dynamic_service.DYNAMIC_TARIFF_METHODS.get("ha_entity") == (
        "home_assistant_forecast_entities"
    )

