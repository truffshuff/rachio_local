"""Handler for Rachio Smart Hose Timer devices."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from aiohttp import ClientSession
from homeassistant.helpers import entity_registry as er
from .const import (
    CLOUD_BASE_URL,
    VALVE_GET_BASE_STATION_ENDPOINT,
    VALVE_LIST_VALVES_ENDPOINT,
    SUMMARY_VALVE_VIEWS,
    VALVE_START,
    VALVE_STOP,
    PROGRAM_GET,
    PROGRAM_GET_V2,
    DOMAIN,
)
from .utils import get_update_interval

_LOGGER = logging.getLogger(__name__)

class RachioSmartHoseTimerHandler:
    def __init__(self, api_key: str, device_data: dict, user_id: str = None, hass = None, config_entry = None) -> None:
        self.api_key = api_key
        self.device_data = device_data
        self.device_id = device_data["id"]
        self.user_id = user_id  # Store user_id for program queries
        self.hass = hass  # Store hass instance for entity registry access
        self.config_entry = config_entry  # Store config entry for direct option access
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
        self._expected_end_times = {}  # Track expected end times for running valves (zone_id -> end_time)
        self.api_call_count = 0
        self.api_rate_limit = None
        self.api_rate_remaining = None
        self.api_rate_reset = None

        # Configurable polling intervals (in seconds)
        self.idle_polling_interval = 300  # 5 minutes when idle
        self.active_polling_interval = 120  # 2 minutes when actively watering

        # Run history summaries (populated from API)
        self.valve_run_summaries = {}  # valve_id -> {previous_run: {...}, next_run: {...}}
        self.program_run_summaries = {}  # program_id -> {previous_run: {...}, next_run: {...}}
        self.valve_day_views = []  # Raw valve day views data for calendar

        # Program details cache with timestamps (for enabled/disabled status and other details)
        self._program_details = {}  # program_id -> {details: {...}, last_fetched: timestamp}
        self._program_details_refresh_interval = 3600  # Refresh hourly (in seconds)
        self._first_update_complete = False  # Track if we've done the initial update
        self._deleted_programs = set()  # Track programs that have been deleted from Rachio (to avoid repeated API calls)

         # Base station specific attributes
        self.base_station_connected = False
        self.base_station_firmware = None
        self.base_station_wifi_firmware = None
        self.base_station_serial = device_data.get("serialNumber")
        self.base_station_mac = None
        self.base_station_rssi = None

    async def _make_request(self, session, url: str, method: str = "GET", json_data: dict = None) -> dict | None:
        try:
            if method == "POST":
                async with session.post(url, headers=self.headers, json=json_data) as resp:
                    return await self._process_response(resp, url)
            else:
                async with session.get(url, headers=self.headers) as resp:
                    return await self._process_response(resp, url)
        except Exception as err:
            _LOGGER.error("Error in _make_request: %s", err)
            return None

    async def _process_response(self, resp, url: str) -> dict | None:
        """Process API response and extract rate limit headers."""
        self.api_call_count += 1

        # Only update rate limit values if they're present (don't overwrite with None)
        if "X-RateLimit-Limit" in resp.headers:
            self.api_rate_limit = resp.headers.get("X-RateLimit-Limit")
        if "X-RateLimit-Remaining" in resp.headers:
            self.api_rate_remaining = resp.headers.get("X-RateLimit-Remaining")
        if "X-RateLimit-Reset" in resp.headers:
            self.api_rate_reset = resp.headers.get("X-RateLimit-Reset")

        # Commented out to reduce log noise (called on every API request)
        # _LOGGER.debug(f"[_make_request] Rate limit headers: Limit={self.api_rate_limit}, Remaining={self.api_rate_remaining}, Reset={self.api_rate_reset}")

        if resp.status == 404:
            _LOGGER.debug("%s: No data found at %s", self.name, url)
            return None
        resp.raise_for_status()
        return await resp.json()

    async def _fetch_program_details(self, session, program_id: str, force_refresh: bool = False) -> dict | None:
        """Fetch detailed program information using getProgramV2 API with smart caching.

        Args:
            session: aiohttp ClientSession
            program_id: The program ID to fetch
            force_refresh: If True, bypass cache and fetch fresh data

        Returns:
            Program details dict or None if fetch failed
        """
        current_time = time.time()

        # Check if we have cached data and it's still fresh (unless force_refresh is True)
        if not force_refresh and program_id in self._program_details:
            cached = self._program_details[program_id]
            age = current_time - cached["last_fetched"]
            if age < self._program_details_refresh_interval:
                # Commented out to reduce log noise (called frequently during updates)
                # _LOGGER.debug(f"Using cached program details for {program_id} (age: {age:.0f}s)")
                return cached["details"]

        # Fetch fresh data
        url = f"{CLOUD_BASE_URL}/{PROGRAM_GET_V2.format(id=program_id)}"
        # Commented out to reduce log noise
        # _LOGGER.debug(f"Fetching fresh program details for {program_id}")
        data = await self._make_request(session, url)

        if data:
            # Cache the result with timestamp
            self._program_details[program_id] = {
                "details": data,
                "last_fetched": current_time
            }
            # Commented out to reduce log noise
            # _LOGGER.debug(f"Cached program details for {program_id}")
            return data

        return None

    async def _remove_program_entities(self, program_ids: list[str]) -> None:
        """Remove entities for deleted programs from the entity registry.

        This ensures that entities for deleted programs are completely removed,
        even if they were disabled by default and never enabled by the user.

        Note: This only removes entities for programs that no longer exist in Rachio.
        Entities that are just disabled by the user will NOT be removed.
        """
        if not self.hass or not program_ids:
            return

        try:
            registry = er.async_get(self.hass)

            for program_id in program_ids:
                # Find and remove sensor entity using unique_id (more reliable than entity_id)
                sensor_unique_id = f"{self.device_id}_program_{program_id}"
                sensor_entry = registry.async_get_entity_id("sensor", DOMAIN, sensor_unique_id)
                if sensor_entry:
                    registry.async_remove(sensor_entry)
                    _LOGGER.info(f"Removed sensor entity for deleted program {program_id}")
                else:
                    _LOGGER.debug(f"Sensor entity for program {program_id} not found in registry (may have been manually removed)")

                # Find and remove button entity
                button_unique_id = f"{self.device_id}_refresh_program_{program_id}"
                button_entry = registry.async_get_entity_id("button", DOMAIN, button_unique_id)
                if button_entry:
                    registry.async_remove(button_entry)
                    _LOGGER.info(f"Removed button entity for deleted program {program_id}")
                else:
                    _LOGGER.debug(f"Button entity for program {program_id} not found in registry (may have been manually removed)")

        except Exception as e:
            _LOGGER.warning(f"Error removing entities for deleted programs: {e}")

    def force_program_details_refresh(self) -> None:
        """Mark all program details cache as stale to force refresh on next update.

        This method doesn't actually fetch data - it just marks the cache as stale.
        Call this before triggering a coordinator refresh to ensure fresh program details.
        """
        _LOGGER.info(f"Marking all program details cache as stale for {self.name}")

        # Clear all program detail caches to force fresh fetch on next update
        for program_id in list(self._program_details.keys()):
            self._program_details[program_id]["last_fetched"] = 0  # Force stale

        _LOGGER.debug(f"Marked {len(self._program_details)} program caches as stale")

    async def async_update(self) -> None:
        try:
            # Commented out to reduce log noise (called on every update)
            # _LOGGER.debug("Updating smart hose timer: %s", self.device_id)
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

                # Get programs (schedules) using getValveDayViews summary API
                # This API returns program information including multi-valve programs
                # Query the next 7 days to get scheduled program information
                url = f"{CLOUD_BASE_URL}/{SUMMARY_VALVE_VIEWS}"
                today = datetime.now()

                # Query 1 day in the past and N days in the future (user-configurable)
                start_date = today - timedelta(days=1)
                # Try to get summary_end_days from config entry options (per device)
                summary_end_days = 7
                config_options_debug = None
                if self.config_entry is not None:
                    from .number import CONF_SUMMARY_END_DAYS
                    config_key = f"{CONF_SUMMARY_END_DAYS}_{self.device_id}"
                    config_options_debug = dict(self.config_entry.options)
                    summary_end_days = self.config_entry.options.get(config_key, 7)
                _LOGGER.debug(f"[DEBUG] Config entry options for {self.device_id}: {config_options_debug}, using summary_end_days={summary_end_days}")
                end_date = today + timedelta(days=summary_end_days)

                payload = {
                    "start": {
                        "year": start_date.year,
                        "month": start_date.month,
                        "day": start_date.day
                    },
                    "end": {
                        "year": end_date.year,
                        "month": end_date.month,
                        "day": end_date.day
                    },
                    "resourceId": {
                        "baseStationId": self.device_id
                    }
                }


                data = await self._make_request(session, url, method="POST", json_data=payload)
                _LOGGER.debug(f"getValveDayViews API response (baseStationId={self.device_id}, end_days={summary_end_days}): {data}")

                # Store raw valve_day_views data for calendar
                self.valve_day_views = data.get("valveDayViews", []) if data else []

                # Extract unique programs from the summary data
                # Also parse run summaries for valves and programs
                programs_map = {}  # programId -> program info
                valve_run_history = {}  # valve_id -> list of runs
                program_run_history = {}  # program_id -> list of runs
                current_time = datetime.now(timezone.utc)

                if data and "valveDayViews" in data:
                    for day_view in data["valveDayViews"]:
                        # Process program runs
                        for program_run in day_view.get("valveProgramRunSummaries", []):
                            program_id = program_run.get("programId")
                            if program_id:
                                # Store program info
                                if program_id not in programs_map:
                                    # Build valve list for this program
                                    valve_ids = []
                                    for valve_run in program_run.get("valveRunSummaries", []):
                                        valve_id = valve_run.get("valveId")
                                        if valve_id and valve_id not in valve_ids:
                                            valve_ids.append(valve_id)

                                    # Check if we have cached program details with enabled status
                                    enabled_status = True  # Default to enabled if in schedule
                                    if program_id in self._program_details:
                                        cached_program = self._program_details[program_id]["details"].get("program", {})
                                        enabled_status = cached_program.get("enabled", True)

                                    programs_map[program_id] = {
                                        "id": program_id,
                                        "name": program_run.get("programName", "Unknown Program"),
                                        "valveIds": valve_ids,
                                        "active": False,  # Will be determined by running zones
                                        "enabled": enabled_status,  # Use cached value if available
                                        "programColor": program_run.get("programColor", "#00A7E1"),
                                        "skippable": program_run.get("skippable", False),
                                    }

                                # Store program run history
                                if program_id not in program_run_history:
                                    program_run_history[program_id] = []

                                start_str = program_run.get("start")
                                if start_str:
                                    try:
                                        start_time = datetime.fromisoformat(start_str.replace("Z", "+00:00"))

                                        # Determine if this is a past or future run
                                        is_future = start_time > current_time

                                        # Extract all valve runs for this program
                                        total_duration = 0
                                        all_skipped = True
                                        skip_info = None
                                        manual_skip = False

                                        for valve_run in program_run.get("valveRunSummaries", []):
                                            duration = valve_run.get("durationSeconds", 0)
                                            total_duration += duration

                                            # Check if this valve run was skipped
                                            if valve_run.get("skip"):
                                                skip_info = valve_run.get("skip", {})
                                                # Check for manual override trigger
                                                if "manualOverrideTrigger" in skip_info:
                                                    manual_skip = True
                                            else:
                                                all_skipped = False

                                        run_info = {
                                            "start": start_time,
                                            "start_str": start_str,
                                            "duration_seconds": program_run.get("totalRunDurationSeconds") or total_duration,
                                            "skipped": all_skipped,
                                            "manual_skip": manual_skip,
                                            "skip_reason": skip_info.get("rainOverrideTrigger") if skip_info else None,
                                            "predicted_precip_mm": skip_info.get("rainOverrideTrigger", {}).get("predictedPrecipMm") if skip_info else None,
                                            "observed_precip_mm": skip_info.get("rainOverrideTrigger", {}).get("observedPrecipMm") if skip_info else None,
                                            "skippable": program_run.get("skippable", False),
                                            "is_future": is_future,
                                        }

                                        program_run_history[program_id].append(run_info)
                                    except (ValueError, KeyError) as e:
                                        _LOGGER.debug(f"Error parsing program run time: {e}")

                                # Process valve runs from this program
                                for valve_run in program_run.get("valveRunSummaries", []):
                                    valve_id = valve_run.get("valveId")
                                    if valve_id:
                                        if valve_id not in valve_run_history:
                                            valve_run_history[valve_id] = []

                                        valve_start_str = valve_run.get("start")
                                        if valve_start_str:
                                            try:
                                                valve_start_time = datetime.fromisoformat(valve_start_str.replace("Z", "+00:00"))
                                                is_future = valve_start_time > current_time

                                                valve_run_info = {
                                                    "start": valve_start_time,
                                                    "start_str": valve_start_str,
                                                    "duration_seconds": valve_run.get("durationSeconds", 0),
                                                    "flow_detected": valve_run.get("flowDetected"),
                                                    "source": "program",
                                                    "program_id": program_id,
                                                    "program_name": program_run.get("programName", "Unknown"),
                                                    "skipped": bool(valve_run.get("skip")),
                                                    "is_future": is_future,
                                                }
                                                valve_run_history[valve_id].append(valve_run_info)
                                            except (ValueError, KeyError) as e:
                                                _LOGGER.debug(f"Error parsing valve run time from program: {e}")

                        # Process quick runs (manual runs via app)
                        for quick_run in day_view.get("valveQuickRunSummaries", []):
                            for valve_run in quick_run.get("valveRunSummaries", []):
                                valve_id = valve_run.get("valveId")
                                if valve_id:
                                    if valve_id not in valve_run_history:
                                        valve_run_history[valve_id] = []

                                    valve_start_str = valve_run.get("start")
                                    if valve_start_str:
                                        try:
                                            valve_start_time = datetime.fromisoformat(valve_start_str.replace("Z", "+00:00"))
                                            is_future = valve_start_time > current_time

                                            valve_run_info = {
                                                "start": valve_start_time,
                                                "start_str": valve_start_str,
                                                "duration_seconds": valve_run.get("durationSeconds", 0),
                                                "flow_detected": valve_run.get("flowDetected"),
                                                "source": "quick_run",
                                                "is_future": is_future,
                                            }
                                            valve_run_history[valve_id].append(valve_run_info)
                                        except (ValueError, KeyError) as e:
                                            _LOGGER.debug(f"Error parsing valve run time from quick run: {e}")

                # Process valve run history to extract previous and next runs
                for valve_id, runs in valve_run_history.items():
                    # Sort runs by start time
                    sorted_runs = sorted(runs, key=lambda x: x["start"])

                    # Find most recent past run and next future run
                    previous_run = None
                    next_run = None

                    for run in reversed(sorted_runs):
                        if not run["is_future"] and previous_run is None:
                            previous_run = run
                        if run["is_future"] and (next_run is None or run["start"] < next_run["start"]):
                            next_run = run

                    self.valve_run_summaries[valve_id] = {
                        "previous_run": previous_run,
                        "next_run": next_run,
                    }

                # Process program run history to extract previous and next runs
                for program_id, runs in program_run_history.items():
                    # Sort runs by start time
                    sorted_runs = sorted(runs, key=lambda x: x["start"])

                    # Find most recent past run and next future run
                    previous_run = None
                    next_run = None

                    for run in reversed(sorted_runs):
                        if not run["is_future"] and previous_run is None:
                            previous_run = run
                        if run["is_future"] and (next_run is None or run["start"] < next_run["start"]):
                            next_run = run

                    self.program_run_summaries[program_id] = {
                        "previous_run": previous_run,
                        "next_run": next_run,
                    }

                # Also check for programs we've seen before but aren't in current summary
                # (disabled programs won't appear in the summary but we still want to track them)
                # BUT skip programs that have been confirmed as deleted
                all_known_program_ids = set(programs_map.keys())
                for cached_program_id in list(self._program_details.keys()):
                    if cached_program_id not in all_known_program_ids:
                        # Skip if we've already confirmed this program is deleted
                        if cached_program_id in self._deleted_programs:
                            _LOGGER.debug(f"Skipping cached program {cached_program_id} - already confirmed as deleted")
                            continue

                        # This program was cached but isn't in the current summary
                        # It might be disabled - add it back to our schedules with full details
                        cached_details = self._program_details[cached_program_id]["details"]
                        if cached_details and "program" in cached_details:
                            prog = cached_details["program"]
                            if prog.get("id") not in programs_map:
                                # Build valve IDs from assignments
                                valve_ids = [a.get("entityId") for a in prog.get("assignments", []) if a.get("entityId")]

                                programs_map[prog["id"]] = {
                                    "id": prog["id"],
                                    "name": prog.get("name", "Unknown Program"),
                                    "valveIds": valve_ids,
                                    "active": False,
                                    "enabled": prog.get("enabled", False),
                                    "programColor": prog.get("color", "#00A7E1"),
                                    "skippable": False,
                                    # Include all detailed fields from cache
                                    "color": prog.get("color", "#00A7E1"),
                                    "startOn": prog.get("startOn", {}),
                                    "dailyInterval": prog.get("dailyInterval", {}),
                                    "plannedRuns": prog.get("plannedRuns", []),
                                    "assignments": prog.get("assignments", []),
                                    "rainSkipEnabled": prog.get("rainSkipEnabled", False),
                                    "settings": prog.get("settings", {}),
                                }

                                # Copy scheduling type fields
                                if "daysOfWeek" in prog:
                                    programs_map[prog["id"]]["daysOfWeek"] = prog["daysOfWeek"]
                                if "evenDays" in prog:
                                    programs_map[prog["id"]]["evenDays"] = prog["evenDays"]
                                if "oddDays" in prog:
                                    programs_map[prog["id"]]["oddDays"] = prog["oddDays"]

                                _LOGGER.debug(f"Re-added cached program {prog['id']} ({prog.get('name')}) with full details - not in current summary (possibly disabled)")

                # Filter out programs that are known to be deleted
                all_programs = list(programs_map.values())
                self.schedules = [p for p in all_programs if p.get("id") not in self._deleted_programs]

                # Commented out to reduce log noise
                # filtered_count = len(all_programs) - len(self.schedules)
                # if filtered_count > 0:
                #     _LOGGER.debug(f"Filtered out {filtered_count} deleted program(s) from schedules")

                # During first update (startup), check for program entities that exist but aren't in schedules
                # This handles disabled programs that have entities from a previous session
                if not self._first_update_complete and self.hass:
                    try:
                        registry = er.async_get(self.hass)
                        # Find all program sensor entities for this device
                        for entry in registry.entities.values():
                            if entry.domain == "sensor" and entry.platform == DOMAIN:
                                # Check if this is a program sensor for our device
                                if entry.unique_id and entry.unique_id.startswith(f"{self.device_id}_program_"):
                                    # Extract program_id from unique_id
                                    program_id = entry.unique_id.replace(f"{self.device_id}_program_", "")

                                    # Check if this program is already in our schedules
                                    program_exists = any(p.get("id") == program_id for p in self.schedules)

                                    if not program_exists and program_id not in self._deleted_programs:
                                        # This program has an entity but isn't in schedules
                                        # It's likely disabled - fetch its details
                                        _LOGGER.info(f"Found existing entity for program {program_id} not in schedules - will fetch details (likely disabled)")

                                        # Fetch the program details
                                        url = f"{CLOUD_BASE_URL}/{PROGRAM_GET_V2.format(id=program_id)}"
                                        details = await self._make_request(session, url)

                                        if details and "program" in details:
                                            prog = details["program"]
                                            # Build valve IDs from assignments
                                            valve_ids = [a.get("entityId") for a in prog.get("assignments", []) if a.get("entityId")]

                                            # Add to programs_map
                                            program_data = {
                                                "id": prog["id"],
                                                "name": prog.get("name", "Unknown Program"),
                                                "valveIds": valve_ids,
                                                "active": False,
                                                "enabled": prog.get("enabled", False),
                                                "programColor": prog.get("color", "#00A7E1"),
                                                "skippable": False,
                                                "color": prog.get("color", "#00A7E1"),
                                                "startOn": prog.get("startOn", {}),
                                                "dailyInterval": prog.get("dailyInterval", {}),
                                                "plannedRuns": prog.get("plannedRuns", []),
                                                "assignments": prog.get("assignments", []),
                                                "rainSkipEnabled": prog.get("rainSkipEnabled", False),
                                                "settings": prog.get("settings", {}),
                                            }

                                            # Copy scheduling type fields
                                            if "daysOfWeek" in prog:
                                                program_data["daysOfWeek"] = prog["daysOfWeek"]
                                            if "evenDays" in prog:
                                                program_data["evenDays"] = prog["evenDays"]
                                            if "oddDays" in prog:
                                                program_data["oddDays"] = prog["oddDays"]

                                            # Add to schedules
                                            self.schedules.append(program_data)

                                            # Cache the details
                                            current_time_cache = time.time()
                                            self._program_details[program_id] = {
                                                "details": details,
                                                "last_fetched": current_time_cache
                                            }

                                            _LOGGER.info(f"Added disabled program '{prog.get('name')}' ({program_id[:8]}...) to schedules from entity registry")
                                        elif details is None:
                                            # Program was deleted - mark it
                                            self._deleted_programs.add(program_id)
                                            _LOGGER.info(f"Program {program_id} from entity registry appears to be deleted")
                    except Exception as e:
                        _LOGGER.warning(f"Error checking entity registry for missing programs: {e}")

                if self.schedules:
                    # Commented out to reduce log noise (called on every update)
                    # _LOGGER.debug(f"Found {len(self.schedules)} programs for device {self.device_id}")
                    pass

                    # Fetch detailed program information for new programs and hourly refresh
                    programs_needing_details = []
                    current_time = time.time()

                    for program in self.schedules:
                        program_id = program.get("id")
                        if program_id:
                            # Skip programs that are known to be deleted
                            if program_id in self._deleted_programs:
                                # Commented out to reduce log noise
                                # _LOGGER.debug(f"Program {program_id} is in deleted set, skipping API call")
                                continue

                            # Fetch details if:
                            # 1. This is the first update (startup)
                            # 2. Program is new (not in cache)
                            # 3. Cache is stale (older than refresh interval)
                            should_fetch = False

                            if not self._first_update_complete:
                                # Force refresh all programs on first update
                                should_fetch = True
                                # Commented out to reduce log noise
                                # _LOGGER.debug(f"Program {program_id} fetching on startup")
                            elif program_id not in self._program_details:
                                should_fetch = True
                                # Commented out to reduce log noise
                                # _LOGGER.debug(f"Program {program_id} is new, will fetch details")
                            else:
                                cache_age = current_time - self._program_details[program_id]["last_fetched"]
                                if cache_age >= self._program_details_refresh_interval:
                                    should_fetch = True
                                    # Commented out to reduce log noise
                                    # _LOGGER.debug(f"Program {program_id} cache is stale ({cache_age:.0f}s), will refresh")
                                else:
                                    # Apply cached details to program if cache is still valid
                                    cached_details = self._program_details[program_id]["details"]
                                    if cached_details and "program" in cached_details:
                                        program_details = cached_details["program"]
                                        # Merge cached details into program data
                                        program["enabled"] = program_details.get("enabled", True)
                                        program["color"] = program_details.get("color", "#00A7E1")
                                        program["startOn"] = program_details.get("startOn", {})
                                        program["dailyInterval"] = program_details.get("dailyInterval", {})
                                        program["plannedRuns"] = program_details.get("plannedRuns", [])
                                        program["assignments"] = program_details.get("assignments", [])
                                        program["rainSkipEnabled"] = program_details.get("rainSkipEnabled", False)
                                        program["settings"] = program_details.get("settings", {})

                                        # Copy scheduling type fields (daysOfWeek, evenDays, oddDays)
                                        if "daysOfWeek" in program_details:
                                            program["daysOfWeek"] = program_details["daysOfWeek"]
                                        if "evenDays" in program_details:
                                            program["evenDays"] = program_details["evenDays"]
                                        if "oddDays" in program_details:
                                            program["oddDays"] = program_details["oddDays"]

                                        # Update valveIds from assignments to get complete list
                                        # (summary API may only show valves from a specific run)
                                        if program_details.get("assignments"):
                                            valve_ids = [a.get("entityId") for a in program_details["assignments"] if a.get("entityId")]
                                            if valve_ids:
                                                program["valveIds"] = valve_ids

                                        # Legacy fields for backward compatibility (may not exist for Smart Hose Timers)
                                        if program_details.get("schedule"):
                                            program["schedule"] = program_details["schedule"]
                                        if program_details.get("durationSeconds"):
                                            program["durationSeconds"] = program_details["durationSeconds"]
                                        if program_details.get("createdAt"):
                                            program["createdAt"] = program_details["createdAt"]
                                        if program_details.get("updatedAt"):
                                            program["updatedAt"] = program_details["updatedAt"]

                            if should_fetch:
                                programs_needing_details.append(program_id)

                    # Fetch program details for programs that need it
                    if programs_needing_details:
                        _LOGGER.info(f"Fetching details for {len(programs_needing_details)} program(s)")
                        programs_to_remove = []  # Track programs that failed to fetch (likely deleted)

                        for program_id in programs_needing_details:
                            _LOGGER.debug(f"Calling _fetch_program_details for program {program_id}")
                            details = await self._fetch_program_details(session, program_id, force_refresh=True)
                            if details:
                                _LOGGER.debug(f"Received details for program {program_id}: keys={list(details.keys())}")
                                # Extract the program object from the response
                                program_details = details.get("program", {})
                                _LOGGER.debug(f"Program details keys for {program_id}: {list(program_details.keys())}")

                                # Merge details into program data
                                for program in self.schedules:
                                    if program.get("id") == program_id:
                                        # Update enabled status and other details from API
                                        program["enabled"] = program_details.get("enabled", True)
                                        program["color"] = program_details.get("color", "#00A7E1")
                                        program["startOn"] = program_details.get("startOn", {})
                                        program["dailyInterval"] = program_details.get("dailyInterval", {})
                                        program["plannedRuns"] = program_details.get("plannedRuns", [])
                                        program["assignments"] = program_details.get("assignments", [])
                                        program["rainSkipEnabled"] = program_details.get("rainSkipEnabled", False)
                                        program["settings"] = program_details.get("settings", {})

                                        # Copy scheduling type fields (daysOfWeek, evenDays, oddDays)
                                        if "daysOfWeek" in program_details:
                                            program["daysOfWeek"] = program_details["daysOfWeek"]
                                        if "evenDays" in program_details:
                                            program["evenDays"] = program_details["evenDays"]
                                        if "oddDays" in program_details:
                                            program["oddDays"] = program_details["oddDays"]

                                        # Update valveIds from assignments to get complete list
                                        # (summary API may only show valves from a specific run)
                                        if program_details.get("assignments"):
                                            valve_ids = [a.get("entityId") for a in program_details["assignments"] if a.get("entityId")]
                                            if valve_ids:
                                                old_valve_ids = program.get("valveIds", [])
                                                program["valveIds"] = valve_ids
                                                _LOGGER.debug(f"Program {program_id} valveIds updated from {len(old_valve_ids)} to {len(valve_ids)} valves")

                                        # Legacy fields for backward compatibility
                                        program["schedule"] = program_details.get("schedule", {})
                                        program["durationSeconds"] = program_details.get("durationSeconds")
                                        program["createdAt"] = program_details.get("createdAt")
                                        program["updatedAt"] = program_details.get("updatedAt")

                                        _LOGGER.info(f"Updated program '{program.get('name')}' ({program_id[:8]}...) - enabled={program['enabled']}, rainSkip={program['rainSkipEnabled']}, startOn={program.get('startOn')}, interval={program.get('dailyInterval')}, plannedRuns={len(program.get('plannedRuns', []))} run(s), valves={len(program.get('valveIds', []))}")
                                        _LOGGER.debug(f"Program {program_id} now has keys: {list(program.keys())}")
                                        break
                            else:
                                # Program details returned None - likely deleted from Rachio
                                _LOGGER.warning(f"Failed to fetch details for program {program_id} - details returned None or empty (program may have been deleted)")
                                programs_to_remove.append(program_id)

                        # Remove deleted programs from cache and schedules
                        if programs_to_remove:
                            for program_id in programs_to_remove:
                                # Remove from cache
                                if program_id in self._program_details:
                                    del self._program_details[program_id]
                                    _LOGGER.info(f"Removed program {program_id} from cache (deleted from Rachio)")

                                # Remove from schedules list
                                self.schedules = [p for p in self.schedules if p.get("id") != program_id]

                                # Remove from sensor and button tracking sets
                                if hasattr(self, '_program_sensor_ids') and program_id in self._program_sensor_ids:
                                    self._program_sensor_ids.discard(program_id)
                                    _LOGGER.debug(f"Removed program {program_id} from sensor tracking")

                                if hasattr(self, '_program_button_ids') and program_id in self._program_button_ids:
                                    self._program_button_ids.discard(program_id)
                                    _LOGGER.debug(f"Removed program {program_id} from button tracking")

                                # Add to deleted programs set to prevent future API calls
                                self._deleted_programs.add(program_id)
                                _LOGGER.debug(f"Added program {program_id} to deleted programs set")

                            # Remove entities from entity registry (for both enabled and disabled entities)
                            await self._remove_program_entities(programs_to_remove)

                            _LOGGER.info(f"Removed {len(programs_to_remove)} deleted program(s) from integration")

                    # Mark first update as complete after fetching all program details
                    if not self._first_update_complete:
                        self._first_update_complete = True
                        _LOGGER.debug("First update complete - subsequent updates will use cached program details")

                    # Dynamically create sensors for new programs
                    if hasattr(self, '_program_sensor_ids') and hasattr(self, '_sensor_add_entities_callback'):
                        new_programs = []
                        for program in self.schedules:
                            program_id = program.get("id")
                            if program_id and program_id not in self._program_sensor_ids:
                                new_programs.append(program)
                                self._program_sensor_ids.add(program_id)
                                _LOGGER.info(f"Detected new program: {program.get('name', program_id)}")

                        if new_programs:
                            # Import here to avoid circular dependency
                            from .sensor import RachioSmartHoseTimerProgramSensor
                            new_sensors = [
                                RachioSmartHoseTimerProgramSensor(self.coordinator, self, program)
                                for program in new_programs
                            ]
                            self._sensor_add_entities_callback(new_sensors)
                            _LOGGER.info(f"Added {len(new_sensors)} new program sensors")

                    # Dynamically create buttons for new programs
                    if hasattr(self, '_program_button_ids') and hasattr(self, '_button_add_entities_callback'):
                        _LOGGER.debug(f"Button creation check: has _program_button_ids={hasattr(self, '_program_button_ids')}, has callback={hasattr(self, '_button_add_entities_callback')}, tracked_ids={self._program_button_ids if hasattr(self, '_program_button_ids') else 'N/A'}")
                        new_program_buttons = []
                        for program in self.schedules:
                            program_id = program.get("id")
                            _LOGGER.debug(f"Checking program {program_id} ({program.get('name')}): in_tracked_set={program_id in self._program_button_ids if program_id else 'N/A'}")
                            if program_id and program_id not in self._program_button_ids:
                                new_program_buttons.append(program)
                                self._program_button_ids.add(program_id)
                                _LOGGER.debug(f"Detected new program for button creation: {program.get('name', program_id)}")

                        if new_program_buttons:
                            # Import here to avoid circular dependency
                            from .button import RachioRefreshProgramButton
                            new_buttons = [
                                RachioRefreshProgramButton(self.coordinator, self, program)
                                for program in new_program_buttons
                            ]
                            self._button_add_entities_callback(new_buttons)
                            _LOGGER.info(f"Added {len(new_buttons)} new program refresh buttons")
                        else:
                            _LOGGER.debug(f"No new buttons to create (all {len(self.schedules)} programs already tracked)")
                else:
                    _LOGGER.debug(f"No programs configured for device {self.device_id}")

                # Detect running zones by calculating if lastWateringAction is still active
                running_zones = {}
                current_time = datetime.now()

                # Track which valves were running last cycle (to detect completions)
                previously_running = set(self.running_zones.keys())

                _LOGGER.debug(f"Checking {len(self.zones)} valves for running/completed status")

                for valve in self.zones:
                    valve_id = valve["id"]
                    state = valve.get("state", {}).get("reportedState", {})
                    last_action = state.get("lastWateringAction", {})

                    # Commented out to reduce log noise (verbose debugging)
                    # _LOGGER.debug(f"Valve {valve_id}: has lastWateringAction={last_action is not None and len(last_action) > 0}, has start={last_action.get('start') is not None}, has duration={last_action.get('durationSeconds') is not None}")

                    # Check if there's a watering action with start time and duration
                    if last_action.get("start") and last_action.get("durationSeconds"):
                        # Commented out to reduce log noise (verbose debugging)
                        # if last_action:
                        #     _LOGGER.debug(f"Valve {valve_id} lastWateringAction keys: {list(last_action.keys())}")
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
                                    # Commented out to reduce log noise
                                    # _LOGGER.debug(f"Valve {valve_id} force stopped {time_since_stop:.0f}s ago, ignoring API data")
                                    continue
                                else:
                                    # Clear old force stop tracking
                                    self._force_stopped.pop(valve_id, None)

                            # Check if we manually stopped this valve recently
                            # If so, ignore stale API data showing it's still running
                            # But still allow completion time updates for newer runs
                            if valve_id in self._last_watering_completed:
                                last_completed = self._last_watering_completed[valve_id]
                                # If the API action ended before our manual stop AND it's not currently running,
                                # this is stale data - ignore it
                                if end_time <= last_completed and start_time <= current_time <= end_time_buffer:
                                    # Commented out to reduce log noise
                                    # _LOGGER.debug(f"Valve {valve_id} ignoring stale API data showing as running (ended {end_time} vs last completed {last_completed})")
                                    continue

                            # Check if currently watering
                            if start_time <= current_time <= end_time_buffer:
                                remaining_seconds = (end_time - current_time).total_seconds()
                                running_zones[valve_id] = {
                                    "id": valve_id,
                                    "remaining": max(0, remaining_seconds),
                                    "start_time": start_time,  # Store start time for program matching
                                    "duration": duration_seconds,
                                    # Store program ID if available in lastWateringAction
                                    "program_id": last_action.get("programId") or last_action.get("program_id"),
                                }
                                # Track expected end time for completion detection
                                self._expected_end_times[valve_id] = end_time
                                # Commented out to reduce log noise (called on every update when valve is running)
                                # _LOGGER.debug(f"Valve {valve_id} is running, {remaining_seconds:.0f}s remaining, program_id={running_zones[valve_id].get('program_id')}, expected_end={end_time}")
                            elif current_time > end_time_buffer:
                                # Watering has completed, record/update completion time
                                # Always update to ensure we capture the most recent completion
                                old_completed = self._last_watering_completed.get(valve_id)
                                if valve_id not in self._last_watering_completed or self._last_watering_completed[valve_id] < end_time:
                                    self._last_watering_completed[valve_id] = end_time
                                    _LOGGER.info(f"Valve {valve_id} watering completed at {end_time} (was: {old_completed})")
                                else:
                                    # Commented out to reduce log noise
                                    # _LOGGER.debug(f"Valve {valve_id} watering already completed at {old_completed}, API end_time {end_time} is not newer")
                                    pass
                        except (ValueError, KeyError) as e:
                            _LOGGER.warning(f"Error parsing watering times for valve {valve_id}: {e}")

                # Merge API-detected running zones with optimistically-started zones
                # This preserves valves we just started that the API hasn't caught up with yet
                for zone_id, zone_data in list(self.running_zones.items()):
                    # If a zone is in our current running_zones but not detected by API
                    if zone_id not in running_zones:
                        # Check if it's still in pending_start (within 60s window)
                        if zone_id in self._pending_start:
                            import time as time_module
                            if self._pending_start[zone_id] > time_module.time():
                                # Keep it in running_zones (API just hasn't caught up yet)
                                running_zones[zone_id] = zone_data
                                _LOGGER.debug(f"Valve {zone_id} keeping optimistic running state (still in pending window)")

                self.running_zones = running_zones

                # Detect completions: valves that were running but are no longer
                # (The API removes lastWateringAction after completion, so we track expected end times)
                for valve_id in previously_running:
                    if valve_id not in running_zones and valve_id in self._expected_end_times:
                        # Valve was running but is no longer - it has completed
                        expected_end = self._expected_end_times[valve_id]
                        # Only record if this is a new or more recent completion
                        if valve_id not in self._last_watering_completed or self._last_watering_completed[valve_id] < expected_end:
                            self._last_watering_completed[valve_id] = expected_end
                            _LOGGER.info(f"Valve {valve_id} detected as completed at {expected_end} (no longer running, API removed lastWateringAction)")
                        # Clean up the expected end time
                        del self._expected_end_times[valve_id]

                # Clean up expected end times for valves that are no longer running and already recorded as completed
                for valve_id in list(self._expected_end_times.keys()):
                    if valve_id not in running_zones:
                        del self._expected_end_times[valve_id]

                # Detect running schedules by matching running valves to programs based on timing
                running_schedules = {}

                # Commented out to reduce log noise (verbose debugging)
                # if running_zones:
                #     _LOGGER.debug(f"Currently running valves: {list(running_zones.keys())}")
                #     for valve_id, zone_data in running_zones.items():
                #         _LOGGER.debug(f"  Valve {valve_id}: start_time={zone_data.get('start_time')}, remaining={zone_data.get('remaining'):.0f}s")

                # First, try to match running valves to programs using valve_run_summaries
                # This contains the actual program association from the API
                valve_to_program_map = {}  # valve_id -> program_id for currently running valves

                for valve_id, zone_data in running_zones.items():
                    valve_start_time = zone_data.get("start_time")
                    if not valve_start_time:
                        # Commented out to reduce log noise
                        # _LOGGER.debug(f"Valve {valve_id} has no start_time, skipping program matching")
                        continue

                    # First check if lastWateringAction directly provides program_id
                    direct_program_id = zone_data.get("program_id")
                    if direct_program_id:
                        valve_to_program_map[valve_id] = direct_program_id
                        _LOGGER.info(f"Valve {valve_id} matched to program {direct_program_id} via lastWateringAction.programId")
                        continue

                    # Check if this valve has a recent run in valve_run_summaries that matches timing
                    if valve_id in self.valve_run_summaries:
                        summaries = self.valve_run_summaries[valve_id]
                        # Commented out to reduce log noise (verbose debugging)
                        # _LOGGER.debug(f"Valve {valve_id} (started at {valve_start_time}) checking run summaries - previous_run: {summaries.get('previous_run') is not None}, next_run: {summaries.get('next_run') is not None}")

                        # Check previous run (most recent past run - could be currently running)
                        prev_run = summaries.get("previous_run")
                        if prev_run:
                            # Commented out to reduce log noise (verbose debugging)
                            # _LOGGER.debug(f"  previous_run: source={prev_run.get('source')}, start={prev_run.get('start')}, program_id={prev_run.get('program_id')}")
                            if prev_run.get("source") == "program":
                                run_start = prev_run.get("start")
                                if run_start:
                                    time_diff = abs((run_start - valve_start_time).total_seconds())
                                    # Commented out to reduce log noise (verbose debugging)
                                    # _LOGGER.debug(f"  Time difference: {time_diff:.0f}s (threshold: 3600s)")
                                    # If the run start time is within 1 hour of the valve start time, it's a match
                                    # Increased threshold to handle API lag for manual program runs
                                    if time_diff < 3600:
                                        program_id = prev_run.get("program_id")
                                        if program_id:
                                            valve_to_program_map[valve_id] = program_id
                                            _LOGGER.info(f"Valve {valve_id} matched to program {program_id} via previous_run timing (diff: {time_diff:.0f}s)")
                                            continue

                        # Check next run (scheduled future run - might have just started)
                        next_run = summaries.get("next_run")
                        if next_run:
                            # Commented out to reduce log noise (verbose debugging)
                            # _LOGGER.debug(f"  next_run: source={next_run.get('source')}, start={next_run.get('start')}, program_id={next_run.get('program_id')}")
                            if next_run.get("source") == "program":
                                run_start = next_run.get("start")
                                if run_start:
                                    time_diff = abs((run_start - valve_start_time).total_seconds())
                                    # Commented out to reduce log noise (verbose debugging)
                                    # _LOGGER.debug(f"  Time difference: {time_diff:.0f}s (threshold: 1800s)")
                                    # If the scheduled start time is within 30 minutes of the valve start time, it's a match
                                    if time_diff < 1800:
                                        program_id = next_run.get("program_id")
                                        if program_id:
                                            valve_to_program_map[valve_id] = program_id
                                            _LOGGER.info(f"Valve {valve_id} matched to program {program_id} via next_run timing (diff: {time_diff:.0f}s)")
                                            continue

                        # Commented out to reduce log noise
                        # _LOGGER.debug(f"Valve {valve_id} could not be matched to any program (no timing match)")
                    else:
                        # Commented out to reduce log noise
                        # _LOGGER.debug(f"Valve {valve_id} has no run summaries")
                        pass

                    # Fallback: Match based on program's next scheduled run time (from plannedRuns)
                    if valve_id not in valve_to_program_map:
                        # Find program with scheduled run closest to valve start time
                        best_match = None
                        best_time_diff = float('inf')

                        for program in self.schedules:
                            if valve_id not in program.get("valveIds", []):
                                continue  # This program doesn't use this valve

                            # Check plannedRuns for this program
                            planned_runs = program.get("plannedRuns", [])
                            for planned_run in planned_runs:
                                # plannedRuns contains start time info
                                start_info = planned_run.get("start", {})
                                if start_info:
                                    try:
                                        # Parse the planned start time
                                        year = start_info.get("year")
                                        month = start_info.get("month")
                                        day = start_info.get("day")
                                        hour = start_info.get("hour", 0)
                                        minute = start_info.get("minute", 0)

                                        if year and month and day:
                                            planned_start = datetime(year, month, day, hour, minute, tzinfo=timezone.utc)
                                            time_diff = abs((planned_start - valve_start_time).total_seconds())

                                            _LOGGER.debug(f"  Program {program.get('id')} ({program.get('name')}): planned_start={planned_start}, diff={time_diff:.0f}s")

                                            if time_diff < best_time_diff:
                                                best_time_diff = time_diff
                                                best_match = program.get("id")
                                    except Exception as e:
                                        _LOGGER.debug(f"Error parsing planned run time for program {program.get('id')}: {e}")

                        if best_match and best_time_diff < 3600:  # Within 1 hour
                            valve_to_program_map[valve_id] = best_match
                            _LOGGER.info(f"Valve {valve_id} matched to program {best_match} via plannedRuns timing (diff: {best_time_diff:.0f}s)")
                        elif best_match:
                            _LOGGER.debug(f"Best program match for valve {valve_id} is {best_match} but time diff ({best_time_diff:.0f}s) exceeds 1 hour - likely a manual run")

                # Debug: Log valve-to-program mapping
                if valve_to_program_map:
                    _LOGGER.debug(f"Valve-to-program mapping: {valve_to_program_map}")
                else:
                    _LOGGER.debug("No valves mapped to programs")

                # Now determine which programs are running based on valve-to-program mapping
                for program in self.schedules:
                    program_id = program.get("id")
                    valve_ids = program.get("valveIds", [])

                    # Check if any of this program's valves are:
                    # 1. Currently running AND
                    # 2. Mapped to this specific program (via timing analysis)
                    running_valves = []
                    for valve_id in valve_ids:
                        if valve_id in running_zones:
                            # Valve is running - check if it's mapped to this program
                            if valve_to_program_map.get(valve_id) == program_id:
                                running_valves.append(valve_id)
                                _LOGGER.debug(f"Program {program_id} ({program.get('name')}): valve {valve_id} matched")
                            elif valve_id not in valve_to_program_map:
                                # No mapping found - could be a quick run or manual run
                                # Don't attribute it to any program
                                _LOGGER.debug(f"Program {program_id} ({program.get('name')}): valve {valve_id} running but not mapped to any program")

                    is_running = len(running_valves) > 0

                    # Update the program's active state
                    program["active"] = is_running

                    if is_running:
                        # Calculate remaining time for this program (max of all its running valves)
                        max_remaining = 0
                        for valve_id in running_valves:
                            remaining = running_zones[valve_id].get("remaining", 0)
                            max_remaining = max(max_remaining, remaining)

                        program["remaining"] = max_remaining
                        running_schedules[program_id] = program
                        _LOGGER.info(f"Program {program.get('name')} ({program_id[:8]}...) is RUNNING - {len(running_valves)} valve(s) active, {max_remaining:.0f}s remaining")
                    else:
                        _LOGGER.debug(f"Program {program_id} ({program.get('name')}): not running (0 matched valves)")

                self.running_schedules = running_schedules
        except Exception as err:
            _LOGGER.error("Error updating smart hose timer: %s", err)
            raise

    def _is_valve_connected(self, zone_id):
        """Check if a specific valve is connected to the base station."""
        for valve in self.zones:
            if valve["id"] == zone_id:
                state = valve.get("state", {}).get("reportedState", {})
                return state.get("connected", False)
        return False

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
                    # Always mark as pending (for optimistic UI updates)
                    # But only add to running_zones if both base station AND valve are connected
                    self._pending_start[zone_id] = time.time() + 60
                    valve_connected = self._is_valve_connected(zone_id)
                    if self.base_station_connected and valve_connected:
                        self.running_zones[zone_id] = {"id": zone_id, "remaining": duration}
                        _LOGGER.debug(f"Valve {zone_id} start command sent - marked as running (base station and valve connected)")
                    elif not self.base_station_connected:
                        _LOGGER.warning(f"Valve {zone_id} start command sent but base station is offline - not marking as running")
                    elif not valve_connected:
                        _LOGGER.warning(f"Valve {zone_id} start command sent but valve is not connected - not marking as running")
                    return True
                resp.raise_for_status()
                try:
                    result = await resp.json()
                    self._pending_start[zone_id] = time.time() + 60
                    valve_connected = self._is_valve_connected(zone_id)
                    if self.base_station_connected and valve_connected:
                        self.running_zones[zone_id] = {"id": zone_id, "remaining": duration}
                    return result
                except Exception:
                    return True

    async def async_stop_zone(self, zone_id):
        # Immediately mark as force stopped to prevent race conditions
        now = datetime.now(timezone.utc)
        self._force_stopped[zone_id] = now

        # Only update last_watering_completed if we can confirm the valve actually ran
        # We check multiple conditions to avoid false positives:
        # 1. Valve was in running_zones (API confirmed it was running) - SAFE to record
        # 2. Valve was only in pending_start (optimistic state) - RISKY, need more checks
        should_record = False

        _LOGGER.debug(f"Valve {zone_id} stop check: in running_zones={zone_id in self.running_zones}, in pending_start={zone_id in self._pending_start}, running_zones={list(self.running_zones.keys())}, pending_start={self._pending_start}")

        if zone_id in self.running_zones:
            # Valve was confirmed running by API - safe to record
            should_record = True
            _LOGGER.debug(f"Valve {zone_id} was confirmed running - will record completion time")
        elif zone_id in self._pending_start:
            # Valve was only optimistically started - need to verify
            # Only record if base station is currently connected, valve is connected, AND valve has recent activity
            valve_connected = self._is_valve_connected(zone_id)
            if self.base_station_connected and valve_connected:
                # Check if valve has a recent lastWateringAction that started after we sent the command
                valve_actually_started = False

                # Check if we're still within the pending window (60 seconds after start command)
                # If so, trust that the valve actually started even if API hasn't caught up
                import time as time_module
                if zone_id in self._pending_start and self._pending_start[zone_id] > time_module.time():
                    # Still within 60-second window - assume valve actually started
                    valve_actually_started = True
                    pending_time_left = self._pending_start[zone_id] - time_module.time()
                    _LOGGER.debug(f"Valve {zone_id} stopped within pending window ({pending_time_left:.0f}s remaining) - assuming it started")
                else:
                    # Outside pending window - need API confirmation
                    for valve in self.zones:
                        if valve["id"] == zone_id:
                            state = valve.get("state", {}).get("reportedState", {})
                            last_action = state.get("lastWateringAction", {})
                            if last_action.get("start"):
                                try:
                                    start_str = last_action["start"]
                                    start_time = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                                    # If the last action started within the last 2 minutes, the valve likely actually ran
                                    time_since_start = (now - start_time).total_seconds()
                                    if 0 <= time_since_start <= 120:
                                        valve_actually_started = True
                                        _LOGGER.debug(f"Valve {zone_id} has recent API activity ({time_since_start:.0f}s ago) - will record completion time")
                                        break
                                except (ValueError, KeyError):
                                    pass

                should_record = valve_actually_started
                if not valve_actually_started:
                    _LOGGER.debug(f"Valve {zone_id} was pending but no recent API activity - not recording completion time")
            elif not self.base_station_connected:
                _LOGGER.debug(f"Valve {zone_id} was pending but base station is offline - not recording completion time")
            elif not valve_connected:
                _LOGGER.debug(f"Valve {zone_id} was pending but valve is not connected - not recording completion time")
            else:
                _LOGGER.debug(f"Valve {zone_id} was pending but connection checks failed - not recording completion time")

        if should_record:
            self._last_watering_completed[zone_id] = now
            _LOGGER.debug(f"Valve {zone_id} stopped - recorded completion time")

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
        now = time.time()

        # Check if we have a pending start that's still valid
        has_pending_start = zone_id in self._pending_start and self._pending_start[zone_id] > now

        # If we force stopped this valve, only consider it off if the stop was AFTER any pending start
        if zone_id in self._force_stopped:
            if has_pending_start:
                # If there's a valid pending start that hasn't expired, the start happened more recently
                pending_start_expiry = self._pending_start[zone_id]
                if pending_start_expiry > now:  # pending start is still valid
                    # This means start() was called after stop(), so the valve should be considered "on"
                    return True
            else:
                # No valid pending start, and we have a force stop, so it's off
                return False

        return zone_id in self.running_zones or has_pending_start

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
