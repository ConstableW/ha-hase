"""HASE integration identity and agent configuration keys."""

DOMAIN = "hase"
# hass.data key: {HA device id: frozenset of allowed area ids} for CODE27 devices with an area limit.
CODIE_AREA_LIMITS_KEY = "hase_codie_area_limits"

CONF_ENTRY_KIND = "entry_kind"

ENTRY_KIND_CODE27_DEVICE = "code27_device"

ENTRY_KIND_CODE27_EXTERNAL_HUB = "code27_external_hub"

CONF_CODE27_EXTERNAL_ENTRY_ID = "code27_entry_id"

ENTRY_KIND_SPELLBOOKER = "spellbooker"

ENTRY_KIND_SPELLBOOKER_SATELLITE = "spellbooker_satellite"

SUBENTRY_TYPE_SATELLITE = "satellite"

ENTRY_KIND_MCP_SERVER = "mcp_server"

SUBENTRY_TYPE_MCP_BRIDGE = "mcp_bridge"

CONF_SPELLBOOKER_MODE = "spellbooker_mode"

SPELLBOOKER_MODE_SPELLBOOK_ONLY = "spellbook_only"

DEFAULT_SPELLBOOKER_MODE = SPELLBOOKER_MODE_SPELLBOOK_ONLY

CONF_SPELLBOOKER_BOOK = "spellbooker_book"

CONF_SPELLBOOKER_ALLOWED_AREAS = "spellbooker_allowed_areas"

CONF_SPELLBOOKER_UNRESTRICTED = "spellbooker_unrestricted"

CONF_SPELLBOOKER_QUERIES_ALLOWED_AREAS = "spellbooker_queries_allowed_areas"

CONF_SPELLBOOKER_QUERIES_UNRESTRICTED = "spellbooker_queries_unrestricted"

CONF_SPELLBOOKER_NO_MATCH_RESPONSE = "spellbooker_no_match_response"

CONF_SPELLBOOKER_PROFILE_ID = "spellbooker_profile_id"

# Shared by standalone Spellbookers and provider frontends. Kept in the
# integration contract rather than imported from any optional provider.
