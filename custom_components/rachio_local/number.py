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
CONF_PROGRAM_DETAILS_REFRESH_INTERVAL = "program_details_refresh_interval"
CONF_SUMMARY_END_DAYS = "summary_end_days"


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

        # Add program details refresh interval entity
        entities.append(RachioProgramDetailsRefreshIntervalNumber(coordinator, handler, entry))



    # Add summary end days number per device (base station)
    for device_id, device_info in data["devices"].items():
        entities.append(RachioSummaryEndDaysNumber(entry, device_id, device_info["handler"]))

    async_add_entities(entities)

# Global number entity for summary end days (future days for getValveDayViews)
class RachioSummaryEndDaysNumber(NumberEntity):
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_native_unit_of_measurement = "days"
    _attr_native_min_value = 1
    _attr_native_max_value = 30
    _attr_native_step = 1
    _attr_has_entity_name = True
    _attr_name = "Summary end days (future)"
    _attr_icon = "mdi:calendar-range"

    def __init__(self, entry: ConfigEntry, device_id: str, handler) -> None:
        self.entry = entry
        self.device_id = device_id
        self.handler = handler
        self._attr_unique_id = f"{device_id}_summary_end_days"


    @property
    def native_value(self) -> float:
        # Device-specific config key using constant
        config_key = f"{CONF_SUMMARY_END_DAYS}_{self.device_id}"
        return self.entry.options.get(config_key, 7)


    async def async_set_native_value(self, value: float) -> None:
        int_value = int(value)
        config_key = f"{CONF_SUMMARY_END_DAYS}_{self.device_id}"
        new_options = {**self.entry.options, config_key: int_value}
        self.hass.config_entries.async_update_entry(self.entry, options=new_options)
        _LOGGER.info("Summary end days for %s changed to %d (persisted)", self.device_id, int_value)

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self.device_id)},
            "name": self.handler.name,
            "manufacturer": "Rachio",
            "model": self.handler.model,
        }


class RachioPollingIntervalNumber(CoordinatorEntity, NumberEntity):
    """Base class for Rachio polling interval number entities."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_native_unit_of_measurement = "s"
    _attr_native_min_value = 30
    _attr_native_max_value = 2400
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

    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_native_unit_of_measurement = "s"
    _attr_native_min_value = 30
    _attr_native_max_value = 2400
    _attr_native_step = 5

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

class RachioProgramDetailsRefreshIntervalNumber(CoordinatorEntity, NumberEntity):
    """Number entity for program details refresh interval."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_native_unit_of_measurement = "min"
    _attr_native_min_value = 5  # Minimum 5 minutes
    _attr_native_max_value = 1440  # Maximum 24 hours
    _attr_native_step = 5  # Step by 5 minutes

    def __init__(self, coordinator, handler, entry: ConfigEntry) -> None:
        """Initialize the program details refresh interval number."""
        super().__init__(coordinator)
        self.handler = handler
        self.entry = entry
        self._attr_unique_id = f"{handler.device_id}_program_details_refresh_interval"
        self._attr_name = "Program details refresh interval"
        self._attr_icon = "mdi:timer-cog-outline"
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

    @property
    def native_value(self) -> float:
        """Return the current value in minutes."""
        # Convert from seconds to minutes
        return getattr(self.handler, "_program_details_refresh_interval", 3600) / 60

    async def async_set_native_value(self, value: float) -> None:
        """Set the program details refresh interval."""
        # Convert from minutes to seconds
        int_value = int(value * 60)
        old_value_minutes = getattr(self.handler, "_program_details_refresh_interval", 3600) / 60

        self.handler._program_details_refresh_interval = int_value

        # Invalidate all program cache timestamps to force refresh on next coordinator update
        # This ensures the new interval takes effect immediately
        if hasattr(self.handler, '_program_details'):
            for program_id in self.handler._program_details:
                self.handler._program_details[program_id]["last_fetched"] = 0
            _LOGGER.debug(
                "%s: Invalidated %d program cache entries to apply new refresh interval",
                self.handler.name,
                len(self.handler._program_details)
            )

        # Persist to config entry options
        config_key = f"{CONF_PROGRAM_DETAILS_REFRESH_INTERVAL}_{self.handler.device_id}"
        new_options = {**self.entry.options, config_key: int_value}
        self.hass.config_entries.async_update_entry(self.entry, options=new_options)

        _LOGGER.info(
            "%s: Program details refresh interval changed from %.0f minutes to %.0f minutes (%d seconds, persisted)",
            self.handler.name,
            old_value_minutes,
            value,
            int_value,
        )

        # Trigger coordinator refresh to apply the new interval immediately
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
