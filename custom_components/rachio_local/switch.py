"""Rachio switch platform."""
import logging
from datetime import datetime

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Rachio switches."""
    coordinator = hass.data[DOMAIN][config_entry.entry_id]
    
    switches = []
    for zone in coordinator.zones:
        switches.append(RachioZoneSwitch(coordinator, zone))
    for schedule in coordinator.schedules:
        switches.append(RachioScheduleSwitch(coordinator, schedule))
    
    async_add_entities(switches)

class RachioZoneSwitch(CoordinatorEntity, SwitchEntity):
    """Representation of a Rachio zone watering switch."""

    def __init__(self, coordinator, zone):
        """Initialize the switch."""
        super().__init__(coordinator)
        self._zone = zone
        self._attr_name = f"Rachio {zone['name']} Watering"
        self._attr_unique_id = f"rachio_zone_{zone['id']}_watering"
        self._attr_friendly_name = self._attr_name

    @property
    def is_on(self):
        """Return true if zone is currently running."""
        is_running = any(
            running_zone['id'] == self._zone['id']
            for running_zone in self.coordinator.running_zones
        )
        _LOGGER.debug(f"Zone {self._zone['id']} ({self._zone['name']}) - Is running: {is_running}")
        return is_running

    @property
    def extra_state_attributes(self):
        """Return additional state attributes."""
        running_info = next(
            (running_zone for running_zone in self.coordinator.running_zones 
             if running_zone['id'] == self._zone['id']), 
            None
        )
        
        attrs = {
            'zone_id': self._zone['id'],
            'zone_number': self._zone.get('zoneNumber'),
            'last_watered': self._zone.get('lastWateredDate'),
            'running': bool(running_info),
            'remaining_time': running_info.get('remaining_time') if running_info else None,
            'schedule_name': running_info.get('schedule_name') if running_info else None,
            'run_type': running_info.get('run_type') if running_info else None,
            'detected_externally': running_info.get('run_type') == 'external' if running_info else False
        }
        return attrs

    async def async_turn_on(self, **kwargs):
        """Turn the switch on."""
        duration = kwargs.get('duration', 600)  # Default 10 minutes
        try:
            await self.coordinator.start_zone(self._zone['id'], duration)
            await self.coordinator.async_request_refresh()
        except Exception as e:
            _LOGGER.error(f"Error turning on zone {self._zone['name']}: {e}")

    async def async_turn_off(self, **kwargs):
        """Turn the switch off."""
        try:
            await self.coordinator.stop_zone(self._zone['id'])
            await self.coordinator.async_request_refresh()
        except Exception as e:
            _LOGGER.error(f"Error turning off zone {self._zone['name']}: {e}")

class RachioScheduleSwitch(CoordinatorEntity, SwitchEntity):
    """Representation of a Rachio schedule watering switch."""

    def __init__(self, coordinator, schedule):
        """Initialize the switch."""
        super().__init__(coordinator)
        self._schedule = schedule
        self._attr_name = f"Rachio {schedule.get('name', 'Unknown')} Schedule Watering"
        self._attr_unique_id = f"rachio_schedule_{schedule.get('id')}_watering"
        self._attr_friendly_name = self._attr_name

    @property
    def is_on(self):
        """Return true if schedule is currently running."""
        is_running = any(
            running_schedule['id'] == self._schedule['id']
            for running_schedule in self.coordinator.running_schedules
        )
        _LOGGER.debug(f"Schedule {self._schedule['id']} ({self._schedule.get('name', 'Unknown')}) - Is running: {is_running}")
        return is_running

    @property
    def extra_state_attributes(self):
        """Return additional state attributes."""
        running_info = next(
            (running_schedule for running_schedule in self.coordinator.running_schedules 
             if running_schedule['id'] == self._schedule['id']),
            None
        )
        active_schedule_info = self.coordinator._active_schedules.get(self._schedule['id'], {})

        attrs = {
            'schedule_id': self._schedule['id'],
            'schedule_name': self._schedule.get('name'),
            'total_duration': active_schedule_info.get('total_duration') if active_schedule_info else self._schedule.get('totalDuration'),  # Updated
            'running': bool(running_info),
            'current_zone_id': running_info.get('running_zone_id') if running_info else None,
            'current_zone_name': running_info.get('running_zone_name', 'Unknown Zone') if running_info else 'No Zone Active',
            'detected_externally': running_info is not None and 'run_type' not in running_info,
            'manually_persisted': bool(active_schedule_info and (not running_info or running_info.get('running_zone_id') is None)),
            'remaining_time': (
                (active_schedule_info.get('total_duration') - 
                 (datetime.now() - active_schedule_info.get('start_time', datetime.now())).total_seconds())
                if active_schedule_info else None
            )
        }
        return attrs

    async def async_turn_on(self, **kwargs):
        """Turn the switch on."""
        try:
            await self.coordinator.start_schedule(self._schedule['id'])
            await self.coordinator.async_request_refresh()
        except Exception as e:
            _LOGGER.error(f"Error turning on schedule {self._schedule.get('name', 'Unknown')}: {e}")

    async def async_turn_off(self, **kwargs):
        """Turn the switch off."""
        try:
            await self.coordinator.stop_schedule(self._schedule['id'])
            await self.coordinator.async_request_refresh()
        except Exception as e:
            _LOGGER.error(f"Error turning off schedule {self._schedule.get('name', 'Unknown')}: {e}")
