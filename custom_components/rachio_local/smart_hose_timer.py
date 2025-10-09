"""Handler for Rachio Smart Hose Timer devices."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from aiohttp import ClientSession
from .const import (
    CLOUD_BASE_URL,
    VALVE_GET_BASE_STATION_ENDPOINT,
    VALVE_LIST_VALVES_ENDPOINT,
    PROGRAM_LIST_PROGRAMS_ENDPOINT,
    VALVE_START,
    VALVE_STOP,
    PROGRAM_GET,
)
from .utils import get_update_interval

_LOGGER = logging.getLogger(__name__)

class RachioSmartHoseTimerHandler:
    def __init__(self, api_key: str, device_data: dict) -> None:
        self.api_key = api_key
        self.device_data = device_data
        self.device_id = device_data["id"]
        self.type = device_data.get("device_type")
        self.name = device_data.get("name") or device_data.get("serialNumber") or "Smart Hose Timer"
        self.model = device_data.get("model", "")
        self.zones = []
        self.schedules = []
        self.running_zones = {}
        self.running_schedules = {}
        self.status = device_data.get("status", "OFFLINE")
        self.headers = {"Authorization": f"Bearer {api_key}"}
        self.coordinator = None
        self._pending_start = {}
        self._last_watering_completed = {}  # Track completed watering times
        self._force_stopped = {}  # Track valves we've force stopped (zone_id -> timestamp)
        self.api_call_count = 0
        self.api_rate_limit = None
        self.api_rate_remaining = None
        self.api_rate_reset = None

         # Base station specific attributes
        self.base_station_connected = False
        self.base_station_firmware = None
        self.base_station_wifi_firmware = None
        self.base_station_serial = device_data.get("serialNumber")
        self.base_station_mac = None
        self.base_station_rssi = None

    async def _make_request(self, session, url: str) -> dict | None:
        try:
            async with session.get(url, headers=self.headers) as resp:
                self.api_call_count += 1

                # Only update rate limit values if they're present (don't overwrite with None)
                if "X-RateLimit-Limit" in resp.headers:
                    self.api_rate_limit = resp.headers.get("X-RateLimit-Limit")
                if "X-RateLimit-Remaining" in resp.headers:
                    self.api_rate_remaining = resp.headers.get("X-RateLimit-Remaining")
                if "X-RateLimit-Reset" in resp.headers:
                    self.api_rate_reset = resp.headers.get("X-RateLimit-Reset")

                # Log rate limit headers for debugging
                _LOGGER.debug(f"[_make_request] Rate limit headers: Limit={self.api_rate_limit}, Remaining={self.api_rate_remaining}, Reset={self.api_rate_reset}")

                if resp.status == 404:
                    _LOGGER.debug("%s: No data found at %s", self.name, url)
                    return None
                resp.raise_for_status()
                return await resp.json()
        except Exception as err:
            _LOGGER.error("Error in _make_request: %s", err)
            return None

    async def async_update(self) -> None:
        try:
            _LOGGER.debug("Updating smart hose timer: %s", self.device_id)
            async with ClientSession() as session:
                # Get base station info
                url = f"{CLOUD_BASE_URL}{VALVE_GET_BASE_STATION_ENDPOINT.format(id=self.device_id)}"
                data = await self._make_request(session, url)
                if data:
                    self.device_data = data
                    # Handle both single baseStation and array baseStations format
                    base_stations = data.get("baseStations", [])
                    if base_stations:
                        base_station = base_stations[0]
                    else:
                        base_station = data.get("baseStation", {})

                    state = base_station.get("reportedState", {})

                    # Update base station attributes
                    self.base_station_connected = state.get("connected", False)
                    # Prefer bleHubFirmwareVersion, fall back to firmwareVersion
                    self.base_station_firmware = state.get("bleHubFirmwareVersion") or state.get("firmwareVersion")
                    self.base_station_wifi_firmware = state.get("wifiBridgeFirmwareVersion")
                    self.base_station_mac = base_station.get("macAddress")
                    self.base_station_rssi = state.get("rssi")
                    self.status = "ONLINE" if state.get("connected") else "OFFLINE"
                else:
                    self.device_data = {}
                    self.status = "OFFLINE"
                    self.base_station_connected = False

                # Get valves (zones)
                url = f"{CLOUD_BASE_URL}{VALVE_LIST_VALVES_ENDPOINT.format(baseStationId=self.device_id)}"
                data = await self._make_request(session, url)
                if data:
                    self.zones = data.get("valves", [])
                else:
                    self.zones = []

                # Get programs (schedules)
                url = f"{CLOUD_BASE_URL}{PROGRAM_LIST_PROGRAMS_ENDPOINT.format(baseStationId=self.device_id)}"
                data = await self._make_request(session, url)
                if data:
                    self.schedules = data.get("programs", [])
                else:
                    self.schedules = []

                # Detect running zones by calculating if lastWateringAction is still active
                running_zones = {}
                current_time = datetime.now()

                for valve in self.zones:
                    valve_id = valve["id"]
                    state = valve.get("state", {}).get("reportedState", {})
                    last_action = state.get("lastWateringAction", {})

                    # Check if there's a watering action with start time and duration
                    if last_action.get("start") and last_action.get("durationSeconds"):
                        try:
                            # Parse the start time (ISO 8601 format)
                            start_str = last_action["start"]
                            # Handle both with and without milliseconds
                            if "." in start_str:
                                start_time = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                            else:
                                start_time = datetime.fromisoformat(start_str.replace("Z", "+00:00"))

                            duration_seconds = int(last_action["durationSeconds"])
                            end_time = start_time + timedelta(seconds=duration_seconds)

                            # Add 30 second buffer for API lag
                            end_time_buffer = end_time + timedelta(seconds=30)

                            # Make current_time timezone-aware if start_time is
                            if start_time.tzinfo is not None:
                                current_time = datetime.now(start_time.tzinfo)
                            else:
                                current_time = datetime.now()

                            # Check if we force stopped this valve recently (within last 30 seconds)
                            # This prevents race conditions where coordinator updates overwrite manual stops
                            if valve_id in self._force_stopped:
                                force_stop_time = self._force_stopped[valve_id]
                                time_since_stop = (current_time - force_stop_time).total_seconds()
                                if time_since_stop < 30:  # Ignore API data for 30 seconds after force stop
                                    _LOGGER.debug(f"Valve {valve_id} force stopped {time_since_stop:.0f}s ago, ignoring API data")
                                    continue
                                else:
                                    # Clear old force stop tracking
                                    self._force_stopped.pop(valve_id, None)

                            # Check if we manually stopped this valve recently
                            # If so, ignore stale API data showing it's still running
                            if valve_id in self._last_watering_completed:
                                last_completed = self._last_watering_completed[valve_id]
                                # If the API action ended before our manual stop, ignore it
                                if end_time <= last_completed:
                                    _LOGGER.debug(f"Valve {valve_id} ignoring stale API data (ended {end_time} vs stopped {last_completed})")
                                    continue

                            # Check if currently watering
                            if start_time <= current_time <= end_time_buffer:
                                remaining_seconds = (end_time - current_time).total_seconds()
                                running_zones[valve_id] = {
                                    "id": valve_id,
                                    "remaining": max(0, remaining_seconds)
                                }
                                _LOGGER.debug(f"Valve {valve_id} is running, {remaining_seconds:.0f}s remaining")
                            elif current_time > end_time_buffer:
                                # Watering has completed, record completion time
                                if valve_id not in self._last_watering_completed:
                                    self._last_watering_completed[valve_id] = end_time
                                    _LOGGER.debug(f"Valve {valve_id} watering completed at {end_time}")
                        except (ValueError, KeyError) as e:
                            _LOGGER.warning(f"Error parsing watering times for valve {valve_id}: {e}")

                self.running_zones = running_zones

                # Set running schedules (if any)
                self.running_schedules = {prog["id"]: prog for prog in self.schedules if prog.get("active", False)}
        except Exception as err:
            _LOGGER.error("Error updating smart hose timer: %s", err)
            raise

    async def async_start_zone(self, zone_id, duration=600):
        async with ClientSession() as session:
            url = f"{CLOUD_BASE_URL}/{VALVE_START}"
            payload = {"valveId": zone_id, "durationSeconds": duration}
            method = session.put
            _LOGGER.info("Starting valve: %s with payload: %s", url, payload)
            async with method(url, headers=self.headers, json=payload) as resp:
                _LOGGER.info("Start response status: %s", resp.status)
                if resp.status >= 400:
                    _LOGGER.error("Start response text: %s", await resp.text())
                if resp.status in (200, 204):
                    self.running_zones[zone_id] = {"id": zone_id, "remaining": duration}
                    self._pending_start[zone_id] = time.time() + 60
                    return True
                resp.raise_for_status()
                try:
                    result = await resp.json()
                    self.running_zones[zone_id] = {"id": zone_id, "remaining": duration}
                    return result
                except Exception:
                    return True

    async def async_stop_zone(self, zone_id):
        # Immediately mark as force stopped to prevent race conditions
        now = datetime.now(timezone.utc)
        self._force_stopped[zone_id] = now
        self._last_watering_completed[zone_id] = now
        self.running_zones.pop(zone_id, None)
        self._pending_start.pop(zone_id, None)
        _LOGGER.debug(f"Force stopped valve {zone_id} - cleared all local state")

        # Now make the API call
        async with ClientSession() as session:
            url = f"{CLOUD_BASE_URL}/{VALVE_STOP}"
            payload = {"valveId": zone_id}
            _LOGGER.info("Stopping valve: %s with payload: %s", url, payload)
            async with session.put(url, headers=self.headers, json=payload) as resp:
                _LOGGER.info("Stop response status: %s", resp.status)
                resp.raise_for_status()

                # Try to parse response
                if resp.status == 204:
                    return True
                try:
                    return await resp.json()
                except Exception:
                    return True

    async def async_start_schedule(self, schedule_id):
        async with ClientSession() as session:
            url = f"{CLOUD_BASE_URL}/{PROGRAM_GET.format(id=schedule_id)}"
            payload = {"programId": schedule_id}
            method = session.put
            _LOGGER.info("Starting program: %s with payload: %s", url, payload)
            async with method(url, headers=self.headers, json=payload) as resp:
                _LOGGER.info("Start response status: %s", resp.status)
                if resp.status in (200, 204):
                    self._pending_start[schedule_id] = time.time() + 60
                    if self.coordinator:
                        await self.coordinator.async_request_refresh()
                    return True
                resp.raise_for_status()
                try:
                    result = await resp.json()
                    self._pending_start[schedule_id] = time.time() + 60
                    if self.coordinator:
                        await self.coordinator.async_request_refresh()
                    return result
                except Exception:
                    return True

    async def async_stop_schedule(self, schedule_id):
        # Implement if needed
        pass

    def get_zone_default_duration(self, zone_id):
        for zone in self.zones:
            if zone.get("id") == zone_id:
                return zone.get("duration") or zone.get("defaultRuntime") or 600
        return 600

    def is_zone_optimistically_on(self, zone_id):
        # If we force stopped this valve, it's definitely off
        if zone_id in self._force_stopped:
            return False

        now = time.time()
        return zone_id in self.running_zones or (
            zone_id in self._pending_start and self._pending_start[zone_id] > now
        )

    def _get_update_interval(self) -> timedelta:
        return get_update_interval(self)

    def _get_remaining_time(self) -> float:
        """Get remaining time in minutes."""
        remaining_secs = 0
        for zone in self.running_zones.values():
            remaining_secs = max(remaining_secs, zone.get("remaining", 0))
        for schedule in self.running_schedules.values():
            remaining_secs = max(remaining_secs, schedule.get("remaining", 0))
        return remaining_secs / 60  # Convert to minutes
