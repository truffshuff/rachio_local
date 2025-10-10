"""Support for Rachio switches."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback, async_get_current_platform
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    STATE_ONLINE,
    DEVICE_TYPE_CONTROLLER,
    DEVICE_TYPE_SMART_HOSE_TIMER,
)

_LOGGER = logging.getLogger(__name__)

RAIN_DELAY_OPTIONS = [
    (12, "12 hours"),
    (24, "24 hours"),
    (48, "2 days"),
    (72, "3 days"),
    (168, "1 week"),
]

class RachioRainDelayDurationSelect(SelectEntity):
    def __init__(self, handler):
        self._handler = handler
        self._attr_name = f"{handler.name} Rain Delay Duration"
        self._attr_unique_id = f"{handler.device_id}_rain_delay_duration"
        self._attr_entity_category = EntityCategory.CONFIG
        self._attr_options = [label for _, label in RAIN_DELAY_OPTIONS]
        self._selected_hours = 24
        self._attr_current_option = self._get_label(self._selected_hours)

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._handler.device_id)},
            "name": self._handler.name,
            "model": self._handler.model,
            "manufacturer": "Rachio",
        }

    def _get_label(self, hours):
        for h, label in RAIN_DELAY_OPTIONS:
            if h == hours:
                return label
        return f"{hours} hours"

    @property
    def current_option(self):
        return self._attr_current_option

    async def async_select_option(self, option: str):
        for hours, label in RAIN_DELAY_OPTIONS:
            if label == option:
                self._selected_hours = hours
                self._attr_current_option = label
                self.async_write_ha_state()
                return

    def get_selected_hours(self):
        return self._selected_hours

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Rachio switches from config entry."""
    entities = []
    entry_data = hass.data[DOMAIN][config_entry.entry_id]["devices"]

    for device_id, data in entry_data.items():
        handler = data["handler"]
        coordinator = data["coordinator"]

        if handler.type == DEVICE_TYPE_CONTROLLER:
            entities.append(RachioStandbySwitch(coordinator, handler))
            for zone in handler.zones:
                if zone.get("enabled", True):
                    entities.append(RachioZoneSwitch(coordinator, handler, zone))
            for schedule in handler.schedules:
                entities.append(RachioScheduleSwitch(coordinator, handler, schedule))
            # Always add rain delay duration select for controllers
            entities.append(RachioRainDelayDurationSelect(handler))
        elif handler.type == DEVICE_TYPE_SMART_HOSE_TIMER:
            for valve in handler.zones:
                entities.append(RachioValveSwitch(coordinator, handler, valve))
            # Note: Program switches removed - Smart Hose Timers run programs automatically
            # on their configured schedule. Program information is available as sensors instead.
    async_add_entities(entities)

    # Register 'duration' as an optional parameter for turn_on, and register turn_off for custom stop
    platform = async_get_current_platform()
    platform.async_register_entity_service(
        "turn_on",
        {"duration": int},
        "async_turn_on",
    )
    platform.async_register_entity_service(
        "turn_off",
        {},
        "async_turn_off",
    )

class RachioSwitch(CoordinatorEntity, SwitchEntity):
    """Base class for Rachio switches."""

    def __init__(self, coordinator, handler):
        """Initialize the switch."""
        super().__init__(coordinator)
        self.handler = handler
        self._attr_has_entity_name = True

    @property
    def device_info(self):
        """Return device info."""
        return {
            "identifiers": {(DOMAIN, self.handler.device_id)},
            "name": self.handler.name,
            "model": self.handler.model,
            "manufacturer": "Rachio",
        }

