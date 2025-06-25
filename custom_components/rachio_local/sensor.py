"""Support for Rachio sensors."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    STATE_ONLINE,
    STATE_OFFLINE,
    STATE_WATERING,
    STATE_NOT_WATERING,
    DEVICE_TYPE_CONTROLLER,
    DEVICE_TYPE_SMART_HOSE_TIMER,
    # Add battery status constants if needed
)

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the Rachio sensors from config entry."""
    entities = []
    entry_data = hass.data[DOMAIN][config_entry.entry_id]

    for device_id, data in entry_data.items():
        handler = data["handler"]
        coordinator = data["coordinator"]
        _LOGGER.debug(f"Setting up sensors for device {device_id} ({getattr(handler, 'name', 'unknown')}) of type {getattr(handler, 'type', 'unknown')}")

        # Device-level sensors
        if handler.type == DEVICE_TYPE_CONTROLLER:
            entities.append(RachioConnectionSensor(coordinator, handler))
            for zone in handler.zones:
                if zone.get("enabled", True):
                    entities.extend([
                        RachioZoneStatusSensor(coordinator, handler, zone),
                        RachioZoneLastWateredSensor(coordinator, handler, zone),
                    ])
                    _LOGGER.debug(f"Added zone sensors for {zone.get('name', zone.get('id'))}")
            for schedule in handler.schedules:
                entities.append(RachioScheduleStatusSensor(coordinator, handler, schedule))
                _LOGGER.debug(f"Added schedule status sensor for {schedule.get('name', schedule.get('id'))}")
            # Add diagnostic sensors
            entities.append(RachioDeviceStatusSensor(coordinator, handler))
            entities.append(RachioRainSensorTrippedBinarySensor(coordinator, handler))
            entities.append(RachioPausedBinarySensor(coordinator, handler))
            entities.append(RachioOnBinarySensor(coordinator, handler))
            entities.append(RachioAPICallSensor(coordinator, handler))
            _LOGGER.debug(f"Added diagnostic sensors for controller {handler.name}")
        elif handler.type == DEVICE_TYPE_SMART_HOSE_TIMER:
            for valve in handler.zones:
                entities.extend([
                    RachioValveStatusSensor(coordinator, handler, valve),
                    RachioValveLastWateredSensor(coordinator, handler, valve),
                    RachioValveBatterySensor(coordinator, handler, valve),
                ])
                _LOGGER.debug(f"Added valve sensors for {valve.get('name', valve.get('id'))}")
    _LOGGER.info(f"Adding {len(entities)} Rachio sensor entities: {[e.name for e in entities]}")
    async_add_entities(entities)

class RachioBaseEntity(CoordinatorEntity):
    """Base class for Rachio entities."""

    def __init__(self, coordinator, handler, device_class=None):
        """Initialize entity properties."""
        super().__init__(coordinator)
        self.handler = handler
        self._attr_device_class = device_class
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

class RachioDeviceStatusSensor(RachioBaseEntity, SensorEntity):
    """Sensor showing device online/offline status."""

    def __init__(self, coordinator, handler):
        """Initialize the sensor."""
        super().__init__(coordinator, handler)
        self._attr_name = f"{handler.name} Status"
        self._attr_unique_id = f"{handler.device_id}_status"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self):
        """Return the state of the sensor."""
        return STATE_ONLINE if self.handler.status == "ONLINE" else STATE_OFFLINE

    @property
    def extra_state_attributes(self):
        d = self.handler.device_data
        return {
            "serial_number": d.get("serialNumber"),
            "mac_address": d.get("macAddress"),
            "latitude": d.get("latitude"),
            "longitude": d.get("longitude"),
            "zip": d.get("zip"),
            "elevation": d.get("elevation"),
            "time_zone": d.get("timeZone"),
            "webhooks": d.get("webhooks"),
            "schedule_rules": d.get("scheduleRules"),
            "flex_schedule_rules": d.get("flexScheduleRules"),
        }

class RachioRainSensorTrippedBinarySensor(RachioBaseEntity, SensorEntity):
    """Binary sensor for rain sensor tripped status."""
    def __init__(self, coordinator, handler):
        super().__init__(coordinator, handler)
        self._attr_name = f"{handler.name} Rain Sensor Tripped"
        self._attr_unique_id = f"{handler.device_id}_rain_sensor_tripped"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self):
        return bool(self.handler.device_data.get("rainSensorTripped"))

class RachioPausedBinarySensor(RachioBaseEntity, SensorEntity):
    """Binary sensor for controller paused status."""
    def __init__(self, coordinator, handler):
        super().__init__(coordinator, handler)
        self._attr_name = f"{handler.name} Paused"
        self._attr_unique_id = f"{handler.device_id}_paused"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self):
        return bool(self.handler.device_data.get("paused"))

class RachioOnBinarySensor(RachioBaseEntity, SensorEntity):
    """Binary sensor for controller on status."""
    def __init__(self, coordinator, handler):
        super().__init__(coordinator, handler)
        self._attr_name = f"{handler.name} On"
        self._attr_unique_id = f"{handler.device_id}_on"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self):
        return bool(self.handler.device_data.get("on"))

class RachioZoneStatusSensor(RachioBaseEntity, SensorEntity):
    """Sensor showing zone watering status."""

    def __init__(self, coordinator, handler, zone):
        """Initialize the sensor."""
        super().__init__(coordinator, handler)
        self.zone_id = zone["id"]
        self.zone_name = zone.get("name", f"Zone {zone.get('zoneNumber', '')}")
        self._attr_name = f"{self.zone_name} Status"
        self._attr_unique_id = f"{handler.device_id}_{self.zone_id}_status"

    @property
    def native_value(self):
        """Return the state of the sensor."""
        return STATE_WATERING if self.zone_id in self.handler.running_zones else STATE_NOT_WATERING

