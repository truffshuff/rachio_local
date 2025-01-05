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
    
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    
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
        self.running_schedules = []
        self.current_schedule = None
        self._active_watering = False
        # Add pending operations tracking
        self._pending_operations = {}

    async def _make_request(self, method, endpoint, data=None):
        """Make an API request."""
        url = f"https://api.rach.io/1/public/{endpoint}"
        try:
            response = await self.hass.async_add_executor_job(
                lambda: requests.request(
                    method=method,
                    url=url,
                    headers=self.headers,
                    json=data
                )
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
            # Get device details including current state
            device_state = await self._make_request(
                "GET",
                f"device/{self.device_id}"
            )

            # Get current schedule
            current = await self._make_request(
                "GET",
                f"device/{self.device_id}/current_schedule"
            )

            return {
                "device_state": device_state,
                "current_schedule": current
            }
        except Exception as err:
            _LOGGER.error(f"Failed to get device state: {err}")
            return None

    async def _async_update_data(self):
        """Update data via API."""
        try:
            # If we don't have a device ID yet, get it from person info
            if not self.device_id:
                person_info = await self._make_request("GET", "person/info")
                self.person_id = person_info["id"]

                person = await self._make_request("GET", f"person/{self.person_id}")
                if not person["devices"]:
                    raise UpdateFailed("No Rachio devices found")

                self.device_id = person["devices"][0]["id"]
                self.device_info = person["devices"][0]

            # Get current device state
            state_data = await self._get_device_state()
            if not state_data:
                return None

            device_state = state_data["device_state"]
            current_schedule = state_data["current_schedule"]

            # Update stored data
            self.zones = device_state.get("zones", [])
            self.schedules = device_state.get("scheduleRules", [])
            self.current_schedule = current_schedule

            # Process running zones and schedules
            current_time = datetime.now()
            self.running_zones = []
            self.running_schedules = []

            # Check pending operations first
            for entity_id, operation in list(self._pending_operations.items()):
                if current_time >= operation['expiry']:
                    self._pending_operations.pop(entity_id)
                else:
                    # If operation is still pending, add it to running lists
                    if operation['type'] == 'zone':
                        self.running_zones.append({
                            "id": operation['id'],
                            "name": operation.get('name', 'Unknown Zone'),
                            "duration": operation.get('duration', 0),
                            "remaining_time": operation.get('duration', 0),
                        })
                    elif operation['type'] == 'schedule':
                        self.running_schedules.append({
                            "id": operation['id'],
                            "name": operation.get('name', 'Unknown Schedule'),
                        })

            # Process actual running state from API
            if current_schedule:
                schedule_id = current_schedule.get("scheduleId")
                schedule_name = current_schedule.get("scheduleName", "Unknown Schedule")

                active_zones = [
                    zone for zone in current_schedule.get("zoneSequence", [])
                    if zone.get("running", False)
                ]

                if active_zones:
                    self.running_schedules.append({
                        "id": schedule_id,
                        "name": schedule_name,
                        "running_zone_id": active_zones[0].get("zoneId")
                    })

                    for zone in active_zones:
                        self.running_zones.append({
                            "id": zone.get("zoneId"),
                            "name": zone.get("zoneName", "Unknown Zone"),
                            "duration": zone.get("duration", 0),
                            "remaining_time": zone.get("durationMinutes", 0) * 60,
                            "schedule_id": schedule_id,
                            "schedule_name": schedule_name
                        })

            self._active_watering = bool(self.running_zones)

            # Adjust update interval based on activity
            if self._active_watering or self._pending_operations:
                self.update_interval = timedelta(seconds=30)
            else:
                self.update_interval = timedelta(minutes=5)

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
            # Send the start request
            await self._make_request(
                "PUT",
                "zone/start",
                {"id": zone_id, "duration": duration}
            )

            # Add pending operation
            zone_info = next((zone for zone in self.zones if zone['id'] == zone_id), {})
            self._pending_operations[f"zone_{zone_id}"] = {
                'type': 'zone',
                'id': zone_id,
                'name': zone_info.get('name', 'Unknown Zone'),
                'duration': duration,
                'expiry': datetime.now() + timedelta(seconds=10)  # 10 second grace period
            }

            # Trigger an immediate refresh after a brief delay
            await asyncio.sleep(2)  # Small delay to let API catch up
            await self.async_refresh()
            return True
        except Exception as err:
            _LOGGER.error(f"Failed to start zone {zone_id}: {err}")
            return False

    async def start_schedule(self, schedule_id: str):
        """Start a specific schedule."""
        try:
            # Send the start request
            await self._make_request(
                "PUT",
                "schedulerule/start",
                {"id": schedule_id}
            )

            # Add pending operation
            schedule_info = next((schedule for schedule in self.schedules if schedule['id'] == schedule_id), {})
            self._pending_operations[f"schedule_{schedule_id}"] = {
                'type': 'schedule',
                'id': schedule_id,
                'name': schedule_info.get('name', 'Unknown Schedule'),
                'expiry': datetime.now() + timedelta(seconds=10)  # 10 second grace period
            }

            # Trigger an immediate refresh after a brief delay
            await asyncio.sleep(2)  # Small delay to let API catch up
            await self.async_refresh()
            return True
        except Exception as err:
            _LOGGER.error(f"Failed to start schedule {schedule_id}: {err}")
            return False

    async def stop_zone(self, zone_id: str):
        """Stop a specific zone."""
        try:
            await self._make_request(
                "PUT",
                "device/stop_water",
                {"id": self.device_id}
            )

            # Remove any pending operations
            self._pending_operations.pop(f"zone_{zone_id}", None)

            # Trigger an immediate refresh after a brief delay
            await asyncio.sleep(2)  # Small delay to let API catch up
            await self.async_refresh()
            return True
        except Exception as err:
            _LOGGER.error(f"Failed to stop zone {zone_id}: {err}")
            return False
