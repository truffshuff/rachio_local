"""Constants for the Rachio Local Control integration."""
DOMAIN = "rachio_local"
CONF_API_KEY = "api_key"

DEFAULT_SCAN_INTERVAL = 30
RACHIO_API_URL = "https://api.rach.io/1/public"

# Action constants
ACTION_START = "start"
ACTION_STOP = "stop"
ACTION_SKIP = "skip"

# State constants
STATE_WATERING = "WATERING"
STATE_IDLE = "IDLE"
STATE_SCHEDULED = "SCHEDULED"
