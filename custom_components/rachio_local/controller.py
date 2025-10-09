"""Controller for Rachio irrigation controllers."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from aiohttp import ClientSession

from .const import (
    API_BASE_URL,
    DEVICE_CURRENT_SCHEDULE,
    DEVICE_GET_ENDPOINT,
    DEVICE_STOP_WATER,
    SCHEDULE_START,
    SCHEDULE_STOP,
    ZONE_START,
)
from .utils import get_update_interval

_LOGGER = logging.getLogger(__name__)

class RachioControllerHandler:
    """Handler for Rachio Controller devices."""

    OPTIMISTIC_WINDOW = 60  # seconds, increased from 30 for better UX

    def __init__(self, api_key: str, device_data: dict) -> None:
        """Initialize the Rachio controller."""
        self.api_key = api_key
        self.device_data = device_data
        self.device_id = device_data["id"]
        self.type = device_data.get("device_type", "CONTROLLER")
        self.name = device_data.get("name", "")
        self.model = device_data.get("model", "")
        self.zones = []
        self.schedules = []
        self.running_zones = {}
        self.running_schedules = {}
        self.status = device_data.get("status", "OFFLINE")
        self.headers = {"Authorization": f"Bearer {api_key}"}
        self.coordinator = None
        self._pending_start = {}
        self.api_call_count = 0
        self.api_rate_limit = None
        self.api_rate_remaining = None
        self.api_rate_reset = None

        # Configurable polling intervals (in seconds)
        self.idle_polling_interval = 300  # 5 minutes when idle
        self.active_polling_interval = 120  # 2 minutes when actively watering

    async def _make_request(self, session, url: str) -> dict | None:
        try:
            async with session.get(url, headers=self.headers) as resp:
                self.api_call_count = int(resp.headers.get("X-RateLimit-Limit", self.api_call_count) or 0)

                # Only update rate limit values if they're present (don't overwrite with None)
                if "X-RateLimit-Limit" in resp.headers:
                    self.api_rate_limit = resp.headers.get("X-RateLimit-Limit")
                if "X-RateLimit-Remaining" in resp.headers:
                    self.api_rate_remaining = resp.headers.get("X-RateLimit-Remaining")
                if "X-RateLimit-Reset" in resp.headers:
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
        """Update controller data and reconcile optimistic state with actual API state."""
        try:
            now = time.time()
            # --- Rate limit guard ---
            if self.api_rate_remaining is not None and self.api_rate_reset is not None:
                try:
                    remaining = int(self.api_rate_remaining)
                    reset = int(self.api_rate_reset)
                    if remaining <= 1 and now < reset:
                        _LOGGER.warning(f"[POLL] API rate limit reached, skipping poll until reset at {datetime.fromtimestamp(reset)}")
                        return
                except Exception as e:
                    _LOGGER.debug(f"[POLL] Could not parse rate limit headers: {e}")

            _LOGGER.debug(f"[POLL] Updating controller: {self.device_id} at {datetime.now().isoformat()}")
            async with ClientSession() as session:
                # Device info
                url = f"{API_BASE_URL}/{DEVICE_GET_ENDPOINT.format(id=self.device_id)}"
                data = await self._make_request(session, url)
                _LOGGER.debug(f"[POLL] Device info: status={data.get('status') if data else 'None'}, zones={len(data.get('zones', []) if data else [])}, schedules={len(data.get('scheduleRules', []) if data else [])}")
                _LOGGER.debug(f"[POLL] Full device API response: {data}")
                _LOGGER.debug(f"[POLL] Rate limit: remaining={self.api_rate_remaining}, reset={self.api_rate_reset}")
                if data:
                    self.device_data = data
                    self.status = data.get("status", "OFFLINE")
                    self.zones = data.get("zones", [])
                    self.schedules = data.get("scheduleRules", [])
                else:
                    self.device_data = {}
                    self.status = "OFFLINE"
                    self.zones = []
                    self.schedules = []

                # --- ENHANCED: Detect running zones by checking all zones for remaining > 0 ---
                running_zones = {}
                device_status = self.status
                # Check all zones for remaining > 0
                for zone in self.zones:
                    zone_id = zone.get("id")
                    remaining = zone.get("remaining", 0)
                    if remaining > 0 and zone_id:
                        running_zones[zone_id] = {"id": zone_id, "remaining": remaining}
                        _LOGGER.debug(f"[POLL] Detected running zone: id={zone_id}, remaining={remaining}")
                # Fallback: legacy logic for WATERING/zoneId
                if not running_zones and data and device_status == "WATERING":
                    zone_id = data.get("zoneId")
                    if zone_id:
                        remaining = 0
                        for zone in self.zones:
                            if zone.get("id") == zone_id:
                                remaining = zone.get("remaining", 0)
                                break
                        running_zones[zone_id] = {"id": zone_id, "remaining": remaining}
                        _LOGGER.debug(f"[POLL] Device endpoint: WATERING zone_id={zone_id}, remaining={remaining}")

                # Current schedule (for schedule info and fallback)
                url = f"{API_BASE_URL}/{DEVICE_CURRENT_SCHEDULE.format(id=self.device_id)}"
                data = await self._make_request(session, url)
                _LOGGER.warning(f"[DEBUG] /current_schedule API response: {data}")
                # --- AUTHORITATIVE: Use only /current_schedule for running_zones ---
                running_zones = {}
                running_schedules = {}
                if isinstance(data, list):
                    for sched in data:
                        zone_id = sched.get("zoneId")
                        # Prefer remainingSeconds, fallback to remaining, then zoneDuration/duration if status is PROCESSING
                        remaining = sched.get("remainingSeconds")
                        if remaining is None:
                            remaining = sched.get("remaining")
                        if (remaining is None or remaining == 0) and sched.get("status", "").upper() in ("PROCESSING", "WATERING"):
                            remaining = sched.get("zoneDuration") or sched.get("duration") or 0
                        sched_type = sched.get("scheduleType")
                        sched_id = sched.get("scheduleRuleId") or sched.get("id")
                        if zone_id and remaining and remaining > 0:
                            running_zones[zone_id] = {
                                "id": zone_id,
                                "remaining": remaining,
                                "schedule_type": sched_type,
                                "schedule_id": sched_id,
                                "zone_name": sched.get("zoneName"),
                                "zone_number": sched.get("zoneNumber"),
                                "started_at": sched.get("zoneStartDate"),
                            }
                            if sched_id:
                                running_schedules[sched_id] = sched
                            _LOGGER.debug(f"[POLL] DEVICE_CURRENT_SCHEDULE: Running zone: id={zone_id}, remaining={remaining}, type={sched_type}, sched_id={sched_id}")
                elif data:
                    # Some controllers may return a single object instead of a list
                    zone_id = data.get("zoneId")
                    # Try to get remaining time from all possible fields
                    remaining = (
                        data.get("remainingSeconds")
                        or data.get("remaining")
                        or 0
                    )
                    sched_type = data.get("scheduleType")
                    sched_id = data.get("scheduleRuleId") or data.get("id")
                    # If remaining is 0 or missing, but status is PROCESSING/WATERING and zoneId is present, use zoneDuration or duration
                    if zone_id and (remaining > 0 or (data.get("status") in ("PROCESSING", "WATERING") and (data.get("zoneDuration") or data.get("duration")))):
                        if remaining <= 0:
                            remaining = data.get("zoneDuration") or data.get("duration") or 0
                        running_zones[zone_id] = {
                            "id": zone_id,
                            "remaining": remaining,
                            "schedule_type": sched_type,
                            "schedule_id": sched_id,
                            "zone_name": data.get("zoneName"),
                            "zone_number": data.get("zoneNumber"),
                            "started_at": data.get("zoneStartDate"),
                        }
                        if sched_id:
                            running_schedules[sched_id] = data
                        _LOGGER.debug(f"[POLL] DEVICE_CURRENT_SCHEDULE: Running zone: id={zone_id}, remaining={remaining}, type={sched_type}, sched_id={sched_id}")
                # Use only /current_schedule for running_zones and running_schedules
                self.running_zones = running_zones
                self.running_schedules = running_schedules
                _LOGGER.warning(f"[DEBUG] running_zones after poll: {self.running_zones}")

            # Reconcile optimistic state: clear any pending starts if not running
            now = time.time()
            to_remove = []
            for zone_id, until in self._pending_start.items():
                if zone_id not in self.running_zones and now > until:
                    to_remove.append(zone_id)
            for zone_id in to_remove:
                self._pending_start.pop(zone_id, None)

            # Summary log
            _LOGGER.debug(f"[POLL] Schedules: {list(self.running_schedules.keys())} | Zones: {list(self.running_zones.keys())} | Optimistic: {self._pending_start}")
        except Exception as err:
            _LOGGER.error(f"[POLL] Error updating controller: {err}")
            raise

    async def async_start_zone(self, zone_id, duration=600):
        """Start a zone."""
        async with ClientSession() as session:
            url = f"{API_BASE_URL}/{ZONE_START}"
            payload = {"id": zone_id, "duration": duration}
            method = session.put
            _LOGGER.info("Starting zone: %s with payload: %s", url, payload)
            async with method(url, headers=self.headers, json=payload) as resp:
                _LOGGER.info("Start response status: %s", resp.status)
                if resp.status >= 400:
                    _LOGGER.error("Start response text: %s", await resp.text())
                if resp.status in (200, 204):
                    self.running_zones[zone_id] = {"id": zone_id, "remaining": duration}
                    self._pending_start[zone_id] = time.time() + self.OPTIMISTIC_WINDOW
                    _LOGGER.debug(f"[OPTIMISTIC] Set pending_start for zone {zone_id} until {self._pending_start[zone_id]}")
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
        """Stop a zone."""
        async with ClientSession() as session:
            url = f"{API_BASE_URL}/{DEVICE_STOP_WATER}"
            payload = {"id": self.device_id}
            _LOGGER.info("Stopping zone: %s with payload: %s", url, payload)
            async with session.put(url, headers=self.headers, json=payload) as resp:
                _LOGGER.info("Stop response status: %s", resp.status)
                if resp.status == 204:
                    self.running_zones.pop(zone_id, None)
                    self._pending_start.pop(zone_id, None)  # Clear optimistic timer on stop
                    if self.coordinator:
                        await self.coordinator.async_request_refresh()
                    return True
                resp.raise_for_status()
                try:
                    result = await resp.json()
                    self.running_zones.pop(zone_id, None)
                    self._pending_start.pop(zone_id, None)  # Clear optimistic timer on stop
                    if self.coordinator:
                        await self.coordinator.async_request_refresh()
                    return result
                except Exception:
                    return True

    async def async_set_rain_delay(self, duration_hours: int = 24):
        """Set rain delay for the controller (default 24 hours)."""
        async with ClientSession() as session:
            url = f"{API_BASE_URL}/device/rain_delay"
            payload = {"id": self.device_id, "duration": duration_hours * 3600}
            _LOGGER.info("Setting rain delay: %s with payload: %s", url, payload)
            async with session.put(url, headers=self.headers, json=payload) as resp:
                _LOGGER.info("Rain delay response status: %s", resp.status)
                if resp.status >= 400:
                    _LOGGER.error("Rain delay response text: %s", await resp.text())
                resp.raise_for_status()
                return await resp.json()

    async def async_clear_rain_delay(self):
        """Clear rain delay for the controller (set duration to 0)."""
        async with ClientSession() as session:
            url = f"{API_BASE_URL}/device/rain_delay"
            payload = {"id": self.device_id, "duration": 0}
            _LOGGER.info("Clearing rain delay: %s with payload: %s", url, payload)
            async with session.put(url, headers=self.headers, json=payload) as resp:
                _LOGGER.info("Clear rain delay response status: %s", resp.status)
                if resp.status >= 400:
                    _LOGGER.error("Clear rain delay response text: %s", await resp.text())
                resp.raise_for_status()
                return await resp.json()

    def get_zone_default_duration(self, zone_id):
        """Get the default duration for a zone."""
        for zone in self.zones:
            if zone.get("id") == zone_id:
                return zone.get("duration") or zone.get("defaultRuntime") or 600
        return 600

    def is_zone_optimistically_on(self, zone_id):
        """Check if a zone is optimistically considered 'on'."""
        now = time.time()
        pending = self._pending_start.get(zone_id, 0) > now
        running = zone_id in self.running_zones
        _LOGGER.debug(f"[OPTIMISTIC] is_zone_optimistically_on: zone_id={zone_id}, running={running}, pending={pending}, now={now}, pending_until={self._pending_start.get(zone_id)}, running_zones={list(self.running_zones.keys())}")
        # Extra debug: print full running_zones dict for troubleshooting
        _LOGGER.debug(f"[OPTIMISTIC] running_zones full: {self.running_zones}")
        return running or pending

    @staticmethod
    def calculate_safe_polling_interval(num_devices: int, target_max_calls_per_hour: int = 80) -> int:
        """
        Calculate a safe polling interval (in seconds) based on the number of devices/controllers.
        Each device makes 2 API calls per poll. The default target is 80 calls/hour (20% below 100/hr limit).
        """
        if num_devices < 1:
            num_devices = 1
        calls_per_poll = num_devices * 2
        # interval = seconds between polls
        min_interval = 30  # never poll more often than every 30s
        max_interval = 900  # never poll less often than every 15min
        # interval = (calls_per_poll * polls_per_hour) <= target_max_calls_per_hour
        # polls_per_hour = 3600 / interval
        # calls_per_poll * (3600 / interval) <= target_max_calls_per_hour
        # interval >= (calls_per_poll * 3600) / target_max_calls_per_hour
        interval = int((calls_per_poll * 3600) / target_max_calls_per_hour)
        interval = max(min_interval, interval)
        interval = min(max_interval, interval)
        return interval

    def _get_update_interval(self) -> timedelta:
        # Get number of controllers from coordinator or config
        num_devices = 1
        if self.coordinator and hasattr(self.coordinator, 'num_devices'):
            num_devices = self.coordinator.num_devices
        safe_min = self.calculate_safe_polling_interval(num_devices)
        # Dynamic interval based on remaining time
        remaining = self._get_remaining_time()  # in minutes
        if not self.running_zones and not self.running_schedules:
            interval = max(safe_min, 300)  # 5 min idle
        elif remaining > 10:
            interval = max(safe_min, 120)  # 2 min
        elif remaining > 5:
            interval = max(safe_min, 60)   # 1 min
        elif remaining > 1:
            interval = max(safe_min, 30)   # 30 sec
        else:
            interval = max(safe_min, 20)   # 20 sec for last minute
        return timedelta(seconds=interval)

    def _get_remaining_time(self) -> float:
        """Get remaining time in minutes."""
        remaining_secs = 0
        for zone in self.running_zones.values():
            remaining_secs = max(remaining_secs, zone.get("remaining", 0))
        for schedule in self.running_schedules.values():
            remaining_secs = max(remaining_secs, schedule.get("remaining", 0))
        return remaining_secs / 60  # Convert to minutes

    async def async_start_schedule(self, schedule_id, duration=None):
        """Start a schedule on the controller using the Rachio API and reflect state immediately with optimistic timing."""
        async with ClientSession() as session:
            url = f"{API_BASE_URL}/{SCHEDULE_START}"
            payload = {"id": schedule_id}
            if duration:
                payload["duration"] = duration
            _LOGGER.info("Starting schedule: %s with payload: %s", url, payload)
            async with session.put(url, headers=self.headers, json=payload) as resp:
                _LOGGER.info("Start schedule response status: %s", resp.status)
                response_text = await resp.text()
                _LOGGER.debug("Start schedule response text: %s", response_text)
                if resp.status >= 400:
                    _LOGGER.error("Start schedule failed: %s", response_text)
                    return False
                resp.raise_for_status()
                # Optimistically set running_schedules and pending start for immediate UI feedback
                self.running_schedules[schedule_id] = {"id": schedule_id, "optimistic": True}
                self._pending_start[schedule_id] = time.time() + self.OPTIMISTIC_WINDOW  # Use same window as zones
                if self.coordinator:
                    await self.coordinator.async_request_refresh()
                try:
                    result = await resp.json()
                    return result
                except Exception:
                    return True

    async def async_stop_schedule(self, schedule_id):
        """Stop all watering on the controller using the Rachio API (device/stop_water) and reflect state immediately."""
        async with ClientSession() as session:
            url = f"{API_BASE_URL}/{DEVICE_STOP_WATER}"
            payload = {"id": self.device_id}
            _LOGGER.info("Stopping all watering: %s with payload: %s", url, payload)
            async with session.put(url, headers=self.headers, json=payload) as resp:
                _LOGGER.info("Stop watering response status: %s", resp.status)
                response_text = await resp.text()
                _LOGGER.debug("Stop watering response text: %s", response_text)
                if resp.status >= 400:
                    _LOGGER.error("Stop watering failed: %s", response_text)
                    return False
                resp.raise_for_status()
                # Optimistically clear running_schedules and pending start for immediate UI feedback
                self.running_schedules = {}
                self._pending_start.pop(schedule_id, None)
                if self.coordinator:
                    await self.coordinator.async_request_refresh()
                try:
                    result = await resp.json()
                    return result
                except Exception:
                    return True
