"""CODE27 protocol and device configuration constants."""

CONF_USER_ID = "user_id"

CONF_LOCAL_SECRET = "local_secret"

CONF_MACHINE_ID = "machine_id"

CONF_CHALLENGE_ID = "challenge_id"

DEFAULT_PORT = 8081

DEFAULT_SCAN_INTERVAL = 15

MIN_SCAN_INTERVAL = 5

MAX_SCAN_INTERVAL = 120

CONF_SCAN_INTERVAL = "scan_interval"

CONF_REMOTE_RECOVERY_ENABLED = "remote_recovery_enabled"

# Firmware >= 1.6.0: MCP Bridge ("<entry_id>:<subentry_id>") whose Spells
# Codie may cast through the tool gateway; "" / unset = tools off.
CONF_CODIE_TOOL_BRIDGE = "codie_tool_bridge"

# Firmware >= 1.6.0: conversation agent that receives the user's sentence
# from Codie's ``home_command`` tool (normally a HASE Spellbooker agent; HA's
# default Assist answers whatever it does not know). Unset = no home_command.
CONF_CODIE_AGENT = "codie_agent"
# Area limit for Codie's sentences (HASE-side): unrestricted, or only these areas.
CONF_CODIE_ALLOWED_AREAS = "codie_allowed_areas"
CONF_CODIE_UNRESTRICTED = "codie_unrestricted"

MUSIC_SOURCE_LOCAL_TF = "LOCAL_TF"

MUSIC_SOURCE_BLUETOOTH = "BLUETOOTH"

MUSIC_SOURCES = [MUSIC_SOURCE_LOCAL_TF, MUSIC_SOURCE_BLUETOOTH]

# Key of this provider's own shared bucket inside ``hass.data[DOMAIN]``.
# The CODE27 music runtime lives there (see providers/code27/music_runtime.py),
# next to the per-device coordinators and the "mcp" bucket - so a provider-owned
# feature never introduces HASE-wide state.
DATA_CODE27 = "code27"

ALARM_DAY_BITS = {
    "sun": 1,
    "mon": 2,
    "tue": 4,
    "wed": 8,
    "thu": 16,
    "fri": 32,
    "sat": 64,
}

ALARM_WEEK_ORDER = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

# Option: keep one switch entity per alarm (the pre-existing behaviour). Off
# removes them again - alarms are then edited through the "Alarms" to-do list
# and the dashboard card instead.
CONF_ALARM_SWITCHES = "alarm_switches"


def alarm_days(mask: int) -> list[str]:
    """Weekday keys (Monday first) set in an alarm's ``repeat_mask``."""
    return [day for day in ALARM_WEEK_ORDER if int(mask or 0) & ALARM_DAY_BITS[day]]


MANUFACTURER = "CODE27"

MODEL = "CODIE"

LIGHT_EFFECT_STATIC = "static"

LIGHT_EFFECT_BREATHE = "breathe"

LIGHT_EFFECT_PULSE = "pulse"

LIGHT_EFFECT_BLINK = "blink"

LIGHT_EFFECT_CHASE = "chase"

LIGHT_EFFECT_TO_TYPE = {
    LIGHT_EFFECT_STATIC: "STATIC",
    LIGHT_EFFECT_BREATHE: "BREATHE",
    LIGHT_EFFECT_PULSE: "PULSE",
    LIGHT_EFFECT_BLINK: "BLINK",
    LIGHT_EFFECT_CHASE: "CHASE",
}

# Public values used by older scenes and automations remain accepted.
LIGHT_EFFECT_ALIASES = {
    "Statisch": "static", "Atmen": "breathe", "Pulsieren": "pulse",
    "Blinken": "blink", "Lauflicht": "chase",
}

DEFAULT_AMBIENT_RGB = (255, 200, 120)

DEFAULT_AMBIENT_BRIGHTNESS = 200
