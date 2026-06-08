from emhass.dynamic_tariffs.providers.amberelectric import (
    HomeAssistantAmberSensorsProvider,
    HomeAssistantAmberServiceProvider,
)
from emhass.dynamic_tariffs.providers.base import PROVIDERS
from emhass.dynamic_tariffs.providers.home_assistant_entities import (
    HomeAssistantForecastEntitiesProvider,
)

PROVIDERS.update(
    {
        "home_assistant_forecast_entities": HomeAssistantForecastEntitiesProvider,
        "home_assistant_amber_sensors": HomeAssistantAmberSensorsProvider,
        "home_assistant_amber_service": HomeAssistantAmberServiceProvider,
    }
)

__all__ = [
    "PROVIDERS",
    "HomeAssistantForecastEntitiesProvider",
    "HomeAssistantAmberSensorsProvider",
    "HomeAssistantAmberServiceProvider",
]
