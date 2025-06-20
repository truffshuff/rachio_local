"""Authentication for Rachio devices."""
from __future__ import annotations

import logging
from typing import Any

import aiohttp
from aiohttp import ClientSession

from .const import (
    API_BASE_URL,
    CLOUD_BASE_URL,
    PERSON_INFO_ENDPOINT,
    PERSON_GET_ENDPOINT,
    VALVE_LIST_BASE_STATIONS_ENDPOINT,
    DEVICE_GET_ENDPOINT,
    DEVICE_CURRENT_SCHEDULE,
    DEVICE_TYPE_CONTROLLER,
    DEVICE_TYPE_SMART_HOSE_TIMER,
)

_LOGGER = logging.getLogger(__name__)

class RachioAuth:
    """Class to make authenticated requests to Rachio APIs."""

    def __init__(self, api_key: str) -> None:
        """Initialize Rachio authentication."""
        self.api_key = api_key
        self.user_id = None
        self.headers = {"Authorization": f"Bearer {api_key}"}

    def _log_rate_limits(self, resp) -> None:
        """Log rate limit information from response headers."""
        if resp.status == 429:
            limit = resp.headers.get("X-RateLimit-Limit", "unknown")
            remaining = resp.headers.get("X-RateLimit-Remaining", "unknown")
            reset = resp.headers.get("X-RateLimit-Reset", "unknown")
            _LOGGER.warning(
                "Rate limited! Limit=%s, Remaining=%s, Reset=%s",
                limit, remaining, reset
            )

    async def async_get_user_info(self) -> dict[str, Any]:
        """Get user info from Rachio API."""
        async with ClientSession() as session:
            async with session.get(
                f"{API_BASE_URL}/{PERSON_INFO_ENDPOINT}",
                headers=self.headers,
            ) as resp:
                self._log_rate_limits(resp)
                resp.raise_for_status()
                data = await resp.json()
                self.user_id = data.get("id")
                return data

    async def async_discover_devices(self) -> list[dict[str, Any]]:
        """Discover all Rachio devices."""
        if not self.user_id:
            await self.async_get_user_info()

        devices = []
        # Discover controllers
        async with ClientSession() as session:
            async with session.get(
                f"{API_BASE_URL}/{PERSON_GET_ENDPOINT.format(id=self.user_id)}",
                headers=self.headers,
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
                for device in data.get("devices", []):
                    model = device.get("model", "").upper()
                    if any(x in model for x in ["GENERATION", "8ZULW", "16ZULW"]):
                        device["device_type"] = DEVICE_TYPE_CONTROLLER
                        devices.append(device)
        # Discover smart hose timers
        async with ClientSession() as session:
            async with session.get(
                f"{CLOUD_BASE_URL}{VALVE_LIST_BASE_STATIONS_ENDPOINT.format(userId=self.user_id)}",
                headers=self.headers,
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    for timer in data.get("baseStations", []):
                        timer["device_type"] = DEVICE_TYPE_SMART_HOSE_TIMER
                        devices.append(timer)
        _LOGGER.info("Discovered %d total devices: %s", len(devices), [d.get('name', d.get('serialNumber')) for d in devices])
        return devices
