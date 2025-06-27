# Changelog

All notable changes to this project will be documented in this file.

## v2.3.1 (2025-06-27)
- Fix: Smart Hose Timer switch and sensor now correctly reflect stopped state when stopped from either the app or Home Assistant.
- Improved: Enhanced polling logic for Smart Hose Timer to detect active/inactive valves based on reported state and last watering action.
- Chore: Bump manifest version to 2.3.1.

## v2.3.0 (2025-06-27)
- Fix: Zone and schedule sensors now always reflect the true running state, even for manual runs started from the Rachio app.
- Improved: Polling logic is now fully dynamic and API-efficient, with interval and device count exposed via a Home Assistant sensor.
- Fix: Polling status sensor now correctly shows the total number of devices/controllers.
- Improved: API call sensor resets and tracks calls per window more accurately.
- Refactor: Data structure and setup logic for better multi-device support and diagnostics.
- Debug: Enhanced logging for all state transitions and API responses.

## v2.2.0 (2025-06-23)
- Feature: Added support for starting and stopping schedules using the Rachio ScheduleRuleService API (`/public/schedulerule/start` and `/public/schedulerule/stop`).
- No changes to zone logic; zone start/stop remains as before.

## v2.0.1 (2025-06-23)
- Fix: Prevent KeyError if no schedule is running (safe access to schedule id in controller).

## v2.0.0 (2025-06-20)
- Major refactor: Device handler logic split into dedicated files for controllers and smart hose timers.
- Added full support for Rachio Smart Hose Timer devices (valves, battery, last watered, etc.).
- Improved and fixed optimistic timer and state clearing logic for both device types.
- Persistent caching for last watered on smart hose timer.
- Added new diagnostic sensors: battery, paused, on, rain sensor tripped, and schedule status.
- Rain delay control now includes a switch and a dropdown (select entity) for duration.
- Removed connection sensor for smart hose timers (still present for controllers).
- Improved Home Assistant compatibility and removed warnings about device_class/unit_of_measurement.
- Robust error handling and more efficient polling logic.
- All sensors/entities are now registered with the correct handler/coordinator.
- Many bug fixes and code cleanups.

## v1.2.0 (2024-12-10)
- Feature: Added rain delay control and improved error handling.
- Feature: Added support for multiple controllers.
- Fix: Improved compatibility with Home Assistant 2024.x releases.

## v1.1.0 (2024-10-05)
- Feature: Added support for zone last watered sensor.
- Feature: Added connection status sensor.
- Fix: Improved polling reliability and reduced API calls.

## v1.0.0 (2024-08-01)
- Initial release: Basic support for Rachio controllers and zone switches in Home Assistant.

---

### Breaking Change in v2.0.0
- The device handler logic was refactored and split into dedicated files for controllers and smart hose timers.
- Entity unique IDs and structure may have changed. You may need to reconfigure or re-add entities in Home Assistant after upgrading to v2.0.0.
