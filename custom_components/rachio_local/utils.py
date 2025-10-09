from datetime import timedelta, datetime, timezone
import email.utils

def get_update_interval(handler) -> timedelta:
    """Smart polling: poll based on the currently running zone's remaining time, else schedule, else idle. Pause polling if API limit exceeded."""
    # If API rate limit is exceeded, pause polling for 30 minutes (or until reset)
    if hasattr(handler, 'api_rate_remaining') and handler.api_rate_remaining is not None:
        try:
            if int(handler.api_rate_remaining) <= 0:
                # If we know the reset time, calculate the wait
                reset = handler.api_rate_reset
                if reset:
                    try:
                        # Try parsing as RFC 1123 (HTTP date)
                        reset_dt = email.utils.parsedate_to_datetime(reset)
                        now = datetime.now(timezone.utc)
                        wait = (reset_dt - now).total_seconds()
                        if wait > 0:
                            return timedelta(seconds=min(wait, 1800))  # Wait until reset, max 30 min
                    except Exception:
                        pass
                return timedelta(minutes=30)
        except Exception:
            pass
    active = False
    zone_remaining = None
    schedule_remaining = None
    # Check running zones and schedules
    if hasattr(handler, 'running_zones') and hasattr(handler, 'running_schedules'):
        running_zones = handler.running_zones.values() if isinstance(handler.running_zones, dict) else handler.running_zones
        running_schedules = handler.running_schedules.values() if isinstance(handler.running_schedules, dict) else handler.running_schedules
        # Find the zone with the minimum remaining time (should only be one active per controller)
        for zone in running_zones:
            if zone.get("remaining", 0) > 0:
                active = True
                if zone_remaining is None or zone["remaining"] < zone_remaining:
                    zone_remaining = zone["remaining"]
        # If no zone is running, check for schedule remaining
        for schedule in running_schedules:
            if schedule.get("remaining", 0) > 0:
                active = True
                if schedule_remaining is None or schedule["remaining"] < schedule_remaining:
                    schedule_remaining = schedule["remaining"]

    # Also check for pending starts (optimistic state)
    if hasattr(handler, '_pending_start') and handler._pending_start:
        import time as time_module
        now = time_module.time()
        for zone_id, expires_at in handler._pending_start.items():
            if expires_at > now:
                active = True
                # Get the actual remaining time from running_zones if available
                # (which contains the duration we sent when starting the zone)
                if zone_id in handler.running_zones:
                    pending_remaining = handler.running_zones[zone_id].get("remaining", 600)
                else:
                    # Fallback: try to get default duration from zone attributes
                    if hasattr(handler, 'get_zone_default_duration'):
                        pending_remaining = handler.get_zone_default_duration(zone_id)
                    else:
                        pending_remaining = 600  # Ultimate fallback: 10 minutes

                if zone_remaining is None or pending_remaining < zone_remaining:
                    zone_remaining = pending_remaining
    # Use zone remaining if available, else schedule, else idle
    remaining_secs = zone_remaining if zone_remaining is not None else schedule_remaining

    # Get configurable polling intervals (with defaults)
    idle_interval = getattr(handler, 'idle_polling_interval', 300)
    active_interval = getattr(handler, 'active_polling_interval', 120)

    if not active or remaining_secs is None:
        return timedelta(seconds=idle_interval)

    # When actively watering, use the active polling interval
    # For very short remaining times (< 2 minutes), poll more frequently
    remaining_mins = remaining_secs / 60
    if remaining_mins < 2:
        return timedelta(seconds=min(active_interval, 60))
    else:
        return timedelta(seconds=active_interval)
