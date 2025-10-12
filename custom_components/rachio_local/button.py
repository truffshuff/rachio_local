"""Support for Rachio buttons."""
from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, DEVICE_TYPE_SMART_HOSE_TIMER

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the Rachio buttons from config entry."""
    entities = []
    entry_data = hass.data[DOMAIN][config_entry.entry_id]["devices"]

    # Store the async_add_entities callback for dynamic button creation
    if "button_add_entities" not in hass.data[DOMAIN][config_entry.entry_id]:
        hass.data[DOMAIN][config_entry.entry_id]["button_add_entities"] = async_add_entities

    for device_id, data in entry_data.items():
        handler = data["handler"]
        coordinator = data["coordinator"]

        # Add full coordinator refresh button for all device types
        entities.append(RachioFullRefreshButton(coordinator, handler))
        _LOGGER.debug(f"Added full refresh button for device {handler.name}")

        # Add refresh button for Smart Hose Timer programs
        if handler.type == DEVICE_TYPE_SMART_HOSE_TIMER:
            # Track which programs we've created buttons for
            if not hasattr(handler, '_program_button_ids'):
                handler._program_button_ids = set()

            for program in handler.schedules:
                program_id = program.get("id")
                if program_id:
                    handler._program_button_ids.add(program_id)
                    entities.append(RachioRefreshProgramButton(coordinator, handler, program))
                    _LOGGER.debug(f"Added refresh button for program {program.get('name', program_id)}")

            # Set up callback for dynamic button creation when new programs are detected
            handler._button_add_entities_callback = async_add_entities

    _LOGGER.info(f"Adding {len(entities)} Rachio button entities")
    async_add_entities(entities)


class RachioBaseButtonEntity(CoordinatorEntity, ButtonEntity):
    """Base class for Rachio button entities."""

    def __init__(self, coordinator, handler):
        """Initialize button properties."""
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


class RachioRefreshProgramButton(RachioBaseButtonEntity):
    """Button to refresh program details from API."""

    def __init__(self, coordinator, handler, program):
        """Initialize the button."""
        super().__init__(coordinator, handler)
        self.program_id = program.get("id")
        program_name = program.get("name", f"Program {self.program_id[:8]}")

        self._attr_name = f"Refresh: {program_name} Details"
        self._attr_unique_id = f"{handler.device_id}_refresh_program_{self.program_id}"
        self._attr_icon = "mdi:refresh"
        # No entity_category = shows in Controls section
        self._attr_entity_registry_enabled_default = False  # Hidden by default

    @property
    def available(self) -> bool:
        """Return True if entity is available (program still exists)."""
        # Program is unavailable only if it's been confirmed as deleted
        # Disabled programs should still show as available
        if hasattr(self.handler, '_deleted_programs'):
            return self.program_id not in self.handler._deleted_programs
        # Fallback: check if program exists in schedules
        for schedule in self.handler.schedules:
            if schedule.get("id") == self.program_id:
                return True
        return False

    async def async_press(self) -> None:
        """Handle the button press - refresh program details."""
        from aiohttp import ClientSession

        _LOGGER.info(f"Refreshing program details for {self.program_id}")

        # Directly fetch fresh program details (single API call)
        async with ClientSession() as session:
            details = await self.handler._fetch_program_details(session, self.program_id, force_refresh=True)

            if details and "program" in details:
                program_details = details["program"]

                # Update the program in schedules with fresh data
                for program in self.handler.schedules:
                    if program.get("id") == self.program_id:
                        # Update all program details
                        program["enabled"] = program_details.get("enabled", True)
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

                        _LOGGER.info(f"Successfully refreshed program '{program.get('name')}' details")
                        break

                # Trigger a state update for sensors (without doing a full refresh)
                self.coordinator.async_set_updated_data(self.coordinator.data)
            else:
                _LOGGER.warning(f"Failed to refresh program {self.program_id} - program may have been deleted")

        _LOGGER.debug(f"Program {self.program_id} refresh complete (1 API call)")


class RachioFullRefreshButton(RachioBaseButtonEntity):
    """Button to trigger a full coordinator refresh."""

    def __init__(self, coordinator, handler):
        """Initialize the button."""
        super().__init__(coordinator, handler)

        self._attr_name = "Refresh: Full"
        self._attr_unique_id = f"{handler.device_id}_full_refresh"
        self._attr_icon = "mdi:refresh-circle"
        # No entity_category = shows in Controls section
        self._attr_entity_registry_enabled_default = False  # Hidden by default

    async def async_press(self) -> None:
        """Handle the button press - trigger full coordinator refresh."""
        _LOGGER.info(f"Full coordinator refresh requested for device {self.handler.name}")

        # Trigger full coordinator refresh (base station + valves + summary + program details)
        await self.coordinator.async_request_refresh()

        _LOGGER.info(f"Full coordinator refresh complete for device {self.handler.name}")
