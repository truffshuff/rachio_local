"""Support for Rachio sensors."""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
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
    entry_data = hass.data[DOMAIN][config_entry.entry_id]["devices"]

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
            entities.append(RachioPollingStatusSensor(coordinator, handler))  # Add polling status sensor
            _LOGGER.debug(f"Added diagnostic sensors for controller {handler.name}")
        elif handler.type == DEVICE_TYPE_SMART_HOSE_TIMER:
            # Add base station sensors
            entities.append(RachioBaseStationConnectionSensor(coordinator, handler))
            entities.append(RachioBaseStationBLEFirmwareSensor(coordinator, handler))
            entities.append(RachioBaseStationWiFiFirmwareSensor(coordinator, handler))
            entities.append(RachioBaseStationRSSISensor(coordinator, handler))
            entities.append(RachioAPICallSensor(coordinator, handler))
            entities.append(RachioPollingStatusSensor(coordinator, handler))
            _LOGGER.debug(f"Added base station sensors for {handler.name}")
            
            for valve in handler.zones:
                entities.extend([
                    RachioValveStatusSensor(coordinator, handler, valve),
                    RachioValveLastWateredSensor(coordinator, handler, valve),
                    RachioValveBatterySensor(coordinator, handler, valve),
                    RachioValveConnectionSensor(coordinator, handler, valve),
                    RachioValveFirmwareSensor(coordinator, handler, valve),
                    RachioValveRSSISensor(coordinator, handler, valve),
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
        """Return the state of the sensor (optimistic or real)."""
        is_on = self.handler.is_zone_optimistically_on(self.zone_id)
        _LOGGER.debug(f"[ZoneStatusSensor] native_value: zone_id={self.zone_id}, is_on={is_on}, running_zones={list(self.handler.running_zones.keys())}, pending_start={getattr(self.handler, '_pending_start', {})}")
        return STATE_WATERING if is_on else STATE_NOT_WATERING

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
        # Check if we have a persisted/cached completion time first
        if hasattr(self.handler, "_last_watering_completed") and self.valve_id in self.handler._last_watering_completed:
            return dt_util.as_utc(self.handler._last_watering_completed[self.valve_id])

        # Otherwise try to get from valve data
        for valve in self.handler.zones:
            if valve["id"] == self.valve_id:
                reported_state = valve.get("state", {}).get("reportedState", {})
                last_action = reported_state.get("lastWateringAction", {})

                if last_action.get("start") and last_action.get("durationSeconds"):
                    try:
                        start_str = last_action["start"]
                        start_time = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                        duration_seconds = int(last_action["durationSeconds"])
                        end_time = start_time + timedelta(seconds=duration_seconds)

                        # Only return if watering has completed (end time is in the past)
                        current_time = datetime.now(timezone.utc)
                        if end_time < current_time:
                            return dt_util.as_utc(end_time)
                    except (ValueError, KeyError) as e:
                        _LOGGER.debug(f"Error parsing lastWateringAction for valve {self.valve_id}: {e}")

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
        # Read directly from handler.zones instead of making extra API calls
        for valve in self.handler.zones:
            if valve["id"] == self.valve_id:
                reported_state = valve.get("state", {}).get("reportedState", {})
                return reported_state.get("batteryStatus")
        return None

class RachioValveConnectionSensor(RachioBaseEntity, SensorEntity):
    """Sensor showing valve connection status and diagnostic information."""
    def __init__(self, coordinator, handler, valve):
        super().__init__(coordinator, handler)
        self.valve_id = valve["id"]
        self.valve_name = valve.get("name", f"Valve {valve.get('id')}")
        self._attr_name = f"Valve: {self.valve_name}"
        self._attr_unique_id = f"{handler.device_id}_{self.valve_id}_connection"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self):
        """Return the connection status."""
        for valve in self.handler.zones:
            if valve["id"] == self.valve_id:
                reported_state = valve.get("state", {}).get("reportedState", {})
                return STATE_ONLINE if reported_state.get("connected") else STATE_OFFLINE
        return STATE_OFFLINE

    @property
    def extra_state_attributes(self):
        """Return all valve diagnostic attributes from the API."""
        for valve in self.handler.zones:
            if valve["id"] == self.valve_id:
                reported_state = valve.get("state", {}).get("reportedState", {})
                desired_state = valve.get("state", {}).get("desiredState", {})

                return {
                    "connection_id": valve.get("connectionId"),
                    "color": valve.get("color"),
                    "detect_flow": valve.get("detectFlow"),
                    "base_station_id": valve.get("baseStationId"),
                    "created": valve.get("created"),
                    "updated": valve.get("updated"),
                    # Reported state
                    "connected": reported_state.get("connected"),
                    "default_runtime_seconds": reported_state.get("defaultRuntimeSeconds"),
                    "last_state_update": reported_state.get("lastStateUpdate"),
                    "battery_status": reported_state.get("batteryStatus"),
                    "firmware_version": reported_state.get("firmwareVersion"),
                    "firmware_upgrade_required": reported_state.get("firmwareUpgradeRequired"),
                    "firmware_upgrade_available": reported_state.get("firmwareUpgradeAvailable"),
                    "firmware_upgrade_in_progress": reported_state.get("firmwareUpgradeInProgress"),
                    "firmware_retry_required": reported_state.get("firmwareRetryRequired"),
                    "calendar_hash": reported_state.get("calendarHash"),
                    "rssi": reported_state.get("rssi"),
                    "rssi_signal_strength": reported_state.get("rssiSignalStrength"),
                    "reboot_counter": reported_state.get("rebootCounter"),
                    # Desired state
                    "desired_default_runtime_seconds": desired_state.get("defaultRuntimeSeconds"),
                    "desired_calendar_hash": desired_state.get("calendarHash"),
                    "state_matches": valve.get("state", {}).get("matches"),
                }
        return {}

class RachioValveFirmwareSensor(RachioBaseEntity, SensorEntity):
    """Sensor showing valve firmware version."""
    def __init__(self, coordinator, handler, valve):
        super().__init__(coordinator, handler)
        self.valve_id = valve["id"]
        self.valve_name = valve.get("name", f"Valve {valve.get('id')}")
        self._attr_name = f"Valve: {self.valve_name} FW"
        self._attr_unique_id = f"{handler.device_id}_{self.valve_id}_firmware"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_entity_registry_enabled_default = False

    @property
    def native_value(self):
        """Return the firmware version."""
        for valve in self.handler.zones:
            if valve["id"] == self.valve_id:
                reported_state = valve.get("state", {}).get("reportedState", {})
                return reported_state.get("firmwareVersion")
        return None

    @property
    def extra_state_attributes(self):
        """Return firmware upgrade information."""
        for valve in self.handler.zones:
            if valve["id"] == self.valve_id:
                reported_state = valve.get("state", {}).get("reportedState", {})
                return {
                    "upgrade_required": reported_state.get("firmwareUpgradeRequired"),
                    "upgrade_available": reported_state.get("firmwareUpgradeAvailable"),
                    "upgrade_in_progress": reported_state.get("firmwareUpgradeInProgress"),
                    "retry_required": reported_state.get("firmwareRetryRequired"),
                }
        return {}

class RachioValveRSSISensor(RachioBaseEntity, SensorEntity):
    """Sensor showing valve RSSI."""
    def __init__(self, coordinator, handler, valve):
        super().__init__(coordinator, handler)
        self.valve_id = valve["id"]
        self.valve_name = valve.get("name", f"Valve {valve.get('id')}")
        self._attr_name = f"Valve: {self.valve_name} RSSI"
        self._attr_unique_id = f"{handler.device_id}_{self.valve_id}_rssi"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
        self._attr_native_unit_of_measurement = "dBm"
        self._attr_entity_registry_enabled_default = False

    @property
    def native_value(self):
        """Return the RSSI value."""
        for valve in self.handler.zones:
            if valve["id"] == self.valve_id:
                reported_state = valve.get("state", {}).get("reportedState", {})
                return reported_state.get("rssi")
        return None

    @property
    def extra_state_attributes(self):
        """Return signal strength description."""
        for valve in self.handler.zones:
            if valve["id"] == self.valve_id:
                reported_state = valve.get("state", {}).get("reportedState", {})
                return {
                    "signal_strength": reported_state.get("rssiSignalStrength"),
                }
        return {}

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

    @property
    def native_value(self):
        # Show API calls used in current window: rate_limit - rate_remaining
        try:
            _LOGGER.debug(f"[APICallSensor] raw values: limit={self.handler.api_rate_limit}, remaining={self.handler.api_rate_remaining}, reset={self.handler.api_rate_reset}")

            if self.handler.api_rate_limit is None or self.handler.api_rate_remaining is None:
                _LOGGER.debug(f"[APICallSensor] Missing rate limit headers")
                return None

            limit = int(self.handler.api_rate_limit)
            remaining = int(self.handler.api_rate_remaining)
            used = limit - remaining

            _LOGGER.debug(f"[APICallSensor] calculated: limit={limit}, remaining={remaining}, used={used}")
            return used
        except (ValueError, TypeError) as e:
            _LOGGER.error(f"[APICallSensor] Error calculating value: {e}")
            return None

    @property
    def extra_state_attributes(self):
        reset_utc = self.handler.api_rate_reset
        reset_local = None
        if reset_utc:
            try:
                if str(reset_utc).isdigit():
                    reset_dt = datetime.fromtimestamp(int(reset_utc), tz=timezone.utc)
                else:
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

class RachioPollingStatusSensor(RachioBaseEntity, SensorEntity):
    """Sensor showing current polling interval and logic explanation."""
    def __init__(self, coordinator, handler):
        super().__init__(coordinator, handler)
        self._attr_name = f"{handler.name} Polling Status"
        self._attr_unique_id = f"{handler.device_id}_polling_status"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self):
        # Show the current polling interval in seconds
        interval = self.handler._get_update_interval().total_seconds()
        return int(interval)

    @property
    def extra_state_attributes(self):
        # Try to get the global num_devices from hass.data
        num_devices = 1
        hass = None
        if hasattr(self.handler, 'coordinator') and hasattr(self.handler.coordinator, 'hass'):
            hass = self.handler.coordinator.hass
        if hass is not None:
            try:
                # Find the entry where this device_id is in the devices dict
                for eid, entry in hass.data.get(DOMAIN, {}).items():
                    if (
                        isinstance(entry, dict)
                        and "devices" in entry
                        and self.handler.device_id in entry["devices"]
                        and "num_devices" in entry
                    ):
                        num_devices = entry["num_devices"]
                        break
            except Exception as e:
                _LOGGER.debug(f"[PollingStatusSensor] Could not get global num_devices: {e}")
        else:
            if self.handler.coordinator and hasattr(self.handler.coordinator, 'num_devices'):
                num_devices = self.handler.coordinator.num_devices
        interval = self.handler._get_update_interval().total_seconds()
        calls_per_poll = num_devices * 2
        max_calls_per_hour = 80
        explanation = (
            f"Polling interval is dynamically calculated based on the number of devices/controllers. "
            f"Each device makes 2 API calls per poll. Interval is set to avoid exceeding {max_calls_per_hour} calls/hour. "
            f"Current: {num_devices} device(s), {calls_per_poll} calls/poll, {interval}s interval."
        )
        return {
            "num_devices": num_devices,
            "calls_per_poll": calls_per_poll,
            "max_calls_per_hour": max_calls_per_hour,
            "polling_interval_seconds": interval,
            "explanation": explanation,
        }

