from __future__ import annotations

from typing import Any, Protocol

import pandas as pd

from emhass.dynamic_tariffs.ha_client import HomeAssistantTariffClient
from emhass.dynamic_tariffs.models import DynamicTariffPair


class DynamicTariffProvider(Protocol):
    source_name: str

    async def fetch_pair(
        self,
        *,
        client: HomeAssistantTariffClient,
        optim_conf: dict[str, Any],
        retrieve_hass_conf: dict[str, Any],
        forecast_dates: pd.DatetimeIndex,
        logger,
    ) -> DynamicTariffPair:
        raise NotImplementedError


PROVIDERS: dict[str, type[DynamicTariffProvider]] = {}
