"""Number platform for Rachio Local integration."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Config entry option keys
CONF_IDLE_POLLING_INTERVAL = "idle_polling_interval"
CONF_ACTIVE_POLLING_INTERVAL = "active_polling_interval"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Rachio number entities."""
    data = hass.data[DOMAIN][entry.entry_id]
    entities = []

    for device_id, device_info in data["devices"].items():
        handler = device_info["handler"]
        coordinator = device_info["coordinator"]

        # Add polling interval entities
        entities.append(RachioIdlePollingIntervalNumber(coordinator, handler, entry))
        entities.append(RachioActivePollingIntervalNumber(coordinator, handler, entry))

    async_add_entities(entities)


class RachioPollingIntervalNumber(CoordinatorEntity, NumberEntity):
    """Base class for Rachio polling interval number entities."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_mode = NumberMode.BOX
    _attr_native_unit_of_measurement = "s"
    _attr_native_min_value = 30
    _attr_native_max_value = 600
    _attr_native_step = 10

    def __init__(self, coordinator, handler, entry: ConfigEntry, interval_type: str) -> None:
        """Initialize the number entity."""
        super().__init__(coordinator)
        self.handler = handler
        self.entry = entry
        self.interval_type = interval_type
        self._attr_unique_id = f"{handler.device_id}_{interval_type}_polling_interval"
        self._attr_has_entity_name = True

    @property
    def device_info(self):
        """Return device info."""
        return {
            "identifiers": {(DOMAIN, self.handler.device_id)},
            "name": self.handler.name,
            "manufacturer": "Rachio",
            "model": self.handler.model,
        }


class RachioIdlePollingIntervalNumber(RachioPollingIntervalNumber):
    """Number entity for idle polling interval."""

    def __init__(self, coordinator, handler, entry: ConfigEntry) -> None:
        """Initialize the idle polling interval number."""
        super().__init__(coordinator, handler, entry, "idle")
        self._attr_name = "Idle polling interval"
        self._attr_icon = "mdi:timer-outline"

    @property
    def native_value(self) -> float:
        """Return the current value."""
        return getattr(self.handler, "idle_polling_interval", 300)

    async def async_set_native_value(self, value: float) -> None:
        """Set the polling interval."""
        int_value = int(value)
        self.handler.idle_polling_interval = int_value

        # Persist to config entry options
        config_key = f"{CONF_IDLE_POLLING_INTERVAL}_{self.handler.device_id}"
        new_options = {**self.entry.options, config_key: int_value}
        self.hass.config_entries.async_update_entry(self.entry, options=new_options)

        _LOGGER.info(
            "%s: Idle polling interval changed to %d seconds (persisted)",
            self.handler.name,
            int_value,
        )
        # Trigger coordinator update to recalculate interval
        await self.coordinator.async_request_refresh()


class RachioActivePollingIntervalNumber(RachioPollingIntervalNumber):
    """Number entity for active watering polling interval."""

    def __init__(self, coordinator, handler, entry: ConfigEntry) -> None:
        """Initialize the active polling interval number."""
        super().__init__(coordinator, handler, entry, "active")
        self._attr_name = "Active watering polling interval"
        self._attr_icon = "mdi:timer"

    @property
    def native_value(self) -> float:
        """Return the current value."""
        return getattr(self.handler, "active_polling_interval", 120)

    async def async_set_native_value(self, value: float) -> None:
        """Set the polling interval."""
        int_value = int(value)
        self.handler.active_polling_interval = int_value

        # Persist to config entry options
        config_key = f"{CONF_ACTIVE_POLLING_INTERVAL}_{self.handler.device_id}"
        new_options = {**self.entry.options, config_key: int_value}
        self.hass.config_entries.async_update_entry(self.entry, options=new_options)

        _LOGGER.info(
            "%s: Active polling interval changed to %d seconds (persisted)",
            self.handler.name,
            int_value,
        )
        # Trigger coordinator update to recalculate interval
        await self.coordinator.async_request_refresh()
