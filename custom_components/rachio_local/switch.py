"""Rachio switch platform."""
import logging

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
    
    # Create switches
    switches = []
    
    # Zone watering switches
    for zone in coordinator.zones:
        switches.append(RachioZoneSwitch(coordinator, zone))
    
    # Schedule watering switches
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

    @property
    def is_on(self):
        """Return true if zone is currently running."""
        # Check if zone is directly running
        direct_running = any(
            running_zone['id'] == self._zone['id'] 
            for running_zone in self.coordinator.running_zones
        )
        
        # Check if zone is running as part of a schedule
        schedule_running = False
        if hasattr(self.coordinator, 'current_schedule'):
            current_schedule = self.coordinator.current_schedule
            if current_schedule and 'zones' in current_schedule:
                schedule_running = any(
                    zone.get('id') == self._zone['id'] and zone.get('active', False)
                    for zone in current_schedule['zones']
                )
        
        return direct_running or schedule_running

    @property
    def extra_state_attributes(self):
        """Return additional state attributes."""
        running_info = next(
            (running_zone for running_zone in self.coordinator.running_zones 
             if running_zone['id'] == self._zone['id']), 
            None
        )
        
        # Check schedule running info
        schedule_info = None
        if hasattr(self.coordinator, 'current_schedule'):
            current_schedule = self.coordinator.current_schedule
            if current_schedule and 'zones' in current_schedule:
                schedule_info = next(
                    (zone for zone in current_schedule['zones'] 
                     if zone.get('id') == self._zone['id'] and zone.get('active', False)),
                    None
                )
        
        attrs = {
            'zone_id': self._zone['id'],
            'zone_number': self._zone.get('zoneNumber'),
            'last_watered': self._zone.get('lastWateredDate'),
            'running': False,
            'remaining_time': None,
            'schedule_name': None
        }
        
        if running_info:
            attrs.update({
                'running': True,
                'remaining_time': running_info.get('remaining_duration', 0),
                'run_type': 'manual'
            })
        elif schedule_info:
            attrs.update({
                'running': True,
                'remaining_time': schedule_info.get('remainingDuration', 0),
                'run_type': 'schedule',
                'schedule_name': self.coordinator.current_schedule.get('name')
            })
        
        return attrs

    async def async_turn_on(self, **kwargs):
        """Turn the switch on."""
        duration = kwargs.get('duration', 600)  # Default 10 minutes
        try:
            await self.coordinator.start_zone(
                self._zone['id'], 
                duration
            )
        except Exception as e:
            _LOGGER.error(f"Error turning on zone {self._zone['name']}: {e}")

    async def async_turn_off(self, **kwargs):
        """Turn the switch off."""
        try:
            await self.coordinator.stop_zone(
                self._zone['id']
            )
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

    @property
    def is_on(self):
        """Return true if schedule is currently running."""
        return any(
            running_schedule['id'] == self._schedule['id'] 
            for running_schedule in self.coordinator.running_schedules
        )

    @property
    def extra_state_attributes(self):
        """Return additional state attributes."""
        running_info = next(
            (running_schedule for running_schedule in self.coordinator.running_schedules 
             if running_schedule['id'] == self._schedule['id']),
            None
        )

        attrs = {
            'schedule_id': self._schedule['id'],
            'schedule_name': self._schedule.get('name'),
            'total_duration': self._schedule.get('totalDuration'),
            'running': bool(running_info),
            'current_zone_id': running_info.get('running_zone_id') if running_info else None
        }
        
        return attrs

    async def async_turn_on(self, **kwargs):
        """Turn the switch on."""
        try:
            await self.coordinator.start_schedule(
                self._schedule['id']
            )
        except Exception as e:
            _LOGGER.error(f"Error turning on schedule {self._schedule['name']}: {e}")

    async def async_turn_off(self, **kwargs):
        """Turn the switch off."""
        try:
            await self.coordinator.stop_schedule(
                self._schedule['id']
            )
        except Exception as e:
            _LOGGER.error(f"Error turning off schedule {self._schedule['name']}: {e}")