class RachioBaseStationConnectionSensor(RachioBaseEntity, SensorEntity):
    """Sensor showing base station connection status."""
    def __init__(self, coordinator, handler):
        super().__init__(coordinator, handler)
        # Get base station name from API
        d = handler.device_data
        base_stations = d.get("baseStations", [])
        if base_stations:
            base_station = base_stations[0]
        else:
            base_station = d.get("baseStation", {})
        base_station_name = base_station.get("name", handler.name)

        self._attr_name = f"Basestation: {base_station_name}"
        self._attr_unique_id = f"{handler.device_id}_connection"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self):
        return STATE_ONLINE if self.handler.status == "ONLINE" else STATE_OFFLINE

    @property
    def extra_state_attributes(self):
        """Return all base station attributes from the API."""
        d = self.handler.device_data

        # Extract base station data (handle both formats)
        base_stations = d.get("baseStations", [])
        if base_stations:
            base_station = base_stations[0]
        else:
            base_station = d.get("baseStation", {})

        reported_state = base_station.get("reportedState", {})

        return {
            "id": base_station.get("id"),
            "serial_number": base_station.get("serialNumber"),
            "mac_address": base_station.get("macAddress"),
            "name": base_station.get("name"),
            "shared": base_station.get("shared"),
            "created": base_station.get("created"),
            "updated": base_station.get("updated"),
            # Reported state details
            "connected": reported_state.get("connected"),
            "ble_hub_firmware_version": reported_state.get("bleHubFirmwareVersion"),
            "wifi_bridge_firmware_version": reported_state.get("wifiBridgeFirmwareVersion"),
            "ble_hub_firmware_upgrade_required": reported_state.get("bleHubFirmwareUpgradeRequired"),
            "wifi_bridge_firmware_upgrade_required": reported_state.get("wifiBridgeFirmwareUpgradeRequired"),
            "firmware_retry_required": reported_state.get("firmwareRetryRequired"),
            "firmware_upgrade_available": reported_state.get("firmwareUpgradeAvailable"),
            "firmware_upgrade_in_progress": reported_state.get("firmwareUpgradeInProgress"),
            "rssi": reported_state.get("rssi"),
            "rssi_signal_strength": reported_state.get("rssiSignalStrength"),
            "reboot_counter": reported_state.get("rebootCounter"),
            "last_state_update": reported_state.get("lastStateUpdate"),
        }