class RachioZoneSwitch(RachioSwitch):
    """Representation of a zone switch."""

    def __init__(self, coordinator, handler, zone):
        """Initialize the zone switch."""
        super().__init__(coordinator, handler)
        self.zone_id = zone["id"]
        self.zone_name = zone.get("name", f"Zone {zone.get('zoneNumber', '')}")
        self._attr_name = f"{self.zone_name} Zone"
        self._attr_unique_id = f"{handler.device_id}_{self.zone_id}_zone"

    @property
    def is_on(self):
        # Use handler's shared optimistic state logic
        state = self.handler.is_zone_optimistically_on(self.zone_id)
        _LOGGER.debug(f"[ZoneSwitch] is_on check: zone_id={self.zone_id}, is_on={state}, running_zones={list(self.handler.running_zones.keys())}, pending_start={getattr(self.handler, '_pending_start', {})}")
        return state

    @property
    def extra_state_attributes(self):
        """Return extra state attributes."""
        return {
            "default_duration": self.handler.get_zone_default_duration(self.zone_id)
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        duration = kwargs.get("duration")
        if duration is None:
            duration = self.handler.get_zone_default_duration(self.zone_id)
        await self.handler.async_start_zone(self.zone_id, duration=duration)
        self.async_write_ha_state()  # Update UI immediately
        # Schedule refresh after 5 seconds to allow API to catch up
        asyncio.create_task(self._delayed_refresh(5))

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        _LOGGER.debug(f"[ZoneSwitch] async_turn_off called: zone_id={self.zone_id}")
        # Always call async_stop_zone - it will handle whether the zone is actually running
        # We need to clear optimistic state even if the zone isn't actually running
        await self.handler.async_stop_zone(self.zone_id)
        # async_stop_zone already clears running_zones and _pending_start
        self.async_write_ha_state()  # Update UI immediately
        # Schedule refresh after 5 seconds to allow API to catch up
        asyncio.create_task(self._delayed_refresh(5))

    async def _delayed_refresh(self, delay: int) -> None:
        """Refresh coordinator after a delay."""
        await asyncio.sleep(delay)
        await self.coordinator.async_request_refresh()

class RachioScheduleSwitch(RachioSwitch):
    """Representation of a schedule switch."""

    def __init__(self, coordinator, handler, schedule):
        """Initialize the schedule switch."""
        super().__init__(coordinator, handler)
        self.schedule_id = schedule["id"]
        self.schedule_name = schedule.get("name", "Schedule")
        self._attr_name = f"{self.schedule_name} Schedule"
        self._attr_unique_id = f"{handler.device_id}_{self.schedule_id}_schedule"
        # Remove entity category to move to Controls
        self._attr_entity_category = None

    @property
    def is_on(self):
        """Return if the switch is on (true API state, not just optimistic)."""
        return self.schedule_id in self.handler.running_schedules

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        await self.handler.async_start_schedule(self.schedule_id)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        if self.is_on:
            await self.handler.async_stop_schedule(self.schedule_id)
            await self.coordinator.async_request_refresh()

class RachioValveSwitch(RachioZoneSwitch):
    """Representation of a valve switch."""

    @property
    def is_on(self):
        """Return if the valve is on (using optimistic state)."""
        return self.handler.is_zone_optimistically_on(self.zone_id)

    @property
    def extra_state_attributes(self):
        """Return extra state attributes including run history."""
        attributes = {
            "default_duration": self.handler.get_zone_default_duration(self.zone_id)
        }

        # Add run summary information if available
        if hasattr(self.handler, 'valve_run_summaries') and self.zone_id in self.handler.valve_run_summaries:
            summaries = self.handler.valve_run_summaries[self.zone_id]

            # Add previous run information
            if summaries.get("previous_run"):
                prev = summaries["previous_run"]
                attributes["previous_run_start"] = prev["start_str"]
                attributes["previous_run_duration_seconds"] = prev["duration_seconds"]
                attributes["previous_run_duration_minutes"] = prev["duration_seconds"] // 60
                if prev.get("flow_detected") is not None:
                    attributes["previous_run_flow_detected"] = prev["flow_detected"]
                if prev.get("source"):
                    attributes["previous_run_source"] = prev["source"]
                if prev.get("program_name"):
                    attributes["previous_run_program"] = prev["program_name"]
                if prev.get("skipped"):
                    attributes["previous_run_skipped"] = prev["skipped"]

            # Add next run information
            if summaries.get("next_run"):
                next_run = summaries["next_run"]
                attributes["next_run_start"] = next_run["start_str"]
                attributes["next_run_duration_seconds"] = next_run["duration_seconds"]
                attributes["next_run_duration_minutes"] = next_run["duration_seconds"] // 60
                if next_run.get("source"):
                    attributes["next_run_source"] = next_run["source"]
                if next_run.get("program_name"):
                    attributes["next_run_program"] = next_run["program_name"]

        return attributes

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        duration = kwargs.get("duration")
        if duration is None:
            duration = self.handler.get_zone_default_duration(self.zone_id)
        await self.handler.async_start_zone(self.zone_id, duration=duration)
        self.async_write_ha_state()  # Update UI immediately
        # Schedule refresh after 5 seconds to allow API to catch up
        asyncio.create_task(self._delayed_refresh(5))

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        _LOGGER.debug(f"[ValveSwitch] async_turn_off called: zone_id={self.zone_id}")
        # Always call async_stop_zone - it will handle whether the valve is actually running
        # We need to clear optimistic state even if the valve isn't actually running
        await self.handler.async_stop_zone(self.zone_id)
        # async_stop_zone already clears running_zones and _pending_start
        self.async_write_ha_state()  # Update UI immediately
        # Schedule refresh after 5 seconds to allow API to catch up
        asyncio.create_task(self._delayed_refresh(5))

class RachioTimerProgramSwitch(RachioScheduleSwitch):
    """Representation of a valve program switch."""

    def __init__(self, coordinator, handler, program):
        super().__init__(coordinator, handler, program)
        self.valve_ids = program.get("valveIds") or program.get("zoneIds") or []

    def _is_valve_running(self, valve):
        # Check if the valve is currently running based on lastWateringAction
        action = valve.get("state", {}).get("reportedState", {}).get("lastWateringAction", {})
        start_str = action.get("start")
        duration = action.get("durationSeconds", 0)
        if not start_str or duration == 0:
            return False
        try:
            start = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
        except Exception:
            return False
        end = start + timedelta(seconds=duration)
        now = datetime.now(timezone.utc)
        return now < end

    @property
    def is_on(self):
        # Optimistic: always on for 60 seconds after start
        now = time.time()
        optimistic_window = 60
        if hasattr(self.handler, '_pending_start') and self.schedule_id in self.handler._pending_start:
            if self.handler._pending_start[self.schedule_id] > now:
                return True  # Still in optimistic window
        # After 60s, use real valve status
        if self.valve_ids and hasattr(self.handler, "zones"):
            for valve in self.handler.zones:
                if valve.get("id") in self.valve_ids and self._is_valve_running(valve):
                    return True
            return False
        # Fallback to optimistic logic if no valve IDs
        return (
            self.schedule_id in self.handler.running_schedules
        )

class RachioStandbySwitch(RachioSwitch):
    """Representation of a standby switch."""

    def __init__(self, coordinator, handler):
        """Initialize the standby switch."""
        super().__init__(coordinator, handler)
        self._attr_name = f"{handler.name} Standby"
        self._attr_unique_id = f"{handler.device_id}_standby"
        self._attr_entity_category = EntityCategory.CONFIG

    @property
    def is_on(self):
        """Return if the switch is on."""
        return not self.handler.device_data.get("disabled", False)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on (disable standby)."""
        # Enable device
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off (enable standby)."""
        # Disable device
        await self.coordinator.async_request_refresh()

class RachioRainDelaySwitch(RachioSwitch):
    """Representation of a rain delay switch."""

    def __init__(self, coordinator, handler):
        super().__init__(coordinator, handler)
        self._attr_name = f"{handler.name} Rain Delay"
        self._attr_unique_id = f"{handler.device_id}_rain_delay"
        self._attr_entity_category = EntityCategory.CONFIG

    @property
    def is_on(self):
        return bool(self.handler.device_data.get("rainDelayExpirationDate"))

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on (enable rain delay for selected duration)."""
        # Find the select entity for this handler
        duration_hours = 24
        for entity in self.platform.entities:
            if isinstance(entity, RachioRainDelayDurationSelect) and entity._handler == self.handler:
                duration_hours = entity.get_selected_hours()
                break
        await self.handler.async_set_rain_delay(duration_hours)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off (disable rain delay)."""
        await self.handler.async_clear_rain_delay()
        await self.coordinator.async_request_refresh()
