from __future__ import annotations

from typing import Any
from urllib.parse import quote

import aiohttp

from emhass.dynamic_tariffs.models import DynamicTariffError


class HomeAssistantTariffClient:
    def __init__(self, hass_url: str, token: str, logger):
        self.hass_url = (hass_url or "").rstrip("/")
        self.token = token or ""
        self.logger = logger

    def _endpoint(self, path: str) -> str:
        if self.hass_url.startswith("http://supervisor/core/api"):
            return f"{self.hass_url}{path.removeprefix('/api')}"
        return f"{self.hass_url}{path}"

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def _json_request(self, method: str, url: str, **kwargs) -> dict[str, Any]:
        try:
            async with aiohttp.ClientSession(headers=self._headers) as session:
                async with session.request(method, url, **kwargs) as response:
                    text = await response.text()
                    if response.status >= 400:
                        raise DynamicTariffError(
                            f"Home Assistant dynamic tariff request failed "
                            f"with HTTP {response.status}: {text[:200]}"
                        )
                    try:
                        return await response.json(content_type=None)
                    except Exception as exc:
                        raise DynamicTariffError(
                            "Home Assistant dynamic tariff response was not valid JSON"
                        ) from exc
        except aiohttp.ClientError as exc:
            raise DynamicTariffError(
                f"Home Assistant dynamic tariff request failed: {exc}"
            ) from exc

    async def get_state(self, entity_id: str) -> dict[str, Any]:
        if not entity_id:
            raise DynamicTariffError("Dynamic tariff forecast entity is not configured")
        url = self._endpoint(f"/api/states/{quote(entity_id, safe='')}")
        return await self._json_request("GET", url)

    async def call_service_response(
        self, domain: str, service: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        url = self._endpoint(f"/api/services/{domain}/{service}?return_response")
        return await self._json_request("POST", url, json=payload)
