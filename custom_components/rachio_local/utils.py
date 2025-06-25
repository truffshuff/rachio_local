from datetime import timedelta

def get_update_interval(handler) -> timedelta:
    """Get update interval based on remaining time for any handler."""
    active = False
    remaining_mins = 0
    # Check running zones and schedules
    if hasattr(handler, 'running_zones') and hasattr(handler, 'running_schedules'):
        running_zones = handler.running_zones.values() if isinstance(handler.running_zones, dict) else handler.running_zones
        running_schedules = handler.running_schedules.values() if isinstance(handler.running_schedules, dict) else handler.running_schedules
        for zone in running_zones:
            active = True
            if remaining := zone.get("remaining", 0):
                remaining_mins = max(remaining_mins, remaining / 60)
        for schedule in running_schedules:
            active = True
            if remaining := schedule.get("remaining", 0):
                remaining_mins = max(remaining_mins, remaining / 60)
    if not active:
        return timedelta(minutes=5)  # Changed from 30 to 5 minutes
    elif remaining_mins < 1:
        return timedelta(seconds=10)
    elif remaining_mins < 5:
        return timedelta(minutes=1)
    elif remaining_mins < 15:
        return timedelta(minutes=5)
    else:
        return timedelta(minutes=min(int(remaining_mins), 30))
