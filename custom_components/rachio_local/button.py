"""Support for Rachio buttons."""
from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
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

        self._attr_name = f"Refresh {program_name} Details"
        self._attr_unique_id = f"{handler.device_id}_refresh_program_{self.program_id}"
        self._attr_icon = "mdi:refresh"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_entity_registry_enabled_default = False  # Hidden by default

    async def async_press(self) -> None:
        """Handle the button press - refresh program details."""
        _LOGGER.info(f"Refreshing program details for {self.program_id}")

        # Force refresh the program details by clearing the cache timestamp
        if self.program_id in self.handler._program_details:
            # Set last_fetched to 0 to force a refresh on next coordinator update
            self.handler._program_details[self.program_id]["last_fetched"] = 0
            _LOGGER.debug(f"Cleared cache timestamp for program {self.program_id}")

        # Trigger coordinator refresh to fetch fresh data
        await self.coordinator.async_request_refresh()
        _LOGGER.info(f"Program {self.program_id} details refresh requested")
