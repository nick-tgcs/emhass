from __future__ import annotations

from typing import Any

import pandas as pd

from emhass.dynamic_tariffs.intervals import (
    canonicalise_interval_rows,
    resolve_intervals_to_forecast,
)
from emhass.dynamic_tariffs.models import (
    DynamicTariffError,
    DynamicTariffPair,
    ExportSignPolicy,
    TimeBoundaryStrategy,
)


def _time_zone(retrieve_hass_conf: dict[str, Any]) -> str:
    time_zone = retrieve_hass_conf.get("time_zone")
    if time_zone is None:
        raise DynamicTariffError("Home Assistant time_zone is required for dynamic tariffs")
    return str(time_zone)


def _rows_from_state(state: dict[str, Any], attribute: str, source_name: str) -> list[dict[str, Any]]:
    try:
        rows = state["attributes"][attribute]
    except KeyError as exc:
        raise DynamicTariffError(
            f"{source_name} forecast entity is missing attribute {attribute!r}"
        ) from exc
    if not isinstance(rows, list):
        raise DynamicTariffError(
            f"{source_name} forecast attribute {attribute!r} must be a list"
        )
    return rows


def _provider_conf(
    optim_conf: dict[str, Any],
    *,
    boundary_strategy: str | None = None,
    export_sign: str | None = None,
    import_price_key: str | None = None,
    export_price_key: str | None = None,
) -> dict[str, Any]:
    conf = dict(optim_conf)
    conf.setdefault("dynamic_tariff_forecast_attribute", "forecasts")
    conf.setdefault("dynamic_tariff_start_key", "start_time")
    conf.setdefault("dynamic_tariff_end_key", "end_time")
    conf.setdefault("dynamic_tariff_duration_key", "duration")
    conf.setdefault("dynamic_tariff_import_price_key", "per_kwh")
    conf.setdefault("dynamic_tariff_export_price_key", "per_kwh")
    conf.setdefault("dynamic_tariff_time_boundary_strategy", "explicit_start_end")
    conf.setdefault("dynamic_tariff_export_sign", "source_signed")
    if boundary_strategy is not None:
        conf["dynamic_tariff_time_boundary_strategy"] = boundary_strategy
    if export_sign is not None:
        conf["dynamic_tariff_export_sign"] = export_sign
    if import_price_key is not None:
        conf["dynamic_tariff_import_price_key"] = import_price_key
    if export_price_key is not None:
        conf["dynamic_tariff_export_price_key"] = export_price_key
    return conf


class HomeAssistantForecastEntitiesProvider:
    source_name = "home_assistant_forecast_entities"

    async def fetch_pair(
        self,
        *,
        client,
        optim_conf: dict[str, Any],
        retrieve_hass_conf: dict[str, Any],
        forecast_dates: pd.DatetimeIndex,
        logger,
    ) -> DynamicTariffPair:
        conf = _provider_conf(optim_conf)
        import_entity = conf.get("dynamic_tariff_import_forecast_entity")
        export_entity = conf.get("dynamic_tariff_export_forecast_entity")
        if not import_entity or not export_entity:
            raise DynamicTariffError(
                "Both dynamic tariff import and export forecast entities must be configured"
            )
        import_state = await client.get_state(import_entity)
        export_state = await client.get_state(export_entity)
        return self._resolve_pair(
            import_state, export_state, conf, retrieve_hass_conf, forecast_dates
        )

    def _resolve_pair(
        self,
        import_state: dict[str, Any],
        export_state: dict[str, Any],
        optim_conf: dict[str, Any],
        retrieve_hass_conf: dict[str, Any],
        forecast_dates: pd.DatetimeIndex,
    ) -> DynamicTariffPair:
        attribute = optim_conf["dynamic_tariff_forecast_attribute"]
        time_zone = _time_zone(retrieve_hass_conf)
        boundary_strategy = TimeBoundaryStrategy(
            optim_conf["dynamic_tariff_time_boundary_strategy"]
        )
        import_rows = _rows_from_state(import_state, attribute, f"{self.source_name} import")
        export_rows = _rows_from_state(export_state, attribute, f"{self.source_name} export")
        import_intervals = canonicalise_interval_rows(
            import_rows,
            time_zone=time_zone,
            start_key=optim_conf["dynamic_tariff_start_key"],
            end_key=optim_conf["dynamic_tariff_end_key"],
            duration_key=optim_conf["dynamic_tariff_duration_key"],
            price_key=optim_conf["dynamic_tariff_import_price_key"],
            boundary_strategy=boundary_strategy,
        )
        export_intervals = canonicalise_interval_rows(
            export_rows,
            time_zone=time_zone,
            start_key=optim_conf["dynamic_tariff_start_key"],
            end_key=optim_conf["dynamic_tariff_end_key"],
            duration_key=optim_conf["dynamic_tariff_duration_key"],
            price_key=optim_conf["dynamic_tariff_export_price_key"],
            boundary_strategy=boundary_strategy,
            export_sign_policy=ExportSignPolicy(optim_conf["dynamic_tariff_export_sign"]),
        )
        return DynamicTariffPair(
            load_cost_forecast=resolve_intervals_to_forecast(
                import_intervals, forecast_dates, source_name=f"{self.source_name} import"
            ),
            prod_price_forecast=resolve_intervals_to_forecast(
                export_intervals, forecast_dates, source_name=f"{self.source_name} export"
            ),
            import_source=str(import_state.get("entity_id", "import")),
            export_source=str(export_state.get("entity_id", "export")),
        )
