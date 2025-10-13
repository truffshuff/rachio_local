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
PLATFORMS: list[Platform] = [
    Platform.SENSOR, Platform.SWITCH, Platform.NUMBER, Platform.BUTTON, Platform.CALENDAR
]


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
                handler = RachioSmartHoseTimerHandler(api_key, device, auth.user_id, hass, entry)
            else:
                handler = RachioControllerHandler(api_key, device)

            # Load saved polling intervals from config entry options
            idle_key = f"idle_polling_interval_{device_id}"
            active_key = f"active_polling_interval_{device_id}"
            program_details_key = f"program_details_refresh_interval_{device_id}"

            if idle_key in entry.options:
                handler.idle_polling_interval = entry.options[idle_key]
                _LOGGER.info(f"Loaded idle polling interval for {handler.name}: {entry.options[idle_key]}s")
            if active_key in entry.options:
                handler.active_polling_interval = entry.options[active_key]
                _LOGGER.info(f"Loaded active polling interval for {handler.name}: {entry.options[active_key]}s")
            if program_details_key in entry.options:
                handler._program_details_refresh_interval = entry.options[program_details_key]
                _LOGGER.info(f"Loaded program details refresh interval for {handler.name}: {entry.options[program_details_key]}s ({entry.options[program_details_key]/60:.0f} minutes)")

            handler._fast_poll_count = 0  # Track fast polls
            handler._max_fast_polls = 3   # Max number of 30s polls

            async def _async_update(handler=handler):
                # Fixed incorrect log level (was WARNING, should be debug) - commented out to reduce log noise
                # _LOGGER.debug("[COORDINATOR] _async_update called for %s at %s", handler.name, datetime.now().isoformat())
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
        
        # Register Smart Hose Timer program management services
        async def handle_enable_program(call):
            """Handle enable_program service call."""
            await _handle_program_update(call, {"enabled": True})
        
        async def handle_disable_program(call):
            """Handle disable_program service call."""
            await _handle_program_update(call, {"enabled": False})
        
        async def handle_update_program(call):
            """Handle update_program service call."""
            # Build update payload from service data
            update_data = {}
            if "enabled" in call.data:
                update_data["enabled"] = call.data["enabled"]
            if "name" in call.data:
                update_data["name"] = call.data["name"]
            if "rain_skip_enabled" in call.data:
                update_data["rainSkipEnabled"] = call.data["rain_skip_enabled"]
            if "color" in call.data:
                # Convert RGB list to hex color if needed
                color = call.data["color"]
                if isinstance(color, (list, tuple)) and len(color) == 3:
                    update_data["color"] = f"#{color[0]:02x}{color[1]:02x}{color[2]:02x}"
                else:
                    update_data["color"] = color
            
            await _handle_program_update(call, update_data)
        
        async def _handle_program_update(call, update_data: dict):
            """Common handler for program update operations."""
            program_entity_id = call.data.get("program_id")
            if not program_entity_id:
                _LOGGER.error("No program_id provided in service call")
                return
            
            # Get the entity from the entity registry
            from homeassistant.helpers import entity_registry as er
            registry = er.async_get(hass)
            entity_entry = registry.async_get(program_entity_id)
            
            if not entity_entry:
                _LOGGER.error(f"Entity {program_entity_id} not found in registry")
                return
            
            # Extract program_id from unique_id (format: {device_id}_program_{program_id})
            if "_program_" not in entity_entry.unique_id:
                _LOGGER.error(
                    f"Entity {program_entity_id} is not a program sensor. "
                    f"Please select a sensor entity whose name starts with 'Program:'"
                )
                return
            
            unique_id_parts = entity_entry.unique_id.split("_program_")
            if len(unique_id_parts) != 2:
                _LOGGER.error(f"Invalid unique_id format for entity {program_entity_id}: {entity_entry.unique_id}")
                return
            
            program_id = unique_id_parts[1]
            device_id = unique_id_parts[0]
            
            # Find the handler for this device
            handler = None
            for device in hass.data[DOMAIN][entry.entry_id]["devices"].values():
                if device["handler"].device_id == device_id:
                    handler = device["handler"]
                    break
            
            if not handler:
                _LOGGER.error(f"Handler not found for device {device_id}")
                return
            
            # Verify this is a Smart Hose Timer
            from .smart_hose_timer import RachioSmartHoseTimerHandler
            if not isinstance(handler, RachioSmartHoseTimerHandler):
                _LOGGER.error(f"Device {handler.name} is not a Smart Hose Timer")
                return
            
            # Make API call to update program
            url = f"{CLOUD_BASE_URL}/program/updateProgramV2"
            payload = {
                "id": program_id,
                **update_data
            }
            
            try:
                async with ClientSession() as session:
                    async with session.put(url, json=payload, headers=handler.headers) as resp:
                        if resp.status == 200:
                            result = await resp.json()
                            _LOGGER.info(f"Successfully updated program {program_id}: {update_data}")
                            
                            # Force refresh of only this program's details to reflect changes
                            details = await handler._fetch_program_details(session, program_id, force_refresh=True)
                            
                            # Update the program in handler.schedules with fresh data from API
                            if details and "program" in details:
                                program_details = details["program"]
                                for program in handler.schedules:
                                    if program.get("id") == program_id:
                                        # Merge all details from API response
                                        program["enabled"] = program_details.get("enabled", True)
                                        program["name"] = program_details.get("name", program.get("name"))
                                        program["color"] = program_details.get("color", "#00A7E1")
                                        program["startOn"] = program_details.get("startOn", {})
                                        program["dailyInterval"] = program_details.get("dailyInterval", {})
                                        program["plannedRuns"] = program_details.get("plannedRuns", [])
                                        program["assignments"] = program_details.get("assignments", [])
                                        program["rainSkipEnabled"] = program_details.get("rainSkipEnabled", False)
                                        program["settings"] = program_details.get("settings", {})
                                        
                                        # Copy scheduling type fields
                                        if "daysOfWeek" in program_details:
                                            program["daysOfWeek"] = program_details["daysOfWeek"]
                                        if "evenDays" in program_details:
                                            program["evenDays"] = program_details["evenDays"]
                                        if "oddDays" in program_details:
                                            program["oddDays"] = program_details["oddDays"]
                                        
                                        # Update valve IDs from assignments
                                        if program_details.get("assignments"):
                                            valve_ids = [a.get("entityId") for a in program_details["assignments"] if a.get("entityId")]
                                            if valve_ids:
                                                program["valveIds"] = valve_ids
                                        
                                        _LOGGER.info(f"Updated local program data for {program_id}")
                                        break
                            
                            # Trigger a lightweight coordinator data update without polling
                            # This notifies entities to refresh their state from handler.schedules
                            handler.coordinator.async_set_updated_data(handler.coordinator.data)
                            _LOGGER.info(f"Program {program_id} updated - triggered entity refresh (no additional API calls)")
                        else:
                            error_text = await resp.text()
                            _LOGGER.error(f"Failed to update program {program_id}: {resp.status} - {error_text}")
            except Exception as err:
                _LOGGER.error(f"Error updating program {program_id}: {err}")
        
        # Register services
        hass.services.async_register(DOMAIN, "enable_program", handle_enable_program)
        hass.services.async_register(DOMAIN, "disable_program", handle_disable_program)
        hass.services.async_register(DOMAIN, "update_program", handle_update_program)
        _LOGGER.info("Registered Smart Hose Timer program management services")
        
        return True

    except Exception as err:
        _LOGGER.error("Error setting up Rachio integration: %s", err)
        return False


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)
        
        # Unregister services
        hass.services.async_remove(DOMAIN, "enable_program")
        hass.services.async_remove(DOMAIN, "disable_program")
        hass.services.async_remove(DOMAIN, "update_program")
        _LOGGER.info("Unregistered Smart Hose Timer program management services")
        
    return unload_ok
