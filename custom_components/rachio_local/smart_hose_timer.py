"""Handler for Rachio Smart Hose Timer devices."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
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
        self.valve_diagnostics = {}  # Cache for per-valve diagnostics
        self.api_call_count = 0
        self.api_rate_limit = None
        self.api_rate_remaining = None
        self.api_rate_reset = None

    async def _make_request(self, session, url: str) -> dict | None:
        try:
            async with session.get(url, headers=self.headers) as resp:
                self.api_call_count += 1
                self.api_rate_limit = resp.headers.get("X-RateLimit-Limit")
                self.api_rate_remaining = resp.headers.get("X-RateLimit-Remaining")
                self.api_rate_reset = resp.headers.get("X-RateLimit-Reset")
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
                    self.status = "ONLINE" if data.get("connected") else "OFFLINE"
                else:
                    self.device_data = {}
                    self.status = "OFFLINE"

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

                # --- ENHANCED: Detect running zones by reportedState ---
                running_zones = {}
                for valve in self.zones:
                    valve_id = valve["id"]
                    state = valve.get("state", {}).get("reportedState", {})
                    # Check if valve is actively watering (OPEN or RUNNING or similar)
                    is_running = False
                    # Rachio API may use 'OPEN', 'RUNNING', or 'WATERING' for active, 'CLOSED' or 'OFF' for stopped
                    if state.get("watering") is True or state.get("status") in ("OPEN", "RUNNING", "WATERING"):
                        is_running = True
                    # Fallback: if lastWateringAction has a start but no end, consider running
                    last_action = state.get("lastWateringAction", {})
                    if last_action.get("start") and not last_action.get("end"):
                        is_running = True
                    if is_running:
                        running_zones[valve_id] = valve
                self.running_zones = running_zones

                # Set running schedules (if any)
                self.running_schedules = {prog["id"]: prog for prog in self.schedules if prog.get("active", False)}

                # Fetch and cache per-valve diagnostics
                await self._update_valve_diagnostics(session)
        except Exception as err:
            _LOGGER.error("Error updating smart hose timer: %s", err)
            raise

    async def _update_valve_diagnostics(self, session):
        """Fetch diagnostics for each valve using /valve/getValve/{id}."""
        if not self.zones:
            return
        if not hasattr(self, 'valve_diagnostics_persist'):
            self.valve_diagnostics_persist = {}
        cache = {}
        for valve in self.zones:
            valve_id = valve["id"]
            url = f"{CLOUD_BASE_URL}/valve/getValve/{valve_id}"
            try:
                async with session.get(url, headers=self.headers) as resp:
                    if resp.status != 200:
                        _LOGGER.warning(f"Failed to fetch diagnostics for valve {valve_id}: %s", await resp.text())
                        continue
                    data = await resp.json()
                    v = data.get("valve", {})
                    state = v.get("state", {}).get("reportedState", {})

                    # Get the last watering action
                    last_watered = None
                    last_action = state.get("lastWateringAction", {})
                    
                    # Only use the end timestamp if watering is complete
                    if last_action.get("end"):
                        last_watered = last_action.get("end")
                    # If currently watering (has start but no end), keep previous timestamp
                    elif last_action.get("start") and not last_action.get("end"):
                        last_watered = self.valve_diagnostics_persist.get(valve_id)
                    # Otherwise try to get from persist cache
                    else:
                        last_watered = self.valve_diagnostics_persist.get(valve_id)
                    
                    # Only update persist cache if we have a valid end timestamp
                    if last_watered and last_action.get("end"):
                        self.valve_diagnostics_persist[valve_id] = last_watered
                    
                    diag = {
                        "lastWatered": last_watered,
                        "connected": state.get("connected"),
                        "batteryStatus": state.get("batteryStatus"),
                    }
                    cache[valve_id] = diag
            except Exception as err:
                _LOGGER.error(f"Error fetching diagnostics for valve {valve_id}: %s", err)
        self.valve_diagnostics = cache

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
                    if self.coordinator:
                        await self.coordinator.async_request_refresh()
                    return True
                resp.raise_for_status()
                try:
                    result = await resp.json()
                    self.running_zones[zone_id] = {"id": zone_id, "remaining": duration}
                    if self.coordinator:
                        await self.coordinator.async_request_refresh()
                    return result
                except Exception:
                    return True

    async def async_stop_zone(self, zone_id):
        async with ClientSession() as session:
            url = f"{CLOUD_BASE_URL}/{VALVE_STOP}"
            payload = {"valveId": zone_id}
            _LOGGER.info("Stopping valve: %s with payload: %s", url, payload)
            async with session.put(url, headers=self.headers, json=payload) as resp:
                _LOGGER.info("Stop response status: %s", resp.status)
                if resp.status == 204:
                    self.running_zones.pop(zone_id, None)
                    self._pending_start.pop(zone_id, None)
                    if self.coordinator:
                        await self.coordinator.async_request_refresh()
                    return True
                resp.raise_for_status()
                try:
                    result = await resp.json()
                    self.running_zones.pop(zone_id, None)
                    self._pending_start.pop(zone_id, None)
                    if self.coordinator:
                        await self.coordinator.async_request_refresh()
                    return result
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
