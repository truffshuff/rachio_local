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
                # Try to find the entity by searching all entries
                _LOGGER.debug(f"Entity {program_entity_id} not found with async_get, searching registry...")
                for entry_item in registry.entities.values():
                    if entry_item.entity_id == program_entity_id:
                        entity_entry = entry_item
                        _LOGGER.debug(f"Found entity {program_entity_id} via search")
                        break
                
                if not entity_entry:
                    _LOGGER.error(f"Entity {program_entity_id} not found in registry. Available program sensors: {[e.entity_id for e in registry.entities.values() if e.domain == 'sensor' and e.platform == DOMAIN and '_program_' in e.unique_id]}")
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
            
            _LOGGER.debug(f"API payload being sent: {payload}")
            
            try:
                async with ClientSession() as session:
                    async with session.put(url, json=payload, headers=handler.headers) as resp:
                        if resp.status == 200:
                            result = await resp.json()
                            _LOGGER.info(f"Successfully updated program {program_id}")
                            _LOGGER.debug(f"API response: {result}")
                            
                            # Force refresh of only this program's details to reflect changes
                            details = await handler._fetch_program_details(session, program_id, force_refresh=True)
                            _LOGGER.debug(f"Fetched program details after update: {details}")
                            
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
        
        async def handle_enable_program(call):
            """Handle enable_program service call."""
            await _handle_program_update(call, {"enabled": True})
        
        async def handle_disable_program(call):
            """Handle disable_program service call."""
            await _handle_program_update(call, {"enabled": False})
        
        async def handle_create_program(call):
            """Handle create_program service call."""
            # Build create payload from service data (similar to update but different endpoint)
            create_data = {}
            
            # Get device_id (required for create)
            device_id = call.data.get("device_id")
            if not device_id:
                _LOGGER.error("No device_id provided for create_program")
                return
            
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
            
            # Validate mutually exclusive scheduling options
            scheduling_types = []
            if "days_of_week" in call.data:
                scheduling_types.append("days_of_week")
            if "interval_days" in call.data:
                scheduling_types.append("interval_days")
            
            # Check if more than one scheduling type is specified
            if len(scheduling_types) > 1:
                _LOGGER.error(
                    f"Invalid program create: Multiple scheduling types specified ({', '.join(scheduling_types)}). "
                    f"Only one of the following can be used: days_of_week or interval_days."
                )
                return
            
            # Required fields
            if "name" not in call.data:
                _LOGGER.error("Program name is required for create_program")
                return
            
            # Validate required date fields
            required_date_fields = [
                "start_on_year", "start_on_month", "start_on_day",
                "end_on_year", "end_on_month", "end_on_day"
            ]
            missing_date_fields = [f for f in required_date_fields if f not in call.data]
            if missing_date_fields:
                _LOGGER.error(f"Missing required date fields for create_program: {', '.join(missing_date_fields)}")
                return
            
            # Simple boolean/string fields
            create_data["deviceId"] = device_id
            create_data["name"] = call.data["name"]
            create_data["enabled"] = call.data.get("enabled", True)
            
            # Add start/end dates
            create_data["startOn"] = {
                "year": int(call.data["start_on_year"]),
                "month": int(call.data["start_on_month"]),
                "day": int(call.data["start_on_day"])
            }
            create_data["endOn"] = {
                "year": int(call.data["end_on_year"]),
                "month": int(call.data["end_on_month"]),
                "day": int(call.data["end_on_day"])
            }
            
            _LOGGER.debug(f"Program dates: start={create_data['startOn']}, end={create_data['endOn']}")
            
            if "rain_skip_enabled" in call.data:
                create_data["rainSkipEnabled"] = call.data["rain_skip_enabled"]
            
            # Color field - convert RGB list to hex if needed
            if "color" in call.data:
                color = call.data["color"]
                if isinstance(color, (list, tuple)) and len(color) == 3:
                    create_data["color"] = f"#{color[0]:02x}{color[1]:02x}{color[2]:02x}"
                else:
                    create_data["color"] = color
            
            # Days of week - convert to daysOfWeek object with uppercase day names
            if "days_of_week" in call.data:
                days = call.data["days_of_week"]
                if isinstance(days, list):
                    # Convert day names to uppercase (e.g., "monday" -> "MONDAY")
                    uppercase_days = [day.upper() if isinstance(day, str) else day for day in days]
                    create_data["daysOfWeek"] = {
                        "daysOfWeek": uppercase_days
                    }
            
            # Interval days - convert to dailyInterval object with intervalDays field
            if "interval_days" in call.data:
                interval = call.data["interval_days"]
                if isinstance(interval, (int, float)) and interval > 0:
                    create_data["dailyInterval"] = {
                        "intervalDays": int(interval)
                    }
            
            # Check if user provided easy UI fields (run_1, run_2, run_3) or advanced runs field
            has_easy_runs = (
                any(key.startswith(("run_1_", "run_2_", "run_3_")) for key in call.data.keys())
                or "valves" in call.data
            )
            has_advanced_runs = "runs" in call.data
            
            if has_easy_runs and has_advanced_runs:
                _LOGGER.error(
                    "Invalid program create: Cannot use both the easy run fields (run_1_*, run_2_*, run_3_*) "
                    "and the advanced 'runs' field. Please use one or the other."
                )
                return
            
            # Process easy UI run fields (run_1, run_2, run_3)
            if has_easy_runs:
                from homeassistant.helpers import entity_registry as er
                registry = er.async_get(hass)
                
                def time_to_seconds(time_str):
                    """Convert HH:MM:SS or HH:MM time string to seconds."""
                    if isinstance(time_str, (int, float)):
                        return int(time_str)  # Already in seconds
                    if not isinstance(time_str, str):
                        return 300  # Default 5 minutes
                    
                    parts = time_str.split(":")
                    if len(parts) == 3:
                        hours, minutes, seconds = int(parts[0]), int(parts[1]), int(parts[2])
                        return hours * 3600 + minutes * 60 + seconds
                    elif len(parts) == 2:
                        hours, minutes = int(parts[0]), int(parts[1])
                        return hours * 3600 + minutes * 60
                    else:
                        return 300  # Default 5 minutes
                
                # Get global valve configuration
                global_valves = call.data.get("valves", [])
                if isinstance(global_valves, str):
                    global_valves = [global_valves]
                
                # Get valve durations - check for positional durations (valve_duration_1, valve_duration_2, etc.)
                total_duration_raw = call.data.get("total_duration", "00:05:00")
                total_duration = time_to_seconds(total_duration_raw)
                
                positional_durations = []
                for i in range(1, 5):  # Support up to 4 valves with individual durations
                    duration_key = f"valve_duration_{i}"
                    if duration_key in call.data:
                        duration_seconds = time_to_seconds(call.data[duration_key])
                        positional_durations.append(duration_seconds)
                
                # Build entity runs list from global valves
                global_entity_runs = []
                if global_valves:
                    for valve_idx, entity_id in enumerate(global_valves):
                        entity_entry = registry.async_get(entity_id)
                        if entity_entry:
                            valve_id = None
                            # Check for Smart Hose Timer zones (format: {device_id}_{zone_id}_zone)
                            if "_zone" in entity_entry.unique_id:
                                parts = entity_entry.unique_id.split("_zone")
                                if len(parts) == 2 and parts[0]:
                                    device_and_zone = parts[0]
                                    zone_id = device_and_zone.split("_", 1)[1] if "_" in device_and_zone else device_and_zone
                                    valve_id = zone_id
                            # Check for controller valves (format: {device_id}_valve_{valve_id})
                            elif "_valve_" in entity_entry.unique_id:
                                valve_id = entity_entry.unique_id.split("_valve_")[-1]
                            
                            if valve_id:
                                # Determine duration for this valve
                                if valve_idx < len(positional_durations):
                                    # Use positional duration (valve_duration_1, valve_duration_2, etc.)
                                    duration = positional_durations[valve_idx]
                                    _LOGGER.debug(f"Valve {valve_idx + 1} ({entity_id}) using valve_duration_{valve_idx + 1}: {duration}s")
                                else:
                                    # Split total duration equally across all valves
                                    duration = int(total_duration / len(global_valves))
                                    _LOGGER.debug(f"Valve {valve_idx + 1} ({entity_id}) using split duration: {duration}s (total: {total_duration}s / {len(global_valves)} valves)")
                                
                                global_entity_runs.append({
                                    "entityId": valve_id,
                                    "durationSec": str(duration)
                                })
                                _LOGGER.debug(f"Added valve {valve_idx + 1}: {entity_id} (id={valve_id}) with duration {duration}s")
                            else:
                                _LOGGER.warning(f"Entity {entity_id} is not a valve/zone entity (unique_id: {entity_entry.unique_id})")
                        else:
                            _LOGGER.warning(f"Entity {entity_id} not found in registry")
                
                # Now process each run's timing configuration
                processed_runs = []
                
                for run_num in [1, 2, 3]:
                    prefix = f"run_{run_num}_"
                    
                    # Check if this run has timing configuration
                    start_time = call.data.get(f"{prefix}start_time")
                    sun_event = call.data.get(f"{prefix}sun_event")
                    sun_offset = call.data.get(f"{prefix}sun_offset", 0)
                    run_concurrently = call.data.get(f"{prefix}run_concurrently", False)
                    cycle_and_soak = call.data.get(f"{prefix}cycle_and_soak", False)
                    
                    # Skip if no timing specified
                    if not start_time and not sun_event:
                        _LOGGER.debug(f"Run {run_num}: Skipping - no start time or sun event specified")
                        continue
                    
                    # Validate mutually exclusive start types
                    if start_time and sun_event:
                        _LOGGER.error(
                            f"Invalid run {run_num}: Both start_time and sun_event specified. "
                            f"Only one can be used per run."
                        )
                        continue
                    
                    run_data = {}
                    
                    # Handle fixed start time
                    if start_time:
                        if isinstance(start_time, str) and ":" in start_time:
                            parts = start_time.split(":")
                            hour = int(parts[0])
                            minute = int(parts[1]) if len(parts) > 1 else 0
                            run_data["fixedStart"] = {
                                "startAt": {
                                    "hour": hour,
                                    "minute": minute,
                                    "second": 0
                                }
                            }
                            _LOGGER.debug(f"Run {run_num}: Fixed start time {hour:02d}:{minute:02d}")
                    
                    # Handle sun-based start time
                    elif sun_event:
                        offset_seconds = int(sun_offset * 60) if sun_offset else 0
                        run_data["sunStart"] = {
                            "sunEvent": sun_event,
                            "offsetSeconds": str(offset_seconds)
                        }
                        _LOGGER.debug(f"Run {run_num}: Sun event {sun_event} with offset {sun_offset} minutes")
                    
                    # Apply global entity runs to this run
                    if global_entity_runs:
                        run_data["entityRuns"] = global_entity_runs
                        _LOGGER.debug(f"Run {run_num}: Applied {len(global_entity_runs)} global valve(s)")
                    
                    # Add run concurrently and cycle and soak settings
                    run_data["runConcurrently"] = run_concurrently
                    run_data["cycleAndSoak"] = cycle_and_soak
                    
                    if run_concurrently:
                        _LOGGER.debug(f"Run {run_num}: Will run valves concurrently")
                    if cycle_and_soak:
                        _LOGGER.debug(f"Run {run_num}: Cycle and soak enabled")
                    
                    # Only add run if it has configuration
                    if run_data:
                        processed_runs.append(run_data)
                        _LOGGER.info(f"Run {run_num}: Configured with {len(run_data.get('entityRuns', []))} valve(s)")
                
                # Add runs if any were configured
                if processed_runs:
                    create_data["plannedRuns"] = {
                        "runs": processed_runs
                    }
                    _LOGGER.info(f"Configured {len(processed_runs)} run(s) with {len(global_entity_runs)} valve(s) each")
                else:
                    _LOGGER.warning("No runs configured - at least one run with timing must be specified")
            
            # Handle advanced runs configuration (supports multiple runs per day)
            elif "runs" in call.data:
                runs_config = call.data["runs"]
                if isinstance(runs_config, (list, dict)):
                    # Handle both list format and dict format
                    runs_list = runs_config if isinstance(runs_config, list) else [runs_config]
                    
                    # Get entity registry to resolve entity IDs to valve IDs
                    from homeassistant.helpers import entity_registry as er
                    registry = er.async_get(hass)
                    
                    processed_runs = []
                    for run_idx, run_entry in enumerate(runs_list):
                        if not isinstance(run_entry, dict):
                            continue
                        
                        run_data = {}
                        
                        # Validate mutually exclusive start types within this run
                        has_fixed_start = "start_time" in run_entry
                        has_sun_start = "sun_event" in run_entry
                        
                        if has_fixed_start and has_sun_start:
                            _LOGGER.error(
                                f"Invalid run {run_idx + 1}: Both start_time and sun_event specified. "
                                f"Only one can be used per run."
                            )
                            continue
                        
                        # Fixed start time
                        if has_fixed_start:
                            time_str = run_entry["start_time"]
                            if isinstance(time_str, str) and ":" in time_str:
                                parts = time_str.split(":")
                                hour = int(parts[0])
                                minute = int(parts[1]) if len(parts) > 1 else 0
                                run_data["fixedStart"] = {
                                    "startAt": {
                                        "hour": hour,
                                        "minute": minute,
                                        "second": 0
                                    }
                                }
                        
                        # Sun-based start time
                        elif has_sun_start:
                            sun_event = run_entry["sun_event"]
                            offset_minutes = run_entry.get("sun_offset_minutes", 0)
                            offset_seconds = int(offset_minutes * 60)
                            run_data["sunStart"] = {
                                "sunEvent": sun_event,
                                "offsetSeconds": str(offset_seconds)
                            }
                        
                        # Process valves for this run
                        if "valves" in run_entry:
                            valves_config = run_entry["valves"]
                            if isinstance(valves_config, (list, dict)):
                                valve_list = valves_config if isinstance(valves_config, list) else [valves_config]
                                
                                entity_runs = []
                                for valve_entry in valve_list:
                                    if not isinstance(valve_entry, dict):
                                        continue
                                    
                                    entity_id = valve_entry.get("entity_id")
                                    duration = valve_entry.get("duration", 300)
                                    
                                    if not entity_id:
                                        continue
                                    
                                    entity_entry = registry.async_get(entity_id)
                                    if entity_entry:
                                        valve_id = None
                                        # Check for Smart Hose Timer zones (format: {device_id}_{zone_id}_zone)
                                        if "_zone" in entity_entry.unique_id:
                                            parts = entity_entry.unique_id.split("_zone")
                                            if len(parts) == 2 and parts[0]:
                                                # Extract zone_id (everything after device_id and before _zone)
                                                device_and_zone = parts[0]
                                                # Find device_id prefix and extract the zone_id part
                                                # Format is: {device_id}_{zone_id}_zone
                                                zone_id = device_and_zone.split("_", 1)[1] if "_" in device_and_zone else device_and_zone
                                                valve_id = zone_id
                                        # Check for controller valves (format: {device_id}_valve_{valve_id})
                                        elif "_valve_" in entity_entry.unique_id:
                                            valve_id = entity_entry.unique_id.split("_valve_")[-1]
                                        
                                        if valve_id:
                                            entity_runs.append({
                                                "entityId": valve_id,
                                                "durationSec": str(duration)
                                            })
                                        else:
                                            _LOGGER.warning(f"Entity {entity_id} is not a valve/zone entity (unique_id: {entity_entry.unique_id})")
                                    else:
                                        _LOGGER.warning(f"Entity {entity_id} not found in registry")
                                
                                if entity_runs:
                                    run_data["entityRuns"] = entity_runs
                        
                        # Only add run if it has configuration
                        if run_data:
                            processed_runs.append(run_data)
                            _LOGGER.debug(f"Added run {run_idx + 1}: {list(run_data.keys())}")
                    
                    if processed_runs:
                        create_data["plannedRuns"] = {
                            "runs": processed_runs
                        }
                        _LOGGER.info(f"Configured {len(processed_runs)} run(s) for program")
            
            # Make the API call
            url = f"{CLOUD_BASE_URL}/program/createProgramV2"
            payload = create_data
            
            _LOGGER.debug(f"API payload being sent to createProgramV2: {payload}")
            
            try:
                async with ClientSession() as session:
                    async with session.post(url, json=payload, headers=handler.headers) as resp:
                        if resp.status == 200:
                            result = await resp.json()
                            _LOGGER.info(f"Successfully created program '{create_data.get('name', 'Unknown')}' on device {device_id}")
                            _LOGGER.debug(f"API response: {result}")
                            
                            # Force refresh to get new program
                            await handler.async_update()
                            handler.coordinator.async_set_updated_data(handler.coordinator.data)
                            _LOGGER.info(f"Program created - triggered entity refresh")
                        else:
                            error_text = await resp.text()
                            _LOGGER.error(f"Failed to create program on device {device_id}: {resp.status} - {error_text}")
            except Exception as err:
                _LOGGER.error(f"Error creating program on device {device_id}: {err}")
        
        async def handle_update_program(call):
            """Handle update_program service call."""
            # Build update payload from service data
            update_data = {}
            
            # Validate mutually exclusive scheduling options
            scheduling_types = []
            if "days_of_week" in call.data:
                scheduling_types.append("days_of_week")
            if "interval_days" in call.data:
                scheduling_types.append("interval_days")
            
            # Check if more than one scheduling type is specified
            if len(scheduling_types) > 1:
                _LOGGER.error(
                    f"Invalid program update: Multiple scheduling types specified ({', '.join(scheduling_types)}). "
                    f"Only one of the following can be used: days_of_week or interval_days."
                )
                return
            
            # Simple boolean/string fields
            if "enabled" in call.data:
                update_data["enabled"] = call.data["enabled"]
            if "name" in call.data:
                update_data["name"] = call.data["name"]
            if "rain_skip_enabled" in call.data:
                update_data["rainSkipEnabled"] = call.data["rain_skip_enabled"]
            
            # Color field - convert RGB list to hex if needed
            if "color" in call.data:
                color = call.data["color"]
                if isinstance(color, (list, tuple)) and len(color) == 3:
                    update_data["color"] = f"#{color[0]:02x}{color[1]:02x}{color[2]:02x}"
                else:
                    update_data["color"] = color
            
            # Start/end dates (optional for update - must provide all 3 fields for each date)
            start_date_fields = ["start_on_year", "start_on_month", "start_on_day"]
            end_date_fields = ["end_on_year", "end_on_month", "end_on_day"]
            
            has_start_fields = any(f in call.data for f in start_date_fields)
            has_end_fields = any(f in call.data for f in end_date_fields)
            
            if has_start_fields:
                missing_start = [f for f in start_date_fields if f not in call.data]
                if missing_start:
                    _LOGGER.error(f"Incomplete start date - missing fields: {', '.join(missing_start)}")
                    return
                update_data["startOn"] = {
                    "year": int(call.data["start_on_year"]),
                    "month": int(call.data["start_on_month"]),
                    "day": int(call.data["start_on_day"])
                }
                _LOGGER.debug(f"Updating start date: {update_data['startOn']}")
            
            if has_end_fields:
                missing_end = [f for f in end_date_fields if f not in call.data]
                if missing_end:
                    _LOGGER.error(f"Incomplete end date - missing fields: {', '.join(missing_end)}")
                    return
                update_data["endOn"] = {
                    "year": int(call.data["end_on_year"]),
                    "month": int(call.data["end_on_month"]),
                    "day": int(call.data["end_on_day"])
                }
                _LOGGER.debug(f"Updating end date: {update_data['endOn']}")
            
            # Days of week - convert to daysOfWeek object with uppercase day names
            if "days_of_week" in call.data:
                days = call.data["days_of_week"]
                if isinstance(days, list):
                    # Convert day names to uppercase (e.g., "monday" -> "MONDAY")
                    uppercase_days = [day.upper() if isinstance(day, str) else day for day in days]
                    update_data["daysOfWeek"] = {
                        "daysOfWeek": uppercase_days
                    }
            
            # Interval days - convert to dailyInterval object with intervalDays field
            if "interval_days" in call.data:
                interval = call.data["interval_days"]
                if isinstance(interval, (int, float)) and interval > 0:
                    update_data["dailyInterval"] = {
                        "intervalDays": int(interval)
                    }
            
            # Check if user provided easy UI fields (run_1, run_2, run_3) or advanced runs field
            has_easy_runs = (
                any(key.startswith(("run_1_", "run_2_", "run_3_")) for key in call.data.keys())
                or "valves" in call.data
            )
            has_advanced_runs = "runs" in call.data
            
            if has_easy_runs and has_advanced_runs:
                _LOGGER.error(
                    "Invalid program update: Cannot use both the easy run fields (run_1_*, run_2_*, run_3_*) "
                    "and the advanced 'runs' field. Please use one or the other."
                )
                return
            
            # Process easy UI run fields (run_1, run_2, run_3)
            if has_easy_runs:
                from homeassistant.helpers import entity_registry as er
                registry = er.async_get(hass)
                
                def time_to_seconds(time_str):
                    """Convert HH:MM:SS or HH:MM time string to seconds."""
                    if isinstance(time_str, (int, float)):
                        return int(time_str)  # Already in seconds
                    if not isinstance(time_str, str):
                        return 300  # Default 5 minutes
                    
                    parts = time_str.split(":")
                    if len(parts) == 3:
                        hours, minutes, seconds = int(parts[0]), int(parts[1]), int(parts[2])
                        return hours * 3600 + minutes * 60 + seconds
                    elif len(parts) == 2:
                        hours, minutes = int(parts[0]), int(parts[1])
                        return hours * 3600 + minutes * 60
                    else:
                        return 300  # Default 5 minutes
                
                # Get global valve configuration
                global_valves = call.data.get("valves", [])
                if isinstance(global_valves, str):
                    global_valves = [global_valves]
                
                # Get valve durations - check for positional durations (valve_duration_1, valve_duration_2, etc.)
                total_duration_raw = call.data.get("total_duration", "00:05:00")
                total_duration = time_to_seconds(total_duration_raw)
                
                positional_durations = []
                for i in range(1, 5):  # Support up to 4 valves with individual durations
                    duration_key = f"valve_duration_{i}"
                    if duration_key in call.data:
                        duration_seconds = time_to_seconds(call.data[duration_key])
                        positional_durations.append(duration_seconds)
                
                # Build entity runs list from global valves
                global_entity_runs = []
                if global_valves:
                    for valve_idx, entity_id in enumerate(global_valves):
                        entity_entry = registry.async_get(entity_id)
                        if entity_entry:
                            valve_id = None
                            # Check for Smart Hose Timer zones (format: {device_id}_{zone_id}_zone)
                            if "_zone" in entity_entry.unique_id:
                                parts = entity_entry.unique_id.split("_zone")
                                if len(parts) == 2 and parts[0]:
                                    device_and_zone = parts[0]
                                    zone_id = device_and_zone.split("_", 1)[1] if "_" in device_and_zone else device_and_zone
                                    valve_id = zone_id
                            # Check for controller valves (format: {device_id}_valve_{valve_id})
                            elif "_valve_" in entity_entry.unique_id:
                                valve_id = entity_entry.unique_id.split("_valve_")[-1]
                            
                            if valve_id:
                                # Determine duration for this valve
                                if valve_idx < len(positional_durations):
                                    # Use positional duration (valve_duration_1, valve_duration_2, etc.)
                                    duration = positional_durations[valve_idx]
                                    _LOGGER.debug(f"Valve {valve_idx + 1} ({entity_id}) using valve_duration_{valve_idx + 1}: {duration}s")
                                else:
                                    # Split total duration equally across all valves
                                    duration = int(total_duration / len(global_valves))
                                    _LOGGER.debug(f"Valve {valve_idx + 1} ({entity_id}) using split duration: {duration}s (total: {total_duration}s / {len(global_valves)} valves)")
                                
                                global_entity_runs.append({
                                    "entityId": valve_id,
                                    "durationSec": str(duration)
                                })
                                _LOGGER.debug(f"Added valve {valve_idx + 1}: {entity_id} (id={valve_id}) with duration {duration}s")
                            else:
                                _LOGGER.warning(f"Entity {entity_id} is not a valve/zone entity (unique_id: {entity_entry.unique_id})")
                        else:
                            _LOGGER.warning(f"Entity {entity_id} not found in registry")
                
                # Now process each run's timing configuration
                processed_runs = []
                has_any_run_timing = False
                
                for run_num in [1, 2, 3]:
                    prefix = f"run_{run_num}_"
                    
                    # Check if this run has timing configuration
                    start_time = call.data.get(f"{prefix}start_time")
                    sun_event = call.data.get(f"{prefix}sun_event")
                    sun_offset = call.data.get(f"{prefix}sun_offset", 0)
                    run_concurrently = call.data.get(f"{prefix}run_concurrently", False)
                    cycle_and_soak = call.data.get(f"{prefix}cycle_and_soak", False)
                    
                    # Track if any run timing was specified
                    if start_time or sun_event:
                        has_any_run_timing = True
                    
                    # Skip if no timing specified
                    if not start_time and not sun_event:
                        _LOGGER.debug(f"Run {run_num}: Skipping - no start time or sun event specified")
                        continue
                    
                    # Validate mutually exclusive start types
                    if start_time and sun_event:
                        _LOGGER.error(
                            f"Invalid run {run_num}: Both start_time and sun_event specified. "
                            f"Only one can be used per run."
                        )
                        continue
                    
                    run_data = {}
                    
                    # Handle fixed start time
                    if start_time:
                        if isinstance(start_time, str) and ":" in start_time:
                            parts = start_time.split(":")
                            hour = int(parts[0])
                            minute = int(parts[1]) if len(parts) > 1 else 0
                            run_data["fixedStart"] = {
                                "startAt": {
                                    "hour": hour,
                                    "minute": minute,
                                    "second": 0
                                }
                            }
                            _LOGGER.debug(f"Run {run_num}: Fixed start time {hour:02d}:{minute:02d}")
                    
                    # Handle sun-based start time
                    elif sun_event:
                        offset_seconds = int(sun_offset * 60) if sun_offset else 0
                        run_data["sunStart"] = {
                            "sunEvent": sun_event,
                            "offsetSeconds": str(offset_seconds)
                        }
                        _LOGGER.debug(f"Run {run_num}: Sun event {sun_event} with offset {sun_offset} minutes")
                    
                    # Apply global entity runs to this run
                    if global_entity_runs:
                        run_data["entityRuns"] = global_entity_runs
                        _LOGGER.debug(f"Run {run_num}: Applied {len(global_entity_runs)} global valve(s)")
                    
                    # Add run concurrently and cycle and soak settings
                    run_data["runConcurrently"] = run_concurrently
                    run_data["cycleAndSoak"] = cycle_and_soak
                    
                    if run_concurrently:
                        _LOGGER.debug(f"Run {run_num}: Will run valves concurrently")
                    if cycle_and_soak:
                        _LOGGER.debug(f"Run {run_num}: Cycle and soak enabled")
                    
                    # Only add run if it has configuration
                    if run_data:
                        processed_runs.append(run_data)
                        _LOGGER.info(f"Run {run_num}: Configured with {len(run_data.get('entityRuns', []))} valve(s)")
                
                # Only update plannedRuns if the user specified run timing
                # If they only specified valves without run timing, fetch existing runs and update valves
                if processed_runs:
                    update_data["plannedRuns"] = {
                        "runs": processed_runs
                    }
                    _LOGGER.info(f"Configured {len(processed_runs)} run(s) with {len(global_entity_runs)} valve(s) each")
                elif global_entity_runs and not has_any_run_timing:
                    # User specified valves but no run timing - need to fetch existing program and update valves
                    _LOGGER.info(f"Valves specified without run timing - fetching existing program to preserve run schedule")
                    
                    # Collect any run-specific settings that were provided
                    run_settings = {}
                    for run_num in [1, 2, 3]:
                        prefix = f"run_{run_num}_"
                        settings = {}
                        if f"{prefix}run_concurrently" in call.data:
                            settings["runConcurrently"] = call.data[f"{prefix}run_concurrently"]
                        if f"{prefix}cycle_and_soak" in call.data:
                            settings["cycleAndSoak"] = call.data[f"{prefix}cycle_and_soak"]
                        if settings:
                            run_settings[run_num - 1] = settings  # 0-indexed
                    
                    # Get program entity to extract program_id
                    program_entity_id = call.data.get("program_id")
                    entity_entry = registry.async_get(program_entity_id)
                    if entity_entry and "_program_" in entity_entry.unique_id:
                        program_id = entity_entry.unique_id.split("_program_")[1]
                        device_id = entity_entry.unique_id.split("_program_")[0]
                        
                        # Find handler
                        handler = None
                        for device in hass.data[DOMAIN][entry.entry_id]["devices"].values():
                            if device["handler"].device_id == device_id:
                                handler = device["handler"]
                                break
                        
                        if handler:
                            # Fetch current program details
                            async with ClientSession() as session:
                                details = await handler._fetch_program_details(session, program_id, force_refresh=True)
                                
                                if details and "program" in details:
                                    existing_runs = details["program"].get("plannedRuns", [])
                                    
                                    if existing_runs:
                                        # Update each existing run with new valves and any provided settings
                                        updated_runs = []
                                        for run_idx, run in enumerate(existing_runs):
                                            updated_run = run.copy()
                                            updated_run["entityRuns"] = global_entity_runs
                                            
                                            # Apply any run-specific settings if provided
                                            if run_idx in run_settings:
                                                for key, value in run_settings[run_idx].items():
                                                    updated_run[key] = value
                                                    _LOGGER.debug(f"Run {run_idx + 1}: Updated {key} = {value}")
                                            
                                            updated_runs.append(updated_run)
                                        
                                        update_data["plannedRuns"] = {
                                            "runs": updated_runs
                                        }
                                        _LOGGER.info(f"Updated {len(updated_runs)} existing run(s) with {len(global_entity_runs)} new valve(s)")
                                    else:
                                        _LOGGER.warning("No existing runs found - cannot update valves without specifying run timing")
                                else:
                                    _LOGGER.error("Failed to fetch existing program details")
                        else:
                            _LOGGER.error("Handler not found for program update")
                    else:
                        _LOGGER.error("Invalid program entity for valve-only update")
            
            # Handle advanced runs configuration (supports multiple runs per day)
            elif "runs" in call.data:
                runs_config = call.data["runs"]
                if isinstance(runs_config, (list, dict)):
                    # Handle both list format and dict format
                    runs_list = runs_config if isinstance(runs_config, list) else [runs_config]
                    
                    # Get entity registry to resolve entity IDs to valve IDs
                    from homeassistant.helpers import entity_registry as er
                    registry = er.async_get(hass)
                    
                    processed_runs = []
                    for run_idx, run_entry in enumerate(runs_list):
                        if not isinstance(run_entry, dict):
                            continue
                        
                        run_data = {}
                        
                        # Validate mutually exclusive start types within this run
                        has_fixed_start = "start_time" in run_entry
                        has_sun_start = "sun_event" in run_entry
                        
                        if has_fixed_start and has_sun_start:
                            _LOGGER.error(
                                f"Invalid run {run_idx + 1}: Both start_time and sun_event specified. "
                                f"Only one can be used per run."
                            )
                            continue
                        
                        # Fixed start time
                        if has_fixed_start:
                            time_str = run_entry["start_time"]
                            if isinstance(time_str, str) and ":" in time_str:
                                parts = time_str.split(":")
                                hour = int(parts[0])
                                minute = int(parts[1]) if len(parts) > 1 else 0
                                run_data["fixedStart"] = {
                                    "startAt": {
                                        "hour": hour,
                                        "minute": minute,
                                        "second": 0
                                    }
                                }
                        
                        # Sun-based start time
                        elif has_sun_start:
                            sun_event = run_entry["sun_event"]
                            offset_minutes = run_entry.get("sun_offset_minutes", 0)
                            offset_seconds = int(offset_minutes * 60)
                            run_data["sunStart"] = {
                                "sunEvent": sun_event,
                                "offsetSeconds": str(offset_seconds)
                            }
                        
                        # Process valves for this run
                        if "valves" in run_entry:
                            valves_config = run_entry["valves"]
                            if isinstance(valves_config, (list, dict)):
                                valve_list = valves_config if isinstance(valves_config, list) else [valves_config]
                                
                                entity_runs = []
                                for valve_entry in valve_list:
                                    if not isinstance(valve_entry, dict):
                                        continue
                                    
                                    entity_id = valve_entry.get("entity_id")
                                    duration = valve_entry.get("duration", 300)
                                    
                                    if not entity_id:
                                        continue
                                    
                                    entity_entry = registry.async_get(entity_id)
                                    if entity_entry:
                                        valve_id = None
                                        # Check for Smart Hose Timer zones (format: {device_id}_{zone_id}_zone)
                                        if "_zone" in entity_entry.unique_id:
                                            parts = entity_entry.unique_id.split("_zone")
                                            if len(parts) == 2 and parts[0]:
                                                # Extract zone_id (everything after device_id and before _zone)
                                                device_and_zone = parts[0]
                                                # Find device_id prefix and extract the zone_id part
                                                # Format is: {device_id}_{zone_id}_zone
                                                zone_id = device_and_zone.split("_", 1)[1] if "_" in device_and_zone else device_and_zone
                                                valve_id = zone_id
                                        # Check for controller valves (format: {device_id}_valve_{valve_id})
                                        elif "_valve_" in entity_entry.unique_id:
                                            valve_id = entity_entry.unique_id.split("_valve_")[-1]
                                        
                                        if valve_id:
                                            entity_runs.append({
                                                "entityId": valve_id,
                                                "durationSec": str(duration)
                                            })
                                        else:
                                            _LOGGER.warning(f"Entity {entity_id} is not a valve/zone entity (unique_id: {entity_entry.unique_id})")
                                    else:
                                        _LOGGER.warning(f"Entity {entity_id} not found in registry")
                                
                                if entity_runs:
                                    run_data["entityRuns"] = entity_runs
                        
                        # Only add run if it has configuration
                        if run_data:
                            processed_runs.append(run_data)
                            _LOGGER.debug(f"Added run {run_idx + 1}: {list(run_data.keys())}")
                    
                    if processed_runs:
                        update_data["plannedRuns"] = {
                            "runs": processed_runs
                        }
                        _LOGGER.info(f"Configured {len(processed_runs)} run(s) for program")
            
            await _handle_program_update(call, update_data)
        
        # Register services
        hass.services.async_register(DOMAIN, "enable_program", handle_enable_program)
        hass.services.async_register(DOMAIN, "disable_program", handle_disable_program)
        hass.services.async_register(DOMAIN, "create_program", handle_create_program)
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
        hass.services.async_remove(DOMAIN, "create_program")
        hass.services.async_remove(DOMAIN, "update_program")
        _LOGGER.info("Unregistered Smart Hose Timer program management services")
        
    return unload_ok
