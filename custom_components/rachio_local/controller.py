"""Controller for Rachio irrigation controllers."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from aiohttp import ClientSession

from .const import (
    API_BASE_URL,
    DEVICE_CURRENT_SCHEDULE,
    DEVICE_GET_ENDPOINT,
    DEVICE_STOP_WATER,
    SCHEDULE_START,
    SCHEDULE_STOP,
    ZONE_START,
)
from .utils import get_update_interval

_LOGGER = logging.getLogger(__name__)

class RachioControllerHandler:
    """Handler for Rachio Controller devices."""

    def __init__(self, api_key: str, device_data: dict) -> None:
        """Initialize the Rachio controller."""
        self.api_key = api_key
        self.device_data = device_data
        self.device_id = device_data["id"]
        self.type = device_data.get("device_type", "CONTROLLER")
        self.name = device_data.get("name", "")
        self.model = device_data.get("model", "")
        self.zones = []
        self.schedules = []
        self.running_zones = {}
        self.running_schedules = {}
        self.status = device_data.get("status", "OFFLINE")
        self.headers = {"Authorization": f"Bearer {api_key}"}
        self.coordinator = None
        self._pending_start = {}

    async def _make_request(self, session, url: str) -> dict | None:
        try:
            async with session.get(url, headers=self.headers) as resp:
                if resp.status == 404:
                    _LOGGER.debug("%s: No data found at %s", self.name, url)
                    return None
                resp.raise_for_status()
                return await resp.json()
        except Exception as err:
            _LOGGER.error("Error in _make_request: %s", err)
            return None

    async def async_update(self) -> None:
        """Update controller data."""
        try:
            _LOGGER.debug("Updating controller: %s", self.device_id)
            async with ClientSession() as session:
                # Device info
                url = f"{API_BASE_URL}/{DEVICE_GET_ENDPOINT.format(id=self.device_id)}"
                data = await self._make_request(session, url)
                if data:
                    self.device_data = data
                    self.status = data.get("status", "OFFLINE")
                    self.zones = data.get("zones", [])
                    self.schedules = data.get("scheduleRules", [])
                else:
                    self.device_data = {}
                    self.status = "OFFLINE"
                    self.zones = []
                    self.schedules = []

                # Current schedule
                url = f"{API_BASE_URL}/{DEVICE_CURRENT_SCHEDULE.format(id=self.device_id)}"
                data = await self._make_request(session, url)
                if data and "id" in data:
                    self.running_schedules = {data["id"]: data}
                    self.running_zones = {
                        zone["id"]: zone for zone in data.get("zones", [])
                        if zone.get("remaining", 0) > 0
                    }
                else:
                    self.running_schedules = {}
                    self.running_zones = {}
        except Exception as err:
            _LOGGER.error("Error updating controller: %s", err)
            raise

    async def async_start_zone(self, zone_id, duration=600):
        """Start a zone."""
        async with ClientSession() as session:
            url = f"{API_BASE_URL}/{ZONE_START}"
            payload = {"id": zone_id, "duration": duration}
            method = session.put
            _LOGGER.info("Starting zone: %s with payload: %s", url, payload)
            async with method(url, headers=self.headers, json=payload) as resp:
                _LOGGER.info("Start response status: %s", resp.status)
                if resp.status >= 400:
                    _LOGGER.error("Start response text: %s", await resp.text())
                if resp.status in (200, 204):
                    self.running_zones[zone_id] = {"id": zone_id, "remaining": duration}
                    self._pending_start[zone_id] = time.time() + 30  # Reverted back to 30 seconds for optimistic window
                    if self.coordinator:
                        await self.coordinator.async_request_refresh()
                    return True
                resp.raise_for_status()
                try:
                    result = await resp.json()
                    self.running_zones[zone_id] = {"id": zone_id, "remaining": duration}
                    if self.coordinator:
                        await self.coordinator.async_request_refresh()
                    return result
                except Exception:
                    return True

    async def async_stop_zone(self, zone_id):
        """Stop a zone."""
        async with ClientSession() as session:
            url = f"{API_BASE_URL}/{DEVICE_STOP_WATER}"
            payload = {"id": self.device_id}
            _LOGGER.info("Stopping zone: %s with payload: %s", url, payload)
            async with session.put(url, headers=self.headers, json=payload) as resp:
                _LOGGER.info("Stop response status: %s", resp.status)
                if resp.status == 204:
                    self.running_zones.pop(zone_id, None)
                    self._pending_start.pop(zone_id, None)  # Clear optimistic timer on stop
                    if self.coordinator:
                        await self.coordinator.async_request_refresh()
                    return True
                resp.raise_for_status()
                try:
                    result = await resp.json()
                    self.running_zones.pop(zone_id, None)
                    self._pending_start.pop(zone_id, None)  # Clear optimistic timer on stop
                    if self.coordinator:
                        await self.coordinator.async_request_refresh()
                    return result
                except Exception:
                    return True

    async def async_set_rain_delay(self, duration_hours: int = 24):
        """Set rain delay for the controller (default 24 hours)."""
        async with ClientSession() as session:
            url = f"{API_BASE_URL}/device/rain_delay"
            payload = {"id": self.device_id, "duration": duration_hours * 3600}
            _LOGGER.info("Setting rain delay: %s with payload: %s", url, payload)
            async with session.put(url, headers=self.headers, json=payload) as resp:
                _LOGGER.info("Rain delay response status: %s", resp.status)
                if resp.status >= 400:
                    _LOGGER.error("Rain delay response text: %s", await resp.text())
                resp.raise_for_status()
                return await resp.json()

    async def async_clear_rain_delay(self):
        """Clear rain delay for the controller (set duration to 0)."""
        async with ClientSession() as session:
            url = f"{API_BASE_URL}/device/rain_delay"
            payload = {"id": self.device_id, "duration": 0}
            _LOGGER.info("Clearing rain delay: %s with payload: %s", url, payload)
            async with session.put(url, headers=self.headers, json=payload) as resp:
                _LOGGER.info("Clear rain delay response status: %s", resp.status)
                if resp.status >= 400:
                    _LOGGER.error("Clear rain delay response text: %s", await resp.text())
                resp.raise_for_status()
                return await resp.json()

    def get_zone_default_duration(self, zone_id):
        """Get the default duration for a zone."""
        for zone in self.zones:
            if zone.get("id") == zone_id:
                return zone.get("duration") or zone.get("defaultRuntime") or 600
        return 600

    def is_zone_optimistically_on(self, zone_id):
        """Check if a zone is optimistically considered 'on'."""
        now = time.time()
        return zone_id in self.running_zones or (
            zone_id in self._pending_start and self._pending_start[zone_id] > now
        )

    def _get_update_interval(self) -> timedelta:
        return get_update_interval(self)

    def _get_remaining_time(self) -> float:
        """Get remaining time in minutes."""
        remaining_secs = 0
        for zone in self.running_zones.values():
            remaining_secs = max(remaining_secs, zone.get("remaining", 0))
        for schedule in self.running_schedules.values():
            remaining_secs = max(remaining_secs, schedule.get("remaining", 0))
        return remaining_secs / 60  # Convert to minutes

    async def async_start_schedule(self, schedule_id, duration=None):
        """Start a schedule on the controller using the Rachio API."""
        async with ClientSession() as session:
            url = f"{API_BASE_URL}/{SCHEDULE_START}"
            payload = {"id": schedule_id}
            if duration:
                payload["duration"] = duration
            _LOGGER.info("Starting schedule: %s with payload: %s", url, payload)
            async with session.put(url, headers=self.headers, json=payload) as resp:
                _LOGGER.info("Start schedule response status: %s", resp.status)
                if resp.status >= 400:
                    _LOGGER.error("Start schedule response text: %s", await resp.text())
                    return False
                resp.raise_for_status()
                try:
                    result = await resp.json()
                    if self.coordinator:
                        await self.coordinator.async_request_refresh()
                    return result
                except Exception:
                    return True

    async def async_stop_schedule(self, schedule_id):
        """Stop a schedule on the controller using the Rachio API."""
        async with ClientSession() as session:
            url = f"{API_BASE_URL}/{SCHEDULE_STOP}"
            payload = {"id": schedule_id}
            _LOGGER.info("Stopping schedule: %s with payload: %s", url, payload)
            async with session.put(url, headers=self.headers, json=payload) as resp:
                _LOGGER.info("Stop schedule response status: %s", resp.status)
                if resp.status >= 400:
                    _LOGGER.error("Stop schedule response text: %s", await resp.text())
                    return False
                resp.raise_for_status()
                try:
                    result = await resp.json()
                    if self.coordinator:
                        await self.coordinator.async_request_refresh()
                    return result
                except Exception:
                    return True
