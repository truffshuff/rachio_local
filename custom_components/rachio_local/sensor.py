"""Rachio sensor platform."""
import logging
from datetime import datetime

from homeassistant.components.sensor import (
    SensorEntity, 
    SensorDeviceClass, 
    SensorStateClass
)
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
    """Set up Rachio sensors."""
    coordinator = hass.data[DOMAIN][config_entry.entry_id]
    
    # Create sensors
    sensors = []
    
    # Device-level sensor
    sensors.append(RachioDeviceStatusSensor(coordinator))
    
    # Sensors for ALL zones
    for zone in coordinator.zones:
        sensors.extend([
            RachioZoneLastWateredSensor(coordinator, zone),
            RachioZoneStatusSensor(coordinator, zone)
        ])
    
    # Sensors for ALL schedules
    for schedule in coordinator.schedules:
        sensors.append(RachioScheduleStatusSensor(coordinator, schedule))
    
    async_add_entities(sensors)

class RachioDeviceStatusSensor(CoordinatorEntity, SensorEntity):
    """Representation of the Rachio device status sensor."""

    def __init__(self, coordinator):
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_name = "Rachio Device Status"
        self._attr_unique_id = f"{DOMAIN}_device_status"

    @property
    def state(self):
        """Return the state of the device."""
        return self.coordinator.device_info.get('status', 'Unknown')

    @property
    def extra_state_attributes(self):
        """Return device information."""
        return {
            "device_id": self.coordinator.device_id,
            "name": self.coordinator.device_info.get('name'),
            "model": self.coordinator.device_info.get('model')
        }

class RachioZoneLastWateredSensor(CoordinatorEntity, SensorEntity):
    """Representation of a Rachio zone last watered timestamp sensor."""

    def __init__(self, coordinator, zone):
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._zone = zone
        self._attr_name = f"Rachio {zone['name']} Last Watered"
        self._attr_unique_id = f"rachio_zone_{zone['id']}_last_watered"
        self._attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def state(self):
        """Return the state of the sensor."""
        last_watered = self._zone.get('lastWateredDate')
        if last_watered:
            return datetime.fromtimestamp(last_watered / 1000).isoformat()
        return None

class RachioZoneStatusSensor(CoordinatorEntity, SensorEntity):
    """Representation of a Rachio zone status sensor."""

    def __init__(self, coordinator, zone):
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._zone = zone
        self._attr_name = f"Rachio {zone['name']} Status"
        self._attr_unique_id = f"rachio_zone_{zone['id']}_status"

    @property
    def state(self):
        """Return the state of the zone."""
        return self._zone.get('status', 'Unknown')

    @property
    def extra_state_attributes(self):
        """Return additional zone attributes."""
        return {
            "zone_id": self._zone['id'],
            "zone_number": self._zone.get('zoneNumber'),
            "enabled": self._zone.get('enabled', False)
        }

class RachioScheduleStatusSensor(CoordinatorEntity, SensorEntity):
    """Representation of a Rachio schedule status sensor."""

    def __init__(self, coordinator, schedule):
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._schedule = schedule
        self._attr_name = f"Rachio {schedule.get('name', 'Unknown')} Schedule Status"
        self._attr_unique_id = f"rachio_schedule_{schedule.get('id')}_status"

    @property
    def state(self):
        """Return the state of the schedule."""
        return self._schedule.get('status', 'Unknown')

    @property
    def extra_state_attributes(self):
        """Return additional schedule attributes."""
        return {
            "schedule_id": self._schedule['id'],
            "schedule_name": self._schedule.get('name'),
            "total_duration": self._schedule.get('totalDuration')
        }