class RachioBaseStationBLEFirmwareSensor(RachioBaseEntity, SensorEntity):
    """Sensor showing base station BLE Hub firmware version."""
    def __init__(self, coordinator, handler):
        super().__init__(coordinator, handler)
        # Get base station name from API
        d = handler.device_data
        base_stations = d.get("baseStations", [])
        if base_stations:
            base_station = base_stations[0]
        else:
            base_station = d.get("baseStation", {})
        base_station_name = base_station.get("name", handler.name)

        self._attr_name = f"Basestation: {base_station_name} BLE FW"
        self._attr_unique_id = f"{handler.device_id}_ble_firmware"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_entity_registry_enabled_default = False

    @property
    def native_value(self):
        return self.handler.base_station_firmware

    @property
    def extra_state_attributes(self):
        """Return BLE firmware upgrade information."""
        d = self.handler.device_data

        # Extract base station data (handle both formats)
        base_stations = d.get("baseStations", [])
        if base_stations:
            base_station = base_stations[0]
        else:
            base_station = d.get("baseStation", {})

        reported_state = base_station.get("reportedState", {})

        return {
            "upgrade_required": reported_state.get("bleHubFirmwareUpgradeRequired"),
            "upgrade_available": reported_state.get("firmwareUpgradeAvailable"),
            "upgrade_in_progress": reported_state.get("firmwareUpgradeInProgress"),
        }

