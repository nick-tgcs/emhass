# Dynamic Tariffs

EMHASS can read full-horizon import and export price forecasts from Home Assistant
providers and feed them through the existing list tariff path:

- `load_cost_forecast` becomes `unit_load_cost`
- `prod_price_forecast` becomes `unit_prod_price`

The optimizer sign conventions and objective logic are unchanged.

## Generic Home Assistant Forecast Entities

Use this when Home Assistant entities expose forecast rows as an attribute.

```yaml
dynamic_tariff_source: home_assistant_forecast_entities
dynamic_tariff_import_forecast_entity: sensor.my_import_forecast
dynamic_tariff_export_forecast_entity: sensor.my_export_forecast
dynamic_tariff_forecast_attribute: forecasts
dynamic_tariff_start_key: start_time
dynamic_tariff_end_key: end_time
dynamic_tariff_duration_key: duration
dynamic_tariff_import_price_key: price
dynamic_tariff_export_price_key: price
dynamic_tariff_time_boundary_strategy: explicit_start_end
dynamic_tariff_export_sign: source_signed
```

## Amber Forecast Sensors

Use this with Home Assistant Amber forecast sensors.

```yaml
dynamic_tariff_source: home_assistant_amber_sensors
dynamic_tariff_import_forecast_entity: sensor.beckton_general_forecast
dynamic_tariff_export_forecast_entity: sensor.beckton_feed_in_forecast
dynamic_tariff_forecast_attribute: forecasts
dynamic_tariff_import_price_key: per_kwh
dynamic_tariff_export_price_key: per_kwh
dynamic_tariff_time_boundary_strategy: end_minus_duration
dynamic_tariff_export_sign: source_signed
```

Amber interval starts are canonicalized as `end_time - duration`, because the
Home Assistant Amber `start_time` can be offset from the exact boundary. Feed-in
prices from Home Assistant Amber are already source-signed and are not inverted.

## Amber Service Forecasts

Use this when calling the Home Assistant Amber forecast service.

```yaml
dynamic_tariff_source: home_assistant_amber_service
dynamic_tariff_amber_config_entry_id: your_config_entry_id
dynamic_tariff_import_channel_type: general
dynamic_tariff_export_channel_type: feed_in
dynamic_tariff_import_price_key: per_kwh
dynamic_tariff_export_price_key: per_kwh
```

`advanced_price_predicted` can be used as either price key when the Amber service
response contains that field.

## Runtime Precedence

If runtime parameters contain both `load_cost_forecast` and
`prod_price_forecast`, those explicit lists win and the configured dynamic tariff
provider is skipped.

If exactly one runtime tariff list is supplied while `dynamic_tariff_source` is
not `none`, setup fails. EMHASS does not mix one runtime side with one provider
side.

## Failure Behavior

Dynamic tariff setup fails before solver creation when:

- the import forecast entity is missing
- the export forecast entity is missing
- any import or export timestep is uncovered
- provider rows overlap or contain invalid timestamps, durations, or prices
- a one-sided runtime tariff override conflicts with a configured provider

Current price sensors are not used to fill dynamic tariff gaps. CSV remains a
legacy EMHASS tariff method and is not part of configured dynamic tariff
providers.
