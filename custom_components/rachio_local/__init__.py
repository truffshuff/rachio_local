"""The Rachio Local Control integration."""
import asyncio
import logging
from datetime import timedelta, datetime
import requests

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, CONF_API_KEY

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SWITCH, Platform.SENSOR]

async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Rachio Local component."""
    hass.data.setdefault(DOMAIN, {})
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Rachio Local from a config entry."""
    api_key = entry.data[CONF_API_KEY]
    
    coordinator = RachioDataUpdateCoordinator(hass, api_key)
    
    try:
        await coordinator.async_config_entry_first_refresh()
    except UpdateFailed as err:
        raise ConfigEntryNotReady from err
    
    hass.data[DOMAIN][entry.entry_id] = coordinator
    
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok

class RachioDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching Rachio data."""

    def __init__(self, hass: HomeAssistant, api_key: str):
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=5),
        )
        self.api_key = api_key
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        self.person_id = None
        self.device_id = None
        self.device_info = None
        self.zones = []
        self.schedules = []
        self.running_zones = []
        self.running_schedules = []  # Persist across updates
        self.current_schedule = None
        self._active_watering = False
        self._pending_operations = {}
        self._active_schedules = {}  # {schedule_id: {"start_time": datetime, "total_duration": int}}

    async def _make_request(self, method, endpoint, data=None):
        """Make an API request."""
        url = f"https://api.rach.io/1/public/{endpoint}"
        try:
            response = await self.hass.async_add_executor_job(
                lambda: requests.request(method=method, url=url, headers=self.headers, json=data)
            )
            response.raise_for_status()
            return response.json() if response.content else None
        except requests.exceptions.RequestException as err:
            _LOGGER.error(f"API request failed: {err}")
            raise UpdateFailed(f"API request failed: {err}")

    async def _get_device_state(self):
        """Get current device state including running zones."""
        if not self.device_id:
            return None
        try:
            device_state = await self._make_request("GET", f"device/{self.device_id}")
            current = await self._make_request("GET", f"device/{self.device_id}/current_schedule")
            return {"device_state": device_state, "current_schedule": current}
        except Exception as err:
            _LOGGER.error(f"Failed to get device state: {err}")
            return None

    async def _detect_external_operations(self):
        """Detect operations started from the Rachio app."""
        try:
            current_state = await self._make_request("GET", f"device/{self.device_id}/current_schedule")
            if current_state and current_state.get("status") in ("PROCESSING", "RUNNING"):
                schedule_id = (
                    current_state.get("seriesId") or 
                    current_state.get("scheduleId") or 
                    current_state.get("scheduleRuleId") or 
                    current_state.get("id")
                )
                if schedule_id and schedule_id not in [s["id"] for s in self.running_schedules]:
                    _LOGGER.debug(f"Detected externally started schedule: {schedule_id}")
                    return {"type": "schedule", "id": schedule_id, "name": current_state.get("scheduleName", "Unknown Schedule")}
                elif current_state.get("zoneId") and current_state.get("zoneId") not in [z["id"] for z in self.running_zones]:
                    _LOGGER.debug(f"Detected externally started zone: {current_state.get('zoneId')}")
                    return {"type": "zone", "id": current_state.get("zoneId"), "name": "Unknown Zone"}
            return None
        except Exception as err:
            _LOGGER.error(f"Failed to detect external operations: {err}")
            return None

    async def _async_update_data(self):
        """Update data via API."""
        try:
            if not self.device_id:
                person_info = await self._make_request("GET", "person/info")
                self.person_id = person_info["id"]
                person = await self._make_request("GET", f"person/{self.person_id}")
                if not person["devices"]:
                    raise UpdateFailed("No Rachio devices found")
                self.device_id = person["devices"][0]["id"]
                self.device_info = person["devices"][0]

            state_data = await self._get_device_state()
            if not state_data:
                _LOGGER.warning("No state data returned from API")
                return None

            device_state = state_data["device_state"]
            current_schedule = state_data["current_schedule"]
            _LOGGER.debug(f"Device state: {device_state}")
            _LOGGER.debug(f"Current schedule: {current_schedule}")

            # Reset zones but preserve schedules unless explicitly stopped
            self.running_zones = []
            self.zones = device_state.get("zones", [])
            self.schedules = device_state.get("scheduleRules", [])
            self.current_schedule = current_schedule

            # Process current schedule from API
            api_schedules = []
            if current_schedule and current_schedule.get('id'):
                schedule_id = (
                    current_schedule.get("seriesId") or 
                    current_schedule.get("scheduleId") or 
                    current_schedule.get("scheduleRuleId") or 
                    current_schedule.get("id")
                )
                schedule_name = current_schedule.get("scheduleName", "Unknown Schedule")
                schedule_status = current_schedule.get("status", "UNKNOWN")
                _LOGGER.debug(f"Processing schedule {schedule_id} - Status: {schedule_status}")

                zone_sequence = current_schedule.get("zoneSequence", []) or current_schedule.get("zones", [])
                current_time = current_schedule.get("currentTime", datetime.now().timestamp() * 1000)
                total_duration = current_schedule.get("totalDuration") or sum(zone.get("duration", 0) for zone in zone_sequence) / 1000

                # Determine active zone
                active_zone = None
                for zone in zone_sequence:
                    start_time = zone.get("startTime", 0)
                    duration = zone.get("duration", 0) / 1000
                    end_time = start_time + (duration * 1000)
                    if start_time <= current_time < end_time:
                        active_zone = zone
                        _LOGGER.debug(f"Active zone found: {zone.get('zoneId', zone.get('id'))}")
                        break

                if not active_zone and schedule_status in ("RUNNING", "PROCESSING"):
                    active_zone = zone_sequence[0] if zone_sequence else None
                    _LOGGER.debug(f"No active zone but schedule running, defaulting to: {active_zone}")

                if schedule_status in ("RUNNING", "PROCESSING"):
                    api_schedules.append({
                        "id": schedule_id,
                        "name": schedule_name,
                        "running_zone_id": active_zone.get("zoneId", active_zone.get("id")) if active_zone else None,
                        "running_zone_name": active_zone.get("zoneName", "Unknown Zone") if active_zone else "No Zone Active"
                    })
                    if schedule_id not in self._active_schedules:
                        self._active_schedules[schedule_id] = {
                            "start_time": datetime.now(),
                            "total_duration": total_duration or 3600  # Default 1 hour if unknown
                        }
                    if active_zone:
                        zone_id = active_zone.get("zoneId", active_zone.get("id"))
                        remaining_time = max(0, (end_time - current_time) / 1000)
                        self.running_zones.append({
                            "id": zone_id,
                            "name": active_zone.get("zoneName", "Unknown Zone"),
                            "duration": active_zone.get("duration", 0) / 1000,
                            "remaining_time": remaining_time,
                            "schedule_id": schedule_id,
                            "schedule_name": schedule_name,
                            "run_type": "schedule"
                        })
                        _LOGGER.debug(f"Zone {zone_id} added to running_zones from schedule")
                elif current_schedule.get("zoneId") and schedule_status in ("RUNNING", "PROCESSING"):
                    zone_id = current_schedule.get("zoneId")
                    zone_info = next((z for z in self.zones if z["id"] == zone_id), {})
                    remaining_time = current_schedule.get("duration", 0) - (
                        current_time - current_schedule.get("startTime", 0)
                    ) / 1000 if current_schedule.get("startTime") else current_schedule.get("duration", 0)
                    self.running_zones.append({
                        "id": zone_id,
                        "name": zone_info.get("name", "Unknown Zone"),
                        "duration": current_schedule.get("duration", 0) / 1000,
                        "remaining_time": max(0, remaining_time / 1000),
                        "run_type": "manual"
                    })
                    _LOGGER.debug(f"Manual zone {zone_id} added to running_zones")

            # Update running_schedules: Combine API data with manually persisted schedules
            current_time_dt = datetime.now()
            persisted_schedules = []
            for schedule_id, info in list(self._active_schedules.items()):
                elapsed_time = (current_time_dt - info["start_time"]).total_seconds()
                if elapsed_time < info["total_duration"]:
                    schedule_info = next((s for s in self.schedules if s["id"] == schedule_id), {})
                    persisted_schedules.append({
                        "id": schedule_id,
                        "name": schedule_info.get("name", "Unknown Schedule"),
                        "running_zone_id": None,  # API might not provide this if current_schedule drops
                        "running_zone_name": "Unknown Zone"
                    })
                    _LOGGER.debug(f"Persisting schedule {schedule_id} - Elapsed: {elapsed_time}s, Total: {info['total_duration']}s")
                else:
                    del self._active_schedules[schedule_id]
                    _LOGGER.debug(f"Schedule {schedule_id} duration expired")

            # Combine API-detected and persisted schedules, avoiding duplicates
            self.running_schedules = api_schedules
            for persisted in persisted_schedules:
                if persisted["id"] not in [s["id"] for s in self.running_schedules]:
                    self.running_schedules.append(persisted)

            # Handle pending operations
            for entity_id, operation in list(self._pending_operations.items()):
                if current_time_dt >= operation['expiry']:
                    self._pending_operations.pop(entity_id)
                    _LOGGER.debug(f"Pending operation {entity_id} expired")
                elif operation['id'] not in [z['id'] for z in self.running_zones] and operation['id'] not in [s['id'] for s in self.running_schedules]:
                    if operation['type'] == 'zone':
                        self.running_zones.append({
                            "id": operation['id'],
                            "name": operation.get('name'),
                            "duration": operation.get('duration', 0),
                            "remaining_time": operation.get('duration', 0),
                            "run_type": "manual"
                        })
                    elif operation['type'] == 'schedule':
                        self.running_schedules.append({
                            "id": operation['id'],
                            "name": operation.get('name'),
                            "running_zone_id": None,
                            "running_zone_name": None
                        })
                        if operation['id'] not in self._active_schedules:
                            self._active_schedules[operation['id']] = {
                                "start_time": datetime.now(),
                                "total_duration": 3600  # Default 1 hour if unknown
                            }

            # Fallback for external operations
            external_op = await self._detect_external_operations()
            if external_op and external_op["id"] not in [s["id"] for s in self.running_schedules] and external_op["id"] not in [z["id"] for z in self.running_zones]:
                if external_op["type"] == "schedule":
                    self.running_schedules.append({
                        "id": external_op["id"],
                        "name": external_op["name"],
                        "running_zone_id": None,
                        "running_zone_name": None
                    })
                elif external_op["type"] == "zone":
                    self.running_zones.append({
                        "id": external_op["id"],
                        "name": external_op["name"],
                        "duration": 0,
                        "remaining_time": 0,
                        "run_type": "external"
                    })

            self._active_watering = bool(self.running_zones or self.running_schedules)
            self.update_interval = (
                timedelta(seconds=20) if self._active_watering or self._pending_operations 
                else timedelta(minutes=5)
            )
            _LOGGER.debug(f"Running zones: {self.running_zones}")
            _LOGGER.debug(f"Running schedules: {self.running_schedules}")
            _LOGGER.debug(f"Active schedules: {self._active_schedules}")

            return {
                "device": self.device_info,
                "zones": self.zones,
                "schedules": self.schedules,
                "running_zones": self.running_zones,
                "running_schedules": self.running_schedules,
                "current_schedule": self.current_schedule
            }
        except Exception as err:
            _LOGGER.error(f"Update failed: {err}")
            raise UpdateFailed(f"Update failed: {err}")

    async def start_zone(self, zone_id: str, duration: int = 1800):
        """Start a specific zone."""
        try:
            await self._make_request("PUT", "zone/start", {"id": zone_id, "duration": duration})
            zone_info = next((zone for zone in self.zones if zone['id'] == zone_id), {})
            self._pending_operations[f"zone_{zone_id}"] = {
                'type': 'zone',
                'id': zone_id,
                'name': zone_info.get('name', 'Unknown Zone'),
                'duration': duration,
                'expiry': datetime.now() + timedelta(minutes=30)
            }
            await asyncio.sleep(2)
            await self.async_refresh()
            return True
        except Exception as err:
            _LOGGER.error(f"Failed to start zone {zone_id}: {err}")
            return False

    async def start_schedule(self, schedule_id: str):
        """Start a specific schedule."""
        try:
            await self._make_request("PUT", "schedulerule/start", {"id": schedule_id})
            schedule_info = next((schedule for schedule in self.schedules if schedule['id'] == schedule_id), {})
            total_duration = schedule_info.get("totalDuration", 3600)  # Default 1 hour if unknown
            self._pending_operations[f"schedule_{schedule_id}"] = {
                'type': 'schedule',
                'id': schedule_id,
                'name': schedule_info.get('name', 'Unknown Schedule'),
                'expiry': datetime.now() + timedelta(hours=2)
            }
            self._active_schedules[schedule_id] = {
                "start_time": datetime.now(),
                "total_duration": total_duration / 1000 if total_duration > 1000 else total_duration  # Convert ms to s if needed
            }
            await asyncio.sleep(2)
            await self.async_refresh()
            return True
        except Exception as err:
            _LOGGER.error(f"Failed to start schedule {schedule_id}: {err}")
            return False

    async def stop_zone(self, zone_id: str):
        """Stop a specific zone."""
        try:
            await self._make_request("PUT", "device/stop_water", {"id": self.device_id})
            self._pending_operations.pop(f"zone_{zone_id}", None)
            await asyncio.sleep(2)
            await self.async_refresh()
            return True
        except Exception as err:
            _LOGGER.error(f"Failed to stop zone {zone_id}: {err}")
            return False

    async def stop_schedule(self, schedule_id: str):
        """Stop a specific schedule."""
        try:
            await self._make_request("PUT", "device/stop_water", {"id": self.device_id})
            self._pending_operations.pop(f"schedule_{schedule_id}", None)
            self._active_schedules.pop(schedule_id, None)
            self.running_schedules = [s for s in self.running_schedules if s["id"] != schedule_id]  # Explicitly remove
            await asyncio.sleep(2)
            await self.async_refresh()
            return True
        except Exception as err:
            _LOGGER.error(f"Failed to stop schedule {schedule_id}: {err}")
            return False