class RachioBaseStationWiFiFirmwareSensor(RachioBaseEntity, SensorEntity):
    """Sensor showing base station WiFi Bridge firmware version."""
    def __init__(self, coordinator, handler):
        super().__init__(coordinator, handler)
        # Get base station name from API
        d = handler.device_data
        base_stations = d.get("baseStations", [])
        if base_stations:
            base_station = base_stations[0]
        else:
            base_station = d.get("baseStation", {})
        base_station_name = base_station.get("name", handler.name)

        self._attr_name = f"Basestation: {base_station_name} WIFI FW"
        self._attr_unique_id = f"{handler.device_id}_wifi_firmware"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_entity_registry_enabled_default = False

    @property
    def native_value(self):
        return self.handler.base_station_wifi_firmware

    @property
    def extra_state_attributes(self):
        """Return WiFi firmware upgrade information."""
        d = self.handler.device_data

        # Extract base station data (handle both formats)
        base_stations = d.get("baseStations", [])
        if base_stations:
            base_station = base_stations[0]
        else:
            base_station = d.get("baseStation", {})

        reported_state = base_station.get("reportedState", {})

        return {
            "upgrade_required": reported_state.get("wifiBridgeFirmwareUpgradeRequired"),
            "upgrade_available": reported_state.get("firmwareUpgradeAvailable"),
            "upgrade_in_progress": reported_state.get("firmwareUpgradeInProgress"),
        }

class RachioBaseStationRSSISensor(RachioBaseEntity, SensorEntity):
    """Sensor showing base station RSSI."""
    def __init__(self, coordinator, handler):
        super().__init__(coordinator, handler)
        # Get base station name from API
        d = handler.device_data
        base_stations = d.get("baseStations", [])
        if base_stations:
            base_station = base_stations[0]
        else:
            base_station = d.get("baseStation", {})
        base_station_name = base_station.get("name", handler.name)

        self._attr_name = f"Basestation: {base_station_name} RSSI"
        self._attr_unique_id = f"{handler.device_id}_rssi"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
        self._attr_native_unit_of_measurement = "dBm"
        self._attr_entity_registry_enabled_default = False

    @property
    def native_value(self):
        return self.handler.base_station_rssi

    @property
    def extra_state_attributes(self):
        """Return signal strength description."""
        d = self.handler.device_data

        # Extract base station data (handle both formats)
        base_stations = d.get("baseStations", [])
        if base_stations:
            base_station = base_stations[0]
        else:
            base_station = d.get("baseStation", {})

        reported_state = base_station.get("reportedState", {})

        return {
            "signal_strength": reported_state.get("rssiSignalStrength"),
        }