class RachioZoneLastWateredSensor(RachioBaseEntity, SensorEntity):
    """Sensor showing when the zone was last watered."""

    def __init__(self, coordinator, handler, zone):
        """Initialize the sensor."""
        super().__init__(coordinator, handler, device_class=SensorDeviceClass.TIMESTAMP)
        self.zone_id = zone["id"]
        self.zone_name = zone.get("name", f"Zone {zone.get('zoneNumber', '')}")
        self._attr_name = f"{self.zone_name} Last Watered"
        self._attr_unique_id = f"{handler.device_id}_{self.zone_id}_last_watered"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self):
        """Return the state of the sensor."""
        for zone in self.handler.zones:
            if zone["id"] == self.zone_id:
                if last_watered := zone.get("lastWateredDate"):
                    # Convert timestamp to UTC datetime
                    return dt_util.as_utc(datetime.fromtimestamp(last_watered / 1000))
        return None

class RachioValveStatusSensor(RachioZoneStatusSensor):
    """Sensor showing valve watering status."""

class RachioValveLastWateredSensor(RachioZoneLastWateredSensor):
    """Sensor showing when the valve was last watered (Smart Hose Timer)."""
    def __init__(self, coordinator, handler, valve):
        super().__init__(coordinator, handler, valve)
        self.valve_id = valve["id"]
    @property
    def native_value(self):
        # Use diagnostics cache if available
        if hasattr(self.handler, "valve_diagnostics"):
            diag = self.handler.valve_diagnostics.get(self.valve_id)
            if diag and diag.get("lastWatered"):
                try:
                    dt = datetime.fromisoformat(diag["lastWatered"].replace("Z", "+00:00"))
                    return dt_util.as_utc(dt)
                except Exception:
                    return None
        return None

class RachioValveBatterySensor(RachioBaseEntity, SensorEntity):
    """Sensor showing valve battery status (Smart Hose Timer)."""
    def __init__(self, coordinator, handler, valve):
        super().__init__(coordinator, handler)
        self.valve_id = valve["id"]
        self.valve_name = valve.get("name", f"Valve {valve.get('id')}")
        self._attr_name = f"{self.valve_name} Battery"
        self._attr_unique_id = f"{handler.device_id}_{self.valve_id}_battery"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self):
        if hasattr(self.handler, "valve_diagnostics"):
            diag = self.handler.valve_diagnostics.get(self.valve_id)
            if diag:
                return diag.get("batteryStatus")
        return None

class RachioScheduleStatusSensor(RachioBaseEntity, SensorEntity):
    """Sensor showing schedule running status for controller."""
    def __init__(self, coordinator, handler, schedule):
        super().__init__(coordinator, handler)
        self.schedule_id = schedule["id"]
        self.schedule_name = schedule.get("name", "Schedule")
        self._attr_name = f"{self.schedule_name} Schedule Status"
        self._attr_unique_id = f"{handler.device_id}_{self.schedule_id}_schedule_status"

    @property
    def native_value(self):
        return STATE_WATERING if self.schedule_id in self.handler.running_schedules else STATE_NOT_WATERING

class RachioConnectionSensor(RachioBaseEntity, SensorEntity):
    """Sensor showing device connection status (online/offline)."""
    def __init__(self, coordinator, handler):
        super().__init__(coordinator, handler)
        self._attr_name = f"{handler.name} Connection"
        self._attr_unique_id = f"{handler.device_id}_connection"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self):
        return STATE_ONLINE if self.handler.status == "ONLINE" else STATE_OFFLINE

    @property
    def extra_state_attributes(self):
        d = self.handler.device_data
        return {
            "serial_number": d.get("serialNumber"),
            "mac_address": d.get("macAddress"),
            "latitude": d.get("latitude"),
            "longitude": d.get("longitude"),
            "zip": d.get("zip"),
            "elevation": d.get("elevation"),
            "time_zone": d.get("timeZone"),
            "webhooks": d.get("webhooks"),
            "schedule_rules": d.get("scheduleRules"),
            "flex_schedule_rules": d.get("flexScheduleRules"),
        }

class RachioAPICallSensor(RachioBaseEntity, SensorEntity):
    """Sensor showing API call count and rate limit info."""
    def __init__(self, coordinator, handler):
        super().__init__(coordinator, handler)
        self._attr_name = f"{handler.name} API Calls"
        self._attr_unique_id = f"{handler.device_id}_api_calls"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_state_class = SensorStateClass.TOTAL_INCREASING

    @property
    def native_value(self):
        return self.handler.api_call_count

    @property
    def extra_state_attributes(self):
        # Convert reset time to local time if possible
        reset_utc = self.handler.api_rate_reset
        reset_local = None
        if reset_utc:
            try:
                # Try parsing as ISO8601 or epoch seconds
                if reset_utc.isdigit():
                    # If it's epoch seconds
                    reset_dt = datetime.fromtimestamp(int(reset_utc), tz=timezone.utc)
                else:
                    # Try parsing as RFC2822 or ISO8601
                    try:
                        from email.utils import parsedate_to_datetime
                        reset_dt = parsedate_to_datetime(reset_utc)
                    except Exception:
                        reset_dt = datetime.fromisoformat(reset_utc)
                reset_local = reset_dt.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
            except Exception:
                reset_local = reset_utc
        return {
            "rate_limit": self.handler.api_rate_limit,
            "rate_remaining": self.handler.api_rate_remaining,
            "rate_reset": reset_local,
        }
