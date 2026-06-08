from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from emhass.dynamic_tariffs.ha_client import HomeAssistantTariffClient
from emhass.dynamic_tariffs.models import DynamicTariffError
from emhass.dynamic_tariffs.providers import PROVIDERS


def _logger(logger):
    return logger or logging.getLogger(__name__)


# Map user-facing forecast method values to dynamic tariff provider source names.
# Selecting one of these values for load_cost_forecast_method /
# production_price_forecast_method activates the matching provider. Add a new
# entry here (plus a provider in PROVIDERS) to expose another tariff source.
DYNAMIC_TARIFF_METHODS: dict[str, str] = {
    "amber": "home_assistant_amber_sensors",
    "ha_entity": "home_assistant_forecast_entities",
}


def _selected_dynamic_source(optim_conf: dict[str, Any]) -> tuple[str | None, str | None]:
    import_source = DYNAMIC_TARIFF_METHODS.get(optim_conf.get("load_cost_forecast_method"))
    export_source = DYNAMIC_TARIFF_METHODS.get(optim_conf.get("production_price_forecast_method"))
    return import_source, export_source


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
    import_source, export_source = _selected_dynamic_source(optim_conf)
    if import_source is None and export_source is None:
        return True
    if import_source != export_source:
        log.error(
            "Dynamic tariff pricing requires both load_cost_forecast_method and "
            "production_price_forecast_method to be set to the same dynamic value "
            "(e.g. 'amber'); got import=%r export=%r.",
            optim_conf.get("load_cost_forecast_method"),
            optim_conf.get("production_price_forecast_method"),
        )
        return False
    source = import_source

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
