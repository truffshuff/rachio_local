"""The Rachio Local integration."""
from __future__ import annotations

import logging
import asyncio
import time
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any

import aiohttp
from aiohttp import ClientSession

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_KEY, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    DOMAIN,
    API_BASE_URL,
    CLOUD_BASE_URL,
    DEVICE_GET_ENDPOINT,
    DEVICE_CURRENT_SCHEDULE,
    VALVE_GET_BASE_STATION_ENDPOINT,
    VALVE_LIST_VALVES_ENDPOINT,
    ZONE_START,
    ZONE_STOP,
    VALVE_START,
    VALVE_STOP,
    DEVICE_STOP_WATER,
)
from .auth import RachioAuth
from .controller import RachioControllerHandler
from .smart_hose_timer import RachioSmartHoseTimerHandler

_LOGGER = logging.getLogger(__name__)
PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.SWITCH, Platform.NUMBER]


async def _handle_request(session, method: str, url: str, headers: dict) -> dict:
    """Make request with rate limit handling."""
    async with session.request(method, url, headers=headers) as resp:
        # Log rate limit info
        limit = resp.headers.get("X-RateLimit-Limit", "unknown")
        remaining = resp.headers.get("X-RateLimit-Remaining", "unknown")
        reset = resp.headers.get("X-RateLimit-Reset", "unknown")

        _LOGGER.debug(
            "API %s %s - Rate limits: limit=%s, remaining=%s, reset=%s",
            method,
            url.split("/")[-1],
            limit,
            remaining,
            reset,
        )

        if resp.status == 429:
            if "X-RateLimit-Reset" in resp.headers:
                reset_time = parsedate_to_datetime(resp.headers["X-RateLimit-Reset"])
                wait_time = (reset_time - datetime.utcnow()).total_seconds()
                _LOGGER.warning(
                    "Rate limited! Limit=%s, Remaining=%s. Will reset at %s (in %.0f seconds)",
                    limit,
                    remaining,
                    reset,
                    max(0, wait_time),
                )
                if wait_time > 0:
                    await asyncio.sleep(wait_time + 1)
            else:
                _LOGGER.warning(
                    "Rate limited! Limit=%s, Remaining=%s. No reset time provided.",
                    limit,
                    remaining,
                )
                await asyncio.sleep(5)
            return None

        resp.raise_for_status()
        return await resp.json()


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Rachio from config entry."""
    api_key = entry.data[CONF_API_KEY]
    auth = RachioAuth(api_key)

    try:
        await auth.async_get_user_info()
        devices = await auth.async_discover_devices()
        _LOGGER.info("Found %d Rachio devices: %s", len(devices), [d.get('name') or d.get('serialNumber') for d in devices])
        
        # Set up data structure for devices and global values
        num_devices = len(devices)
        hass.data.setdefault(DOMAIN, {})
        hass.data[DOMAIN][entry.entry_id] = {
            "devices": {},
            "num_devices": num_devices,
        }
        for device in devices:
            device_id = device["id"]
            if device.get("device_type") == "SMART_HOSE_TIMER":
                handler = RachioSmartHoseTimerHandler(api_key, device, auth.user_id)
            else:
                handler = RachioControllerHandler(api_key, device)

            # Load saved polling intervals from config entry options
            idle_key = f"idle_polling_interval_{device_id}"
            active_key = f"active_polling_interval_{device_id}"
            if idle_key in entry.options:
                handler.idle_polling_interval = entry.options[idle_key]
                _LOGGER.info(f"Loaded idle polling interval for {handler.name}: {entry.options[idle_key]}s")
            if active_key in entry.options:
                handler.active_polling_interval = entry.options[active_key]
                _LOGGER.info(f"Loaded active polling interval for {handler.name}: {entry.options[active_key]}s")

            handler._fast_poll_count = 0  # Track fast polls
            handler._max_fast_polls = 3   # Max number of 30s polls

            async def _async_update(handler=handler):
                _LOGGER.warning("[COORDINATOR] _async_update called for %s at %s", handler.name, datetime.now().isoformat())
                await handler.async_update()
                new_interval = handler._get_update_interval()
                # Switch to dynamic interval after max fast polls
                if handler.coordinator.update_interval.total_seconds() <= 30:
                    handler._fast_poll_count += 1
                    if handler._fast_poll_count >= handler._max_fast_polls:
                        handler.coordinator.update_interval = new_interval
                        _LOGGER.info("%s: Switching to dynamic polling interval: %s", handler.name, str(new_interval))
                else:
                    handler.coordinator.update_interval = new_interval
                if handler.running_zones or handler.running_schedules:
                    _LOGGER.info(
                        "%s: Active watering with %.1f minutes remaining - polling every %s",
                        handler.name,
                        handler._get_remaining_time(),
                        str(handler.coordinator.update_interval)
                    )
                else:
                    _LOGGER.info(
                        "%s: No active watering - polling every %s",
                        handler.name,
                        str(handler.coordinator.update_interval)
                    )

            coordinator = DataUpdateCoordinator(
                hass,
                _LOGGER,
                name=f"Rachio {device.get('name', device.get('serialNumber', 'Device'))}",
                update_method=_async_update,
                update_interval=timedelta(seconds=30),
            )
            coordinator.num_devices = num_devices  # <-- Set total device count here
            handler.coordinator = coordinator
            hass.data[DOMAIN][entry.entry_id]["devices"][device_id] = {
                "handler": handler,
                "coordinator": coordinator,
            }
            await coordinator.async_config_entry_first_refresh()

        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        return True

    except Exception as err:
        _LOGGER.error("Error setting up Rachio integration: %s", err)
        return False


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
