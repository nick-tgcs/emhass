from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from emhass.dynamic_tariffs.ha_client import HomeAssistantTariffClient
from emhass.dynamic_tariffs.models import DynamicTariffError
from emhass.dynamic_tariffs.providers import PROVIDERS


def _logger(logger):
    return logger or logging.getLogger(__name__)


def _runtime_tariff_state(params: dict[str, Any]) -> tuple[bool, bool]:
    passed_data = params.setdefault("passed_data", {})
    return (
        passed_data.get("load_cost_forecast") is not None,
        passed_data.get("prod_price_forecast") is not None,
    )


async def prepare_dynamic_tariffs(
    *,
    params: dict[str, Any],
    retrieve_hass_conf: dict[str, Any],
    optim_conf: dict[str, Any],
    forecast_dates: pd.DatetimeIndex,
    logger,
    client: HomeAssistantTariffClient | None = None,
) -> bool:
    """Fetch configured dynamic tariffs and inject them into EMHASS list forecasts."""
    log = _logger(logger)
    source = optim_conf.get("dynamic_tariff_source", "none") or "none"
    if source == "none":
        return True

    has_load, has_prod = _runtime_tariff_state(params)
    if has_load and has_prod:
        log.debug("Runtime tariff lists supplied; dynamic tariff provider is skipped")
        return True
    if has_load != has_prod:
        log.error(
            "Dynamic tariff source %s is configured, so runtime params must provide "
            "both load_cost_forecast and prod_price_forecast or neither.",
            source,
        )
        return False

    provider_type = PROVIDERS.get(source)
    if provider_type is None:
        log.error("Unknown dynamic tariff source: %s", source)
        return False

    if client is None:
        hass_url = retrieve_hass_conf.get("hass_url")
        token = retrieve_hass_conf.get("long_lived_token")
        if not hass_url or not token:
            log.error("Home Assistant URL and long-lived token are required for dynamic tariffs")
            return False
        client = HomeAssistantTariffClient(hass_url, token, log)

    try:
        pair = await provider_type().fetch_pair(
            client=client,
            optim_conf=optim_conf,
            retrieve_hass_conf=retrieve_hass_conf,
            forecast_dates=forecast_dates,
            logger=log,
        )
    except DynamicTariffError as exc:
        log.error("Dynamic tariff preparation failed: %s", exc)
        return False
    except Exception as exc:  # noqa: BLE001
        log.exception("Dynamic tariff preparation failed unexpectedly: %s", exc)
        return False

    expected = len(forecast_dates)
    if len(pair.load_cost_forecast) != expected or len(pair.prod_price_forecast) != expected:
        log.error(
            "Dynamic tariff provider %s returned lengths import=%d export=%d, expected %d",
            source,
            len(pair.load_cost_forecast),
            len(pair.prod_price_forecast),
            expected,
        )
        return False

    passed_data = params.setdefault("passed_data", {})
    passed_data["load_cost_forecast"] = list(pair.load_cost_forecast)
    passed_data["prod_price_forecast"] = list(pair.prod_price_forecast)
    optim_conf["load_cost_forecast_method"] = "list"
    optim_conf["production_price_forecast_method"] = "list"
    log.info(
        "Dynamic tariff provider %s prepared %d import/export price steps",
        source,
        expected,
    )
    return True
