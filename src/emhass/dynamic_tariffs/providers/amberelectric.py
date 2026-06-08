from __future__ import annotations

from typing import Any

import pandas as pd

from emhass.dynamic_tariffs.intervals import (
    canonicalise_interval_rows,
    resolve_intervals_to_forecast,
)
from emhass.dynamic_tariffs.models import DynamicTariffError, DynamicTariffPair, ExportSignPolicy
from emhass.dynamic_tariffs.providers.home_assistant_entities import (
    HomeAssistantForecastEntitiesProvider,
    _provider_conf,
    _time_zone,
)
from emhass.dynamic_tariffs.models import TimeBoundaryStrategy


class HomeAssistantAmberSensorsProvider(HomeAssistantForecastEntitiesProvider):
    source_name = "home_assistant_amber_sensors"

    async def fetch_pair(
        self,
        *,
        client,
        optim_conf: dict[str, Any],
        retrieve_hass_conf: dict[str, Any],
        forecast_dates: pd.DatetimeIndex,
        logger,
    ) -> DynamicTariffPair:
        conf = _provider_conf(
            optim_conf,
            boundary_strategy=TimeBoundaryStrategy.END_MINUS_DURATION.value,
            export_sign=ExportSignPolicy.SOURCE_SIGNED.value,
            import_price_key=optim_conf.get("dynamic_tariff_import_price_key") or "per_kwh",
            export_price_key=optim_conf.get("dynamic_tariff_export_price_key") or "per_kwh",
        )
        return await super().fetch_pair(
            client=client,
            optim_conf=conf,
            retrieve_hass_conf=retrieve_hass_conf,
            forecast_dates=forecast_dates,
            logger=logger,
        )


class HomeAssistantAmberServiceProvider:
    source_name = "home_assistant_amber_service"

    async def fetch_pair(
        self,
        *,
        client,
        optim_conf: dict[str, Any],
        retrieve_hass_conf: dict[str, Any],
        forecast_dates: pd.DatetimeIndex,
        logger,
    ) -> DynamicTariffPair:
        conf = _provider_conf(
            optim_conf,
            boundary_strategy=TimeBoundaryStrategy.END_MINUS_DURATION.value,
            export_sign=ExportSignPolicy.SOURCE_SIGNED.value,
            import_price_key=optim_conf.get("dynamic_tariff_import_price_key") or "per_kwh",
            export_price_key=optim_conf.get("dynamic_tariff_export_price_key") or "per_kwh",
        )
        import_rows = await self._fetch_channel(
            client, conf, conf.get("dynamic_tariff_import_channel_type") or "general"
        )
        export_rows = await self._fetch_channel(
            client, conf, conf.get("dynamic_tariff_export_channel_type") or "feed_in"
        )
        return self._resolve_rows(import_rows, export_rows, conf, retrieve_hass_conf, forecast_dates)

    async def _fetch_channel(self, client, optim_conf: dict[str, Any], channel_type: str):
        payload = {"channel_type": channel_type}
        config_entry_id = optim_conf.get("dynamic_tariff_amber_config_entry_id")
        if config_entry_id:
            payload["config_entry_id"] = config_entry_id
        response = await client.call_service_response(
            "amberelectric", "get_forecasts", payload
        )
        return self._extract_rows(response, channel_type)

    def _extract_rows(self, response: Any, channel_type: str) -> list[dict[str, Any]]:
        if isinstance(response, list):
            return response
        if not isinstance(response, dict):
            raise DynamicTariffError("Amber forecast service returned an invalid response")
        for key in ("forecasts", "service_response", "response"):
            if key in response:
                return self._extract_rows(response[key], channel_type)
        if channel_type in response:
            return self._extract_rows(response[channel_type], channel_type)
        raise DynamicTariffError("Amber forecast service response did not contain forecasts")

    def _resolve_rows(
        self,
        import_rows: list[dict[str, Any]],
        export_rows: list[dict[str, Any]],
        optim_conf: dict[str, Any],
        retrieve_hass_conf: dict[str, Any],
        forecast_dates: pd.DatetimeIndex,
    ) -> DynamicTariffPair:
        time_zone = _time_zone(retrieve_hass_conf)
        import_intervals = canonicalise_interval_rows(
            import_rows,
            time_zone=time_zone,
            start_key=optim_conf["dynamic_tariff_start_key"],
            end_key=optim_conf["dynamic_tariff_end_key"],
            duration_key=optim_conf["dynamic_tariff_duration_key"],
            price_key=optim_conf["dynamic_tariff_import_price_key"],
            boundary_strategy=TimeBoundaryStrategy.END_MINUS_DURATION,
        )
        export_intervals = canonicalise_interval_rows(
            export_rows,
            time_zone=time_zone,
            start_key=optim_conf["dynamic_tariff_start_key"],
            end_key=optim_conf["dynamic_tariff_end_key"],
            duration_key=optim_conf["dynamic_tariff_duration_key"],
            price_key=optim_conf["dynamic_tariff_export_price_key"],
            boundary_strategy=TimeBoundaryStrategy.END_MINUS_DURATION,
            export_sign_policy=ExportSignPolicy.SOURCE_SIGNED,
        )
        return DynamicTariffPair(
            load_cost_forecast=resolve_intervals_to_forecast(
                import_intervals, forecast_dates, source_name=f"{self.source_name} import"
            ),
            prod_price_forecast=resolve_intervals_to_forecast(
                export_intervals, forecast_dates, source_name=f"{self.source_name} export"
            ),
            import_source=optim_conf.get("dynamic_tariff_import_channel_type", "general"),
            export_source=optim_conf.get("dynamic_tariff_export_channel_type", "feed_in"),
        )
